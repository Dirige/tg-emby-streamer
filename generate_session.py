import asyncio
import glob as glob_mod
import os
from pyrogram import Client
from dotenv import load_dotenv

load_dotenv()

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")
PHONE = os.getenv("TELEGRAM_PHONE")

SINGBOX_ENABLED = os.getenv("SINGBOX_ENABLED", "false").lower() == "true"
SINGBOX_SOCKS_PORT = int(os.getenv("SINGBOX_SOCKS_PORT", "10808"))
PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT = int(os.getenv("PROXY_PORT", 0))
PROXY_SCHEME = os.getenv("PROXY_SCHEME", "socks5")
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "false").lower() == "true"


async def main():
    print("=" * 50)
    print("Telegram Session String 生成工具")
    print("=" * 50)
    print(f"API_ID:  {API_ID}")
    print(f"Phone:   {PHONE}")

    proxy = None
    if SINGBOX_ENABLED:
        proxy = {
            "scheme": "socks5",
            "hostname": "127.0.0.1",
            "port": SINGBOX_SOCKS_PORT,
        }
        print(f"Proxy:   socks5://127.0.0.1:{SINGBOX_SOCKS_PORT} (sing-box)")
    elif PROXY_ENABLED and PROXY_HOST:
        proxy = {
            "scheme": PROXY_SCHEME,
            "hostname": PROXY_HOST,
            "port": PROXY_PORT,
        }
        print(f"Proxy:   {PROXY_SCHEME}://{PROXY_HOST}:{PROXY_PORT}")

    print("=" * 50)

    for f in glob_mod.glob("*.session"):
        os.remove(f)
        print(f"Deleted old session file: {f}")

    client = Client(
        name="tg_emby_fresh_session",
        api_id=int(API_ID),
        api_hash=API_HASH,
        phone_number=PHONE,
        proxy=proxy,
        workdir=".",
    )

    async with client:
        session_string = await client.export_session_string()
        print("\n" + "=" * 50)
        print("Session String 生成成功！")
        print("=" * 50)

        env_path = ".env"
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        found = False
        for i, line in enumerate(lines):
            if line.startswith("TELEGRAM_SESSION_STRING="):
                lines[i] = f"TELEGRAM_SESSION_STRING={session_string}\n"
                found = True
                break
        if not found:
            lines.append(f"\nTELEGRAM_SESSION_STRING={session_string}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        print(f"Session string 已自动写入 .env 文件")
        print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
