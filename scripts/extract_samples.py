"""从每个监控频道提取最近2条视频消息 - 带 warmup"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(override=True)

api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
api_hash = os.getenv("TELEGRAM_API_HASH", "")
session_string = os.getenv("TELEGRAM_SESSION_STRING", "")
monitor_channels_str = os.getenv("TELEGRAM_MONITOR_CHANNELS", "")

if not api_id or not api_hash or not session_string:
    print("Missing credentials in .env")
    sys.exit(1)

monitor_channels = [int(c.strip()) for c in monitor_channels_str.split(",") if c.strip()]

proxy_enabled = os.getenv("PROXY_ENABLED", "false").lower() == "true"
proxy_dict = None
if proxy_enabled:
    proxy_dict = {
        "scheme": os.getenv("PROXY_SCHEME", "socks5"),
        "hostname": os.getenv("PROXY_HOST", ""),
        "port": int(os.getenv("PROXY_PORT", "0")),
    }

OUT_DIR = Path("/vol2/1000/SSD2/Docker/tgemby/samples")
OUT_DIR.mkdir(parents=True, exist_ok=True)

from pyrogram import Client

async def main():
    client = Client(
        name="extract_warm",
        api_id=api_id,
        api_hash=api_hash,
        session_string=session_string,
        proxy=proxy_dict,
        in_memory=True,
    )
    await client.start()
    print(f"Connected as {client.me.id}")

    # Warmup: fetch dialogs to cache peers
    print("Warming up peer cache...")
    count = 0
    async for _ in client.get_dialogs():
        count += 1
    print(f"Warmup done: {count} dialogs cached")

    for cid in monitor_channels:
        try:
            chat = await client.get_chat(cid)
            ch_name = chat.title or str(cid)
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(cid))
            print(f"\n=== {ch_name} ({cid}) ===")

            found = 0
            async for msg in client.get_chat_history(cid, limit=100):
                media = msg.video or msg.document
                if not media:
                    continue
                if msg.document and msg.document.mime_type and not msg.document.mime_type.startswith("video/"):
                    continue

                file_path = OUT_DIR / f"{safe_name}_{found+1}.txt"
                content_parts = []
                content_parts.append(f"CHANNEL_ID={cid}")
                content_parts.append(f"CHANNEL_NAME={ch_name}")
                content_parts.append(f"MESSAGE_ID={msg.id}")
                content_parts.append(f"FILE_NAME={media.file_name or 'N/A'}")
                content_parts.append(f"FILE_SIZE={media.file_size or 0}")
                content_parts.append(f"MIME_TYPE={media.mime_type or 'N/A'}")
                content_parts.append(f"DURATION={media.duration or 0}")
                cap = msg.caption or ""
                content_parts.append(f"=== CAPTION ===")
                content_parts.append(cap)
                content_parts.append(f"=== END ===")
                content_parts.append(f"LINK=https://t.me/c/{str(cid).lstrip('-')}/{msg.id}")

                file_path.write_text("\n".join(content_parts), encoding="utf-8")
                print(f"  [{found+1}] msg_id={msg.id} file={media.file_name}")
                cap_preview = cap[:120].replace("\n", " | ") if cap else "(no caption)"
                print(f"       caption: {cap_preview}")

                found += 1
                if found >= 2:
                    break

            if found == 0:
                print(f"  (no video messages found)")

        except Exception as e:
            print(f"  ERROR for {cid}: {e}")

    await client.stop()
    print(f"\nDone! Files saved to {OUT_DIR}")
    # List files
    for f in sorted(OUT_DIR.iterdir()):
        print(f"  {f.name}")

asyncio.run(main())
