#!/usr/bin/env python3
"""
TG Emby Streamer — 命令行扫描工具

用法:
  python scripts/scan.py --all               扫描所有已启用频道（limit=0 全量）
  python scripts/scan.py --all --limit 200    每个频道扫 200 条
  python scripts/scan.py -c CHANNEL_ID        扫描指定频道
  python scripts/scan.py -c CHANNEL_ID --limit 500

systemd + venv 部署（⚠️ 先停服务，Telegram 会话不可双开）:
  sudo systemctl stop tg-emby.service
  cd /home/Dirige/TGEMBY && CONFIG_PATH=/home/ubuntu/tg-emby/config/config.yaml \
      ./venv/bin/python scripts/scan.py --all --limit 0
  sudo systemctl start tg-emby.service

Docker 容器内执行:
  docker exec -it tg-emby-streamer python scripts/scan.py --all
"""
import argparse
import asyncio
import signal
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.WARNING)
logging.getLogger("app").setLevel(logging.INFO)

from app.database import init_db
from app.scan_cli import scan_all_channels, make_terminal_callbacks, color, C_CYAN, C_BOLD, C_DIM, C_RESET


async def main():
    parser = argparse.ArgumentParser(
        description="TG Emby Streamer 命令行扫描工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  %(prog)s --all                    扫描所有频道（默认 limit=0 全量）\n"
            "  %(prog)s --all --limit 200        每个频道扫 200 条\n"
            "  %(prog)s -c -1001234567890        扫描指定频道\n"
            "  %(prog)s -c -1001234567890 -l 50  扫描指定频道 50 条"
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="扫描所有已启用的频道")
    group.add_argument("-c", "--channel", type=str, help="扫描指定频道 ID")
    parser.add_argument("-l", "--limit", type=int, default=0,
                        help="每个频道扫描的消息条数（0=全部，默认 0）")

    args = parser.parse_args()

    print()
    banner = color(f"{' TG Emby Streamer 命令行扫描 ':=^60}", C_CYAN + C_BOLD)
    print(f"  {banner}")
    print()

    if args.all:
        print(f"  {color('模式:', C_BOLD)} 全量扫描所有已启用频道")
    else:
        print(f"  {color('模式:', C_BOLD)} 扫描频道 {args.channel}")
    print(f"  {color('限制:', C_BOLD)}  每个频道 {args.limit or '全部'} 条消息")
    print()

    await init_db()

    cancelled = False
    task: asyncio.Task | None = None

    def _signal_handler():
        nonlocal cancelled
        if cancelled:
            return
        cancelled = True
        print()
        print(f"  {color('收到中断信号，等待当前频道扫描完成...', C_YELLOW + C_BOLD)}")
        if task:
            task.cancel()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    on_channel_start, on_item, on_channel_done, print_summary = make_terminal_callbacks()

    try:
        result = await scan_all_channels(
            limit=args.limit,
            channel_id=args.channel if not args.all else None,
            on_channel_start=on_channel_start,
            on_item=on_item,
            on_channel_done=on_channel_done,
        )
    except asyncio.CancelledError:
        print()
        print(f"  {color('已中断', C_YELLOW + C_BOLD)}")
        return

    print_summary(result)


if __name__ == "__main__":
    asyncio.run(main())
