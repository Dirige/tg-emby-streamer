#!/usr/bin/env python3
"""Interactively log in with the recipient's account and save it to config.yaml."""

import sys
import os
import asyncio
import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings, save_config


async def main():
    from pyrogram import Client

    api_id = settings.telegram.api_id or int(input("Telegram API ID（my.telegram.org）：").strip())
    api_hash = settings.telegram.api_hash or getpass.getpass("Telegram API Hash：").strip()
    phone = settings.telegram.phone or input("手机号（含国家区号，例如 +86…）：").strip()
    if len(sys.argv) >= 4:
        # Compatibility only: avoid this form because shell history stores credentials.
        phone, api_id, api_hash = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    if not api_id or not api_hash or not phone:
        raise ValueError("API ID、API Hash 和手机号都不能为空")
    proxy = settings.proxy.to_dict()
    if proxy:
        print("将使用配置文件中的代理登录 Telegram。")

    client = Client(
        name="tg_session_gen",
        api_id=api_id,
        api_hash=api_hash,
        phone_number=phone,
        proxy=proxy,
        in_memory=True,
    )

    try:
        await client.start()
        session_string = await client.export_session_string()
        save_config({"telegram.api_id": api_id, "telegram.api_hash": api_hash,
                     "telegram.phone": phone, "telegram.session_string": session_string})
        print("登录成功，凭据已直接保存到配置文件；不会打印 Session String。")
    finally:
        if client.is_connected:
            await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
