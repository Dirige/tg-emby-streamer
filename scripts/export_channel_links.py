#!/usr/bin/env python3
"""用已登录账号只读查询已知频道的公开用户名；不加入频道、不创建邀请链接。"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def collect(config: Path, document: dict) -> dict:
    from pyrogram import Client
    from scripts.upload_tg import upload_config

    logging.getLogger("pyrogram").setLevel(logging.ERROR)
    known = {str(rule["channel_id"]) for rule in document["rules"]}
    known.update(str(row["channel_id"]) for row in document.get("media_only_channels", []))
    links = {str(rule["channel_id"]): rule["join_url"] for rule in document["rules"] if rule.get("join_url")}
    async with Client("tgemby-public-channel-links", in_memory=True, no_updates=True,
                      **upload_config(config)) as client:
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if str(chat.id) in known and chat.username:
                links[str(chat.id)] = "https://t.me/" + chat.username
        for channel_id in sorted(known - set(links)):
            try:
                chat = await client.get_chat(int(channel_id))
                if chat.username:
                    links[channel_id] = "https://t.me/" + chat.username
            except Exception:
                # Unknown/private/left channels require an administrator's invite.
                continue
    return links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--channels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        from scripts.library_bundle import atomic_write
        document = json.loads(args.channels.read_text(encoding="utf-8"))
        links = asyncio.run(collect(args.config, document))
        atomic_write(args.output, json.dumps(links, ensure_ascii=False, indent=2).encode(), mode=0o600)
        print(f"已查询 {len(links)} 个公开频道链接；无公开用户名的频道没有猜测或创建邀请。")
    except Exception as exc:
        print(f"频道链接查询失败（{type(exc).__name__}），已保留原清单。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
