import asyncio
import shutil
from pathlib import Path
from app.database import async_session, init_db
from app.models import Media
from app.media.parser import parse_media_info
from app.media.strm import generate_strm
from sqlalchemy import select


async def fix_all():
    await init_db()

    strm_dir = Path("strm")
    if strm_dir.exists():
        shutil.rmtree(strm_dir)
        print(f"Cleaned old STRM directory: {strm_dir}")

    async with async_session() as session:
        records = (await session.execute(select(Media))).scalars().all()

        if not records:
            print("No records in database")
            return

        for r in records:
            file_name = r.file_name or "unknown.mp4"
            media_info = parse_media_info(file_name)

            r.category = media_info.get("category")
            r.season = media_info.get("season")
            r.episode = media_info.get("episode")
            r.resolution = media_info.get("resolution")
            if media_info.get("tmdb_id"):
                r.tmdb_id = media_info.get("tmdb_id")
            if media_info.get("tmdb_name"):
                r.tmdb_name = media_info.get("tmdb_name")
            elif media_info.get("title"):
                r.tmdb_name = media_info.get("title")

            strm_path = generate_strm(
                message_id=r.message_id,
                file_name=file_name,
                category=media_info.get("category"),
                title=media_info.get("title"),
                season=media_info.get("season"),
                episode=media_info.get("episode"),
                tmdb_id=media_info.get("tmdb_id"),
                tmdb_name=media_info.get("tmdb_name"),
            )
            r.strm_path = str(strm_path) if strm_path else None
            print(f"  [{r.message_id}] {file_name} -> {r.strm_path}")

            await session.commit()

    print(f"\nDone. Regenerated STRM for {len(records)} records.")


if __name__ == "__main__":
    asyncio.run(fix_all())
