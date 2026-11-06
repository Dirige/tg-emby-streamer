import asyncio
import logging
import re
from app.config import settings
from app.database import async_session
from app.models import Media, ChannelRule, Category
from app.media.parser import parse_media_info
from app.media.strm import generate_strm, resolution_tag
from app.telegram.client import get_client
from sqlalchemy import select
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_DURATION_SECONDS = 300


def is_video_message(message) -> bool:
    if message.video:
        return True
    if message.document and message.document.mime_type:
        return message.document.mime_type.startswith("video/")
    return False


async def _get_strm_subdir(category: str) -> str | None:
    if not category:
        return None
    async with async_session() as session:
        result = await session.execute(
            select(Category).where(Category.name == category)
        )
        cat = result.scalar_one_or_none()
        if cat and cat.strm_dir:
            return cat.strm_dir
    return None


async def _get_rule_for_channel(chat_id: int) -> ChannelRule | None:
    async with async_session() as session:
        result = await session.execute(
            select(ChannelRule).where(ChannelRule.channel_id == str(chat_id))
        )
        return result.scalar_one_or_none()


async def _warmup_peer_cache(client):
    try:
        count = 0
        async for _ in client.get_dialogs():
            count += 1
        logger.info(f"Peer cache warmed up: {count} dialogs")
    except Exception as e:
        logger.warning(f"Peer cache warmup failed: {e}")


async def _safe_get_chat(client, channel_id: int):
    try:
        return await client.get_chat(channel_id)
    except Exception as e:
        err = str(e).lower()
        if "peer id invalid" in err or "chat id invalid" in err:
            logger.info(f"Peer id invalid for {channel_id}, warming up cache...")
            await _warmup_peer_cache(client)
            try:
                return await client.get_chat(channel_id)
            except Exception as e2:
                raise e2
        raise


async def _send_notify(message: str):
    from app.telegram.bot import send_notify as bot_send
    await bot_send(message)


def _build_scan_notify(ch_name: str, count: int, category: str, items: list[dict]) -> str:
    """兼容入口（也可直接用 app.telegram.bot.build_scan_notify）。"""
    from app.telegram.bot import build_scan_notify
    return build_scan_notify(ch_name, count, category, items)


async def _merge_part_record(channel_id: int, display_name: str, message_id: int, media, duration: int) -> tuple[bool, int | None]:
    """尝试把 part>1 分片合并进已有的主记录。

    返回 (是否已处理, 关联的主记录 id)。
    """
    async with async_session() as session:
        existing = await session.execute(
            select(Media).where(
                Media.chat_id == str(channel_id),
                Media.title == display_name,
                Media.parts.is_(None),
            ).limit(1)
        )
        base_record = existing.scalar_one_or_none()
        if not base_record:
            return False, None
        old_parts = base_record.parts or ""
        base_record.parts = (old_parts + "," + str(message_id)).strip(",")
        base_record.size = (base_record.size or 0) + (media.file_size or 0)
        base_record.duration = (base_record.duration or 0) + duration
        await session.commit()
        return True, base_record.id


async def refresh_library_async():
    from app.media.emby_notifier import refresh_library
    try:
        await refresh_library()
    except Exception as e:
        logger.warning(f"Emby refresh 异常: {e}")


async def scan_channel(channel_id: int, limit: int = 200, notify_detailed: bool = True, item_callback=None) -> dict:
    try:
        client = await get_client()

        try:
            chat = await _safe_get_chat(client, channel_id)
            ch_name = chat.title or str(channel_id)
            ch_username = chat.username or None
        except Exception as e:
            return {"success": False, "message": f"无法访问频道: {e}"}

        rule = await _get_rule_for_channel(channel_id)
        if not rule or not rule.enabled:
            return {"success": False, "message": f"频道 {channel_id} 未配置规则或规则未启用"}

        episode_digits = 3 if rule.category == "动漫" else 2
        strm_subdir = await _get_strm_subdir(rule.category)
        count = 0
        enrich_ids: list[int] = []
        deferred_parts: list[dict] = []
        scanned_items: list[dict] = []
        async for message in client.get_chat_history(channel_id, limit=limit):
            if not is_video_message(message):
                continue

            media = message.video or message.document
            duration = getattr(media, 'duration', None) or 0

            if duration < MIN_DURATION_SECONDS and duration > 0:
                logger.info(f"Skipped short video msg={message.id} duration={duration}s")
                continue

            fn = media.file_name or f"video_{message.id}.mp4"
            caption = message.caption or ""

            info = parse_media_info(
                caption=caption,
                title_rule=rule.title_rule,
                episode_rule=rule.episode_rule,
                season=rule.season,
                season_rule=rule.season_rule,
                year_rule=rule.year_rule,
                category=rule.category,
                filename=fn,
                filename_rule=rule.filename_rule,
            )

            if info and info.get("title"):
                title = info["title"]
                season_val = info.get("season")
                episode_val = info.get("episode")
                if season_val is not None and episode_val is not None:
                    ep_str = f"E{int(episode_val):0{episode_digits}d}"
                    display_name = f"{title}-S{int(season_val):02d}{ep_str}"
                else:
                    display_name = title
            else:
                title = None
                display_name = fn

            source_link = getattr(message, "link", None) or (
                f"https://t.me/{ch_username}/{message.id}" if ch_username
                else f"https://t.me/c/{str(channel_id).lstrip('-')}/{message.id}"
            )

            part_match = re.search(r"[_.]\s*part(\d+)", fn, re.I)
            part_num = int(part_match.group(1)) if part_match else 0

            if part_num > 1 and title:
                merged, base_id = await _merge_part_record(channel_id, display_name, message.id, media, duration)
                if merged:
                    if base_id:
                        enrich_ids.append(base_id)
                    logger.info(f"合并分片 msg={message.id}")
                    continue
                deferred_parts.append(dict(
                    message_id=message.id, chat_id=str(channel_id),
                    file_name=fn, caption=caption, title=title,
                    display_name=display_name, source_link=source_link,
                    part_num=part_num, info=info,
                    file_size=media.file_size or 0, duration=duration,
                    mime_type=media.mime_type or "video/mp4",
                    width=getattr(media, "width", 0) or 0,
                    height=getattr(media, "height", 0) or 0,
                ))
                continue

            strm_path = generate_strm(
                message_id=message.id,
                chat_id=str(channel_id),
                category=rule.category,
                title=title,
                season=info.get("season") if info else None,
                episode=info.get("episode") if info else None,
                year=info.get("year") if info else None,
                episode_digits=episode_digits,
                strm_subdir=strm_subdir,
                resolution=resolution_tag(getattr(media, "width", 0) or 0, getattr(media, "height", 0) or 0),
            )

            # 去重 + 插入在同一个 session 里完成：
            # (chat_id, message_id) 唯一约束兜底，同一条消息绝不重复入库
            async with async_session() as session:
                existing = await session.execute(
                    select(Media).where(
                        Media.chat_id == str(channel_id),
                        Media.message_id == message.id,
                    )
                )
                if existing.scalar_one_or_none():
                    if item_callback:
                        item_callback(
                            title=fn,
                            category=rule.category,
                            strm_path=None,
                            action="skipped",
                            message="已存在",
                        )
                    continue

                record = Media(
                    message_id=message.id,
                    chat_id=str(channel_id),
                    file_name=fn,
                    caption=caption,
                    file_id=getattr(media, "file_id", None),
                    file_unique_id=getattr(media, "file_unique_id", None),
                    size=media.file_size or 0,
                    duration=duration,
                    mime_type=media.mime_type or "video/mp4",
                    width=getattr(media, "width", 0) or 0,
                    height=getattr(media, "height", 0) or 0,
                    category=rule.category,
                    title=display_name,
                    source_link=source_link,
                    channel_rule_id=rule.id,
                    strm_path=str(strm_path) if strm_path else None,
                )
                session.add(record)
                try:
                    await session.commit()
                except Exception:
                    # 并发/重复竞争：唯一索引兜底
                    await session.rollback()
                    if item_callback:
                        item_callback(
                            title=fn,
                            category=rule.category,
                            strm_path=None,
                            action="skipped",
                            message="已存在",
                        )
                    continue
                enrich_ids.append(record.id)
                count += 1
                if item_callback:
                    item_callback(
                        title=display_name,
                        category=rule.category,
                        strm_path=str(strm_path) if strm_path else None,
                        action="added",
                    )
                scanned_items.append({
                    "file_name": fn,
                    "category": rule.category,
                    "source_link": source_link,
                    "episode": info.get("episode") if info else None,
                })

        for dp in deferred_parts:
            strm_path = generate_strm(
                message_id=dp["message_id"],
                chat_id=dp["chat_id"],
                category=rule.category,
                title=dp["title"],
                season=dp["info"].get("season") if dp["info"] else None,
                episode=dp["info"].get("episode") if dp["info"] else None,
                year=dp["info"].get("year") if dp["info"] else None,
                episode_digits=episode_digits,
                strm_subdir=strm_subdir,
                resolution=resolution_tag(dp["width"], dp["height"]),
            )
            async with async_session() as session:
                record = Media(
                    message_id=dp["message_id"],
                    chat_id=dp["chat_id"],
                    file_name=dp["file_name"],
                    caption=dp["caption"],
                    size=dp["file_size"],
                    duration=dp["duration"],
                    mime_type=dp["mime_type"],
                    width=dp["width"],
                    height=dp["height"],
                    category=rule.category,
                    title=dp["display_name"],
                    source_link=dp["source_link"],
                    channel_rule_id=rule.id,
                    strm_path=str(strm_path) if strm_path else None,
                )
                session.add(record)
                try:
                    await session.commit()
                except Exception:
                    await session.rollback()
                    continue
                enrich_ids.append(record.id)
                count += 1
                if item_callback:
                    item_callback(
                        title=dp.get("display_name") or dp.get("title") or dp.get("file_name", ""),
                        category=rule.category,
                        strm_path=str(strm_path) if strm_path else None,
                        action="added",
                    )
                scanned_items.append({
                    "title": dp.get("display_name") or dp.get("title") or dp.get("file_name", ""),
                    "file_name": dp["file_name"],
                    "category": rule.category,
                    "source_link": dp.get("source_link", ""),
                    "episode": dp["info"].get("episode") if dp.get("info") else None,
                })

        if count > 0:
            # 刮削开启且配了 TMDB：通知推迟到整批刮削完成，届时带上海报图片
            from app.media.enrich_queue import enrich_queue
            deferred_notify = enrich_queue.enqueue_batch(enrich_ids, {
                "ch_name": ch_name,
                "category": rule.category or "未分类",
            })
            if not deferred_notify:
                # 未启用刮削 → 立即发文字通知（原行为）
                if notify_detailed:
                    from app.telegram.bot import build_scan_notify
                    notify_text = build_scan_notify(ch_name, count, rule.category or "未分类", scanned_items)
                else:
                    notify_text = (
                        f"📦 **{ch_name}** 扫描完成\n\n"
                        f"新增: **{count}** 条\n"
                        f"分类: {rule.category or '未分类'}"
                    )
                asyncio.create_task(_send_notify(notify_text))
                if settings.emby.url and settings.emby.api_key:
                    asyncio.create_task(refresh_library_async())
        return {"success": True, "message": f"频道 {ch_name} 扫描完成，新增 {count} 条记录"}

    except ConnectionError as e:
        if "NEED_LOGIN" in str(e):
            return {"success": False, "message": "Telegram 未登录，请在设置页面完成登录"}
        return {"success": False, "message": f"连接失败: {e}"}
    except Exception as e:
        logger.error(f"Scan channel error: {e}")
        return {"success": False, "message": f"扫描失败: {e}"}
