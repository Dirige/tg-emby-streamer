#!/usr/bin/env python3
"""Generate Telegram session string for TG Emby Streamer.

Usage:
  python3 scripts/generate_session.py
  python3 scripts/generate_session.py <phone> <api_id> <api_hash>
"""

import sys
import os
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "")

PROXY_ENABLED = os.getenv("PROXY_ENABLED", "").lower() == "true"
PROXY_SCHEME = os.getenv("PROXY_SCHEME", "socks5")
PROXY_HOST = os.getenv("PROXY_HOST", "")
PROXY_PORT = int(os.getenv("PROXY_PORT", "10808"))

if len(sys.argv) >= 4:
    TELEGRAM_PHONE = sys.argv[1]
    TELEGRAM_API_ID = int(sys.argv[2])
    TELEGRAM_API_HASH = sys.argv[3]


async def main():
    from pyrogram import Client

    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH or not TELEGRAM_PHONE:
        print("ERROR: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE must be set in .env or passed as args")
        sys.exit(1)

    proxy = None
    if PROXY_ENABLED and PROXY_HOST:
        proxy = {
            "scheme": PROXY_SCHEME,
            "hostname": PROXY_HOST,
            "port": PROXY_PORT,
        }
        print(f"Using proxy: {PROXY_SCHEME}://{PROXY_HOST}:{PROXY_PORT}")

    client = Client(
        name="tg_session_gen",
        api_id=TELEGRAM_API_ID,
        api_hash=TELEGRAM_API_HASH,
        phone_number=TELEGRAM_PHONE,
        proxy=proxy,
        in_memory=True,
    )

    await client.start()
    session_string = await client.export_session_string()
    await client.stop()

    print(f"\nSession string generated successfully!")
    print(f"\nAdd this to your .env file:\n")
    print(f"TELEGRAM_SESSION_STRING={session_string}\n")


if __name__ == "__main__":
    asyncio.run(main())
