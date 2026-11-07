import asyncio
import logging
from pyrogram import Client
from pyrogram.errors import (
    AuthKeyDuplicated,
    AuthKeyUnregistered,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    SessionPasswordNeeded,
    Unauthorized,
)
from app.config import settings, save_config_value, KEY_TO_ENV
import socks as socks_mod
import ssl
import socket

logger = logging.getLogger(__name__)

_client: Client | None = None
_client_lock = asyncio.Lock()

_auth_client: Client | None = None
_auth_state: dict = {}
_phone_code_hash: str | None = None


def _get_proxy() -> dict | None:
    if settings.proxy.enabled:
        return settings.proxy.to_dict()
    return None


def is_invalid_auth_key_error(exc: Exception) -> bool:
    text = str(exc)
    return (
        isinstance(exc, (AuthKeyDuplicated, AuthKeyUnregistered, Unauthorized))
        or "AUTH_KEY_DUPLICATED" in text
        or "AUTH_KEY_UNREGISTERED" in text
    )


async def invalidate_saved_session(reason: str = ""):
    global _client
    async with _client_lock:
        if _client:
            try:
                await _client.stop()
            except Exception:
                pass
            _client = None
        save_config_value("telegram.session_string", "")
        settings.reload()
    logger.warning(f"Telegram session 已失效并清空: {reason}")


async def get_client() -> Client:
    global _client
    async with _client_lock:
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

        if not settings.telegram.session_string:
            logger.warning("TELEGRAM_SESSION_STRING 为空")
            raise ConnectionError("NEED_LOGIN")

        _client = Client(
            name="tg_emby_session",
            api_id=settings.telegram.api_id,
            api_hash=settings.telegram.api_hash,
            session_string=settings.telegram.session_string,
            proxy=proxy,
            in_memory=True,
        )
        try:
            await _client.start()
        except Exception as e:
            if is_invalid_auth_key_error(e):
                try:
                    await _client.stop()
                except Exception:
                    pass
                _client = None
                save_config_value("telegram.session_string", "")
                settings.reload()
                logger.error(f"Telegram session 已失效，需要重新登录: {e}")
                raise ConnectionError("NEED_LOGIN:AUTH_KEY_INVALID") from e
            raise
        logger.info("Pyrogram client started")
        return _client


async def stop_client():
    global _client, _auth_client
    async with _client_lock:
        if _client:
            try:
                await _client.stop()
            except Exception:
                pass
            _client = None
    if _auth_client:
        try:
            await _auth_client.stop()
        except Exception:
            pass
        _auth_client = None
    logger.info("Telegram client stopped")


def _make_client(in_memory: bool = True) -> Client:
    return Client(
        name="tg_auth_session",
        api_id=settings.telegram.api_id,
        api_hash=settings.telegram.api_hash,
        proxy=_get_proxy(),
        in_memory=in_memory,
    )


async def send_code(phone: str) -> dict:
    global _auth_client, _phone_code_hash

    if _auth_client:
        try:
            await _auth_client.disconnect()
        except Exception:
            pass
        _auth_client = None

    _auth_client = _make_client()
    try:
        await _auth_client.connect()
        sent_code = await _auth_client.send_code(phone)
        _phone_code_hash = sent_code.phone_code_hash
        logger.info(f"验证码已发送到 {phone}, phone_code_hash={_phone_code_hash}")
        return {"success": True, "message": "验证码已发送，请查看 Telegram 应用"}
    except Exception as e:
        logger.error(f"发送验证码失败: {e}")
        if _auth_client:
            try:
                await _auth_client.disconnect()
            except Exception:
                pass
            _auth_client = None
        return {"success": False, "message": f"发送验证码失败: {e}"}


async def verify_code(phone: str, code: str) -> dict:
    global _auth_client, _client, _phone_code_hash

    if not _auth_client or not _auth_client.is_connected:
        return {"success": False, "message": "请先发送验证码", "need_password": False}

    if not _phone_code_hash:
        return {"success": False, "message": "验证码已过期，请重新发送", "need_password": False}

    try:
        signed_in = await _auth_client.sign_in(phone, _phone_code_hash, code)
        if isinstance(signed_in, bool):
            return {"success": False, "message": "需要注册", "need_password": False}
        user = signed_in
        session_string = await _auth_client.export_session_string()
        save_config_value("telegram.session_string", session_string)
        settings.reload()

        if _client:
            try:
                await _client.stop()
            except Exception:
                pass
            _client = None

        logger.info(f"手机号 {phone} 登录成功, user_id={user.id}")

        if _auth_client:
            try:
                await _auth_client.disconnect()
            except Exception:
                pass
            _auth_client = None
        _phone_code_hash = None

        return {
            "success": True,
            "message": "登录成功",
            "user_id": user.id,
            "need_password": False,
        }
    except SessionPasswordNeeded:
        return {
            "success": True,
            "message": "需要两步验证密码",
            "need_password": True,
        }
    except PhoneCodeExpired:
        _phone_code_hash = None
        return {"success": False, "message": "验证码已过期，请重新发送", "need_password": False}
    except PhoneCodeInvalid:
        return {"success": False, "message": "验证码错误", "need_password": False}
    except Exception as e:
        logger.error(f"验证码校验失败: {e}")
        return {"success": False, "message": f"验证失败: {e}", "need_password": False}


async def check_password(password: str) -> dict:
    global _auth_client, _client, _phone_code_hash

    if not _auth_client or not _auth_client.is_connected:
        return {"success": False, "message": "请先发送验证码"}

    try:
        user = await _auth_client.check_password(password)
        session_string = await _auth_client.export_session_string()
        save_config_value("telegram.session_string", session_string)
        settings.reload()

        if _client:
            try:
                await _client.stop()
            except Exception:
                pass
            _client = None

        logger.info(f"两步验证通过, user_id={user.id}")

        if _auth_client:
            try:
                await _auth_client.disconnect()
            except Exception:
                pass
            _auth_client = None
        _phone_code_hash = None

        return {"success": True, "message": "登录成功", "user_id": user.id}
    except Exception as e:
        logger.error(f"两步验证失败: {e}")
        return {"success": False, "message": f"密码错误: {e}"}


async def get_auth_status() -> dict:
    if settings.telegram.session_string:
        try:
            client = await get_client()
            me = await client.get_me()
            return {
                "logged_in": True,
                "user_id": me.id,
                "phone": me.phone_number or "",
                "username": me.username or "",
                "first_name": me.first_name or "",
            }
        except ConnectionError:
            return {"logged_in": False}
        except Exception as e:
            logger.warning(f"获取登录状态失败: {e}")
            return {"logged_in": False}
    return {"logged_in": False}


async def test_connection() -> dict:
    try:
        if settings.proxy.enabled:
            s = socks_mod.socksocket()
            s.set_proxy(socks_mod.SOCKS5, settings.proxy.hostname, settings.proxy.port)
            s.settimeout(15)
            s.connect(("api.telegram.org", 443))
            ctx = ssl.create_default_context()
            ss = ctx.wrap_socket(s, server_hostname="api.telegram.org")
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect(("api.telegram.org", 443))
            ctx = ssl.create_default_context()
            ss = ctx.wrap_socket(s, server_hostname="api.telegram.org")

        ss.send(b"GET /bot123:fake/getMe HTTP/1.1\r\nHost: api.telegram.org\r\nConnection: close\r\n\r\n")
        resp = ss.recv(4096)
        ss.close()
        status = resp.split(b"\r\n")[0].decode()
        return {"success": True, "detail": f"连接成功\n{status}"}
    except Exception as e:
        return {"success": False, "detail": str(e)}
