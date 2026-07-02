import asyncio
import logging
import re
from app.config import settings
from app.database import async_session
from app.models import Media, ChannelRule, Category
from app.media.parser import parse_media_info
from app.media.strm import generate_strm, remove_old_strm
from app.telegram.client import get_client
from sqlalchemy import select
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_DURATION_SECONDS = 300

_notify_queue: asyncio.Queue = asyncio.Queue()


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


async def scan_channel(channel_id: int, limit: int = 200) -> dict:
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
        deferred_parts: list[dict] = []
        async for message in client.get_chat_history(channel_id, limit=limit):
            if not is_video_message(message):
                continue

            media = message.video or message.document
            duration = getattr(media, 'duration', None) or 0

            if duration < MIN_DURATION_SECONDS and duration > 0:
                logger.info(f"Skipped short video msg={message.id} duration={duration}s")
                continue

            async with async_session() as session:
                result = await session.execute(
                    select(Media).where(Media.message_id == message.id)
                )
                if result.scalar_one_or_none():
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
                async with async_session() as session:
                    existing = await session.execute(
                        select(Media).where(
                            Media.chat_id == str(channel_id),
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
                category=rule.category,
                title=title,
                season=info.get("season") if info else None,
                episode=info.get("episode") if info else None,
                year=info.get("year") if info else None,
                episode_digits=episode_digits,
                strm_subdir=strm_subdir,
            )

            async with async_session() as session:
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
                await session.commit()
                count += 1

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

            strm_path = generate_strm(
                message_id=dp["message_id"],
                category=rule.category,
                title=dp["title"],
                season=dp["info"].get("season") if dp["info"] else None,
                episode=dp["info"].get("episode") if dp["info"] else None,
                year=dp["info"].get("year") if dp["info"] else None,
                episode_digits=episode_digits,
                strm_subdir=strm_subdir,
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
                await session.commit()
                count += 1

        if count > 0:
            asyncio.create_task(_send_notify(
                f"📦 **频道 {ch_name} 新增 {count} 条入库**\n\n"
                f"频道: {ch_name}\n"
                f"新增: {count} 条\n"
                f"分类: {rule.category or '未分类'}"
            ))
        return {"success": True, "message": f"频道 {ch_name} 扫描完成，新增 {count} 条记录"}

    except ConnectionError as e:
        if "NEED_LOGIN" in str(e):
            return {"success": False, "message": "Telegram 未登录，请在设置页面完成登录"}
        return {"success": False, "message": f"连接失败: {e}"}
    except Exception as e:
        logger.error(f"Scan channel error: {e}")
        return {"success": False, "message": f"扫描失败: {e}"}
