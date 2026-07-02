"""初始化数据库：创建表、分类、频道规则"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.basicConfig(level=logging.INFO)
from app.database import init_db, async_session
from app.models import ChannelRule, Category
from sqlalchemy import select

RULES = [
    dict(channel_id='-1001668687022', channel_name='影视剧/电视剧 已完结', category='电视剧',
         title_rule=r'regex:#([^\s#]+?)(?:\s+[^\s#]+)*?(?=\s+第|\s*#|\.mp4|$)', episode_rule='regex:第\\s*(\\d+)\\s*集', season=1),
    dict(channel_id='-1002855500786', channel_name='YunRuo 云弱-电视剧', category='电视剧',
         title_rule=r'regex:#([^\s#]+?)(?:\s+[^\s#]+)*?(?=\s+第|\s*#|\.mp4|$)', episode_rule='regex:第\\s*(\\d+)\\s*集', season=1),
    dict(channel_id='-1001780152064', channel_name='国漫聚合 【国产动漫/动画】', category='动漫',
         title_rule=r'regex:#([^\s#]+?)(?:\s+[^\s#]+)*?(?=\s+第|\s*#|\.mp4|$)', episode_rule='regex:第\\s*(\\d+)\\s*集', season=1),
    dict(channel_id='-1002387671344', channel_name='Netflix电视剧海外剧', category='海外剧',
         title_rule=r'regex:#([^\s#]+?)(?:\s+[^\s#]+)*?(?=\s+第|\s*#|\.mp4|$)', episode_rule='regex:第\\s*(\\d+)\\s*集', season=1),
    dict(channel_id='-1002432317394', channel_name='TG追剧 4K电视剧', category='电视剧',
         title_rule=r'regex:#([^\s#]+?)(?:\s+[^\s#]+)*?(?=\s+第|\s*#|\.mp4|$)', episode_rule='regex:第\\s*(\\d+)\\s*集', season=1),
    dict(channel_id='-1001714422299', channel_name='影集 4K高清电影', category='电影',
         title_rule='line:0', episode_rule='', year_rule=r'regex:\((\d{4})\)', season=None),
    dict(channel_id='-1002480304582', channel_name='热门动漫 4K超清', category='动漫',
         title_rule=r'regex:#([^\s#]+?)(?:\s+[^\s#]+)*?\s+第\s*\d+\s*集', episode_rule='regex:第\\s*(\\d+)\\s*集', season=1),
    dict(channel_id='-1002124166721', channel_name='中文字幕 | 日本AV即是正义！', category='18+',
         title_rule='', episode_rule='', filename_rule=r'regex:([A-Z]+[-_]\d+)', season=None),
]

CATEGORIES = [
    dict(name='电视剧', strm_dir='电视剧', sort_order=1),
    dict(name='电影', strm_dir='电影', sort_order=2),
    dict(name='动漫', strm_dir='动漫', sort_order=3),
    dict(name='海外剧', strm_dir='海外剧', sort_order=4),
    dict(name='18+', strm_dir='18+', sort_order=5),
]


async def main():
    await init_db()
    async with async_session() as session:
        for cat in CATEGORIES:
            session.add(Category(**cat))
        await session.commit()
        print(f'Added {len(CATEGORIES)} categories')

        for r in RULES:
            rule = ChannelRule(**r)
            session.add(rule)
        await session.commit()
        print(f'Added {len(RULES)} rules')

        # Verify
        result = await session.execute(select(ChannelRule))
        all_rules = result.scalars().all()
        print(f'Total rules: {len(all_rules)}')
        for r in all_rules:
            print(f'  {r.channel_id} | {r.channel_name} | {r.category} | filename_rule={r.filename_rule}')


asyncio.run(main())
