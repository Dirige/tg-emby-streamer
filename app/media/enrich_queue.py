"""TMDB / JAV 元数据刮削后台队列。

扫描入库后不再在扫描循环里串行刮削，而是批量丢入队列，由常驻 worker
异步完成；一个批次的媒体全部刮削完成后触发回调（通知 + Emby 刷新），
此时 NFO/海报已生成，通知可以自带海报图片。
"""
import asyncio
import logging
from dataclasses import dataclass, field

from app.config import settings
from app.database import async_session
from app.models import Media, ChannelRule
from sqlalchemy import select

logger = logging.getLogger(__name__)


@dataclass
class Batch:
    batch_id: int
    meta: dict
    total: int
    done: int = 0
    results: list = field(default_factory=list)


class EnrichQueue:
    def __init__(self, workers: int = 2):
        self._queue: asyncio.Queue | None = None
        self._tasks: list[asyncio.Task] = []
        self._workers = workers
        self._batches: dict[int, Batch] = {}
        self._next_batch_id = 1

    # ── lifecycle ────────────────────────────────────────────────

    def start(self):
        if self._tasks:
            return
        self._queue = asyncio.Queue()
        for i in range(self._workers):
            self._tasks.append(asyncio.create_task(self._worker(i)))
        logger.info(f"Enrich queue started with {self._workers} workers")

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._queue = None
        logger.info("Enrich queue stopped")

    # ── public ───────────────────────────────────────────────────

    def enqueue_batch(self, media_ids: list[int], meta: dict | None = None) -> int | None:
        """把一批新入库的 media id 加入刮削队列。

        返回 batch_id；enrich 未启用时返回 None（调用方自行发纯文字通知）。
        """
        ids = [m for m in media_ids if m]
        if not ids:
            return None
        if not settings.enrich.enabled:
            return None
        if self._queue is None:
            self.start()

        batch_id = self._next_batch_id
        self._next_batch_id += 1
        self._batches[batch_id] = Batch(batch_id=batch_id, meta=dict(meta or {}), total=len(ids))
        for mid in ids:
            self._queue.put_nowait((batch_id, mid))
        return batch_id

    # ── internals ────────────────────────────────────────────────

    async def _worker(self, idx: int):
        while True:
            if self._queue is None:
                return
            try:
                batch_id, media_id = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._process_one(batch_id, media_id)
            except Exception as e:
                logger.error(f"Enrich queue error for media {media_id}: {e}")
            finally:
                self._queue.task_done()

    async def _process_one(self, batch_id: int, media_id: int):
        from app.media.enricher import enrich_media_safe

        batch = self._batches.get(batch_id)
        if batch is None:
            return

        result = await enrich_media_safe(media_id)
        record_info = await self._load_record(media_id)

        batch.results.append({
            "media_id": media_id,
            "success": bool(result.get("success")),
            "reason": result.get("reason", ""),
            "skipped": bool(result.get("skipped")),
            "tmdb_title": result.get("tmdb_title") or "",
            "jav_code": result.get("jav_code") or "",
            "jav_title": result.get("jav_title") or "",
            "actors": result.get("actors") or [],
            **record_info,
        })

        batch.done += 1
        if batch.done >= batch.total:
            done_batch = self._batches.pop(batch_id, None)
            if done_batch is not None:
                asyncio.create_task(self._finalize_batch(done_batch))

    async def _load_record(self, media_id: int) -> dict:
        try:
            async with async_session() as session:
                record = await session.get(Media, media_id)
                if not record:
                    return {}
                rule = (await session.execute(
                    select(ChannelRule).where(ChannelRule.channel_id == record.chat_id)
                )).scalar_one_or_none()
                source_link = record.source_link or ""
                if record.chat_id.startswith("-100") and source_link.startswith("https://t.me/c/"):
                    source_link = f"https://t.me/c/{record.chat_id[4:]}/{record.message_id}"
                return {
                    "title": record.title or "",
                    "file_name": record.file_name or "",
                    "category": record.category or "",
                    "source_link": source_link,
                    "source_name": rule.channel_name if rule else "",
                    "chat_id": record.chat_id,
                    "message_id": record.message_id,
                    "caption": record.caption or "",
                    "poster_path": record.poster_path or "",
                    "strm_path": record.strm_path or "",
                }
        except Exception as e:
            logger.warning(f"Load media {media_id} after enrich failed: {e}")
            return {}

    async def _finalize_batch(self, batch: Batch):
        if not batch.results:
            return
        meta = batch.meta
        try:
            from app.telegram.bot import send_enriched_batch_notify
            await send_enriched_batch_notify(meta, batch.results)
        except Exception as e:
            logger.error(f"Enrich batch notify failed: {e}")
        if settings.emby.url and settings.emby.api_key:
            try:
                from app.media.emby_notifier import refresh_library
                codes = [result.get("jav_code") for result in batch.results if result.get("category") == "18+"]
                await refresh_library(jav_codes=[code for code in codes if code])
            except Exception as e:
                logger.warning(f"Emby refresh after enrich batch failed: {e}")


enrich_queue = EnrichQueue()
