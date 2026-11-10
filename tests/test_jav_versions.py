import asyncio
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.database import Base
from app.models import Media
from app.media import strm, emby_versions
from app.media.jav import resolve_jav_code
from app.media.jav_metadata import parse_caption
from app.media.nfo_generator import generate_jav_nfo
from app.telegram import bot, monitor


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(strm, "STRM_BASE_DIR", tmp_path / "strm")
    monkeypatch.setattr(settings.stream, "local_url", "http://stream.test")
    monkeypatch.setattr(settings.web, "stream_token", "test-token")
    monkeypatch.setattr(settings.emby, "url", "")
    monkeypatch.setattr(settings.emby, "api_key", "")
    monkeypatch.setattr(settings.enrich, "enabled", False)
    return tmp_path


@pytest_asyncio.fixture
async def sessions(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'media.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(bot, "async_session", factory)
    monkeypatch.setattr(monitor, "async_session", factory)
    yield factory
    await engine.dispose()


def test_ambiguous_source_is_not_automatically_numbered():
    assert resolve_jav_code("TFSQB-112.mp4", "SAME-096 片名", "SAME-096") is None
    assert resolve_jav_code("ABF-002.mp4", "", "SKMJ-567") == "ABF-002"


def test_caption_first_line_is_used_as_title():
    assert parse_caption("ABF-002 原消息片名\n演员: #演员", "ABF-002") == {"title": "原消息片名", "actors": ["演员"]}


def test_jav_versions_share_folder_and_all_have_version_prefix(files):
    paths = [strm.generate_strm(message_id=message_id, chat_id=chat_id, category="18+", title=title,
                                file_name=filename, resolution="1080p") for message_id, chat_id, title, filename in [
        (1, "-1001", "ABF-002", "ABF-002.mp4"),
        (2, "-1002", "ABF-002 七嶋舞.mp4", "abf_002 七嶋舞.mp4"),
        (3, "-1001", None, "ABF-002 中文字幕.mp4"),
    ]]
    assert len(set(paths)) == 3
    assert {path.parent for path in paths} == {strm.STRM_BASE_DIR / "18+" / "ABF-002"}
    assert all(path.name.startswith("ABF-002 - ") for path in paths)
    assert "中文字幕" in paths[2].name
    assert all(".mp4" not in path.parent.name for path in paths)


def test_more_than_eight_versions_are_not_overwritten(files):
    paths = [strm.generate_strm(message_id=number, chat_id="-1001", category="18+", title="SSIS-806", resolution="1080p") for number in range(1, 13)]
    assert len(set(paths)) == 12
    assert len({path.read_text() for path in paths}) == 12


def test_different_codes_stay_separate_despite_same_scraped_title(files):
    one = strm.generate_strm(message_id=1, chat_id="-1001", category="18+", title="SKMJ-567", file_name="ALDN-570.mp4")
    two = strm.generate_strm(message_id=2, chat_id="-1001", category="18+", title="SKMJ-567", file_name="DASS-984.mp4")
    assert one.parent.name == "ALDN-570"
    assert two.parent.name == "DASS-984"


def test_conflicting_source_is_put_in_separate_folder(files):
    path = strm.generate_strm(message_id=1, chat_id="-1001", category="18+", title="SAME-096", file_name="TFSQB-112.mp4", caption="SAME-096 片名")
    assert "未确认番号" in path.parts
    assert path.parent.name not in {"SAME-096", "TFSQB-112"}


def test_compute_path_does_not_create_directories(files):
    path = strm.compute_strm_path(1, category="18+", title="ABF-002 七嶋舞.mp4")
    assert path.parent.name == "ABF-002"
    assert not strm.STRM_BASE_DIR.exists()


def test_token_regeneration_preserves_version_path(files, monkeypatch):
    path = strm.generate_strm(1, chat_id="-1001", category="18+", title="ABF-002")
    monkeypatch.setattr(settings.web, "stream_token", "new-token")
    again = strm.generate_strm(1, chat_id="-1001", category="18+", title="ABF-002", overwrite=True)
    assert again == path
    assert "new-token" in path.read_text()
    assert len(list(strm.STRM_BASE_DIR.rglob("*.strm"))) == 1


def test_jav_path_does_not_overwrite_another_source(files):
    path = strm.generate_strm(1, chat_id="-1001", category="18+", title="ABF-002")
    path.write_text("http://stream.test/stream/-1002/99?token=other")
    assert strm.generate_strm(1, chat_id="-1001", category="18+", title="ABF-002", overwrite=True) is None
    assert "/-1002/99" in path.read_text()


def test_duplicate_cleanup_keeps_different_versions(files):
    folder = strm.STRM_BASE_DIR / "电影"
    folder.mkdir(parents=True)
    (folder / "Movie.strm").write_text("http://stream.test/stream/-1001/1")
    other = folder / "Movie_2.strm"
    other.write_text("http://stream.test/stream/-1001/2")
    assert strm.cleanup_duplicate_strms() == 0
    assert other.is_file()


def test_jav_nfo_per_version_with_one_shared_cover(files):
    cache = files / "independent.jpg"
    cache.write_bytes(b"\xff\xd8\xffverified-cover")
    movie = {"number": "ABF-002", "title": "原片名", "provider": "JAV321", "id": "abf00002"}
    paths = [strm.generate_strm(number, chat_id="-1001", category="18+", title="ABF-002") for number in (1, 2)]
    results = [generate_jav_nfo(str(path), "ABF-002", "片名 <&>", ["演员 &"], movie, str(cache)) for path in paths]
    assert all(result["success"] for result in results)
    assert len({result["nfo_path"] for result in results}) == 2
    assert len({result["library_poster_path"] for result in results}) == 1
    for result in results:
        root = ET.parse(result["nfo_path"]).getroot()
        assert root.findtext("title") == "ABF-002 片名 <&>"
        assert root.findtext("uniqueid[@type='jav']") == "ABF-002"
        assert root.findtext("uniqueid[@type='MetaTube']") == "JAV321:abf00002"
        assert root.findtext("lockdata") == "true"
        assert root.findtext("actor/name") == "演员 &"
        assert root.findtext("thumb") == "poster.jpg"
    assert Path(results[0]["library_poster_path"]).read_bytes() == cache.read_bytes()


def test_flat_jav_nfos_never_share_poster_or_movie_nfo(files):
    cache = files / "cover.jpg"
    cache.write_bytes(b"\xff\xd8\xffcode-cover")
    results = [generate_jav_nfo(str(files / f"msg_{number}.strm"), code, poster_path=str(cache)) for number, code in [(1, "ALDN-570"), (2, "DASS-984")]]
    assert {Path(result["nfo_path"]).name for result in results} == {"msg_1.nfo", "msg_2.nfo"}
    assert len({result["library_poster_path"] for result in results}) == 2
    assert not (files / "movie.nfo").exists()
    assert not (files / "poster.jpg").exists()


def test_jav_nfo_rejects_wrong_metatube_identity(files):
    result = generate_jav_nfo(str(files / "movie.strm"), "ALDN-570", movie={"number": "SKMJ-567", "provider": "DUGA", "id": "wrong"})
    assert not result["success"]
    assert not (files / "movie.nfo").exists()


def source(number, code="ABF-002", chat="-1001"):
    return {"id": number, "message_id": number, "chat_id": chat, "category": "18+", "title": code,
            "file_name": f"{code}.mp4", "caption": "", "strm_path": f"/strm/18+/{code}/{code}.strm"}


def movie_item(number, record, presentation=None):
    return {"Id": str(number), "Type": "Movie", "Name": "SKMJ-567 错片名", "ProviderIds": {"MetaTube": "DUGA:wrong"},
            "Path": f"/media/tg/18+/{record['title']}/{record['title']}.strm",
            "PresentationUniqueKey": presentation or f"unique-{number}",
            "MediaSources": [{"ItemId": str(number), "Path": f"http://stream.test/stream/{record['chat_id']}/{record['message_id']}?token=private"}]}


def test_merge_plan_uses_original_codes_not_scraped_name_or_id():
    records = [source(1, "ALDN-570"), source(2, "DASS-984")]
    groups = emby_versions.plan_version_groups([movie_item(number, record) for number, record in enumerate(records, 1)], records)
    assert set(groups) == {"ALDN-570", "DASS-984"}
    assert all(len(group.items) == 1 for group in groups.values())


def test_same_code_group_includes_all_sources_and_recognizes_existing_merge():
    records = [source(1), source(2, chat="-1002")]
    items = [movie_item(number, record) for number, record in enumerate(records, 1)]
    group = emby_versions.plan_version_groups(items, records)["ABF-002"]
    assert group.complete and not group.already_merged
    assert group.source_keys == {("-1001", 1), ("-1002", 2)}
    for item in items:
        item["PresentationUniqueKey"] = "merged-key"
    assert emby_versions.plan_version_groups(items, records)["ABF-002"].already_merged


def test_unknown_or_conflicting_sources_block_unsafe_merge():
    records = [source(1), source(2)]
    mixed = movie_item(1, records[0])
    mixed["MediaSources"].append({"Path": "http://stream.test/stream/-1009/99"})
    assert emby_versions.item_records(mixed, emby_versions.index_records(records)) is None
    records[1]["caption"] = "SAME-096"
    assert emby_versions.item_records(movie_item(2, records[1]), emby_versions.index_records(records)) is None


def test_mixed_presentation_group_is_not_expanded():
    records = [source(1), source(2, "DASS-984")]
    items = [movie_item(number, record, "wrongly-shared") for number, record in enumerate(records, 1)]
    assert emby_versions.plan_version_groups(items, records) == {}


def test_missing_indexed_version_is_not_prematurely_marked_complete():
    records = [source(1), source(2)]
    group = emby_versions.plan_version_groups([movie_item(1, records[0])], records)["ABF-002"]
    assert not group.complete


def test_native_extra_parts_are_not_merged_into_the_full_movie_card():
    records = [source(1, "SONE-436"), source(2, "SONE-436"), source(3, "SONE-436"), source(4, "SONE-436")]
    full = movie_item(1, records[0])
    native = movie_item(2, records[1])
    native["PartCount"] = 3
    additional = [movie_item(3, records[2]), movie_item(4, records[3])]
    assert emby_versions.item_records(native, emby_versions.index_records(records)) is None
    group = emby_versions.plan_version_groups([full, native, *additional], records)["SONE-436"]
    assert not group.complete


def test_viewing_history_keeps_favorite_and_latest_position():
    states = [{"IsFavorite": True, "Played": True, "PlayCount": 2, "LastPlayedDate": "2026-11-01T00:00:00Z", "PlaybackPositionTicks": 0},
              {"IsFavorite": False, "Played": False, "PlayCount": 1, "LastPlayedDate": "2026-11-07T00:00:00Z", "PlaybackPositionTicks": 123}]
    merged = emby_versions.merge_user_states(states)
    assert merged["IsFavorite"] and merged["Played"]
    assert merged["PlayCount"] == 2 and merged["PlaybackPositionTicks"] == 123
    assert emby_versions.merge_user_states([{}]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("version_count", [2, 11])
@pytest.mark.parametrize("copy_unlocked_metadata", [False, True])
async def test_merge_api_verifies_one_card_and_all_versions(version_count, copy_unlocked_metadata):
    records = [source(number) for number in range(1, version_count + 1)]
    items = [movie_item(number, record) for number, record in enumerate(records, 1)]
    group = emby_versions.plan_version_groups(items, records)["ABF-002"]
    merged = False
    posted_ids = []
    state = {}
    old_state = {"IsFavorite": True, "Played": False, "PlayCount": 1, "PlaybackPositionTicks": 123, "LastPlayedDate": "2026-11-07T00:00:00Z"}

    def handle(request):
        nonlocal merged, state
        match = re.search(r"/emby/Items/(\d+)$", request.url.path)
        if request.method == "POST" and match:
            body = json.loads(request.content)
            assert not set(body) & {"UserData", "MediaSources", "MediaStreams", "RunTimeTicks", "PartCount"}
            items[int(match.group(1)) - 1].update(body)
            return httpx.Response(204)
        if request.url.path.endswith("Videos/MergeVersions"):
            assert all(emby_versions.identity_is_locked(item, "ABF-002") for item in items)
            posted_ids.extend(request.url.params["Ids"].split(","))
            merged = True
            if copy_unlocked_metadata:
                for item in items:
                    item["ProviderIds"].pop("jav", None)
                    item["LockData"] = False
                    item["LockedFields"] = []
            return httpx.Response(204)
        if request.url.path.endswith("UserData"):
            state = json.loads(request.content)
            return httpx.Response(204)
        match = re.search(r"/Users/admin/Items/(\d+)$", request.url.path)
        if match:
            number = int(match.group(1))
            data = dict(items[number - 1])
            if merged:
                data["MediaSources"] = [item["MediaSources"][0] for item in items]
                data["PresentationUniqueKey"] = "merged"
            data["UserData"] = state if merged else (old_state if number == version_count else {})
            return httpx.Response(200, json=data)
        return httpx.Response(200, json={"Items": [items[0]], "TotalRecordCount": 1})

    async with httpx.AsyncClient(base_url="http://emby.test/emby/", transport=httpx.MockTransport(handle)) as client:
        result = await emby_versions.merge_group(client, group, [{"Id": "admin", "Policy": {"IsAdministrator": True}}])
    assert result["source_count"] == version_count
    assert len(posted_ids) == version_count
    assert state["IsFavorite"] and state["PlaybackPositionTicks"] == 123
    assert len(result["protected_item_ids"]) == version_count
    assert all(emby_versions.identity_is_locked(item, "ABF-002") for item in items)


@pytest.mark.asyncio
async def test_merge_rechecks_sources_before_any_write():
    records = [source(1), source(2)]
    items = [movie_item(number, record) for number, record in enumerate(records, 1)]
    group = emby_versions.plan_version_groups(items, records)["ABF-002"]
    writes = []

    def handle(request):
        if request.method == "POST":
            writes.append(request)
        data = dict(items[0])
        data["MediaSources"] = [{"Path": "http://stream.test/stream/-1009/99"}]
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(base_url="http://emby.test/emby/", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="unknown source"):
            await emby_versions.merge_group(client, group, [{"Id": "admin", "Policy": {"IsAdministrator": True}}])
    assert not writes


def test_identity_lock_requires_number_full_lock_and_fields():
    item = {"ProviderIds": {"jav": "ABF-002"}, "LockData": True,
            "LockedFields": sorted(emby_versions.IDENTITY_LOCK_FIELDS)}
    assert emby_versions.identity_is_locked(item, "ABF-002")
    assert not emby_versions.identity_is_locked(item, "SAME-096")
    assert not emby_versions.identity_is_locked({**item, "ProviderIds": None}, "ABF-002")
    assert not emby_versions.identity_is_locked({**item, "LockData": False}, "ABF-002")
    assert not emby_versions.identity_is_locked({**item, "LockedFields": []}, "ABF-002")


@pytest.mark.asyncio
async def test_already_merged_identity_is_protected_without_merging_or_touching_playback():
    records = [source(1), source(2, chat="-1002")]
    items = [movie_item(number, record, "merged-key") for number, record in enumerate(records, 1)]
    sources = [item["MediaSources"][0] for item in items]
    old_state = {"IsFavorite": True, "Played": False, "PlayCount": 1, "PlaybackPositionTicks": 123}
    for item in items:
        item.update({"MediaSources": sources, "RunTimeTicks": 6000000000,
                     "LockedFields": ["Genres"], "UserData": dict(old_state)})
    items[0]["ProviderIds"]["Jav"] = "WRONG-001"
    group = emby_versions.plan_version_groups(items, records)["ABF-002"]
    writes = []

    def handle(request):
        match = re.search(r"/Items/(\d+)$", request.url.path)
        assert match, "no playback, user-state or merge endpoint should be called"
        item = items[int(match.group(1)) - 1]
        if request.method == "POST":
            writes.append(str(request.url))
            body = json.loads(request.content)
            assert not set(body) & {"UserData", "MediaSources", "MediaStreams", "RunTimeTicks", "Chapters", "PartCount"}
            assert body["ProviderIds"] == {"MetaTube": "DUGA:wrong", "jav": "ABF-002"}
            assert "Genres" in body["LockedFields"]
            item.update(body)
            return httpx.Response(204)
        return httpx.Response(200, json=item)

    async with httpx.AsyncClient(base_url="http://emby.test/emby/", transport=httpx.MockTransport(handle)) as client:
        result = await emby_versions.merge_group(client, group, [{"Id": "admin", "Policy": {"IsAdministrator": True}}])
        again = await emby_versions.merge_group(client, group, [{"Id": "admin", "Policy": {"IsAdministrator": True}}])
    assert result["already_merged"] and result["source_count"] == 2
    assert len(result["protected_item_ids"]) == 2 and not again["protected_item_ids"]
    assert len(writes) == 2
    assert all(item["UserData"] == old_state and item["RunTimeTicks"] == 6000000000 for item in items)
    assert all(item["MediaSources"] == sources and item["PresentationUniqueKey"] == "merged-key" for item in items)


@pytest.mark.asyncio
async def test_failed_identity_lock_does_not_continue_to_merge():
    records = [source(1), source(2)]
    items = [movie_item(number, record) for number, record in enumerate(records, 1)]
    group = emby_versions.plan_version_groups(items, records)["ABF-002"]
    writes = []

    def handle(request):
        if request.method == "POST":
            writes.append(request.url.path)
            return httpx.Response(204)
        number = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=items[number - 1])

    async with httpx.AsyncClient(base_url="http://emby.test/emby/", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="protection was not retained"):
            await emby_versions.merge_group(client, group, [{"Id": "admin", "Policy": {"IsAdministrator": True}}])
    assert writes == ["/emby/Items/1"]


@pytest.mark.asyncio
async def test_reconcile_includes_existing_merges_and_singletons_but_not_incomplete_groups(monkeypatch):
    records = [source(1), source(2), source(3, "DASS-984"), source(4, "SAME-096"), source(5, "SAME-096")]
    items = [movie_item(1, records[0], "merged"), movie_item(2, records[1], "merged"),
             movie_item(3, records[2]), movie_item(4, records[3])]
    monkeypatch.setattr(settings.emby, "url", "http://emby.test")
    monkeypatch.setattr(emby_versions, "load_jav_records", AsyncMock(return_value=records))
    monkeypatch.setattr(emby_versions, "fetch_movies", AsyncMock(return_value=items))
    users = [{"Id": "admin", "Policy": {"IsAdministrator": True}}]
    original_client = httpx.AsyncClient
    monkeypatch.setattr(emby_versions.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=users))))
    merge = AsyncMock(return_value={"already_merged": True, "source_count": 2, "protected_item_ids": ["1"]})
    monkeypatch.setattr(emby_versions, "merge_group", merge)
    result = await emby_versions.reconcile_versions({"ABF-002", "DASS-984", "SAME-096"})
    assert {call.args[1].code for call in merge.await_args_list} == {"ABF-002", "DASS-984"}
    assert result == {"merged": 0, "identity_protected": 2, "completed": {"ABF-002", "DASS-984"}}


def pending(message_id, title="ABF-002", chat_id=-1001):
    return bot.PendingVideo(message_id=message_id, chat_id=chat_id, file_name=f"ABF-002 actor{message_id}.mp4",
                            file_id="file", file_unique_id=f"unique{message_id}", size=100, duration=600,
                            mime_type="video/mp4", width=1920, height=1080, caption="", source_link="", title=title)


@pytest.mark.asyncio
async def test_bot_duplicate_does_not_generate_orphan_after_title_change(files, sessions, monkeypatch):
    async with sessions() as session:
        session.add(Media(chat_id="-1001", message_id=1, category="18+", title="Old title", file_name="ABF-002.mp4"))
        await session.commit()
    generate = Mock()
    monkeypatch.setattr(bot, "generate_strm", generate)
    assert await bot._save_video(pending(1, "ABF-002 新标题"), "18+") is None
    generate.assert_not_called()
    assert not strm.STRM_BASE_DIR.exists()


@pytest.mark.asyncio
async def test_bot_saves_versions_with_correct_nfos_without_scraping(files, sessions):
    one = await bot._save_video(pending(1), "18+")
    two = await bot._save_video(pending(2, "ABF-002 另一演员.mp4"), "18+")
    assert one and two and Path(one).parent == Path(two).parent
    async with sessions() as session:
        records = (await session.execute(select(Media))).scalars().all()
    assert len(records) == 2
    assert all(ET.parse(record.nfo_path).getroot().findtext("uniqueid[@type='jav']") == "ABF-002" for record in records)
    assert await bot._save_video(pending(1, "changed again"), "18+") is None
    assert len(list(strm.STRM_BASE_DIR.rglob("*.strm"))) == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_is_rejected_before_file_generation(files, sessions):
    results = await asyncio.gather(bot._save_video(pending(1), "18+"), bot._save_video(pending(1), "18+"))
    assert sum(result is not None for result in results) == 1
    async with sessions() as session:
        assert (await session.execute(select(func.count(Media.id)))).scalar() == 1
    assert len(list(strm.STRM_BASE_DIR.rglob("*.strm"))) == 1


@pytest.mark.asyncio
async def test_part_message_dedup_matches_exact_id_and_chat(sessions):
    async with sessions() as session:
        session.add(Media(chat_id="-1001", message_id=1, parts="91,912", title="Movie"))
        await session.commit()
    assert await monitor._source_is_saved(-1001, 91)
    assert not await monitor._source_is_saved(-1001, 9)
    assert not await monitor._source_is_saved(-1002, 91)


@pytest.mark.asyncio
async def test_repeated_part_does_not_increment_size_or_duration(sessions):
    async with sessions() as session:
        session.add(Media(chat_id="-1001", message_id=1, title="Movie", size=100, duration=600))
        await session.commit()
    media = SimpleNamespace(file_size=50)
    assert (await monitor._merge_part_record(-1001, "Movie", 2, media, 300))[0]
    assert (await monitor._merge_part_record(-1001, "Movie", 2, media, 300))[0]
    assert (await monitor._merge_part_record(-1001, "Movie", 3, media, 300))[0]
    async with sessions() as session:
        record = (await session.execute(select(Media))).scalar_one()
        assert record.parts == "2,3" and record.size == 200 and record.duration == 1200


@pytest.mark.asyncio
async def test_monitor_duplicate_does_not_write_any_strm(files, sessions, monkeypatch):
    async with sessions() as session:
        session.add(Media(chat_id="-1001", message_id=1, category="18+", title="ABF-002"))
        await session.commit()
    video = SimpleNamespace(file_name="ABF-002 七嶋舞.mp4", duration=600, width=1920, height=1080)
    message = SimpleNamespace(id=1, video=video, document=None, caption="")

    async def history(*args, **kwargs):
        yield message

    client = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(title="source", username=None)), get_chat_history=history)
    monkeypatch.setattr(monitor, "get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(monitor, "_get_rule_for_channel", AsyncMock(return_value=SimpleNamespace(enabled=True, category="18+")))
    monkeypatch.setattr(monitor, "_get_strm_subdir", AsyncMock(return_value=None))
    generate = Mock()
    monkeypatch.setattr(monitor, "generate_strm", generate)
    result = await monitor.scan_channel(-1001)
    assert result["success"]
    generate.assert_not_called()
    assert not strm.STRM_BASE_DIR.exists()


@pytest.mark.asyncio
async def test_monitor_fallback_filename_still_groups_jav_versions(files, sessions, monkeypatch):
    messages = [SimpleNamespace(id=number, document=None, caption="", link=None,
                               video=SimpleNamespace(file_name=f"ABF-002 actor{number}.mp4", duration=600,
                                                     width=1920, height=1080, file_size=100, mime_type="video/mp4"))
                for number in (1, 2)]

    async def history(*args, **kwargs):
        for message in messages:
            yield message

    client = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(title="source", username=None)), get_chat_history=history)
    rule = SimpleNamespace(enabled=True, id=1, category="18+", title_rule=None, episode_rule=None,
                           season=None, season_rule=None, year_rule=None, filename_rule=None)
    monkeypatch.setattr(monitor, "get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(monitor, "_get_rule_for_channel", AsyncMock(return_value=rule))
    monkeypatch.setattr(monitor, "_get_strm_subdir", AsyncMock(return_value=None))
    monkeypatch.setattr(monitor, "parse_media_info", lambda **kwargs: None)
    monkeypatch.setattr(monitor, "_send_notify", AsyncMock())
    result = await monitor.scan_channel(-1001)
    assert result["success"]
    await asyncio.sleep(0)
    async with sessions() as session:
        records = (await session.execute(select(Media))).scalars().all()
    assert len(records) == 2
    assert {Path(record.strm_path).parent.name for record in records} == {"ABF-002"}
    assert all(Path(record.nfo_path).exists() for record in records)
    assert (await monitor.scan_channel(-1001))["success"]
    assert len(list(strm.STRM_BASE_DIR.rglob("*.strm"))) == 2


@pytest.mark.asyncio
async def test_enrich_batch_schedules_merge_of_only_affected_jav_codes(files, monkeypatch):
    from app.media.enrich_queue import Batch, EnrichQueue
    from app.media import emby_notifier
    monkeypatch.setattr(settings.emby, "url", "http://emby.test")
    monkeypatch.setattr(settings.emby, "api_key", "test")
    monkeypatch.setattr(bot, "send_enriched_batch_notify", AsyncMock())
    refresh = AsyncMock()
    monkeypatch.setattr(emby_notifier, "refresh_library", refresh)
    batch = Batch(1, {}, 2, done=2, results=[{"category": "18+", "jav_code": "ABF-002"}, {"category": "电影"}])
    await EnrichQueue()._finalize_batch(batch)
    refresh.assert_awaited_once_with(jav_codes=["ABF-002"])


@pytest.mark.asyncio
async def test_version_reconcile_requests_are_coalesced(monkeypatch):
    monkeypatch.setattr(emby_versions, "_pending", {})
    monkeypatch.setattr(emby_versions, "_task", None)
    monkeypatch.setattr(emby_versions.asyncio, "sleep", AsyncMock())
    reconcile = AsyncMock(return_value={"merged": 1, "completed": {"ABF-002"}})
    monkeypatch.setattr(emby_versions, "reconcile_versions", reconcile)
    emby_versions.schedule_version_reconcile(["ABF-002"])
    task = emby_versions._task
    emby_versions.schedule_version_reconcile(["ABF-002"])
    assert emby_versions._task is task
    await task
    reconcile.assert_awaited_once_with({"ABF-002"})
    assert not emby_versions._pending
