"""重新扫描之前失败的频道"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.basicConfig(level=logging.INFO)
from app.telegram.monitor import scan_channel

FAILED_CHANNELS = [
    (-1001000000020, "示例频道2"),
    (-1001000000040, "示例频道4"),
    (-1001000000050, "TG追剧"),
    (-1001000000070, "热门动漫"),
]

async def main():
    for cid, name in FAILED_CHANNELS:
        print(f"\n--- {name} ({cid}) ---")
        result = await scan_channel(cid, limit=50)
        print(f"  {result.get('message', '')}")

asyncio.run(main())
