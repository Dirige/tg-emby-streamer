import io
import logging
from dataclasses import dataclass
from pyrogram import Client
from pyrogram.raw import functions
from pyrogram.raw.types import InputDocumentFileLocation
from pyrogram.file_id import FileId

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024


@dataclass
class FileLocation:
    dc_id: int
    document_id: int
    access_hash: int
    file_size: int
    file_reference: bytes


class CachedFileDownloader:
    def __init__(self, client: Client, chat_id: int, message_id: int):
        self._client = client
        self._chat_id = chat_id
        self._message_id = message_id
        self._location: FileLocation | None = None

    async def get_location(self) -> FileLocation:
        if self._location is not None:
            return self._location

        messages = await self._client.get_messages(
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

    async def download_chunk(self, offset: int, limit: int) -> bytes:
        location = await self.get_location()

        input_location = InputDocumentFileLocation(
            id=location.document_id,
            access_hash=location.access_hash,
            file_reference=location.file_reference,
            thumb_size="",
        )

        buffer = io.BytesIO()
        current_offset = offset
        remaining = limit

        while remaining > 0:
            chunk_limit = min(remaining, CHUNK_SIZE)
            try:
                part = await self._client.invoke(
                    functions.upload.GetFile(
                        location=input_location,
                        offset=current_offset,
                        limit=chunk_limit,
                    ),
                )
            except Exception as e:
                logger.error(
                    f"MTProto getFile error at offset={current_offset}: {e}"
                )
                raise

            if not part.bytes:
                break

            buffer.write(part.bytes)
            bytes_read = len(part.bytes)
            current_offset += bytes_read
            remaining -= bytes_read

            if bytes_read < chunk_limit:
                break

        return buffer.getvalue()
