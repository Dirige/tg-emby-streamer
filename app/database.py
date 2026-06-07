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
        
        # 添加 caption 列
        if "caption" not in columns:
            await conn.execute(text("ALTER TABLE media ADD COLUMN caption TEXT"))
        
        # 添加 recognized 列
        if "recognized" not in columns:
            await conn.execute(text("ALTER TABLE media ADD COLUMN recognized BOOLEAN NOT NULL DEFAULT 1"))
        
        # 重命名 tmdb_name 为 display_name
        if "tmdb_name" in columns and "display_name" not in columns:
            await conn.execute(text("ALTER TABLE media RENAME COLUMN tmdb_name TO display_name"))
        
        # 删除 tmdb_id 列 (SQLite 不直接支持 DROP COLUMN，需要重建表)
        if "tmdb_id" in columns:
            # 由于 SQLite 限制，我们保留 tmdb_id 列但不再使用它
            pass
            
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



# 18+ Adult 独立数据库
ADULT_DATABASE_URL = "sqlite+aiosqlite:///./data/adult.db"
adult_engine = create_async_engine(ADULT_DATABASE_URL, echo=False)
adult_session = async_sessionmaker(adult_engine, class_=AsyncSession, expire_on_commit=False)


async def init_adult_db():
    """初始化 18+ 独立数据库"""
    async with adult_engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                chat_id VARCHAR NOT NULL,
                file_name VARCHAR,
                file_id VARCHAR,
                file_unique_id VARCHAR,
                size INTEGER,
                duration INTEGER,
                mime_type VARCHAR,
                width INTEGER,
                height INTEGER,
                category VARCHAR,
                display_name VARCHAR,
                season INTEGER,
                episode INTEGER,
                resolution VARCHAR,
                strm_path VARCHAR,
                caption VARCHAR,
                recognized BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_adult_message_id ON media(message_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_adult_chat_id ON media(chat_id)"))


async def get_adult_session() -> AsyncSession:
    """获取 18+ 数据库会话"""
    async with adult_session() as session:
        yield session
