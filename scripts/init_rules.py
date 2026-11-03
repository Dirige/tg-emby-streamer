"""创建频道解析规则并插入数据库"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import async_session, init_db
from app.models import ChannelRule

RULES = [
    {
        "channel_id": "-1001668687022",
        "channel_name": "影视剧/电视剧 已完结【收录】",
        "category": "电视剧",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1002855500786",
        "channel_name": "YunRuo 云弱-电视剧",
        "category": "电视剧",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1001780152064",
        "channel_name": "国漫聚合 【国产动漫/动画】",
        "category": "动漫",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1002387671344",
        "channel_name": "Netflix电视剧海外剧在线看",
        "category": "海外剧",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1002432317394",
        "channel_name": "TG追剧4K电视剧",
        "category": "电视剧",
        "title_rule": "regex:#([^\s#]+?)(?=\s+第|\s*#|$)",
        "episode_rule": "regex:第\s*(\d+)\s*集",
        "season": 1,
    },
    {
        "channel_id": "-1001714422299",
        "channel_name": "影集🎬4K高清电影在线看",
        "category": "电影",
        "title_rule": "line:0",
        "episode_rule": "",
        "year_rule": "regex:\((\d{4})\)",
        "season": None,
    },
    {
        "channel_id": "-1002480304582",
        "channel_name": "热门动漫 4K超清在线看",
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
