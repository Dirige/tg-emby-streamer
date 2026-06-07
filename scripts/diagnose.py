import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func
from app.database import async_session, init_db
from app.models import Media
from app.config import settings


async def diagnose():
    await init_db()
    async with async_session() as session:
        total = await session.scalar(select(func.count(Media.id)))
        recognized = await session.scalar(select(func.count(Media.id)).where(Media.recognized == True))
        unrecognized = await session.scalar(select(func.count(Media.id)).where(Media.recognized == False))
        has_strm = await session.scalar(select(func.count(Media.id)).where(Media.strm_path.isnot(None)))
        no_strm = await session.scalar(select(func.count(Media.id)).where(Media.strm_path.is_(None)))
        cosplay = await session.scalar(select(func.count(Media.id)).where(Media.category == "cosplay"))

        print(f"=== 数据库统计 ===")
        print(f"总记录数: {total}")
        print(f"已识别: {recognized}")
        print(f"未识别: {unrecognized}")
        print(f"有STRM路径: {has_strm}")
        print(f"无STRM路径: {no_strm}")
        print(f"cosplay(18+): {cosplay}")

        print(f"\n=== 分类分布 ===")
        result = await session.execute(
            select(Media.category, func.count(Media.id)).group_by(Media.category)
        )
        for row in result.all():
            print(f"  {row[0] or '(空)'}: {row[1]}")

        print(f"\n=== STRM路径抽样 ===")
        result = await session.execute(
            select(Media.strm_path, Media.file_name, Media.category).where(Media.strm_path.isnot(None)).limit(5)
        )
        for row in result.all():
            print(f"  {row[0]}")
            print(f"    文件: {row[1]}, 分类: {row[2]}")

        print(f"\n=== 未识别文件名抽样(前20条) ===")
        result = await session.execute(
            select(Media.file_name, Media.category).where(Media.recognized == False).limit(20)
        )
        for row in result.all():
            print(f"  {row[0]} | 分类: {row[1]}")

        print(f"\n=== 已识别文件名抽样(前20条) ===")
        result = await session.execute(
            select(Media.file_name, Media.category, Media.season, Media.episode).where(Media.recognized == True).limit(20)
        )
        for row in result.all():
            print(f"  {row[0]} | 分类: {row[1]} | S{row[2]}E{row[3]}")

    strm_dir = settings.strm.output_path
    print(f"\n=== STRM目录: {strm_dir} ===")
    if strm_dir.exists():
        strm_files = list(strm_dir.rglob("*.strm"))
        print(f"STRM文件数量: {len(strm_files)}")
        if strm_files:
            print("前5个:")
            for f in strm_files[:5]:
                print(f"  {f}")
    else:
        print("STRM目录不存在")


if __name__ == "__main__":
    asyncio.run(diagnose())
