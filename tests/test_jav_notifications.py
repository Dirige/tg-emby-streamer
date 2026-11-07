import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
from app.models import Media, ChannelRule
from app.media import image_downloader, metatube, enricher
from app.media.enrich_queue import EnrichQueue
from app.media.jav_metadata import notification_metadata, parse_caption, read_nfo
from app.telegram import bot


@pytest.fixture(autouse=True)
def isolated_poster_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(metatube.settings.cache, "dir", str(tmp_path / "cache"))


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def mock_images(monkeypatch):
    original = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(str(request.url))
        if request.url.path == "/not-image":
            return httpx.Response(200, content=b"<html>error</html>")
        return httpx.Response(200, content=b"\xff\xd8\xff" + request.url.path.encode())

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(image_downloader.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    return requests


@pytest.mark.asyncio
async def test_flat_directory_has_distinct_posters(monkeypatch, tmp_path):
    mock_images(monkeypatch)
    (tmp_path / "poster.jpg").write_bytes(b"\xff\xd8\xffold-shared-poster")
    paths = []
    for code in ["SSIS-740", "SSIS-806"]:
        path = metatube.poster_path(code)
        assert await image_downloader._download_image(f"https://images.test/{code}", path)
        paths.append(str(path))
    assert paths[0] != paths[1]
    from pathlib import Path
    assert Path(paths[0]).read_bytes() != Path(paths[1]).read_bytes()
    assert Path(paths[0]).stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "poster.jpg").read_bytes() == b"\xff\xd8\xffold-shared-poster"


@pytest.mark.asyncio
async def test_html_response_is_not_cached_as_photo(monkeypatch, tmp_path):
    mock_images(monkeypatch)
    assert not await image_downloader._download_image("https://images.test/not-image", tmp_path / "poster.jpg")
    assert not (tmp_path / "poster.jpg").exists()


@pytest.mark.asyncio
async def test_metatube_rejects_wrong_detail_number(monkeypatch):
    async def get(url, params=None):
        if url.endswith("/search"):
            return [
                {"number": "SSIS-740", "provider": "A", "id": "bad"},
                {"number": "SSIS-806", "provider": "B", "id": "irrelevant"},
                {"number": "SSIS-740", "provider": "C", "id": "correct"},
            ]
        if url.endswith("/bad"):
            return {"number": "SSIS-806", "title": "wrong title"}
        return {"number": "SSIS-740", "title": "correct title", "actors": ["miru"]}
    monkeypatch.setattr(metatube, "_get", get)
    result = await metatube.find_movie("SSIS-740")
    assert result["provider"] == "C"
    assert result["actors"] == ["miru"]
    assert result["cover"].endswith("/C/correct")


def test_caption_metadata_preserves_chinese_title_and_actors():
    result = parse_caption("刮削完成\n📛 番号: SSIS-740\n🎬 标题: 原消息中文标题\n👤 演员: miru", "SSIS-740")
    assert result == {"title": "原消息中文标题", "actors": ["miru"]}
    assert parse_caption("番号: SSIS-806\n片名: 错片标题", "SSIS-740") == {}


def test_nfo_for_another_code_is_not_used(tmp_path):
    path = tmp_path / "msg_1.strm"
    path.with_suffix(".nfo").write_text("<movie><title>SSIS-806 错片</title><actor><name>wrong</name></actor></movie>")
    assert read_nfo(str(path), "SSIS-740") == {}


def test_actor_from_simple_filename_when_provider_missing():
    record = SimpleNamespace(caption="", strm_path=None, title="SSIS-740", file_name="SSIS-740 miru.mp4")
    assert notification_metadata(record, "SSIS-740", {"title": "片名"})["actors"] == ["miru"]


def test_requested_caption_format_and_escaping():
    text = bot.build_jav_notify({
        "jav_code": "SSIS-740", "jav_title": "片名 <test>", "actors": ["miru"],
        "source_name": "来源群组 A&B", "source_link": "https://t.me/c/3841916477/9", "success": True,
    })
    assert text.startswith("刮削完成\n\n📛 番号: SSIS-740\n🎬 标题: ")
    assert "👤 演员: miru\n🔗 来源群组:" in text
    assert "&lt;test&gt;" in text
    assert 'href="https://t.me/c/3841916477/9"' in text
    assert "来源群组 A&amp;B" in text


def test_private_message_link_removes_telegram_prefix():
    assert bot._source_link(-1003841916477, 9) == "https://t.me/c/3841916477/9"


@pytest.mark.asyncio
async def test_jav_enrichment_does_not_need_tmdb_and_prefers_file_code(monkeypatch, tmp_path, sessions):
    monkeypatch.setattr(enricher, "async_session", sessions)
    monkeypatch.setattr(enricher.settings.tmdb, "api_key", "")
    tmdb = AsyncMock()
    monkeypatch.setattr(enricher, "_tmdb_get", tmdb)
    mock_images(monkeypatch)
    movie = AsyncMock(return_value={"number": "SSIS-740", "title": "刮削片名", "actors": ["miru"], "cover": "https://images.test/SSIS-740"})
    monkeypatch.setattr(metatube, "find_movie", movie)
    legacy = tmp_path / "msg_1-poster.jpg"
    legacy.write_bytes(b"\xff\xd8\xffEmby-incorrect-old-image")
    async with sessions() as session:
        record = Media(chat_id="-1001", message_id=1, category="18+", title="SSIS-806", file_name="SSIS-740 miru.mp4", strm_path=str(tmp_path / "msg_1.strm"), poster_path=str(tmp_path / "poster.jpg"))
        session.add(record)
        await session.commit()
        mid = record.id
    result = await enricher.enrich_media(mid)
    assert result["success"]
    assert result["jav_code"] == "SSIS-740"
    assert result["actors"] == ["miru"]
    movie.assert_awaited_once_with("SSIS-740")
    tmdb.assert_not_awaited()
    async with sessions() as session:
        assert (await session.get(Media, mid)).poster_path == str(metatube.poster_path("SSIS-740"))
    assert metatube.poster_path("SSIS-740").read_bytes() != legacy.read_bytes()
    assert legacy.read_bytes() == b"\xff\xd8\xffEmby-incorrect-old-image"


@pytest.mark.asyncio
async def test_valid_per_code_cache_survives_temporary_metatube_failure(monkeypatch, tmp_path, sessions):
    monkeypatch.setattr(enricher, "async_session", sessions)
    monkeypatch.setattr(metatube, "find_movie", AsyncMock(return_value=None))
    path = metatube.poster_path("SSIS-740")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xd8\xffverified-code-cache")
    async with sessions() as session:
        record = Media(chat_id="-1001", message_id=1, category="18+", title="SSIS-740", file_name="SSIS-740 miru.mp4", strm_path=str(tmp_path / "msg_1.strm"))
        session.add(record)
        await session.commit()
        mid = record.id
    result = await enricher.enrich_media(mid)
    assert result["success"]
    async with sessions() as session:
        assert (await session.get(Media, mid)).poster_path == str(path)


@pytest.mark.asyncio
async def test_no_match_clears_shared_poster_without_deleting_file(monkeypatch, tmp_path, sessions):
    monkeypatch.setattr(enricher, "async_session", sessions)
    monkeypatch.setattr(metatube, "find_movie", AsyncMock(return_value=None))
    shared = tmp_path / "poster.jpg"
    shared.write_bytes(b"old")
    async with sessions() as session:
        record = Media(chat_id="-1001", message_id=1, category="18+", title="SSIS-740", file_name="SSIS-740.mp4", strm_path=str(tmp_path / "msg_1.strm"), poster_path=str(shared))
        session.add(record)
        await session.commit()
        mid = record.id
    result = await enricher.enrich_media(mid)
    assert not result["success"]
    async with sessions() as session:
        assert (await session.get(Media, mid)).poster_path is None
    assert shared.read_bytes() == b"old"


@pytest.mark.asyncio
async def test_queue_accepts_jav_without_tmdb_key(monkeypatch):
    from app.media import enrich_queue as module
    monkeypatch.setattr(module.settings.enrich, "enabled", True)
    monkeypatch.setattr(module.settings.tmdb, "api_key", "")
    queue = EnrichQueue()
    queue._queue = asyncio.Queue()
    assert queue.enqueue_batch([1], {"category": "18+"}) == 1
    assert queue._queue.get_nowait() == (1, 1)


@pytest.mark.asyncio
async def test_queue_loads_source_group_and_correct_private_link(monkeypatch, sessions):
    from app.media import enrich_queue as module
    monkeypatch.setattr(module, "async_session", sessions)
    async with sessions() as session:
        record = Media(chat_id="-1003841916477", message_id=9, source_link="https://t.me/c/1003841916477/9")
        session.add_all([record, ChannelRule(channel_id="-1003841916477", channel_name="视频来源")])
        await session.commit()
        mid = record.id
    result = await EnrichQueue()._load_record(mid)
    assert result["source_name"] == "视频来源"
    assert result["source_link"] == "https://t.me/c/3841916477/9"


@pytest.fixture
def notify_client(monkeypatch):
    monkeypatch.setattr(bot.settings.notify, "enabled", True)
    monkeypatch.setattr(bot.settings.notify, "target", "123")
    monkeypatch.setattr(bot, "_jav_notify_last_sent", 0.0)
    monkeypatch.setattr(bot, "_jav_notify_lock", asyncio.Lock())
    client = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock(), send_media_group=AsyncMock())
    monkeypatch.setattr(bot, "get_bot", AsyncMock(return_value=client))
    return client


def notice(tmp_path, code, message_id):
    strm = tmp_path / f"msg_{message_id}.strm"
    photo = metatube.poster_path(code)
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"\xff\xd8\xff" + code.encode())
    return {
        "media_id": message_id, "category": "18+", "jav_code": code, "jav_title": f"片名 {code}",
        "actors": [f"actor{message_id}"], "poster_path": str(photo), "strm_path": str(strm),
        "source_name": "视频来源", "source_link": f"https://t.me/c/111/{message_id}", "success": True,
    }


@pytest.mark.asyncio
async def test_two_codes_send_two_matching_photos_and_captions(notify_client, tmp_path):
    items = [notice(tmp_path, "SSIS-740", 1), notice(tmp_path, "SSIS-806", 2)]
    assert await bot.send_enriched_batch_notify({}, items)
    calls = notify_client.send_photo.await_args_list
    assert len(calls) == 2
    assert calls[0].args[1] == items[0]["poster_path"]
    assert calls[1].args[1] == items[1]["poster_path"]
    assert "SSIS-740" in calls[0].kwargs["caption"] and "SSIS-806" not in calls[0].kwargs["caption"]
    assert "SSIS-806" in calls[1].kwargs["caption"] and "SSIS-740" not in calls[1].kwargs["caption"]
    notify_client.send_media_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_poster_never_sent_and_link_preview_disabled(notify_client, tmp_path):
    item = notice(tmp_path, "SSIS-740", 1)
    shared = tmp_path / "poster.jpg"
    shared.write_bytes(b"old")
    item["poster_path"] = str(shared)
    assert await bot.send_enriched_batch_notify({}, [item])
    notify_client.send_photo.assert_not_awaited()
    assert notify_client.send_message.await_args.kwargs["disable_web_page_preview"]
    assert "对应封面未获取" in notify_client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_disabled_notifications_send_nothing(monkeypatch, notify_client, tmp_path):
    monkeypatch.setattr(bot.settings.notify, "enabled", False)
    assert not await bot.send_enriched_batch_notify({}, [notice(tmp_path, "SSIS-740", 1)])
    notify_client.send_photo.assert_not_awaited()
    notify_client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_failure_keeps_photo_on_user_fallback(monkeypatch, notify_client, tmp_path):
    from app.telegram import client as user_module
    notify_client.send_photo.side_effect = RuntimeError("test send failure")
    user = SimpleNamespace(send_photo=AsyncMock())
    monkeypatch.setattr(user_module, "get_client", AsyncMock(return_value=user))
    item = notice(tmp_path, "SSIS-740", 1)
    assert await bot.send_enriched_batch_notify({}, [item])
    assert user.send_photo.await_args.args[1] == item["poster_path"]
    notify_client.send_message.assert_not_awaited()
