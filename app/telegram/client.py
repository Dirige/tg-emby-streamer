import asyncio
import json
import socket
import threading
import time
from urllib.parse import urlparse, parse_qs, unquote
from pyrogram import Client
from app.config import settings, BASE_DIR
import logging

logger = logging.getLogger(__name__)

_client: Client | None = None
_bot_pool: list[Client] = []
_bot_index: int = 0
_bot_lock = threading.Lock()
_sub_nodes: list[dict] = []
_current_node_idx: int = -1


def _get_proxy() -> dict | None:
    from app.proxy.singbox import is_running as _is_singbox_running
    if settings.singbox.enabled:
        return {
            "scheme": "socks5",
            "hostname": "127.0.0.1",
            "port": settings.singbox.socks_port,
        }
    # 即使 singbox.enabled=False（订阅模式），如果 sing-box 进程正在运行，也返回代理信息
    if _is_singbox_running():
        return {
            "scheme": "socks5",
            "hostname": "127.0.0.1",
            "port": settings.singbox.socks_port,
        }
    if settings.proxy.enabled:
        return settings.proxy.to_dict()
    return None


def _fetch_sub_nodes() -> list[dict]:
    url = settings.proxy.sub_url
    if not url:
        return []
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = resp.read().decode("utf-8").strip()
    except Exception as e:
        logger.warning(f"Failed to fetch subscription: {e}")
        return []

    nodes = []
    for line in data.splitlines():
        line = line.strip()
        if line.startswith("vmess://"):
            try:
                import base64 as b64
                raw = b64.b64decode(line[8:]).decode("utf-8")
                obj = json.loads(raw)
                nodes.append({
                    "name": obj.get("ps", f"{obj.get('add','?')}:{obj.get('port','')}"),
                    "address": obj.get("add", ""),
                    "port": int(obj.get("port", 443)),
                    "uuid": obj.get("id", ""),
                    "host": obj.get("host", ""),
                    "path": obj.get("path", ""),
                    "fp": obj.get("fp", "chrome"),
                    "protocol": "vmess",
                })
            except Exception:
                continue
        elif line.startswith("ss://"):
            try:
                import base64 as b64
                body = line[5:]
                if "@" in body:
                    userinfo, serverinfo = body.split("@", 1)
                    if ":" in serverinfo:
                        address, port = serverinfo.rsplit(":", 1)
                        port = int(port.split("?")[0].split("#")[0])
                    else:
                        address, port = serverinfo, 8388
                    if ":" in userinfo:
                        method, password = userinfo.split(":", 1)
                    else:
                        decoded = b64.b64decode(userinfo + "==").decode()
                        method, password = decoded.split(":", 1)
                    nodes.append({
                        "name": f"{address}:{port}",
                        "address": address,
                        "port": port,
                        "uuid": password,
                        "host": "",
                        "path": "",
                        "fp": "",
                        "protocol": "shadowsocks",
                        "cipher": method,
                    })
            except Exception:
                continue
        elif line.startswith("vless://") or line.startswith("trojan://"):
            try:
                parsed = urlparse(line)
                params = parse_qs(parsed.query)
                nodes.append({
                    "name": unquote(parsed.fragment) if parsed.fragment else f"{parsed.hostname}:{parsed.port}",
                    "address": parsed.hostname,
                    "port": parsed.port or 443,
                    "uuid": parsed.username,
                    "host": params.get("host", [""])[0] or params.get("sni", [""])[0],
                    "path": unquote(params.get("path", [""])[0]),
                    "fp": params.get("fp", ["chrome"])[0],
                    "protocol": "vless" if line.startswith("vless://") else "trojan",
                })
            except Exception:
                continue
    return nodes


def _start_singbox_from_node(node: dict) -> bool:
    from app.proxy.singbox import generate_config, start as start_singbox, stop as stop_singbox, is_running
    if is_running():
        stop_singbox()
        time.sleep(1)
    try:
        generate_config(
            vless_address=node["address"],
            vless_port=node["port"],
            vless_uuid=node["uuid"],
            vless_path=node.get("path", ""),
            vless_host=node.get("host", ""),
            vless_tls=True,
            vless_fp=node.get("fp", "chrome"),
            socks_port=settings.singbox.socks_port,
            protocol=node.get("protocol", "vless"),
            cipher=node.get("cipher", "auto"),
        )
        start_singbox(socks_port=settings.singbox.socks_port)
        logger.info(f"Started sing-box with node: {node['name']}")
        return True
    except Exception as e:
        logger.error(f"Failed to start sing-box: {e}")
        return False


def _check_socks5(port: int) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _test_telegram_via_proxy(port: int) -> bool:
    try:
        import socks as socks_mod
        import ssl
        s = socks_mod.socksocket()
        s.set_proxy(socks_mod.SOCKS5, "127.0.0.1", port)
        s.settimeout(10)
        s.connect(("91.108.56.130", 443))
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(s, server_hostname="91.108.56.130")
        ss.close()
        return True
    except Exception:
        return False


async def ensure_proxy() -> dict | None:
    """异步版本的代理确保，不会阻塞事件循环"""
    global _sub_nodes, _current_node_idx

    # 1. 检查外部代理
    if settings.proxy.enabled:
        proxy = settings.proxy.to_dict()
        if proxy:
            logger.info(f"Using external proxy: {proxy['scheme']}://{proxy['hostname']}:{proxy['port']}")
            return proxy

    # 2. 检查 singbox
    port = settings.singbox.socks_port
    if _get_proxy() and await asyncio.to_thread(_check_socks5, port):
        return {"scheme": "socks5", "hostname": "127.0.0.1", "port": port}

    # 3. 从订阅获取
    if not settings.proxy.sub_url:
        return None

    logger.info("No proxy configured, fetching from subscription...")
    nodes = await asyncio.to_thread(_fetch_sub_nodes)
    if not nodes:
        logger.warning("No nodes found in subscription")
        return None

    _sub_nodes = nodes
    logger.info(f"Got {len(nodes)} nodes from subscription")

    for i, node in enumerate(nodes[:5]):
        logger.info(f"Trying node {i+1}/{min(len(nodes), 5)}: {node['name']}")
        if not await asyncio.to_thread(_start_singbox_from_node, node):
            continue
        await asyncio.sleep(3)
        if await asyncio.to_thread(_check_socks5, port):
            _current_node_idx = i
            logger.info(f"Node {i+1} SOCKS5 ready: socks5://127.0.0.1:{port}")
            return {"scheme": "socks5", "hostname": "127.0.0.1", "port": port}
        else:
            logger.warning(f"Node {i+1}: SOCKS5 port not reachable")

    logger.error("All nodes failed")
    return None


def _ensure_proxy() -> dict | None:
    """同步版本，用于非异步上下文"""
    global _sub_nodes, _current_node_idx

    # 1. 检查外部代理
    if settings.proxy.enabled:
        proxy = settings.proxy.to_dict()
        if proxy:
            logger.info(f"Using external proxy: {proxy['scheme']}://{proxy['hostname']}:{proxy['port']}")
            return proxy

    # 2. 检查 singbox
    port = settings.singbox.socks_port
    if _get_proxy() and _check_socks5(port):
        return {"scheme": "socks5", "hostname": "127.0.0.1", "port": port}

    # 3. 从订阅获取
    if not settings.proxy.sub_url:
        return None

    logger.info("No proxy configured, fetching from subscription...")
    nodes = _fetch_sub_nodes()
    if not nodes:
        logger.warning("No nodes found in subscription")
        return None

    _sub_nodes = nodes
    logger.info(f"Got {len(nodes)} nodes from subscription")

    for i, node in enumerate(nodes[:5]):
        logger.info(f"Trying node {i+1}/{min(len(nodes), 5)}: {node['name']}")
        if not _start_singbox_from_node(node):
            continue
        time.sleep(3)
        if _check_socks5(port):
            _current_node_idx = i
            logger.info(f"Node {i+1} SOCKS5 ready: socks5://127.0.0.1:{port}")
            return {"scheme": "socks5", "hostname": "127.0.0.1", "port": port}
        else:
            logger.warning(f"Node {i+1}: SOCKS5 port not reachable")

    logger.error("All nodes failed")
    return None


def get_sub_nodes() -> list[dict]:
    global _sub_nodes
    if not _sub_nodes and settings.proxy.sub_url:
        _sub_nodes = _fetch_sub_nodes()
    return _sub_nodes


def get_current_node_idx() -> int:
    return _current_node_idx


def select_sub_node(idx: int) -> bool:
    global _current_node_idx
    nodes = get_sub_nodes()
    if idx < 0 or idx >= len(nodes):
        return False
    node = nodes[idx]
    if _start_singbox_from_node(node):
        time.sleep(3)
        if _check_socks5(settings.singbox.socks_port):
            _current_node_idx = idx
            logger.info(f"Switched to node {idx+1}: {node['name']}")
            return True
    return False


def get_proxy_mode() -> str:
    if settings.singbox.enabled and settings.singbox.address:
        return "singbox"
    if _current_node_idx >= 0:
        return "subscription"
    if settings.proxy.enabled:
        return "external"
    return "direct"


def _save_session_to_env(session_string: str):
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        env_path.write_text(f"TELEGRAM_SESSION_STRING={session_string}\n", encoding="utf-8")
        return
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        if line.startswith("TELEGRAM_SESSION_STRING="):
            lines[i] = f"TELEGRAM_SESSION_STRING={session_string}\n"
            found = True
            break
    if not found:
        lines.append(f"TELEGRAM_SESSION_STRING={session_string}\n")
    env_path.write_text("".join(lines), encoding="utf-8")


async def _interactive_login(proxy: dict | None) -> Client:
    logger.info("=" * 50)
    logger.info("首次启动，需要登录 Telegram 账号")
    logger.info("=" * 50)

    session_file = BASE_DIR / "tg_emby_session.session"
    if session_file.exists():
        session_file.unlink()
        logger.info("Removed old session file")

    client = Client(
        name="tg_emby_session",
        api_id=settings.telegram.api_id,
        api_hash=settings.telegram.api_hash,
        phone_number=settings.telegram.phone,
        proxy=proxy,
    )
    await client.start()

    session_string = await client.export_session_string()
    _save_session_to_env(session_string)
    logger.info("Session string 已保存到 .env 文件")
    logger.info("下次启动将自动使用，无需再次登录")

    await client.stop()
    return client


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

    # _get_proxy 会检查 sing-box 是否正在运行或外部代理配置
    proxy = _get_proxy()
    # 如果还没有代理，尝试从订阅获取（在后台线程中执行）
    if not proxy and settings.proxy.sub_url:
        logger.info("No proxy available, fetching from subscription...")
        proxy = await asyncio.to_thread(_ensure_proxy)

    if not settings.telegram.session_string:
        logger.warning("TELEGRAM_SESSION_STRING 为空，启动交互式登录...")
        await _interactive_login(proxy)
        settings.telegram.session_string = (
            (BASE_DIR / ".env").read_text(encoding="utf-8")
            .split("TELEGRAM_SESSION_STRING=")[-1].split("\n")[0].strip()
        )

    if not proxy:
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
