from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text as sa_text
from app.config import settings

DATABASE_URL = settings.database.url
if DATABASE_URL.startswith("sqlite:///"):
    DATABASE_URL = DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        from app.models import Media, ChannelRule, Category, UnrecognizedMedia
        await conn.run_sync(Base.metadata.create_all)
        # 复合唯一索引：保证同一频道同一条消息不会重复入库（SQLite 建表后补建）
        await conn.execute(sa_text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_media_chat_message "
            "ON media (chat_id, message_id)"
        ))
        # 新频道可选择安全入库；旧规则默认不变，不重新解释已入库来源。
        try:
            await conn.execute(sa_text(
                "ALTER TABLE channel_rules ADD COLUMN strict_import BOOLEAN NOT NULL DEFAULT 0"
            ))
        except Exception:
            pass
        for col, col_type in [
            ("parts", "VARCHAR"),
            ("tmdb_id", "INTEGER"),
            ("tmdb_type", "VARCHAR"),
            ("tmdb_title", "VARCHAR"),
            ("tmdb_year", "INTEGER"),
            ("tmdb_data", "TEXT"),
            ("nfo_path", "VARCHAR"),
            ("poster_path", "VARCHAR"),
            ("backdrop_path", "VARCHAR"),
        ]:
            try:
                await conn.execute(sa_text(f"ALTER TABLE media ADD COLUMN {col} {col_type}"))
            except Exception:
                pass


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
