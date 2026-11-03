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
