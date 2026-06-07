import asyncio
import argparse
import logging
import sys
from datetime import datetime

sys.path.insert(0, ".")

from app.config import settings
from app.database import async_session, init_db
from app.models import Media
from app.media.parser import parse_media_info
from app.media.strm import generate_strm
from app.telegram.client import get_client, stop_client, warmup_client
from sqlalchemy import select

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scan_history")


def is_video_message(message) -> bool:
    if message.video:
        return True
    if message.document and message.document.mime_type:
        return message.document.mime_type.startswith("video/")
    return False


async def scan_private_channel(limit: int = 0):
    client = await get_client()
    private_channel = settings.telegram.channel_id
    count = 0
    skipped = 0

    logger.info(f"Scanning private channel {private_channel} for existing videos...")

    async for message in client.get_chat_history(private_channel):
        if limit and count >= limit:
            break

        if not is_video_message(message):
            continue

        media = message.video or message.document
        file_name = media.file_name or f"video_{message.id}.mp4"

        async with async_session() as session:
            existing = await session.get(Media, message.id)
            if existing:
                skipped += 1
                continue

        media_info = parse_media_info(file_name)
        strm_path = generate_strm(
            message_id=message.id,
            file_name=file_name,
            category=media_info.get("category"),
            title=media_info.get("title"),
            season=media_info.get("season"),
            episode=media_info.get("episode"),
            tmdb_id=media_info.get("tmdb_id"),
        )

        async with async_session() as session:
            record = Media(
                message_id=message.id,
                chat_id=str(private_channel),
                file_name=file_name,
                file_id=media.file_id if hasattr(media, "file_id") else None,
                file_unique_id=media.file_unique_id if hasattr(media, "file_unique_id") else None,
                size=media.file_size or 0,
                duration=media.duration or 0,
                mime_type=media.mime_type or "video/mp4",
                width=media.width or 0,
                height=media.height or 0,
                category=media_info.get("category"),
                tmdb_id=media_info.get("tmdb_id"),
                tmdb_name=media_info.get("title"),
                season=media_info.get("season"),
                episode=media_info.get("episode"),
                resolution=media_info.get("resolution"),
                strm_path=str(strm_path) if strm_path else None,
            )
            session.add(record)
            await session.commit()

        count += 1
        logger.info(f"[{count}] Recorded: {file_name} -> message_id={message.id}, strm={strm_path}")

    logger.info(f"Scan done. New: {count}, Skipped(existing): {skipped}")


async def scan_monitor_channels(limit_per_channel: int = 0):
    client = await get_client()
    monitor_channels = settings.telegram.monitor_channel_list
    private_channel = settings.telegram.channel_id
    total_forwarded = 0

    logger.info(f"Scanning {len(monitor_channels)} monitor channels...")

    for chat_id in monitor_channels:
        try:
            chat = await client.get_chat(chat_id)
            channel_name = chat.title or str(chat_id)
        except Exception as e:
            logger.error(f"Cannot access channel {chat_id}: {e}")
            continue

        count = 0
        logger.info(f"Scanning channel: {channel_name} ({chat_id})")

        async for message in client.get_chat_history(chat_id):
            if limit_per_channel and count >= limit_per_channel:
                break

            if not is_video_message(message):
                continue

            try:
                forwarded = await client.forward_messages(
                    chat_id=private_channel,
                    from_chat_id=chat_id,
                    message_ids=message.id,
                )
                if isinstance(forwarded, list):
                    forwarded = forwarded[0]
                count += 1
                total_forwarded += 1
                file_name = (message.video or message.document).file_name if (message.video or message.document) else "unknown"
                logger.info(f"[{count}] Forwarded: {file_name} (msg {message.id}) -> {forwarded.id}")
            except Exception as e:
                logger.error(f"Forward failed for msg {message.id}: {e}")

            await asyncio.sleep(0.5)

        logger.info(f"Channel {channel_name}: forwarded {count} videos")

    logger.info(f"All channels done. Total forwarded: {total_forwarded}")


async def main():
    parser = argparse.ArgumentParser(description="Scan historical Telegram messages")
    parser.add_argument(
        "task",
        choices=["forward", "record"],
        help="forward: scan monitor channels -> forward to private channel; "
             "record: scan private channel -> record to DB + generate STRM",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        default=0,
        help="Max messages to process per channel (0 = all)",
    )
    args = parser.parse_args()

    await init_db()
    client = await get_client()
    await warmup_client(client)

    if args.task == "forward":
        await scan_monitor_channels(limit_per_channel=args.limit)
    elif args.task == "record":
        await scan_private_channel(limit=args.limit)

    await stop_client()


if __name__ == "__main__":
    asyncio.run(main())
