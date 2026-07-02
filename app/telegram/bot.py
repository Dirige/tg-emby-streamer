import logging
from pyrogram import Client
from app.config import settings

logger = logging.getLogger(__name__)

_bot_client: Client | None = None


async def get_bot() -> Client | None:
    global _bot_client
    token = settings.notify.bot_token
    if not token:
        return None
    if _bot_client is not None:
        try:
            if _bot_client.is_connected:
                return _bot_client
        except Exception:
            pass
        try:
            await _bot_client.stop()
        except Exception:
            pass
    proxy = None
    if settings.proxy.enabled and settings.proxy.hostname:
        proxy = {
            "scheme": settings.proxy.scheme,
            "hostname": settings.proxy.hostname,
            "port": settings.proxy.port,
        }
    _bot_client = Client(
        name="tg_notify_bot",
        api_id=settings.telegram.api_id,
        api_hash=settings.telegram.api_hash,
        bot_token=token,
        in_memory=True,
        proxy=proxy,
    )
    await _bot_client.start()
    logger.info("Bot client started")
    return _bot_client


async def _send_via_user(message: str) -> bool:
    try:
        from app.telegram.client import get_client
        client = await get_client()
        target_clean = settings.notify.target.lstrip("@")
        try:
            await client.send_message(int(target_clean), message)
        except ValueError:
            await client.send_message(target_clean, message)
        logger.info(f"通知已通过账号发送到 {settings.notify.target}")
        return True
    except Exception as e:
        logger.warning(f"账号发送通知失败: {e}")
        return False


async def send_notify(message: str) -> bool:
    try:
        if not settings.notify.enabled:
            return False
        target = settings.notify.target
        if not target:
            return False
        bot = await get_bot()
        if bot is None:
            return await _send_via_user(message)
        target_clean = target.lstrip("@")
        try:
            await bot.send_message(int(target_clean), message)
        except ValueError:
            await bot.send_message(target_clean, message)
        logger.info(f"通知已通过 Bot 发送到 {target}")
        return True
    except Exception as e:
        logger.warning(f"Bot发送通知失败，尝试账号发送: {e}")
        return await _send_via_user(message)


async def stop_bot():
    global _bot_client
    if _bot_client:
        try:
            await _bot_client.stop()
        except Exception:
            pass
        _bot_client = None
