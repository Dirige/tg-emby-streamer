import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from app.config import settings
from app.database import async_session
from app.models import Media
from sqlalchemy import select
from app.media.parser import parse_media_info
from app.media.strm import generate_strm
from app.telegram.client import get_client, warmup_client

logger = logging.getLogger(__name__)


def is_video_message(message: Message) -> bool:
    if message.video:
        return True
    if message.document and message.document.mime_type:
        return message.document.mime_type.startswith("video/")
    return False


async def forward_to_channel(client: Client, message: Message) -> Message | None:
    try:
        forwarded = await client.forward_messages(
            chat_id=settings.telegram.channel_id,
            from_chat_id=message.chat.id,
            message_ids=message.id,
        )
        if isinstance(forwarded, list):
            return forwarded[0]
        return forwarded
    except Exception as e:
        err_str = str(e)
        if "CHAT_FORWARDS_RESTRICTED" in err_str or "CHAT_RESTRICTED" in err_str:
            logger.info(f"Forward restricted, recording directly: {message.id}")
            # 转发受限，直接入库
            source_channel_id = message.chat.id
            media = message.video or message.document
            file_name = media.file_name or f"video_{message.id}.mp4"
            caption = message.caption or ""
            media_info = parse_media_info(file_name, caption=caption, channel_id=source_channel_id)
            if media_info:
                await _direct_record_message(message, source_channel_id, media_info)
            return None
        logger.error(f"Forward failed: {e}")
        return None


async def process_private_channel_message(message: Message, source_channel_id: int = 0):
    """处理私有频道中的视频消息

    Args:
        message: Pyrogram 消息对象
        source_channel_id: 原始来源频道 ID（用于选择解析规则）
    """
    media = message.video or message.document
    if not media:
        return

    file_name = media.file_name or f"video_{message.id}.mp4"
    file_size = media.file_size or 0
    mime_type = media.mime_type or "video/mp4"
    duration = media.duration or 0
    width = media.width or 0
    height = media.height or 0
    caption = message.caption or ""

    # 使用频道 ID 和 caption 进行解析
    media_info = parse_media_info(file_name, caption=caption, channel_id=source_channel_id)
    if media_info is None:
        # parse_media_info 返回 None 表示应跳过此消息
        logger.info(f"Skipped (blocked by parser): {file_name}")
        return

    strm_path = generate_strm(
        message_id=message.id,
        file_name=file_name,
        category=media_info.get("category"),
        title=media_info.get("title"),
        season=media_info.get("season"),
        episode=media_info.get("episode"),
    )

    async with async_session() as session:
        result = await session.execute(select(Media).where(Media.message_id == message.id))
        existing = result.scalar_one_or_none()
        if existing:
            logger.info(f"Already recorded: message_id={message.id}, skip")
            return

        record = Media(
            message_id=message.id,
            chat_id=str(settings.telegram.channel_id),
            file_name=file_name,
            caption=caption,
            file_id=media.file_id if hasattr(media, "file_id") else None,
            file_unique_id=media.file_unique_id if hasattr(media, "file_unique_id") else None,
            size=file_size,
            duration=duration,
            mime_type=mime_type,
            width=width,
            height=height,
            category=media_info.get("category"),
            season=media_info.get("season"),
            episode=media_info.get("episode"),
            resolution=media_info.get("resolution"),
            strm_path=str(strm_path) if strm_path else None,
            recognized=media_info.get("recognized", False),
        )
        session.add(record)
        await session.commit()

    logger.info(f"Recorded: {file_name} -> message_id={message.id}, strm={strm_path}")


async def _startup_forward(client: Client, monitor_channels: list[int], private_channel: int):
    """启动时扫描监听频道的最近消息并转发到私有频道

    对于转发受限的频道，直接在本地解析并入库（不转发）
    """
    logger.info("[Startup] Scanning recent messages in monitored channels...")
    total = 0
    direct_record = 0
    skipped = []
    for chat_id in monitor_channels:
        try:
            count = 0
            direct_count = 0
            restricted = False
            async for message in client.get_chat_history(chat_id, limit=50):
                if not is_video_message(message):
                    continue
                
                # 检查是否已入库（去重）
                async with async_session() as session:
                    result = await session.execute(select(Media).where(Media.message_id == message.id))
                    if result.scalar_one_or_none():
                        continue
                
                media = message.video or message.document
                fn = media.file_name or f"video_{message.id}.mp4"
                caption = message.caption or ""

                # 先检查是否被 parser 屏蔽（如 cosplay 频道的关键词过滤）
                info = parse_media_info(fn, caption=caption, channel_id=chat_id)
                if info is None:
                    continue

                try:
                    await client.forward_messages(
                        chat_id=private_channel,
                        from_chat_id=chat_id,
                        message_ids=message.id,
                    )
                    count += 1
                    total += 1
                except Exception as e:
                    err_str = str(e)
                    if "CHAT_FORWARDS_RESTRICTED" in err_str or "CHAT_RESTRICTED" in err_str:
                        if not restricted:
                            logger.info(f"[Startup] Channel {chat_id}: forwarding restricted, recording directly")
                            restricted = True
                        # 转发受限，直接在本地解析入库
                        try:
                            await _direct_record_message(message, chat_id, info)
                            direct_count += 1
                            direct_record += 1
                        except Exception as rec_err:
                            logger.warning(f"[Startup] Direct record failed msg {message.id}: {rec_err}")
                        continue
                    else:
                        logger.warning(f"[Startup] Forward failed msg {message.id}: {e}")
                await asyncio.sleep(0.3)
            if count or direct_count:
                parts = []
                if count:
                    parts.append(f"forwarded {count}")
                if direct_count:
                    parts.append(f"direct-recorded {direct_count}")
                logger.info(f"[Startup] Channel {chat_id}: {', '.join(parts)} videos")
        except Exception as e:
            logger.warning(f"[Startup] Cannot access channel {chat_id}: {e}")
            skipped.append(chat_id)
    if total:
        logger.info(f"[Startup] Total forwarded: {total} videos")
    if direct_record:
        logger.info(f"[Startup] Total direct-recorded: {direct_record} videos")
    if skipped:
        logger.info(f"[Startup] {len(skipped)} channels skipped")
    if not total and not direct_record and not skipped:
        logger.info("[Startup] No new videos to process")


async def _direct_record_message(message: Message, source_channel_id: int, media_info: dict):
    """直接将消息入库（不经过转发），用于转发受限的频道"""
    media = message.video or message.document
    if not media:
        return

    file_name = media.file_name or f"video_{message.id}.mp4"
    file_size = media.file_size or 0
    mime_type = media.mime_type or "video/mp4"
    duration = media.duration or 0
    width = media.width or 0
    height = media.height or 0
    caption = message.caption or ""

    strm_path = generate_strm(
        message_id=message.id,
        file_name=file_name,
        category=media_info.get("category"),
        title=media_info.get("title"),
        season=media_info.get("season"),
        episode=media_info.get("episode"),
        display_name=media_info.get("display_name"),
    )

    async with async_session() as session:
        result = await session.execute(select(Media).where(Media.message_id == message.id))
        existing = result.scalar_one_or_none()
        if existing:
            return

        record = Media(
            message_id=message.id,
            chat_id=str(source_channel_id),
            file_name=file_name,
            caption=caption,
            file_id=getattr(media, "file_id", None),
            file_unique_id=getattr(media, "file_unique_id", None),
            size=file_size,
            duration=duration,
            mime_type=mime_type,
            width=width,
            height=height,
            category=media_info.get("category"),
            season=media_info.get("season"),
            episode=media_info.get("episode"),
            resolution=media_info.get("resolution"),
            strm_path=str(strm_path) if strm_path else None,
            recognized=media_info.get("recognized", False),
        )
        session.add(record)
        await session.commit()

    logger.info(f"[Direct] Recorded: {file_name} -> msg={message.id} (source={source_channel_id})")


async def start_monitor():
    client = await get_client()
    await warmup_client(client)
    monitor_channels = settings.telegram.monitor_channel_list
    private_channel = settings.telegram.channel_id

    @client.on_message(filters.chat(monitor_channels) & (filters.video | filters.document))
    async def on_channel_video(_, message: Message):
        if is_video_message(message):
            source_chat_id = message.chat.id
            fn = (message.video or message.document).file_name or "unknown"
            logger.info(f"[Task1] New video in channel {source_chat_id}: {message.id} - {fn}")
            forwarded = await forward_to_channel(client, message)
            if forwarded:
                # 在转发后的消息 caption 中注入来源频道 ID（用于后续解析）
                logger.info(f"[Task1] Forwarded to private channel: {forwarded.id} (source={source_chat_id})")

    @client.on_message(filters.chat(private_channel) & (filters.video | filters.document))
    async def on_private_message(_, message: Message):
        if is_video_message(message):
            # 尝试从转发信息获取原始频道 ID
            source_channel_id = 0
            if message.forward_from_chat:
                source_channel_id = message.forward_from_chat.id
            elif message.forward_sender_name:
                # 有些频道隐藏了发送者信息，无法获取
                pass
            logger.info(f"[Task2] New media in private channel: {message.id} (source={source_channel_id})")
            await process_private_channel_message(message, source_channel_id=source_channel_id)

    logger.info(f"[Task1] Monitoring {len(monitor_channels)} channels for new videos")
    logger.info(f"[Task2] Monitoring private channel {private_channel} for recording")

    return client, monitor_channels, private_channel


async def run_startup_forward(client: Client, monitor_channels: list[int], private_channel: int):
    await _startup_forward(client, monitor_channels, private_channel)
