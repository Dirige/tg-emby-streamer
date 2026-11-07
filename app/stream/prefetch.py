import asyncio
import logging
from app.config import settings
from app.stream.cache import cache, CHUNK_SIZE

logger = logging.getLogger(__name__)


class PrefetchManager:
    def __init__(self):
        self.enabled = settings.cache.prefetch_enabled
        self.prefetch_count = settings.cache.prefetch_chunks
        self._tasks: dict[str, asyncio.Task] = {}

    def schedule_prefetch(self, message_id: int, current_offset: int, file_size: int, fetch_fn):
        if not self.enabled:
            return

        # 防泄漏：定期清掉已完成任务、限制字典规模（此前无限增长）
        if len(self._tasks) > 300:
            for k in list(self._tasks):
                if self._tasks[k].done():
                    self._tasks.pop(k, None)
            while len(self._tasks) > 400:
                self._tasks.pop(next(iter(self._tasks)), None)

        task_key = str(message_id)

        if task_key in self._tasks and not self._tasks[task_key].done():
            return

        async def _prefetch():
            try:
                start_chunk = (current_offset // CHUNK_SIZE) + 1
                end_chunk = min(
                    start_chunk + self.prefetch_count,
                    (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE,
                )
                for chunk_idx in range(start_chunk, end_chunk):
                    prefetch_offset = chunk_idx * CHUNK_SIZE
                    if prefetch_offset >= file_size:
                        break

                    existing = cache.memory.get(message_id, prefetch_offset)
                    if existing is not None:
                        continue

                    async def prefetch_fetch(off: int, limit: int) -> bytes:
                        return await fetch_fn(off, limit)

                    await cache.get(message_id, prefetch_offset, prefetch_fetch)
                    logger.debug(f"Prefetched chunk {chunk_idx} for message {message_id}")

            except Exception as e:
                logger.warning(f"Prefetch error for message {message_id}: {e}")

        self._tasks[task_key] = asyncio.create_task(_prefetch())

    def cancel_all(self):
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()


prefetch_manager = PrefetchManager()
