import asyncio
import io
import logging
from dataclasses import dataclass
from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.raw import functions
from pyrogram.raw.types import InputDocumentFileLocation
from pyrogram.file_id import FileId
from app.config import settings
from app.telegram.client import get_stream_client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 4 * 1024 * 1024  # 4MB


@dataclass
class FileLocation:
    dc_id: int
    document_id: int
    access_hash: int
    file_size: int
    file_reference: bytes


async def _invoke_with_retry(client: Client, request, max_retries: int = None, retry_delay: float = None):
    if max_retries is None:
        max_retries = settings.stream.max_retries
    if retry_delay is None:
        retry_delay = settings.stream.retry_delay

    last_error = None
    for attempt in range(max_retries):
        try:
            return await client.invoke(request)
        except FloodWait as e:
            wait_time = e.value + 1
            logger.warning(f"FloodWait: sleeping {wait_time}s (attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait_time)
            last_error = e
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = retry_delay * (2 ** attempt)
                logger.warning(
                    f"MTProto error (attempt {attempt + 1}/{max_retries}): {e}, "
                    f"retrying in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"MTProto error after {max_retries} attempts: {e}")

    raise last_error


class CachedFileDownloader:
    def __init__(self, client: Client, chat_id: int, message_id: int):
        self._client = client
        self._chat_id = chat_id
        self._message_id = message_id
        self._location: FileLocation | None = None

    async def get_location(self) -> FileLocation:
        if self._location is not None:
            return self._location

        client = self._client
        messages = await client.get_messages(
            chat_id=self._chat_id, message_ids=self._message_id
        )
        message = messages[0] if isinstance(messages, list) else messages

        media = message.video or message.document
        if not media:
            raise ValueError(f"No document found in message {self._message_id}")

        decoded = FileId.decode(media.file_id)

        self._location = FileLocation(
            dc_id=decoded.dc_id,
            document_id=decoded.media_id,
            access_hash=decoded.access_hash,
            file_size=media.file_size or 0,
            file_reference=decoded.file_reference,
        )
        return self._location

    async def get_file_size(self) -> int:
        location = await self.get_location()
        return location.file_size

    async def _download_single_chunk(self, offset: int, limit: int) -> bytes:
        client = self._client

        for attempt in range(2):
            location = await self.get_location()

            input_location = InputDocumentFileLocation(
                id=location.document_id,
                access_hash=location.access_hash,
                file_reference=location.file_reference,
                thumb_size="",
            )

            # Telegram MTProto max chunk is 1MB
            mtproto_limit = min(limit, 1024 * 1024)

            try:
                part = await _invoke_with_retry(
                    client,
                    functions.upload.GetFile(
                        location=input_location,
                        offset=offset,
                        limit=mtproto_limit,
                    ),
                )
                if not part.bytes:
                    return b""
                return part.bytes
            except Exception as e:
                err_str = str(e).lower()
                if "file_reference" in err_str and attempt == 0:
                    logger.warning(f"File reference expired for msg={self._message_id}, refreshing...")
                    self._location = None  # 清掉缓存，下次 get_location 会重新获取
                    continue
                raise

    async def download_chunk(self, offset: int, limit: int) -> bytes:
        location = await self.get_location()

        chunks_needed = (limit + CHUNK_SIZE - 1) // CHUNK_SIZE
        concurrency = min(settings.stream.concurrency, chunks_needed)

        if concurrency <= 1 or limit <= CHUNK_SIZE:
            return await self._download_sequential(offset, limit)

        return await self._download_concurrent(offset, limit, concurrency)

    async def _download_sequential(self, offset: int, limit: int) -> bytes:
        buffer = io.BytesIO()
        current_offset = offset
        remaining = limit

        while remaining > 0:
            chunk_limit = min(remaining, CHUNK_SIZE)
            data = await self._download_single_chunk(current_offset, chunk_limit)

            if not data:
                break

            buffer.write(data)
            bytes_read = len(data)
            current_offset += bytes_read
            remaining -= bytes_read

            if bytes_read < chunk_limit:
                break

        return buffer.getvalue()

    async def _download_concurrent(self, offset: int, limit: int, concurrency: int) -> bytes:
        chunk_offsets = []
        current = offset
        remaining = limit
        while remaining > 0:
            chunk_size = min(remaining, CHUNK_SIZE)
            chunk_offsets.append((current, chunk_size))
            current += chunk_size
            remaining -= chunk_size

        semaphore = asyncio.Semaphore(concurrency)
        results: dict[int, bytes] = {}

        async def _fetch_chunk(idx: int, off: int, sz: int):
            async with semaphore:
                try:
                    data = await self._download_single_chunk(off, sz)
                    results[idx] = data
                except Exception as e:
                    logger.error(f"Concurrent chunk fetch failed at offset={off}: {e}")
                    results[idx] = b""

        tasks = [
            asyncio.create_task(_fetch_chunk(i, off, sz))
            for i, (off, sz) in enumerate(chunk_offsets)
        ]
        await asyncio.gather(*tasks)

        buffer = io.BytesIO()
        for i in range(len(chunk_offsets)):
            data = results.get(i, b"")
            if not data:
                break
            buffer.write(data)
            if len(data) < chunk_offsets[i][1]:
                break

        return buffer.getvalue()
