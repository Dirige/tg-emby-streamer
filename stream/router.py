import re
import logging
from urllib.parse import quote
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
from app.config import settings
from app.database import async_session
from app.models import Media
from app.telegram.client import get_client
from app.stream.range_stream import CachedFileDownloader
from app.stream.cache import cache
from app.stream.prefetch import prefetch_manager
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 每次最多返回 10MB


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


async def get_part_message_ids(record: Media) -> list[int]:
    ids = [record.message_id]
    if record.parts:
        for pid in record.parts.split(","):
            pid = pid.strip()
            if pid and pid.isdigit():
                ids.append(int(pid))
    return ids


async def get_parts_total_size(parts: list[int]) -> int:
    total = 0
    async with async_session() as session:
        for pid in parts:
            result = await session.execute(
                select(Media.size).where(Media.message_id == pid)
            )
            row = result.scalar_one_or_none()
            total += row or 0
    return total


async def serve_stream(
    message_id: int,
    chat_id: int,
    file_size: int | None,
    file_name: str,
    mime_type: str,
    request: Request,
    part_ids: list[int] | None = None,
):
    client = await get_client()
    part_ids = part_ids or [message_id]
    is_multi = len(part_ids) > 1

    if is_multi:
        if file_size is None:
            file_size = await get_parts_total_size(part_ids)
    else:
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
        end = min(start + MAX_RESPONSE_SIZE - 1, file_size - 1)

    content_length = end - start + 1

    if is_multi:
        data = await _fetch_multi_part_range(part_ids, client, start, content_length)
    else:
        downloader = CachedFileDownloader(client, chat_id, message_id)
        async def fetch_from_tg(offset: int, limit: int) -> bytes:
            return await downloader.download_chunk(offset, limit)
        data = await cache.get_range(message_id, start, content_length, fetch_from_tg)
        prefetch_manager.schedule_prefetch(message_id, start, file_size, fetch_from_tg)

    actual_len = len(data)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": mime_type,
        "Content-Length": str(actual_len),
        "Content-Disposition": (
            f"inline; filename*=UTF-8''{quote(file_name)}"
        ),
    }

    if range_header:
        headers["Content-Range"] = f"bytes {start}-{start + actual_len - 1}/{file_size}"
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


async def _fetch_multi_part_range(
    part_ids: list[int], client, global_start: int, length: int
) -> bytes:
    chunks = []
    remaining = length
    global_offset = global_start
    async with async_session() as session:
        for pid in part_ids:
            result = await session.execute(
                select(Media).where(Media.message_id == pid)
            )
            rec = result.scalar_one_or_none()
            if not rec:
                continue
            part_size = rec.size or 0
            chat_id_int = int(rec.chat_id)
            if global_offset < part_size:
                local_start = global_offset
                to_read = min(remaining, part_size - local_start)
                downloader = CachedFileDownloader(client, chat_id_int, pid)
                async def _fetch(offset, limit):
                    return await downloader.download_chunk(offset, limit)
                chunk = await cache.get_range(pid, local_start, to_read, _fetch)
                chunks.append(chunk)
                remaining -= len(chunk)
                if remaining <= 0:
                    break
                global_offset = 0
            else:
                global_offset -= part_size
    return b"".join(chunks)


@router.get("/strm/{message_id}")
@router.get("/stream/{message_id}")
async def stream_by_message_id(message_id: int, request: Request):
    record = await get_media_record(message_id)
    if not record:
        raise HTTPException(status_code=404, detail="Media record not found")
    try:
        part_ids = await get_part_message_ids(record)
        file_name = record.file_name or f"{message_id}.mp4"
        mime_type = record.mime_type or "video/mp4"
        chat_id = int(record.chat_id)
        return await serve_stream(
            message_id, chat_id, record.size, file_name, mime_type, request,
            part_ids=part_ids,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stream error for message_id={message_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Stream error: {e}")


@router.head("/strm/{message_id}")
@router.head("/stream/{message_id}")
async def stream_head(message_id: int):
    record = await get_media_record(message_id)
    if not record:
        raise HTTPException(status_code=404, detail="Media record not found")
    part_ids = await get_part_message_ids(record)
    file_size = record.size
    if len(part_ids) > 1:
        file_size = await get_parts_total_size(part_ids)
    return Response(
        status_code=200,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size or 0),
            "Content-Type": record.mime_type or "video/mp4",
            "Content-Disposition": (
                f"inline; filename*=UTF-8''{quote(record.file_name or f'{message_id}.mp4')}"
            ),
        },
    )
