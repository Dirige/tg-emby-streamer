"""CLI 扫描引擎 — 可被 scripts/scan.py 和 Web 共用"""
import asyncio
import time
import logging
from sqlalchemy import select
from app.database import async_session
from app.models import ChannelRule
from app.telegram.monitor import scan_channel

logger = logging.getLogger(__name__)

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_DIM = "\033[2m"


def color(text: str, code: str) -> str:
    return f"{code}{text}{C_RESET}"


def fmt_duration(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def fmt_title(title: str, maxlen: int = 60) -> str:
    return title if len(title) <= maxlen else title[:maxlen - 3] + "..."


async def scan_all_channels(
    limit: int = 0,
    channel_id: str | None = None,
    on_item=None,
    on_channel_start=None,
    on_channel_done=None,
    on_progress=None,
) -> dict:
    """扫描一个或所有频道，支持实时回调。

    Args:
        limit: 每个频道扫描的消息条数（0=全部）
        channel_id: None=扫描所有已启用，否则扫描指定频道
        on_item(title, category, strm_path, action, message): 每处理一条视频
        on_channel_start(name, channel_id, index, total): 开始扫描频道
        on_channel_done(name, channel_id, count, success, message): 频道扫描完成
        on_progress(name, current, total): 进度更新
    """
    async with async_session() as session:
        if channel_id:
            result = await session.execute(
                select(ChannelRule).where(
                    ChannelRule.channel_id == str(channel_id),
                    ChannelRule.enabled == True,
                )
            )
            rules = [r for r in result.scalars().all() if r]
        else:
            result = await session.execute(
                select(ChannelRule).where(ChannelRule.enabled == True)
            )
            rules = result.scalars().all()

    if not rules:
        return {"success": 0, "failed": 0, "total_added": 0, "results": [], "message": "没有已启用的频道规则"}

    results = []
    total_added = 0
    total_channels = len(rules)
    start_time = time.time()

    def _cb(**kw):
        if on_item:
            on_item(**kw)

    for idx, rule in enumerate(rules):
        cid = int(rule.channel_id)
        ch_name = rule.channel_name or str(rule.channel_id)

        if on_channel_start:
            on_channel_start(name=ch_name, channel_id=cid, index=idx + 1, total=total_channels)

        try:
            r = await scan_channel(
                cid,
                limit=limit,
                notify_detailed=False,
                item_callback=_cb,
            )
            success = r.get("success", False)
            msg = r.get("message", "")
            match = __import__("re").search(r"新增 (\d+) 条", msg)
            count = int(match.group(1)) if match else 0
            if success:
                total_added += count
        except asyncio.CancelledError:
            raise
        except Exception as e:
            success = False
            count = 0
            msg = str(e)

        results.append({
            "channel_id": str(rule.channel_id),
            "channel_name": ch_name,
            "category": rule.category,
            "count": count,
            "success": success,
            "message": msg,
        })

        if on_channel_done:
            on_channel_done(name=ch_name, channel_id=cid, count=count, success=success, message=msg)

    duration = time.time() - start_time
    failed = sum(1 for r in results if not r["success"])

    return {
        "success": total_channels - failed,
        "failed": failed,
        "total_channels": total_channels,
        "total_added": total_added,
        "results": results,
        "duration": duration,
    }


# ── 终端输出回调工厂 ─────────────────────────────────────────────

def make_terminal_callbacks():
    """返回一组回调函数，直接输出彩色终端文本。"""
    _ch_name = [""]

    def on_channel_start(name, channel_id, index, total):
        _ch_name[0] = name
        sep = color("━" * 55, C_DIM)
        print()
        print(f"  {sep}")
        header = color(f"  [{index}/{total}]  {name}  ({channel_id})", C_CYAN + C_BOLD)
        print(f"  {header}")
        print(f"  {sep}")
        print()

    def on_item(title, category, strm_path, action="added", message=""):
        if action == "added":
            line = f"    {color('✓', C_GREEN)}  {fmt_title(title)}"
            if category:
                line += f"  {color(f'({category})', C_DIM)}"
            if strm_path:
                line += f"\n      {color(strm_path, C_DIM)}"
            print(line)
        elif action == "skipped":
            line = f"    {color('○', C_YELLOW)}  {fmt_title(title)}  {color('(已存在)', C_DIM)}"
            print(line)
        else:
            line = f"    {color('✗', C_RED)}  {fmt_title(title)}"
            if message:
                line += f"  {color(message, C_DIM)}"
            print(line)

    def on_channel_done(name, channel_id, count, success, message):
        if success:
            status = color(f"✅ 新增 {count} 条", C_GREEN + C_BOLD)
        else:
            status = color(f"❌ {message}", C_RED + C_BOLD)
        print(f"\n    {status}")
        print()

    def print_summary(result: dict):
        print()
        sep = color("━" * 55, C_DIM)
        print(f"  {sep}")
        print(f"  {color('📊 扫描完成', C_BOLD)}")
        print(f"  {sep}")
        print()
        for r in result.get("results", []):
            name = r["channel_name"] or r.get("channel_id", "")
            if r["success"]:
                cnt = color(f"{r['count']} 条", C_GREEN)
                line = f"    {color('✅', C_GREEN)}  {name[:30]:30s}  {cnt}"
            else:
                msg = r.get("message", "")[:40]
                line = f"    {color('❌', C_RED)}  {name[:30]:30s}  {color(msg, C_RED)}"
            print(line)
        print()
        print(f"    {color('─' * 45, C_DIM)}")
        print(f"    {color('总计', C_BOLD)}:  {result['total_channels']} 个频道")
        print(f"    {color('新增', C_GREEN)}:  {result['total_added']} 条媒体")
        print(f"    {color('失败', C_RED)}:  {result['failed']} 个频道")
        print(f"    {color('耗时', C_YELLOW)}:  {fmt_duration(result['duration'])}")
        print()

    return on_channel_start, on_item, on_channel_done, print_summary
