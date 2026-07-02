import asyncio
import io
import logging
import time
from dataclasses import dataclass
from pyrogram import Client
from pyrogram.errors import FileMigrate, AuthKeyUnregistered
from pyrogram.raw import functions, types
from pyrogram.raw.types import InputDocumentFileLocation
from pyrogram.file_id import FileId
from pyrogram.session import Session, Auth
from app.config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1 * 1024 * 1024  # 1MB

# Telegram 服务端对 >2GB 偏移量的文件不支持 1MB 分片（返回 LIMIT_INVALID）
# 统一使用 512KB 分片以保证在所有偏移量下正常工作
MTROTO_MAX = 512 * 1024


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
        self._media_session: Session | None = None
        self._media_dc_id: int | None = None

    async def get_location(self) -> FileLocation:
        if self._location is not None:
            return self._location

        client = self._client
        try:
            messages = await client.get_messages(
                chat_id=self._chat_id, message_ids=self._message_id
            )
        except Exception as e:
            err = str(e)
            if "peer id invalid" in err.lower() or "chat id invalid" in err.lower():
                try:
                    await client.get_chat(int(self._chat_id))
                    messages = await client.get_messages(
                        chat_id=self._chat_id, message_ids=self._message_id
                    )
                except Exception as e2:
                    raise ValueError(f"无法访问频道")
            else:
                raise

        msg = messages[0] if isinstance(messages, list) else messages
        media = msg.video or msg.document
        if not media:
            raise ValueError(f"消息不存在或已被删除")

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
        loc = await self.get_location()
        return loc.file_size

    async def _get_media_session(self, dc_id: int) -> Session:
        if self._media_session and self._media_dc_id == dc_id:
            return self._media_session

        if self._media_session:
            try:
                await self._media_session.stop()
            except Exception:
                pass
            self._media_session = None
            self._media_dc_id = None

        main_dc = await self._client.storage.dc_id()
        if dc_id == main_dc:
            return self._client.session

        session = await self._create_dc_session(dc_id)
        self._media_session = session
        self._media_dc_id = dc_id
        return session

    async def _create_dc_session(self, dc_id: int) -> Session:
        client = self._client
        auth = await Auth(client, dc_id, await client.storage.test_mode()).create()
        session = Session(
            client, dc_id, auth,
            await client.storage.test_mode(),
            is_media=True,
        )
        await session.start()

        try:
            main_dc = await client.storage.dc_id()
            if dc_id != main_dc:
                exported_auth = await client.invoke(
                    functions.auth.ExportAuthorization(dc_id=dc_id)
                )
                await session.invoke(
                    functions.auth.ImportAuthorization(
                        id=exported_auth.id,
                        bytes=exported_auth.bytes,
                    )
                )
        except Exception as e:
            logger.warning(f"Auth export/import for DC{dc_id}: {e}")

        try:
            await session.invoke(
                functions.ping.Ping(ping_id=int(time.time() * 1000))
            )
        except Exception as e:
            logger.warning(f"Ping DC{dc_id}: {e}")

        return session

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
            req_limit = min(remaining, MTROTO_MAX)
            data = await self._mtproto_fetch(
                input_location, location, current_offset, req_limit
            )
            if not data:
                break
            buffer.write(data)
            current_offset += len(data)
            remaining -= len(data)

        return buffer.getvalue()

    async def _mtproto_fetch(self, input_location, location, offset: int, limit: int) -> bytes:
        # Telegram 服务端限制: offset>2GB 时 limit 必须在 [1024, 524288] 范围内
        effective_limit = max(min(limit, MTROTO_MAX), 1024)
        for attempt in range(3):
            try:
                session = await self._get_media_session(location.dc_id)
                part = await asyncio.wait_for(
                    session.invoke(
                        functions.upload.GetFile(
                            location=input_location,
                            offset=offset,
                            limit=effective_limit,
                        ),
                        sleep_threshold=30,
                    ),
                    timeout=90,
                )
                if not part.bytes:
                    return b""
                return part.bytes
            except asyncio.TimeoutError:
                logger.warning(f"Timeout DC{location.dc_id} offset={offset}, retrying...")
                self._media_session = None
                self._media_dc_id = None
                effective_limit = max(effective_limit // 2, 1024)
                continue
            except FileMigrate as e:
                new_dc = e.value
                self._location = FileLocation(
                    dc_id=new_dc,
                    document_id=location.document_id,
                    access_hash=location.access_hash,
                    file_size=location.file_size,
                    file_reference=location.file_reference,
                )
                location = self._location
                input_location = InputDocumentFileLocation(
                    id=location.document_id,
                    access_hash=location.access_hash,
                    file_reference=location.file_reference,
                    thumb_size="",
                )
                self._media_session = None
                self._media_dc_id = None
                continue
            except Exception as e:
                err_str = str(e).lower()
                logger.debug(f"MTProto fetch error: offset={offset} limit={effective_limit} attempt={attempt} err={err_str}")
                if "offset_invalid" in err_str or "limit_invalid" in err_str:
                    if effective_limit > 1024:
                        effective_limit = max(effective_limit // 2, 1024)
                        logger.info(f"Reducing chunk size to {effective_limit} for msg={self._message_id} offset={offset}")
                        continue
                    if attempt < 2:
                        self._location = None
                        new_loc = await self.get_location()
                        location = new_loc
                        input_location = InputDocumentFileLocation(
                            id=location.document_id,
                            access_hash=location.access_hash,
                            file_reference=location.file_reference,
                            thumb_size="",
                        )
                        self._media_session = None
                        self._media_dc_id = None
                        continue
                if isinstance(e, AuthKeyUnregistered) and attempt < 2:
                    self._media_session = None
                    self._media_dc_id = None
                    self._location = None
                    continue
                raise
