"""New-channel safety, source metadata, schema compatibility and API regression tests."""
import asyncio
import io
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import dashboard, database
from app.config import settings
from app.database import Base
from app.media import enricher, strm
from app.media.nfo_generator import SOURCE_ONLY_CATEGORIES, generate_source_record_nfo
from app.models import Category, ChannelRule, Media
from app.telegram import client as telegram_client, monitor


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    for module in (dashboard, monitor, enricher):
        monkeypatch.setattr(module, "async_session", sessions)
    monkeypatch.setattr(strm, "STRM_BASE_DIR", tmp_path / "strm")
    monkeypatch.setattr(settings.stream, "local_url", "http://stream.test")
    monkeypatch.setattr(settings.web, "stream_token", "test-token")
    monkeypatch.setattr(settings.enrich, "enabled", False)
    monkeypatch.setattr(settings.emby, "url", "")
    monkeypatch.setattr(settings.emby, "api_key", "")
    monkeypatch.setattr(monitor, "_send_notify", AsyncMock())
    yield sessions
    await asyncio.sleep(0)
    await engine.dispose()


async def rule(db, category="18+", strict=True, filename_rule=r"regex:([A-Z]{2,6}[-_]\d{2,5})", title_rule=""):
    async with db() as session:
        session.add(Category(name=category, strm_dir=category))
        session.add(ChannelRule(channel_id="-1001", category=category, enabled=True,
                                strict_import=strict, filename_rule=filename_rule, title_rule=title_rule))
        await session.commit()


def message(number=1, name="ABF-002.mp4", duration=600, caption="", document=False):
    video = SimpleNamespace(file_name=name, duration=duration, width=1920, height=1080,
                            file_size=100, mime_type="video/mp4", file_id="file",
                            file_unique_id=f"unique-{number}", thumbs=[])
    return SimpleNamespace(id=number, video=None if document else video,
                           document=video if document else None, caption=caption, link=None)


def client(monkeypatch, messages):
    async def history(*args, **kwargs):
        for item in messages:
            yield item
    value = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(title="test channel", username=None)),
                            get_chat_history=history)
    monkeypatch.setattr(monitor, "get_client", AsyncMock(return_value=value))
    return value


async def records(db):
    async with db() as session:
        return list((await session.execute(select(Media).order_by(Media.id))).scalars())


@pytest.mark.asyncio
async def test_unmatched_strict_rule_never_uses_raw_mp4_filename(db, monkeypatch):
    await rule(db)
    client(monkeypatch, [message(name="untitled.mp4")])
    generate = Mock()
    monkeypatch.setattr(monitor, "generate_strm", generate)
    result = await monitor.scan_channel(-1001)
    assert result["success"] and not await records(db)
    generate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("document", [False, True])
async def test_strict_rule_skips_unknown_duration(db, monkeypatch, document):
    await rule(db)
    client(monkeypatch, [message(duration=0, document=document)])
    assert (await monitor.scan_channel(-1001))["success"]
    assert not await records(db)


@pytest.mark.asyncio
async def test_five_minute_boundary_and_exact_duplicate_handling(db, monkeypatch):
    await rule(db)
    client(monkeypatch, [message(1, duration=299), message(2, duration=300)])
    await monitor.scan_channel(-1001)
    stored = await records(db)
    assert len(stored) == 1 and stored[0].message_id == 2
    original = Path(stored[0].strm_path).read_bytes()
    await monitor.scan_channel(-1001)
    again = await records(db)
    assert len(again) == 1 and again[0].id == stored[0].id
    assert Path(again[0].strm_path).read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["SONE-436_part1.mp4", "SONE-436.Part2.mp4", "sone-436Part003.mp4", "SONE-436-part 04.mp4"])
async def test_strict_rule_does_not_run_legacy_multipart_merge(db, monkeypatch, name):
    await rule(db)
    client(monkeypatch, [message(name=name)])
    merge = AsyncMock(side_effect=AssertionError("must not run byte-concatenation import"))
    monkeypatch.setattr(monitor, "_merge_part_record", merge)
    await monitor.scan_channel(-1001)
    assert not await records(db)
    merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_rule_leaves_existing_native_and_full_records_unchanged(db, monkeypatch, tmp_path):
    await rule(db)
    async with db() as session:
        session.add(Media(chat_id="-1001", message_id=90, title="SONE-436", size=400, duration=800))
        session.add(Media(chat_id="-1002", message_id=91, title="SONE-436", size=1000, duration=1600))
        await session.commit()
    before = [(r.id, r.message_id, r.size, r.duration, r.parts) for r in await records(db)]
    client(monkeypatch, [message(92, name="sone-436Part003.mp4")])
    await monitor.scan_channel(-1001)
    assert [(r.id, r.message_id, r.size, r.duration, r.parts) for r in await records(db)] == before


@pytest.mark.asyncio
async def test_strict_rule_skips_conflicting_original_codes(db, monkeypatch):
    await rule(db, title_rule=r"regex:([A-Z]+-\d+)")
    client(monkeypatch, [message(name="ABF-002.mp4", caption="SAME-096")])
    await monitor.scan_channel(-1001)
    assert not await records(db)


@pytest.mark.asyncio
async def test_numeric_upload_prefix_is_removed_only_by_explicit_rule(db, monkeypatch):
    await rule(db, filename_rule=r"regex:^(?:\d+)?([A-Z]{2,6}[-_]\d{2,5})")
    client(monkeypatch, [message(name="6JQJ_008_标题.mp4")])
    await monitor.scan_channel(-1001)
    record = (await records(db))[0]
    assert record.title == "JQJ-008" and record.file_name == "6JQJ_008_标题.mp4"
    assert Path(record.strm_path).parent.name == "JQJ-008"
    assert ET.parse(record.nfo_path).getroot().findtext("uniqueid[@type='jav']") == "JQJ-008"


@pytest.mark.asyncio
async def test_old_rule_keeps_its_original_fallback_behavior(db, monkeypatch):
    await rule(db, strict=False, filename_rule="")
    client(monkeypatch, [message(name="ABF-002 actor.mp4")])
    await monitor.scan_channel(-1001)
    record = (await records(db))[0]
    assert record.title == "ABF-002 actor.mp4"
    assert Path(record.strm_path).parent.name == "ABF-002"


@pytest.mark.asyncio
async def test_fruit_title_comes_from_caption_not_numeric_filename(db, monkeypatch):
    await rule(db, category="水果派", filename_rule="", title_rule=r"regex:片名[：:]\s*([^\n]+)")
    client(monkeypatch, [message(name="9.mp4", caption="片名： 原消息标题\n番号：ABF-002")])
    await monitor.scan_channel(-1001)
    record = (await records(db))[0]
    assert record.title == "原消息标题" and record.category == "水果派"
    root = ET.parse(record.nfo_path).getroot()
    assert root.findtext("title") == "原消息标题"
    assert root.findtext("uniqueid[@type='telegram']") == "-1001:1"
    assert root.findtext("uniqueid[@type='tmdb']") is None


@pytest.mark.asyncio
async def test_missing_transport_filename_does_not_replace_valid_caption_title(db, monkeypatch):
    await rule(db, category="国产", filename_rule="", title_rule=r"regex:片名[：:]\s*([^\n]+)")
    client(monkeypatch, [message(name=None, caption="片名：已确认原消息标题")])
    await monitor.scan_channel(-1001)
    record = (await records(db))[0]
    assert record.file_name == "video_1.mp4" and record.title == "已确认原消息标题"
    assert ET.parse(record.nfo_path).getroot().findtext("title") == "已确认原消息标题"


@pytest.mark.parametrize("category", sorted(SOURCE_ONLY_CATEGORIES))
def test_source_nfo_locks_only_source_metadata_and_escapes_xml(tmp_path, category):
    record = SimpleNamespace(strm_path=str(tmp_path / "title.strm"), title="标题 <&>",
                             category=category, caption="原消息 &", chat_id="-1001", message_id=1)
    result = generate_source_record_nfo(record)
    root = ET.parse(result["nfo_path"]).getroot()
    assert root.findtext("title") == "标题 <&>" and root.findtext("plot") == "原消息 &"
    assert root.findtext("uniqueid[@type='telegram']") == "-1001:1"
    assert root.findtext("lockdata") == "true"
    assert root.findtext("genre") == category
    assert root.findtext("actor") is None and root.findtext("uniqueid[@type='tmdb']") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("category", sorted(SOURCE_ONLY_CATEGORIES))
async def test_custom_adult_categories_never_query_tmdb(db, monkeypatch, tmp_path, category):
    path = tmp_path / "source.strm"
    path.write_text("http://stream.test/stream/-1001/1")
    async with db() as session:
        record = Media(chat_id="-1001", message_id=1, title="原消息标题", category=category,
                       strm_path=str(path), caption="")
        session.add(record)
        await session.commit()
        record_id = record.id
    monkeypatch.setattr(settings.tmdb, "api_key", "configured-but-not-used")
    search = AsyncMock(side_effect=AssertionError("must not query ordinary movie database"))
    monkeypatch.setattr(enricher, "search_movie", search)
    monkeypatch.setattr(telegram_client, "get_client", AsyncMock(side_effect=RuntimeError("thumbnail unavailable")))
    result = await enricher.enrich_media_safe(record_id)
    assert result["success"] and result["source_metadata_only"]
    search.assert_not_awaited()
    stored = (await records(db))[0]
    assert stored.tmdb_id is None and stored.title == "原消息标题" and stored.poster_path is None


@pytest.mark.asyncio
async def test_thumbnails_remain_scoped_to_their_own_source(db, monkeypatch, tmp_path):
    async with db() as session:
        for number in (1, 2):
            path = tmp_path / f"version-{number}.strm"
            path.write_text(f"http://stream.test/stream/-1001/{number}")
            session.add(Media(chat_id="-1001", message_id=number, title="原消息标题", category="国产",
                              strm_path=str(path), caption=""))
        await session.commit()
    fake = SimpleNamespace()
    fake.get_messages = AsyncMock(side_effect=lambda chat, mid: SimpleNamespace(
        video=SimpleNamespace(thumbs=[SimpleNamespace(file_id=f"thumb-{mid}", width=320, height=180, file_size=20)]),
        document=None))
    fake.download_media = AsyncMock(side_effect=lambda file_id, **kwargs: io.BytesIO(b"\xff\xd8\xff" + file_id.encode()))
    monkeypatch.setattr(telegram_client, "get_client", AsyncMock(return_value=fake))
    for record in await records(db):
        assert (await enricher.enrich_media_safe(record.id))["success"]
    stored = await records(db)
    assert len({record.poster_path for record in stored}) == 2
    for record in stored:
        assert Path(record.poster_path).read_bytes().endswith(f"thumb-{record.message_id}".encode())
        assert ET.parse(record.nfo_path).getroot().findtext("thumb") == Path(record.poster_path).name


@pytest.mark.asyncio
async def test_strict_import_api_round_trip_preserves_omitted_option(db):
    app = FastAPI()
    app.include_router(dashboard.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        response = await api.post("/api/channel-rule/save", json={"channel_id": "-1001", "strict_import": True})
        assert response.json()["success"]
        response = await api.get("/api/channel-rule", params={"channel_id": "-1001"})
        assert response.json()["rule"]["strict_import"] is True
        assert (await api.get("/api/channel-rules")).json()["rules"][0]["strict_import"] is True
        await api.post("/api/channel-rule/save", json={"channel_id": "-1001", "title_rule": "line:0"})
        assert (await api.get("/api/channel-rule", params={"channel_id": "-1001"})).json()["rule"]["strict_import"] is True
        response = await api.post("/api/channel-rule/save", json={"channel_id": "-1001", "strict_import": "false"})
        assert not response.json()["success"]


@pytest.mark.asyncio
async def test_schema_upgrade_is_idempotent_and_preserves_disabled_rules(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old-schema.db'}")
    monkeypatch.setattr(database, "engine", engine)
    async with engine.begin() as connection:
        columns = ", ".join(f"{col.name} {col.type}" for col in ChannelRule.__table__.columns if col.name != "strict_import")
        await connection.execute(text(f"CREATE TABLE channel_rules ({columns})"))
        await connection.execute(text("INSERT INTO channel_rules(id,channel_id,enabled,title_rule) VALUES (1,'-1001',0,'line:0')"))
    await database.init_db()
    await database.init_db()
    async with engine.connect() as connection:
        row = (await connection.execute(text("SELECT enabled,title_rule,strict_import FROM channel_rules WHERE id=1"))).one()
    assert row == (0, "line:0", 0)
    await engine.dispose()
