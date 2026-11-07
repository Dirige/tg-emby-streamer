import asyncio
import logging
import shutil
import threading
from collections import OrderedDict
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1 * 1024 * 1024


class PureLRU:
    def __init__(self, max_size: int):
        self.max_size = max_size
        self._cache = OrderedDict()

    def get(self, key: str) -> bytes | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, value: bytes):
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = value
            return
        if len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = value


def chunk_key(message_id: int, offset: int) -> str:
    return f"{message_id}:{offset // CHUNK_SIZE}"


class MemoryCache:
    def __init__(self, max_size_mb: int = 256):
        self.max_items = max(1, (max_size_mb * 1024 * 1024) // CHUNK_SIZE)
        self._lru = PureLRU(self.max_items)

    def get(self, message_id: int, offset: int) -> bytes | None:
        return self._lru.get(chunk_key(message_id, offset))

    def put(self, message_id: int, offset: int, data: bytes):
        self._lru.put(chunk_key(message_id, offset), data)


class DiskCache:
    def __init__(self, cache_dir: Path, max_gb: int = 50):
        self.cache_dir = cache_dir
        self.max_bytes = max_gb * 1024 * 1024 * 1024
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._write_count = 0
        self._cleanup_interval = 100
        self._lock = threading.Lock()
        self._bytes: int | None = None  # 增量统计；首次清理时全量重算一次

    def _file_path(self, message_id: int, offset: int) -> Path:
        chunk_index = offset // CHUNK_SIZE
        return self.cache_dir / str(message_id) / f"{chunk_index:010d}.chunk"

    def get(self, message_id: int, offset: int) -> bytes | None:
        path = self._file_path(message_id, offset)
        if path.exists():
            try:
                path.touch(exist_ok=True)
            except Exception:
                pass
            return path.read_bytes()
        return None

    def put(self, message_id: int, offset: int, data: bytes):
        path = self._file_path(message_id, offset)
        if path.exists():
            # chunk 内容不可变（同偏移固定内容），存在即无需重复写
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        with self._lock:
            if self._bytes is not None:
                self._bytes += len(data)
            self._write_count += 1
            if self._write_count % self._cleanup_interval == 0:
                self._cleanup()

    def _recount_bytes(self) -> int:
        total = 0
        for d in self.cache_dir.iterdir():
            if d.is_dir():
                total += sum(f.stat().st_size for f in d.rglob("*.chunk") if f.exists())
        self._bytes = total
        return total

    def _cleanup(self):
        """超预算时按整 message 目录清理（最旧的先删）。

        相比"全量 rglob+按文件删"，以目录为单位动得少得多，
        目录数量千级 vs chunk 文件十万级，巨幅降低清理 IO。
        """
        if self._bytes is None:
            if self._recount_bytes() <= self.max_bytes:
                return
        total = self._bytes or 0
        if total <= self.max_bytes:
            return
        dirs = []
        for d in self.cache_dir.iterdir():
            if d.is_dir():
                try:
                    dirs.append((d.stat().st_mtime, d))
                except FileNotFoundError:
                    continue
        dirs.sort()  # 最旧在前
        target = int(self.max_bytes * 0.8)
        for _mtime, d in dirs:
            if total <= target:
                break
            dir_size = sum(f.stat().st_size for f in d.rglob("*.chunk") if f.exists())
            shutil.rmtree(d, ignore_errors=True)
            total -= dir_size
        self._bytes = total

    def has_full_file(self, message_id: int, file_size: int) -> bool:
        file_dir = self.cache_dir / str(message_id)
        if not file_dir.exists():
            return False
        total = sum(f.stat().st_size for f in file_dir.glob("*.chunk"))
        return total >= file_size


class ChunkCache:
    def __init__(self):
        self.memory = MemoryCache(settings.cache.memory_cache_size_mb)
        self.disk = DiskCache(settings.cache.cache_path, settings.cache.disk_cache_max_gb)
        self.fetch_concurrency = settings.cache.stream_fetch_concurrency
        # 同一 chunk 的并发请求合并为一次 TG 拉取（single-flight），
        # 避免多人看同一文件时相同 chunk 被重复下载，双倍消耗账号带宽
        self._inflight: dict[str, list] = {}  # chunk_key -> [asyncio.Lock, waiters]

    def _entry_for(self, key: str) -> list:
        entry = self._inflight.get(key)
        if entry is None:
            entry = [asyncio.Lock(), 0]
            self._inflight[key] = entry
        return entry

    async def get(self, message_id: int, offset: int, fetch_fn, size: int | None = None) -> bytes:
        key = chunk_key(message_id, offset)
        entry = self._entry_for(key)
        entry[1] += 1
        try:
            async with entry[0]:
                return await self._get_locked(message_id, offset, fetch_fn, size)
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                # 没有等待者了再清掉，防 dict 无限增长
                if self._inflight.get(key) is entry:
                    self._inflight.pop(key, None)

    async def _get_locked(self, message_id: int, offset: int, fetch_fn, size: int | None = None) -> bytes:
        data = self.memory.get(message_id, offset)
        if data is not None:
            return data

        data = self.disk.get(message_id, offset)
        if data is not None:
            self.memory.put(message_id, offset, data)
            return data

        fetch_size = size or CHUNK_SIZE
        data = await asyncio.wait_for(fetch_fn(offset, fetch_size), timeout=120)
        if data:
            self.memory.put(message_id, offset, data)
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self.disk.put, message_id, offset, data)
        return data

    async def get_range(self, message_id: int, start: int, length: int, fetch_fn) -> bytes:
        plan = []
        offset = start
        remaining = length

        while remaining > 0:
            aligned_offset = (offset // CHUNK_SIZE) * CHUNK_SIZE
            chunk_offset_in = offset - aligned_offset
            chunk_available = CHUNK_SIZE - chunk_offset_in
            to_read = min(remaining, chunk_available)
            plan.append((aligned_offset, chunk_offset_in, to_read))
            offset += to_read
            remaining -= to_read

        semaphore = asyncio.Semaphore(self.fetch_concurrency)

        async def load_chunk(aligned_offset: int) -> bytes:
            async with semaphore:
                async def aligned_fetch(off: int, limit: int) -> bytes:
                    return await fetch_fn(off, limit)

                return await self.get(message_id, aligned_offset, aligned_fetch, CHUNK_SIZE)

        tasks = {
            aligned_offset: asyncio.create_task(load_chunk(aligned_offset))
            for aligned_offset in {item[0] for item in plan}
        }
        loaded = {aligned_offset: await task for aligned_offset, task in tasks.items()}

        result = bytearray()
        for aligned_offset, chunk_offset_in, to_read in plan:
            chunk = loaded.get(aligned_offset, b"")
            available = len(chunk) - chunk_offset_in
            if available <= 0:
                break
            actual_read = min(to_read, available)
            result.extend(chunk[chunk_offset_in:chunk_offset_in + actual_read])

        return bytes(result)


cache = ChunkCache()
