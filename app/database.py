from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import settings

DATABASE_URL = settings.database.url
if DATABASE_URL.startswith("sqlite:///"):
    DATABASE_URL = DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def _migrate(conn):
    try:
        result = await conn.execute(text("PRAGMA table_info(media)"))
        columns = [row[1] for row in result.fetchall()]
        if "caption" not in columns:
            await conn.execute(text("ALTER TABLE media ADD COLUMN caption TEXT"))
        if "recognized" not in columns:
            await conn.execute(text("ALTER TABLE media ADD COLUMN recognized BOOLEAN NOT NULL DEFAULT 1"))
            await conn.execute(text("UPDATE media SET recognized = 1 WHERE display_name IS NOT NULL AND display_name != ''"))
            await conn.execute(text("UPDATE media SET recognized = 0 WHERE display_name IS NULL OR display_name = ''"))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Migration skipped or failed (likely already applied): {e}")


async def init_db():
    async with engine.begin() as conn:
        from app.models import Media
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
