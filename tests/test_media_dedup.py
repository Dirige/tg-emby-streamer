"""多频道 message_id 冲突与去重关键 SQL 语义测试（内存库）。"""
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, func

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base
from app.models import Media


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_media_chat_message "
            "ON media (chat_id, message_id)"
        ))
    session_maker = async_sessionmaker(engine, class_=__import__(
        "sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession,
        expire_on_commit=False)
    # 每个测试用独立文件内数据
    yield engine, session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_same_message_id_different_chats_allowed(db):
    """不同频道的相同 message_id 可以并存（多频道不互相漏录）。"""
    engine, session_maker = db
    import asyncio
    async with session_maker() as s:
        s.add(Media(message_id=100, chat_id="-100111", title="A"))
        s.add(Media(message_id=100, chat_id="-100222", title="B"))
        await s.commit()
        n = (await s.execute(select(func.count(Media.id)))).scalar()
        assert n == 2


@pytest.mark.asyncio
async def test_same_chat_same_message_id_rejected(db):
    """同频道同 message_id 违反唯一约束（同一链接不重复入库）。"""
    engine, session_maker = db
    from sqlalchemy.exc import IntegrityError
    async with session_maker() as s:
        s.add(Media(message_id=100, chat_id="-100111", title="A"))
        await s.commit()
        s.add(Media(message_id=100, chat_id="-100111", title="A2"))
        with pytest.raises(IntegrityError):
            await s.commit()


@pytest.mark.asyncio
async def test_chat_scoped_lookup(db):
    """按 (chat_id, message_id) 查询能准确定位记录（防串台）。"""
    engine, session_maker = db
    async with session_maker() as s:
        s.add(Media(message_id=100, chat_id="-100111", title="A"))
        s.add(Media(message_id=100, chat_id="-100222", title="B"))
        await s.commit()
        r = (await s.execute(
            select(Media).where(Media.chat_id == "-100222", Media.message_id == 100)
        )).scalar_one_or_none()
        assert r is not None and r.title == "B"
