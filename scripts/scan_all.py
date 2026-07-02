"""扫描所有配置了规则的频道，每个频道50条"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.basicConfig(level=logging.INFO)
from app.database import async_session, init_db
from app.models import ChannelRule
from app.telegram.monitor import scan_channel
from sqlalchemy import select

async def main():
    await init_db()
    async with async_session() as session:
        result = await session.execute(
            select(ChannelRule).where(ChannelRule.enabled == True)
        )
        rules = result.scalars().all()
    
    print(f"Found {len(rules)} enabled rules\n")
    
    for rule in rules:
        cid = int(rule.channel_id)
        print(f"\n{'='*60}")
        print(f"Scanning {rule.channel_name} ({cid}) [{rule.category}]")
        print(f"  title_rule: {rule.title_rule}")
        print(f"  episode_rule: {rule.episode_rule}")
        print(f"  season: {rule.season}")
        print(f"{'='*60}")
        result = await scan_channel(cid, limit=50)
        print(f"  Result: {result.get('message', result.get('message', ''))}")

asyncio.run(main())
