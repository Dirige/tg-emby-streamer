from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import dashboard
from app.database import Base
from app.models import Category, ChannelRule, Media


@pytest_asyncio.fixture
async def media_api(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'media.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dashboard, "async_session", sessions)
    async with sessions() as session:
        session.add_all([
            Category(name="国产", strm_dir="国产", sort_order=6),
            Category(name="AI短剧", strm_dir="AI短剧", sort_order=7),
            ChannelRule(channel_id="-1001", channel_name="测试国产频道", category="国产"),
            ChannelRule(channel_id="-1002", channel_name="全集频道", category="18+"),
        ])
        data = [
            (1, "SONE-436", "sone-436.mp4", "18+", "-1001"),
            (2, "SONE-436 1080P", "another_full.MP4", "18+", "-1002"),
            (3, "SONE-436", "sone-436Part003.mp4", "国产", "-1001"),
            (4, "导入错误.mp4", "wrong.mp4", "18+", "-1003"),
            (5, None, "Untitled.MP4", "国产", "-1004"),
            (6, "", "No title.mp4", "国产", "-1004"),
            (7, "百分比 100%_ 真爱", "percent.mp4", "电影", "-1003"),
            (8, "普通电影", "plain.mp4", "电影", "-1005"),
            (9, "中文 & + test", "chinese.mp4", "电视剧", "-1005"),
            (10, "其它电影", "other.mp4", "电影", "-1003"),
        ]
        for number, title, filename, category, chat in data:
            session.add(Media(
                id=number, message_id=number, chat_id=chat, title=title,
                file_name=filename, category=category, created_at=datetime(2026, 11, 7),
                caption="SONE-436 说明" if number == 10 else "",
                tmdb_title="SONE-436 元数据片名" if number == 10 else None,
            ))
        await session.commit()
    app = FastAPI()
    app.include_router(dashboard.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    await engine.dispose()


async def search(client, **params):
    response = await client.get("/api/media", params=params)
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_channel_rules_expose_per_channel_media_count(media_api):
    client, _ = media_api
    response = await client.get("/api/channel-rules")
    assert response.status_code == 200
    rules = {rule["channel_id"]: rule for rule in response.json()["rules"]}
    assert rules["-1001"]["media_count"] == 2
    assert rules["-1002"]["media_count"] == 1


@pytest.mark.asyncio
async def test_media_list_without_search_keeps_all_records(media_api):
    client, _ = media_api
    data = await search(client, page_size=3)
    assert data["total"] == 10
    assert [item["id"] for item in data["items"]] == [10, 9, 8]


@pytest.mark.asyncio
async def test_title_search_is_case_insensitive_and_trims_spaces(media_api):
    client, _ = media_api
    data = await search(client, search="  sone-436  ")
    assert data["total"] == 3
    assert {item["id"] for item in data["items"]} == {1, 2, 3}


@pytest.mark.asyncio
async def test_mp4_title_search_does_not_match_normal_video_filenames(media_api):
    client, _ = media_api
    data = await search(client, search=".mp4")
    assert {item["id"] for item in data["items"]} == {4, 5, 6}


@pytest.mark.asyncio
async def test_filename_search_is_explicit(media_api):
    client, _ = media_api
    assert (await search(client, search="another_full"))["total"] == 0
    data = await search(client, search="another_full", search_field="file_name")
    assert [item["id"] for item in data["items"]] == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["-1001", "测试国产"])
async def test_channel_search_matches_id_or_configured_name(media_api, keyword):
    client, _ = media_api
    data = await search(client, search=keyword, search_field="channel")
    assert {item["id"] for item in data["items"]} == {1, 3}


@pytest.mark.asyncio
async def test_search_and_category_filter_share_the_same_count(media_api):
    client, _ = media_api
    data = await search(client, search="SONE-436", category="国产", page_size=1)
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [3]
    tv = await search(client, search="中文", category="tv")
    assert [item["id"] for item in tv["items"]] == [9]


@pytest.mark.asyncio
async def test_search_paginates_all_matches_not_just_the_current_page(media_api):
    client, _ = media_api
    first = await search(client, search="SONE-436", page=1, page_size=2)
    second = await search(client, search="SONE-436", page=2, page_size=2)
    assert first["total"] == second["total"] == 3
    assert [item["id"] for item in first["items"]] == [3, 2]
    assert [item["id"] for item in second["items"]] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["100%", "%", "_", "100%_"])
async def test_search_treats_sql_wildcards_as_literal_characters(media_api, keyword):
    client, _ = media_api
    data = await search(client, search=keyword)
    assert [item["id"] for item in data["items"]] == [7]


@pytest.mark.asyncio
async def test_all_fields_search_includes_caption_and_metadata(media_api):
    client, _ = media_api
    data = await search(client, search="SONE-436", search_field="all")
    assert {item["id"] for item in data["items"]} == {1, 2, 3, 10}


@pytest.mark.asyncio
async def test_empty_and_no_result_queries(media_api):
    client, _ = media_api
    assert (await search(client, search="   "))["total"] == 10
    assert (await search(client, search="不存在的标题"))["items"] == []
    assert (await search(client, search="' OR 1=1 --"))["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"search_field": "invalid"}])
async def test_invalid_search_and_pagination_parameters_are_rejected(media_api, params):
    client, _ = media_api
    response = await client.get("/api/media", params=params)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_does_not_rename_or_delete_sources(media_api):
    client, sessions = media_api
    await search(client, search=".mp4")
    await search(client, search="SONE-436", search_field="all")
    async with sessions() as session:
        records = list((await session.execute(select(Media).order_by(Media.id))).scalars())
        assert len(records) == 10
        assert records[0].title == "SONE-436" and records[0].file_name == "sone-436.mp4"
        assert records[3].title == "导入错误.mp4"


@pytest.mark.asyncio
async def test_custom_category_can_be_listed_and_saved_on_a_channel(media_api):
    client, _ = media_api
    response = await client.post("/api/category/save", json={"name": "水果派", "sort_order": 8})
    assert response.json()["success"]
    categories = (await client.get("/api/categories")).json()["categories"]
    assert [category["name"] for category in categories] == ["国产", "AI短剧", "水果派"]
    response = await client.post("/api/channel-rule/save", json={
        "channel_id": "-1006", "channel_name": "新频道", "category": "水果派", "enabled": False,
    })
    assert response.json()["success"]
    rule = (await client.get("/api/channel-rule", params={"channel_id": "-1006"})).json()["rule"]
    assert rule["category"] == "水果派" and rule["enabled"] is False
