import asyncio
import threading
from pyrogram import Client
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_client: Client | None = None
_bot_pool: list[Client] = []
_bot_index: int = 0
_bot_lock = threading.Lock()


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


async def init_bot_pool():
    global _bot_pool
    tokens = settings.telegram.bot_token_list
    if not tokens:
        logger.info("No bot tokens configured, bot pool disabled")
        return

    proxy = _get_proxy()
    for i, token in enumerate(tokens):
        try:
            bot = Client(
                name=f"tg_bot_{i}",
                api_id=settings.telegram.api_id,
                api_hash=settings.telegram.api_hash,
                bot_token=token,
                proxy=proxy,
                in_memory=True,
            )
            await bot.start()
            _bot_pool.append(bot)
            logger.info(f"Bot #{i} started successfully")
        except Exception as e:
            logger.error(f"Failed to start bot #{i}: {e}")

    if _bot_pool:
        logger.info(f"Bot pool initialized with {len(_bot_pool)} bots")


def get_stream_client() -> Client:
    global _bot_index
    if _bot_pool:
        with _bot_lock:
            idx = _bot_index % len(_bot_pool)
            _bot_index += 1
        return _bot_pool[idx]
    return _client


async def warmup_client(client: Client):
    count = 0
    async for _ in client.get_dialogs():
        count += 1
    logger.info(f"Peer cache warmed up: {count} dialogs loaded")


async def stop_client():
    global _client, _bot_pool
    if _client:
        try:
            await _client.stop()
        except Exception:
            pass
        _client = None

    for bot in _bot_pool:
        try:
            await bot.stop()
        except Exception:
            pass
    _bot_pool.clear()
    logger.info("All clients stopped")
