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
        from app.models import Media, ChannelRule, Category
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(sa_text("ALTER TABLE media ADD COLUMN parts VARCHAR"))
        except Exception:
            pass


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
