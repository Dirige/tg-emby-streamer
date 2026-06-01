import asyncio
import sys
sys.path.insert(0, ".")

from pyrogram import Client
from app.config import settings


async def main():
    proxy = None
    if settings.singbox.enabled:
        proxy = {"scheme": "socks5", "hostname": "127.0.0.1", "port": settings.singbox.socks_port}
    elif settings.proxy.enabled:
        proxy = settings.proxy.to_dict()

    client = Client(
        name="tg_emby_session",
        api_id=settings.telegram.api_id,
        api_hash=settings.telegram.api_hash,
        proxy=proxy,
    )

    await client.start()

    session_string = await client.export_session_string()
    print(f"\n{'='*60}")
    print(f"SESSION_STRING={session_string}")
    print(f"{'='*60}")

    me = await client.get_me()
    print(f"\nLogged in: {me.first_name} (id={me.id})")

    await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
