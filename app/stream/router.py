import re
import logging
from urllib.parse import quote
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
from app.config import settings
from app.database import async_session
from app.models import Media
from app.telegram.client import get_client, get_stream_client
from app.stream.range_stream import CachedFileDownloader
from app.stream.cache import cache
from app.stream.prefetch import prefetch_manager
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter()

CHUNK_SIZE = 1024 * 1024


def parse_range_header(
    range_header: str | None, file_size: int
) -> tuple[int, int | None]:
    if not range_header:
        return 0, None

    match = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not match:
        return 0, None

    start = int(match.group(1))
    end_str = match.group(2)
    end = int(end_str) if end_str else None

    if start >= file_size:
        raise HTTPException(status_code=416, detail="Range Not Satisfiable")

    if end is not None and end >= file_size:
        end = file_size - 1

    return start, end


async def get_media_record(message_id: int) -> Media | None:
    async with async_session() as session:
        result = await session.execute(
            select(Media).where(Media.message_id == message_id)
        )
        return result.scalar_one_or_none()


async def serve_stream(
    message_id: int,
    chat_id: int,
    file_size: int | None,
    file_name: str,
    mime_type: str,
    request: Request,
):
    # Always use user client for streaming (bots can't access channel files)
    client = await get_client()
    downloader = CachedFileDownloader(client, chat_id, message_id)

    if file_size is None:
        try:
            file_size = await downloader.get_file_size()
        except Exception as e:
            logger.error(f"Failed to get file size for {message_id}: {e}")
            raise HTTPException(status_code=404, detail="File not found")

    range_header = request.headers.get("range")
    start, end = parse_range_header(range_header, file_size)

    if end is None:
        end = min(start + CHUNK_SIZE * 10 - 1, file_size - 1)

    content_length = end - start + 1

    async def fetch_from_tg(offset: int, limit: int) -> bytes:
        return await downloader.download_chunk(offset, limit)

    data = await cache.get_range(message_id, start, content_length, fetch_from_tg)
    prefetch_manager.schedule_prefetch(message_id, start, file_size, fetch_from_tg)

    actual_len = len(data)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(actual_len),
        "Content-Range": f"bytes {start}-{start + actual_len - 1}/{file_size}",
        "Content-Type": mime_type,
        "Content-Disposition": (
            f"inline; filename*=UTF-8''{quote(file_name)}"
        ),
    }

    if range_header:
        return Response(
            content=data, status_code=206, headers=headers, media_type=mime_type
        )
    else:
        return Response(
            content=data,
            status_code=200,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(actual_len),
                "Content-Type": mime_type,
            },
            media_type=mime_type,
        )


@router.get("/strm/{message_id}")
@router.get("/stream/{message_id}")
async def stream_by_message_id(message_id: int, request: Request):
    try:
        record = await get_media_record(message_id)
        if record:
            chat_id = int(record.chat_id)
            file_size = record.size
            file_name = record.file_name or f"{message_id}.mp4"
            mime_type = record.mime_type or "video/mp4"
        else:
            chat_id = settings.telegram.channel_id
            file_name = f"{message_id}.mp4"
            mime_type = "video/mp4"
            file_size = None

        return await serve_stream(
            message_id, chat_id, file_size, file_name, mime_type, request
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Stream error for message_id={message_id}: {type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=500, detail=f"Stream error: {type(e).__name__}: {e}"
        )


@router.head("/strm/{message_id}")
@router.head("/stream/{message_id}")
async def stream_head(message_id: int):
    try:
        record = await get_media_record(message_id)
        if record and record.size:
            file_size = record.size
            mime_type = record.mime_type or "video/mp4"
            file_name = record.file_name or f"{message_id}.mp4"
        else:
            chat_id = settings.telegram.channel_id
            client = await get_client()
            downloader = CachedFileDownloader(client, chat_id, message_id)
            file_size = await downloader.get_file_size()
            mime_type = "video/mp4"
            file_name = f"{message_id}.mp4"

        return Response(
            status_code=200,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Type": mime_type,
                "Content-Disposition": (
                    f"inline; filename*=UTF-8''{quote(file_name)}"
                ),
            },
        )
    except Exception as e:
        logger.error(f"HEAD request error for {message_id}: {e}")
        raise HTTPException(status_code=404, detail="File not found")
