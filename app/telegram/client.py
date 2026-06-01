from pyrogram import Client
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_client: Client | None = None


def _get_proxy() -> dict | None:
    if settings.singbox.enabled:
        return {
            "scheme": "socks5",
            "hostname": "127.0.0.1",
            "port": settings.singbox.socks_port,
        }
    if settings.proxy.enabled:
        return settings.proxy.to_dict()
    return None


async def get_client() -> Client:
    global _client
    if _client is not None:
        try:
            if _client.is_connected:
                return _client
        except Exception:
            pass
        try:
            await _client.stop()
        except Exception:
            pass
        _client = None

    proxy = _get_proxy()
    if proxy:
        logger.info(f"Telegram client using proxy: {proxy['scheme']}://{proxy['hostname']}:{proxy['port']}")
    else:
        logger.warning("Telegram client connecting without proxy")

    _client = Client(
        name="tg_emby_session",
        api_id=settings.telegram.api_id,
        api_hash=settings.telegram.api_hash,
        session_string=settings.telegram.session_string,
        proxy=proxy,
        in_memory=True,
    )
    await _client.start()
    logger.info("Pyrogram client started")
    return _client


async def warmup_client(client: Client):
    count = 0
    async for _ in client.get_dialogs():
        count += 1
    logger.info(f"Peer cache warmed up: {count} dialogs loaded")


async def stop_client():
    global _client
    if _client:
        try:
            await _client.stop()
        except Exception:
            pass
        _client = None
