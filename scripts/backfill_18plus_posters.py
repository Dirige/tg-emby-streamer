#!/usr/bin/env python3
"""修复指定 JAV 条目的封面，复用线上刮削逻辑，不连接 Telegram。

  CONFIG_PATH=/home/ubuntu/tg-emby/config/config.yaml ./venv/bin/python \
      scripts/backfill_18plus_posters.py --media-id 12104 --media-id 12105

未指定 ID 时默认最多处理 50 条；--limit 0 明确选择全量。
自动跳过已经使用独立封面文件的条目，不发送补图通知。
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.database import async_session
from app.models import Media
from app.media.enricher import enrich_media_safe
from app.media.jav import detect_jav_code
from app.media.metatube import poster_path


async def backfill(limit: int, media_ids: list[int]) -> dict:
    processed = updated = missed = 0
    before = None
    while not limit or processed < limit:
        async with async_session() as session:
            query = select(Media).where(
                Media.category == "18+", Media.strm_path.isnot(None), Media.strm_path != ""
            ).order_by(Media.id.desc())
            if media_ids:
                query = query.where(Media.id.in_(media_ids))
            if before is not None:
                query = query.where(Media.id < before)
            rows = (await session.execute(query.limit(500))).scalars().all()
        if not rows:
            break
        for record in rows:
            before = record.id
            code = detect_jav_code(record.file_name, record.caption, record.title)
            expected = poster_path(code) if code else None
            if expected and record.poster_path == str(expected) and expected.is_file():
                continue
            result = await enrich_media_safe(record.id)
            processed += 1
            if result.get("success"):
                updated += 1
            else:
                missed += 1
            print(
                f"id={record.id} code={result.get('jav_code', '-')} "
                f"poster={'OK' if result.get('success') else 'MISS'} "
                f"{result.get('reason', '')}", flush=True
            )
            if limit and processed >= limit:
                break
            await asyncio.sleep(0.4)
    stats = {"processed": processed, "updated": updated, "missed": missed}
    print(stats, flush=True)
    return stats


def main():
    parser = argparse.ArgumentParser(description="JAV 独立封面修复（不发送通知）")
    parser.add_argument("--limit", type=int, default=50, help="最多处理条数（默认 50，0=全量）")
    parser.add_argument("--media-id", type=int, action="append", default=[], help="只修复这些 ID，可重复指定")
    parser.add_argument("--resume", action="store_true", help="兼容参数：默认已自动跳过完成项")
    args = parser.parse_args()
    if args.limit < 0 or any(mid <= 0 for mid in args.media_id):
        parser.error("条数不能为负，media-id 必须为正整数")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(backfill(args.limit, args.media_id))


if __name__ == "__main__":
    main()
