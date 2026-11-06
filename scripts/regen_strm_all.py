#!/usr/bin/env python3
"""命令行全量重生成 STRM 文件。

用法（与 systemd 服务使用同一份配置）:
    CONFIG_PATH=/path/to/config.yaml venv/bin/python scripts/regen_strm_all.py [--dry-run]
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if not os.environ.get("CONFIG_PATH"):
    os.environ["CONFIG_PATH"] = str(BASE_DIR / "config.yaml")

sys.path.insert(0, str(BASE_DIR))

import asyncio  # noqa: E402
import logging  # noqa: E402

from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def main():
    from app.database import init_db
    from app.media.strm_regen import regen_all

    print(f"配置文件: {os.environ['CONFIG_PATH']}")
    print(f"STRM 输出目录: {settings.strm.output_dir}")
    print(f"流媒体地址: {settings.stream.local_url}")
    print(f"stream_token: {'已设置' if settings.web.stream_token else '!! 未设置 !!'}")
    confirm = input("确认重生成所有 STRM 文件? [y/N] ").strip().lower()
    if confirm != "y":
        print("已取消")
        return

    await init_db()
    stats = await regen_all()
    print("完成:", stats)


if __name__ == "__main__":
    asyncio.run(main())
