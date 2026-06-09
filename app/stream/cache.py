import os
import logging
import asyncio
from pathlib import Path
from collections import OrderedDict
from app.config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 4 * 1024 * 1024  # 4MB


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
        else:
            if len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[key] = value


def chunk_key(message_id: int, offset: int) -> str:
    chunk_index = offset // CHUNK_SIZE
    return f"{message_id}:{chunk_index}"


class MemoryCache:
    def __init__(self, max_size_mb: int = 256):
        self.max_items = max(1, (max_size_mb * 1024 * 1024) // CHUNK_SIZE)
        self._lru = PureLRU(self.max_items)

    def get(self, message_id: int, offset: int) -> bytes | None:
        key = chunk_key(message_id, offset)
        return self._lru.get(key)

    def put(self, message_id: int, offset: int, data: bytes):
        key = chunk_key(message_id, offset)
        self._lru.put(key, data)


class DiskCache:
    def __init__(self, cache_dir: Path, max_gb: int = 50):
        self.cache_dir = cache_dir
        self.max_bytes = max_gb * 1024 * 1024 * 1024
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._write_count = 0
        self._cleanup_interval = 100  # 每 100 次写入执行一次清理

    def _file_path(self, message_id: int, offset: int) -> Path:
        chunk_index = offset // CHUNK_SIZE
        return self.cache_dir / str(message_id) / f"{chunk_index:010d}.chunk"

    def get(self, message_id: int, offset: int) -> bytes | None:
        path = self._file_path(message_id, offset)
        if path.exists():
            return path.read_bytes()
        return None

    def put(self, message_id: int, offset: int, data: bytes):
        path = self._file_path(message_id, offset)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._write_count += 1
        # 周期性清理，避免每次写入都遍历全目录
        if self._write_count % self._cleanup_interval == 0:
            self._cleanup()

    def _cleanup(self):
        total = sum(f.stat().st_size for f in self.cache_dir.rglob("*.chunk"))
        if total > self.max_bytes:
            files = sorted(self.cache_dir.rglob("*.chunk"), key=lambda f: f.stat().st_atime)
            for f in files:
                if total <= self.max_bytes * 0.8:
                    break
                size = f.stat().st_size
                f.unlink()
                total -= size

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

    async def get(self, message_id: int, offset: int, fetch_fn) -> bytes:
        data = self.memory.get(message_id, offset)
        if data is not None:
            return data

        data = self.disk.get(message_id, offset)
        if data is not None:
            self.memory.put(message_id, offset, data)
            return data

        data = await fetch_fn(offset, CHUNK_SIZE)
        self.memory.put(message_id, offset, data)
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, self.disk.put, message_id, offset, data)
        return data

    async def get_range(self, message_id: int, start: int, length: int, fetch_fn) -> bytes:
        result = bytearray()
        offset = start
        remaining = length

        while remaining > 0:
            aligned_offset = (offset // CHUNK_SIZE) * CHUNK_SIZE
            chunk_offset_in = offset - aligned_offset
            chunk_available = CHUNK_SIZE - chunk_offset_in
            to_read = min(remaining, chunk_available)

            _fetch = fetch_fn

            async def aligned_fetch(off: int, limit: int, _f=_fetch) -> bytes:
                return await _f(off, limit)

            chunk = await self.get(message_id, aligned_offset, aligned_fetch)

            actual_read = min(to_read, len(chunk) - chunk_offset_in)
            if actual_read <= 0:
                break

            result.extend(chunk[chunk_offset_in:chunk_offset_in + actual_read])
            offset += actual_read
            remaining -= actual_read

        return bytes(result)


cache = ChunkCache()
