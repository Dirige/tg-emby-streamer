"""完整 GET 的 HTTP 长度与分块背压；只使用内存假文件，不连接 Telegram。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.stream import router


@pytest.fixture
def fake_stream(monkeypatch):
    content = bytes(range(32))
    reads = []

    class Downloader:
        def __init__(self, client, chat_id, message_id):
            pass

        async def get_file_size(self):
            return len(content)

        async def download_chunk(self, offset, length):
            reads.append((offset, length))
            return content[offset:offset + length]

    async def client():
        return object()

    async def cached_range(key, start, length, fetch):
        assert key == '-10011:7'
        return await fetch(start, length)

    monkeypatch.setattr(router, 'get_client', client)
    monkeypatch.setattr(router, 'CachedFileDownloader', Downloader)
    monkeypatch.setattr(router, 'MAX_RESPONSE_SIZE', 8)
    monkeypatch.setattr(router.cache, 'get_range', cached_range)
    monkeypatch.setattr(router.prefetch_manager, 'schedule_prefetch', Mock())
    return SimpleNamespace(content=content, reads=reads)


async def serve(range_header=None, size=32):
    request = SimpleNamespace(headers={'range': range_header} if range_header else {})
    return await router.serve_stream(7, -10011, size, 'fixture.mp4', 'video/mp4', request)


@pytest.mark.asyncio
async def test_no_range_get_streams_full_length_with_bounded_reads(fake_stream):
    response = await serve()
    assert isinstance(response, StreamingResponse)
    assert response.status_code == 200
    assert response.headers['content-length'] == '32'
    assert 'content-range' not in response.headers
    assert fake_stream.reads == []  # Sending headers does not download the body.
    result = b''.join([chunk async for chunk in response.body_iterator])
    assert result == fake_stream.content
    assert fake_stream.reads == [(0, 8), (8, 8), (16, 8), (24, 8)]


@pytest.mark.asyncio
async def test_disconnect_does_not_download_remaining_file(fake_stream):
    response = await serve()
    assert await response.body_iterator.__anext__() == fake_stream.content[:8]
    await response.body_iterator.aclose()
    assert fake_stream.reads == [(0, 8)]


@pytest.mark.asyncio
async def test_large_bounded_range_is_streamed_with_exact_range_length(fake_stream):
    response = await serve('bytes=3-22')
    assert isinstance(response, StreamingResponse)
    assert response.status_code == 206
    assert response.headers['content-length'] == '20'
    assert response.headers['content-range'] == 'bytes 3-22/32'
    assert b''.join([chunk async for chunk in response.body_iterator]) == fake_stream.content[3:23]
    assert all(length <= 8 for _, length in fake_stream.reads)


@pytest.mark.asyncio
async def test_open_ended_range_retains_existing_bounded_response(fake_stream):
    response = await serve('bytes=10-')
    assert response.status_code == 206
    assert response.headers['content-range'] == 'bytes 10-17/32'
    assert response.body == fake_stream.content[10:18]


@pytest.mark.asyncio
async def test_small_range_is_byte_exact(fake_stream):
    response = await serve('bytes=2-5')
    assert response.status_code == 206
    assert response.headers['content-length'] == '4'
    assert response.headers['content-range'] == 'bytes 2-5/32'
    assert response.body == fake_stream.content[2:6]


@pytest.mark.asyncio
async def test_small_no_range_get_is_complete(fake_stream):
    response = await serve(size=6)
    assert response.status_code == 200
    assert response.headers['content-length'] == '6'
    assert 'content-range' not in response.headers
    assert response.body == fake_stream.content[:6]


@pytest.mark.asyncio
async def test_missing_size_is_resolved_before_stream_headers(fake_stream):
    response = await serve(size=None)
    assert response.headers['content-length'] == '32'
    assert b''.join([chunk async for chunk in response.body_iterator]) == fake_stream.content


@pytest.mark.asyncio
async def test_streaming_aborts_instead_of_hanging_on_empty_upstream(fake_stream, monkeypatch):
    async def missing(*args):
        return b''

    monkeypatch.setattr(router.cache, 'get_range', missing)
    response = await serve()
    with pytest.raises(RuntimeError, match='完整分片'):
        await response.body_iterator.__anext__()


def test_reversed_range_returns_416():
    with pytest.raises(HTTPException) as exception:
        router.parse_range_header('bytes=20-10', 32)
    assert exception.value.status_code == 416
