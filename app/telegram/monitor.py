import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from app.config import settings
from app.database import async_session
from app.models import Media
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
        logger.error(f"Forward failed: {e}")
        return None


async def process_private_channel_message(message: Message):
    media = message.video or message.document
    if not media:
        return

    file_name = media.file_name or f"video_{message.id}.mp4"
    file_size = media.file_size or 0
    mime_type = media.mime_type or "video/mp4"
    duration = media.duration or 0
    width = media.width or 0
    height = media.height or 0

    media_info = parse_media_info(file_name)

    strm_path = generate_strm(
        message_id=message.id,
        file_name=file_name,
        category=media_info.get("category"),
        title=media_info.get("title"),
        season=media_info.get("season"),
        episode=media_info.get("episode"),
        tmdb_id=media_info.get("tmdb_id"),
        tmdb_name=media_info.get("tmdb_name"),
    )

    async with async_session() as session:
        existing = await session.get(Media, message.id)
        if existing:
            logger.info(f"Already recorded: message_id={message.id}, skip")
            return

        record = Media(
            message_id=message.id,
            chat_id=str(settings.telegram.channel_id),
            file_name=file_name,
            file_id=media.file_id if hasattr(media, "file_id") else None,
            file_unique_id=media.file_unique_id if hasattr(media, "file_unique_id") else None,
            size=file_size,
            duration=duration,
            mime_type=mime_type,
            width=width,
            height=height,
            category=media_info.get("category"),
            tmdb_id=media_info.get("tmdb_id"),
            tmdb_name=media_info.get("tmdb_name") or media_info.get("title"),
            season=media_info.get("season"),
            episode=media_info.get("episode"),
            resolution=media_info.get("resolution"),
            strm_path=str(strm_path) if strm_path else None,
        )
        session.add(record)
        await session.commit()

    logger.info(f"Recorded: {file_name} -> message_id={message.id}, strm={strm_path}")


async def start_monitor():
    client = await get_client()
    await warmup_client(client)
    monitor_channels = settings.telegram.monitor_channel_list
    private_channel = settings.telegram.channel_id

    @client.on_message(filters.chat(monitor_channels) & (filters.video | filters.document))
    async def on_channel_video(_, message: Message):
        if is_video_message(message):
            logger.info(f"[Task1] New video in channel {message.chat.id}: {message.id} - {(message.video or message.document).file_name}")
            forwarded = await forward_to_channel(client, message)
            if forwarded:
                logger.info(f"[Task1] Forwarded to private channel: {forwarded.id}")

    @client.on_message(filters.chat(private_channel) & (filters.video | filters.document))
    async def on_private_message(_, message: Message):
        if is_video_message(message):
            logger.info(f"[Task2] New media in private channel: {message.id}")
            await process_private_channel_message(message)

    logger.info(f"[Task1] Monitoring {len(monitor_channels)} channels for new videos")
    logger.info(f"[Task2] Monitoring private channel {private_channel} for recording")
