import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, delete
from app.database import async_session, init_db, engine, Base
from app.models import Media
from app.media.strm import remove_old_strm
from app.config import settings


async def clear_unrecognized():
    await init_db()

    async with async_session() as session:
        result = await session.execute(
            select(Media).where(Media.recognized == False)
        )
        records = result.scalars().all()

        if not records:
            print("没有找到未识别的记录")
            return

        print(f"找到 {len(records)} 条未识别记录")

        strm_deleted = 0
        for record in records:
            if record.strm_path:
                try:
                    remove_old_strm(record.strm_path)
                    strm_deleted += 1
                except Exception as e:
                    print(f"删除STRM文件失败: {record.strm_path} - {e}")

        await session.execute(
            delete(Media).where(Media.recognized == False)
        )
        await session.commit()

        print(f"已删除 {len(records)} 条数据库记录")
        print(f"已删除 {strm_deleted} 个STRM文件")


if __name__ == "__main__":
    asyncio.run(clear_unrecognized())
