"""创建频道解析规则并插入数据库"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import async_session, init_db
from app.models import ChannelRule

RULES = [
    {
        "channel_id": "-1001000000010",
        "channel_name": "示例频道1【收录】",
        "category": "电视剧",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1001000000020",
        "channel_name": "示例频道2",
        "category": "电视剧",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1001000000030",
        "channel_name": "示例频道3",
        "category": "动漫",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1001000000040",
        "channel_name": "示例频道4在线看",
        "category": "海外剧",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1001000000050",
        "channel_name": "TG追剧4K电视剧",
        "category": "电视剧",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1001000000060",
        "channel_name": "影集🎬4K高清电影在线看",
        "category": "电影",
        "title_rule": "line:0",
        "episode_rule": "",
        "year_rule": "regex:\((\d{4})\)",
        "season": None,
    },
    {
        "channel_id": "-1001000000070",
        "channel_name": "示例频道7在线看",
        "category": "动漫",
        "title_rule": "regex:#(.+?)\s+第\s*\d+\s*集",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
]

async def main():
    await init_db()
    async with async_session() as session:
        # Clear existing rules
        from sqlalchemy import delete
        await session.execute(delete(ChannelRule))
        
        for r in RULES:
            rule = ChannelRule(
                channel_id=r["channel_id"],
                channel_name=r["channel_name"],
                category=r["category"],
                title_rule=r["title_rule"],
                episode_rule=r["episode_rule"],
                season=r.get("season"),
                year_rule=r.get("year_rule", ""),
                filename_template=r.get("filename_template", ""),
                enabled=True,
            )
            session.add(rule)
        
        await session.commit()
    
    print("Rules created:")
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(ChannelRule))
        for r in result.scalars().all():
            print(f"  {r.channel_id} ({r.channel_name}) [{r.category}] title={r.title_rule}")

asyncio.run(main())
