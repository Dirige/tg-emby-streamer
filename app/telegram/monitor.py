import asyncio
import logging
import re
from app.config import settings
from app.database import async_session
from app.models import Media, ChannelRule, Category, UnrecognizedMedia
from app.media.parser import parse_media_info
from app.media.strm import generate_pending_strm, move_strm_to_final, remove_old_strm
from app.media.enricher import enrich_media_safe, add_to_unrecognized, identify_media
from app.media.emby_notifier import refresh_library
from app.telegram.client import get_client, invalidate_saved_session, is_invalid_auth_key_error
from app.media.dedupe import is_same_message_stored, find_same_version
from sqlalchemy import select
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_DURATION_SECONDS = 300

_notify_queue: asyncio.Queue = asyncio.Queue()
_scan_lock = asyncio.Lock()

# TMDB 刮削并发上限：批量扫描时上千 create_task 会瞬间打满 TMDB 镜像
_enrich_sem = asyncio.Semaphore(4)


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
    from app.telegram.bot import build_scan_notify
    return build_scan_notify(ch_name, count, category, items)


def _episode_digits(category: str | None) -> int:
    return 3 if category == "动漫" else 2


def _display_title(title: str, season, episode, episode_digits: int) -> str:
    if title and season is not None and episode is not None:
        ep_str = f"E{int(episode):0{episode_digits}d}"
        return f"{title}-S{int(season):02d}{ep_str}"
    return title or ""


async def _enrich_record_later(media_id: int):
    if not settings.enrich.enabled:
        return
    async with _enrich_sem:
        enrich_result = await enrich_media_safe(media_id, source="auto")
    if not enrich_result.get("success") and settings.tmdb.api_key:
        await add_to_unrecognized(media_id, enrich_result.get("reason"))


async def _place_record_after_identify(
    record_id: int,
    pending_path,
    hint_category: str,
    title: str | None,
    file_name: str,
    info: dict | None,
    message_id: int,
) -> dict:
    season = info.get("season") if info else None
    episode = info.get("episode") if info else None
    year = info.get("year") if info else None

    identify_result = await identify_media(
        record_id,
        hint_category=hint_category,
        season=season,
        episode=episode,
        year=year,
    )

    if not identify_result.get("success"):
        await add_to_unrecognized(record_id, identify_result.get("reason"))
        return {
            "category": "未识别",
            "strm_path": str(pending_path) if pending_path else None,
            "message": identify_result.get("reason"),
        }

    final_category = identify_result.get("category") or hint_category or "未识别"
    final_title = identify_result.get("tmdb_title") or title or file_name
    final_year = identify_result.get("tmdb_year") or year
    final_episode_digits = _episode_digits(final_category)
    strm_subdir = await _get_strm_subdir(final_category)
    final_path = move_strm_to_final(
        current_path=pending_path,
        message_id=message_id,
        category=final_category,
        title=final_title,
        season=season,
        episode=episode,
        year=final_year,
        episode_digits=final_episode_digits,
        strm_subdir=strm_subdir,
    )
    final_display = _display_title(final_title, season, episode, final_episode_digits) or file_name

    async with async_session() as session:
        record = await session.get(Media, record_id)
        if record:
            record.category = final_category
            record.title = final_display
            record.strm_path = str(final_path) if final_path else str(pending_path) if pending_path else None
            await session.commit()

    if final_path:
        asyncio.create_task(_enrich_record_later(record_id))

    return {
        "category": final_category,
        "strm_path": str(final_path) if final_path else str(pending_path) if pending_path else None,
        "message": identify_result.get("reason"),
    }


async def scan_channel(channel_id: int, limit: int = 200, notify_detailed: bool = True, item_callback=None) -> dict:
    if _scan_lock.locked():
        logger.info(f"扫描 {channel_id} 等待当前扫描任务完成")
    async with _scan_lock:
        return await _scan_channel_locked(channel_id, limit, notify_detailed, item_callback)


async def _scan_channel_locked(channel_id: int, limit: int = 200, notify_detailed: bool = True, item_callback=None) -> dict:
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

        episode_digits = _episode_digits(rule.category)
        count = 0
        deferred_parts: list[dict] = []
        scanned_items: list[dict] = []
        link_mode = bool(getattr(rule, "link_mode", False))

        def _extract_tme_links(text: str) -> list:
            # 提取 t.me 消息链接 → [(peer, msg_id, username)]；peer 为 -100xxx 整型或用户名
            links = []
            for internal, username, mid in re.findall(
                r"(?:https?://)?t\.me/(?:c/(\d{5,})|([A-Za-z][A-Za-z0-9_]{3,30}))/?(\d{1,12})",
                text or "",
            ):
                peer = int("-100" + internal) if internal else username
                links.append((peer, int(mid), username))
            return links

        async def _resolve_link_targets(message) -> list:
            """集合频道(link_mode)模式：把消息文本里的 t.me 链接取回为视频消息。
            返回 [(实际频道 chat_id, message, username)]，取回失败/非视频的跳过。"""
            targets = []
            for peer, mid, username in _extract_tme_links(message.text or message.caption or ""):
                try:
                    msgs = await client.get_messages(peer, message_ids=[mid])
                except Exception as e:
                    logger.warning(f"链接取回失败 {peer}/{mid}: {e}")
                    continue
                if not isinstance(msgs, list):
                    msgs = [msgs]
                for tm in msgs:
                    if not tm or getattr(tm, "empty", False) or not is_video_message(tm):
                        continue
                    cid = tm.chat.id if tm.chat else peer
                    targets.append((cid, tm, username))
            return targets

        async def _process_video(message, chat_id: int, rule_obj, ch_username=None) -> None:
            """处理单条视频消息入库（chat_id = 消息实际所在频道；ch_username 供 source_link）"""
            nonlocal count
            media = message.video or message.document
            duration = getattr(media, 'duration', None) or 0

            # 每频道可设最短入库时长（rule.min_duration），留空用全局默认 300s
            min_dur = rule_obj.min_duration if getattr(rule_obj, "min_duration", None) is not None else MIN_DURATION_SECONDS
            if duration < min_dur and duration > 0:
                logger.info(f"Skipped short video msg={message.id} duration={duration}s < {min_dur}s")
                return

            fn = media.file_name or f"video_{message.id}.mp4"
            caption = message.caption or ""

            source_link = getattr(message, "link", None) or (
                f"https://t.me/{ch_username}/{message.id}" if ch_username
                else f"https://t.me/c/{str(chat_id).lstrip('-')}/{message.id}"
            )

            async with async_session() as session:
                result = await session.execute(
                    select(Media).where(
                        Media.chat_id == str(chat_id),
                        Media.message_id == message.id,
                    )
                )
                if result.scalar_one_or_none():
                    if item_callback:
                        item_callback(
                            title=fn,
                            category=rule_obj.category,
                            strm_path=None,
                            action="skipped",
                            message="已存在",
                        )
                    return

            info = parse_media_info(
                caption=caption,
                title_rule=rule_obj.title_rule,
                episode_rule=rule_obj.episode_rule,
                season=rule_obj.season,
                season_rule=rule_obj.season_rule,
                year_rule=rule_obj.year_rule,
                category=rule_obj.category,
                filename=fn,
                filename_rule=rule_obj.filename_rule,
            )

            if not info or not info.get("title"):
                # 解析不出标题：不入库（避免 "IMG 0411" 这类垃圾名污染媒体库），
                # 转"未识别"页等人工处理；未来补好规则后点击识别即可入库
                async with async_session() as session:
                    dupe = await session.execute(
                        select(UnrecognizedMedia).where(
                            UnrecognizedMedia.message_id == message.id,
                            UnrecognizedMedia.chat_id == str(chat_id),
                        )
                    )
                    if not dupe.scalar_one_or_none():
                        session.add(UnrecognizedMedia(
                            message_id=message.id,
                            chat_id=str(chat_id),
                            file_name=fn,
                            caption=caption,
                            source_link=source_link,
                            category=rule_obj.category,
                            parsed_title=None,
                            reason="无法解析标题（caption 无规则可提取且文件名非描述性）",
                        ))
                        await session.commit()
                        logger.info(f"msg={message.id}「{fn[:40]}」入库失败转未识别")
                if item_callback:
                    item_callback(
                        title=fn[:50], category=rule_obj.category, strm_path=None,
                        action="unrecognized", message="解析失败",
                    )
                return

            title = info["title"]
            season_val = info.get("season")
            episode_val = info.get("episode")
            ep_digits = _episode_digits(rule_obj.category)
            if season_val is not None and episode_val is not None:
                ep_str = f"E{int(episode_val):0{ep_digits}d}"
                display_name = f"{title}-S{int(season_val):02d}{ep_str}"
            else:
                display_name = title

            part_match = re.search(r"[_.]\s*part(\d+)", fn, re.I)
            part_num = int(part_match.group(1)) if part_match else 0

            # 同版本去重：同频道 + 同标题(含集数) + 同分辨率视为已存在的同一版本，跳过。
            # 不同分辨率(如 4K/1080p) → 不同版本，各保留；
            # 分片(part.x)文件不参与（有自己的合并逻辑）；分辨率未知时不判定。
            if part_num == 0 and title:
                dup_msg = await find_same_version(
                    chat_id, display_name,
                    getattr(media, "width", 0) or 0, getattr(media, "height", 0) or 0,
                )
                if dup_msg:
                    logger.info(
                        f"跳过同版本重复 msg={message.id} 「{display_name}」(已有 msg={dup_msg})"
                    )
                    if item_callback:
                        item_callback(
                            title=display_name,
                            category=rule_obj.category,
                            strm_path=None,
                            action="skipped",
                            message="已存在同版本",
                        )
                    return

            if part_num > 1 and title:
                async with async_session() as session:
                    existing = await session.execute(
                        select(Media).where(
                            Media.chat_id == str(chat_id),
                            Media.title == display_name,
                            Media.parts.is_(None),
                        ).limit(1)
                    )
                    base_record = existing.scalar_one_or_none()
                    if base_record:
                        old_parts = base_record.parts or ""
                        new_parts = (old_parts + "," + str(message.id)).strip(",")
                        base_record.parts = new_parts
                        base_record.size = (base_record.size or 0) + (media.file_size or 0)
                        base_record.duration = (base_record.duration or 0) + duration
                        await session.commit()
                        logger.info(f"合并分片 msg={message.id} → 主消息 {base_record.message_id}")
                        return
                deferred_parts.append(dict(
                    message_id=message.id, chat_id=str(chat_id),
                    file_name=fn, caption=caption, title=title,
                    display_name=display_name, source_link=source_link,
                    part_num=part_num, info=info,
                    rule_id=rule_obj.id, category=rule_obj.category,
                    file_size=media.file_size or 0, duration=duration,
                    mime_type=media.mime_type or "video/mp4",
                    width=getattr(media, "width", 0) or 0,
                    height=getattr(media, "height", 0) or 0,
                ))
                return

            pending_path = generate_pending_strm(
                message_id=message.id,
                title=title or display_name,
                file_name=fn,
            )

            async with async_session() as session:
                record = Media(
                    message_id=message.id,
                    chat_id=str(chat_id),
                    file_name=fn,
                    caption=caption,
                    file_id=getattr(media, "file_id", None),
                    file_unique_id=getattr(media, "file_unique_id", None),
                    size=media.file_size or 0,
                    duration=duration,
                    mime_type=media.mime_type or "video/mp4",
                    width=getattr(media, "width", 0) or 0,
                    height=getattr(media, "height", 0) or 0,
                    category=rule_obj.category,
                    title=display_name,
                    source_link=source_link,
                    channel_rule_id=rule_obj.id,
                    strm_path=str(pending_path) if pending_path else None,
                )
                session.add(record)
                await session.commit()
                record_id = record.id

            place_result = await _place_record_after_identify(
                record_id=record_id,
                pending_path=pending_path,
                hint_category=rule_obj.category,
                title=title,
                file_name=fn,
                info=info,
                message_id=message.id,
            )
            count += 1
            if item_callback:
                item_callback(
                    title=display_name,
                    category=place_result.get("category") or rule_obj.category,
                    strm_path=place_result.get("strm_path"),
                    action="added",
                    message=place_result.get("message"),
                )
            scanned_items.append({
                "file_name": fn,
                "category": place_result.get("category") or rule_obj.category,
                "source_link": source_link,
                "episode": info.get("episode") if info else None,
            })

        async for message in client.get_chat_history(channel_id, limit=limit):
            if not is_video_message(message):
                if link_mode:
                    # 集合频道模式：非视频消息 → 解析文本里的 t.me 链接转发入库
                    for t_chat_id, t_msg, t_username in await _resolve_link_targets(message):
                        t_rule = await _get_rule_for_channel(t_chat_id)
                        if not t_rule:
                            t_rule = rule
                        await _process_video(t_msg, t_chat_id, t_rule, ch_username=t_username)
                continue

            await _process_video(message, channel_id, rule, ch_username=ch_username)

        for dp in deferred_parts:
            async with async_session() as session:
                existing = await session.execute(
                    select(Media).where(
                        Media.chat_id == dp["chat_id"],
                        Media.title == dp["display_name"],
                        Media.parts.is_(None),
                    ).limit(1)
                )
                base_record = existing.scalar_one_or_none()
                if base_record:
                    old_parts = base_record.parts or ""
                    new_parts = (old_parts + "," + str(dp["message_id"])).strip(",")
                    base_record.parts = new_parts
                    base_record.size = (base_record.size or 0) + dp["file_size"]
                    base_record.duration = (base_record.duration or 0) + dp["duration"]
                    await session.commit()
                    logger.info(f"合并延迟分片 msg={dp['message_id']} → 主消息 {base_record.message_id}")
                    continue

            pending_path = generate_pending_strm(
                message_id=dp["message_id"],
                title=dp["title"] or dp["display_name"],
                file_name=dp["file_name"],
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
                    category=dp["category"],
                    title=dp["display_name"],
                    source_link=dp["source_link"],
                    channel_rule_id=dp["rule_id"],
                    strm_path=str(pending_path) if pending_path else None,
                )
                session.add(record)
                await session.commit()
                record_id = record.id

            place_result = await _place_record_after_identify(
                record_id=record_id,
                pending_path=pending_path,
                hint_category=dp["category"],
                title=dp.get("title"),
                file_name=dp.get("file_name", ""),
                info=dp.get("info"),
                message_id=dp["message_id"],
            )
            count += 1
            if item_callback:
                item_callback(
                    title=dp.get("display_name") or dp.get("title") or dp.get("file_name", ""),
                    category=place_result.get("category") or dp["category"],
                    strm_path=place_result.get("strm_path"),
                    action="added",
                    message=place_result.get("message"),
                )
            scanned_items.append({
                "title": dp.get("display_name") or dp.get("title") or dp.get("file_name", ""),
                "file_name": dp.get("file_name", ""),
                "category": place_result.get("category") or dp["category"],
                "source_link": dp.get("source_link", ""),
                "episode": dp["info"].get("episode") if dp.get("info") else None,
            })

        if count > 0:
            if notify_detailed:
                notify_text = _build_scan_notify(
                    ch_name, count, rule.category or "未分类", scanned_items
                )
            else:
                notify_text = (
                    f"📦 **{ch_name}** 扫描完成\n\n"
                    f"新增: **{count}** 条\n"
                    f"分类: {rule.category or '未分类'}"
                )
            asyncio.create_task(_send_notify(notify_text))
            if settings.emby.url and settings.emby.api_key and settings.enrich.enabled:
                asyncio.create_task(refresh_library())
        return {"success": True, "message": f"频道 {ch_name} 扫描完成，新增 {count} 条记录"}

    except ConnectionError as e:
        if "NEED_LOGIN" in str(e):
            return {"success": False, "message": "Telegram 未登录，请在设置页面完成登录"}
        return {"success": False, "message": f"连接失败: {e}"}
    except Exception as e:
        if is_invalid_auth_key_error(e):
            await invalidate_saved_session(str(e))
            return {"success": False, "message": "Telegram 登录已失效，请重新登录"}
        logger.error(f"Scan channel error: {e}")
        return {"success": False, "message": f"扫描失败: {e}"}
