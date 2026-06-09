import logging
import asyncio
import secrets
from contextlib import asynccontextmanager
import hashlib
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings
from app.database import init_db, init_adult_db
from app.stream.router import router as stream_router
from app.dashboard import router as dashboard_router
from app.telegram.client import stop_client, init_bot_pool, ensure_proxy
from app.telegram.monitor import start_monitor, run_startup_forward
from app.stream.prefetch import prefetch_manager
from app.proxy.singbox import (
    generate_config,
    start as start_singbox,
    stop as stop_singbox,
    get_status as get_singbox_status,
    is_healthy as is_singbox_healthy,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_monitor_started = False
_singbox_started = False


def _get_telegram_proxy_config() -> dict | None:
    from app.proxy.singbox import is_running as is_singbox_running
    if settings.singbox.enabled or is_singbox_running():
        return {
            "scheme": "socks5",
            "hostname": "127.0.0.1",
            "port": settings.singbox.socks_port,
        }
    elif settings.proxy.enabled:
        return {
            "scheme": settings.proxy.scheme,
            "hostname": settings.proxy.hostname,
            "port": settings.proxy.port,
        }
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor_started, _singbox_started

    logger.info("Initializing database...")
    await init_db()
    await init_adult_db()

    # 优先级：手动 VLESS 配置 > 外部代理 > 订阅自动代理
    if settings.singbox.enabled and settings.singbox.address:
        # 手动配置的 VLESS 节点，直接启动 sing-box
        logger.info("sing-box proxy is enabled (manual config)")
        logger.info(f"  VLESS server: {settings.singbox.address}:{settings.singbox.port}")
        logger.info(f"  SOCKS5 port: {settings.singbox.socks_port}")

        try:
            sb = settings.singbox
            generate_config(
                vless_address=sb.address,
                vless_port=sb.port,
                vless_uuid=sb.uuid,
                vless_path=sb.path,
                vless_host=sb.host,
                vless_tls=sb.tls,
                vless_fp=sb.fingerprint,
                socks_port=sb.socks_port,
                protocol=sb.protocol,
                cipher=sb.cipher,
            )
            await asyncio.to_thread(start_singbox, socks_port=sb.socks_port)
            _singbox_started = True
            logger.info(f"sing-box started on 127.0.0.1:{sb.socks_port}")
        except Exception as e:
            logger.error(f"sing-box failed to start: {e}")
            _singbox_started = False

    elif settings.proxy.enabled:
        # 外部代理（SOCKS5/HTTP）
        logger.info(
            f"External proxy: {settings.proxy.scheme}://"
            f"{settings.proxy.hostname}:{settings.proxy.port}"
        )

    elif settings.proxy.sub_url:
        # 有订阅地址，延迟启动 sing-box（在 _connect_telegram 中从订阅获取节点）
        logger.info("Subscription URL configured, sing-box will start after fetching nodes...")
        # 不在这里启动，交给 _ensure_proxy 处理

    else:
        logger.warning("No proxy configured - Telegram connections may fail")

    logger.info("Web UI ready!")

    # 在 lifespan 中直接创建异步任务，确保在事件循环运行时立即调度
    async def _run_connect_telegram():
        try:
            await _connect_telegram()
        except Exception as e:
            logger.error(f"Telegram connection task failed: {e}", exc_info=True)

    asyncio.create_task(_run_connect_telegram())

    yield

    logger.info("Shutting down...")
    prefetch_manager.cancel_all()
    if _monitor_started:
        try:
            await stop_client()
        except Exception as e:
            logger.warning(f"Error stopping client: {e}")
    if _singbox_started:
        stop_singbox()


app = FastAPI(
    title="Telegram Emby Stream Server",
    description="Range-based streaming engine for Telegram media via Emby/Jellyfin",
    version="2.0.0",
    lifespan=lifespan,
)

_tg_connected = False
_tg_monitor_task = None
_session_tokens: dict[str, str] = {}  # token -> username mapping


async def _connect_telegram():
    global _tg_connected, _monitor_started, _singbox_started, _tg_monitor_task
    await asyncio.sleep(2)

    max_retries = 5
    for attempt in range(max_retries):
        try:
            # 确保代理可用（优先级：手动 VLESS > 订阅 > 外部代理）
            if settings.singbox.enabled and settings.singbox.address:
                # 手动配置的 VLESS，lifespan 中已启动
                if not _singbox_started:
                    logger.info("Starting sing-box proxy...")
                    try:
                        sb = settings.singbox
                        generate_config(
                            vless_address=sb.address, vless_port=sb.port, vless_uuid=sb.uuid,
                            vless_path=sb.path, vless_host=sb.host, vless_tls=sb.tls,
                            vless_fp=sb.fingerprint, socks_port=sb.socks_port,
                            protocol=sb.protocol, cipher=sb.cipher,
                        )
                        start_singbox(socks_port=sb.socks_port)
                        _singbox_started = True
                        logger.info("sing-box started")
                    except Exception as e:
                        logger.error(f"sing-box failed: {e}")

            elif settings.proxy.sub_url:
                # 订阅模式：从订阅获取节点并启动 sing-box
                logger.info("Fetching proxy from subscription...")
                proxy = await ensure_proxy()
                if proxy:
                    _singbox_started = True
                    logger.info(f"Proxy ready: {proxy['scheme']}://{proxy['hostname']}:{proxy['port']}")
                else:
                    logger.warning("Failed to get proxy from subscription")

            logger.info(f"Connecting to Telegram (attempt {attempt + 1}/{max_retries})...")
            from app.telegram.client import get_client, warmup_client, init_bot_pool
            from app.telegram.monitor import start_monitor, run_startup_forward

            client, channels, private = await start_monitor()
            _monitor_started = True
            _tg_connected = True
            logger.info("Telegram connected!")

            try:
                await init_bot_pool()
            except Exception as e:
                logger.warning(f"Bot pool failed: {e}")

            asyncio.create_task(run_startup_forward(client, channels, private))
            return

        except Exception as e:
            logger.warning(f"Telegram connection failed (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                wait = min(30, 5 * (attempt + 1))
                logger.info(f"Retrying in {wait}s...")
                await asyncio.sleep(wait)

    logger.error("All Telegram connection attempts failed. Web UI is still available.")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            if not settings.web.username or not settings.web.password:
                return await call_next(request)

            path = request.url.path
            if path in ("/login", "/favicon.ico") or path.startswith("/api/diagnose"):
                return await call_next(request)

            session_token = request.cookies.get("session_token")
            if session_token and session_token in _session_tokens:
                return await call_next(request)

            # Stream token auth
            if path.startswith("/stream/"):
                if settings.web.stream_token:
                    req_token = request.query_params.get("token", "")
                    if req_token == settings.web.stream_token:
                        return await call_next(request)
                    return Response(content='{"detail":"Unauthorized"}', status_code=401, media_type="application/json")
                return await call_next(request)

            if (path.startswith("/api/") and not path.startswith("/api/diagnose")) or path.startswith("/proxy/") or path.startswith("/health") or path.startswith("/strm/"):
                return Response(content='{"detail":"Unauthorized"}', status_code=401, media_type="application/json")

            return HTMLResponse(_login_page(path), status_code=200)
        except Exception as e:
            logger.error(f"Auth middleware error: {e}")
            return Response(content='{"detail":"Internal auth error"}', status_code=500, media_type="application/json")


def _login_page(return_url: str = "/dashboard") -> str:
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TG Emby Streamer</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#0a0b0f;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;overflow:hidden}}
body::before{{content:'';position:fixed;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle at 30% 20%,rgba(99,102,241,.15) 0%,transparent 50%),radial-gradient(circle at 70% 80%,rgba(139,92,246,.1) 0%,transparent 50%);animation:float 20s ease-in-out infinite}}@keyframes float{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(-20px,-20px)}}}}
.login-wrapper{{position:relative;z-index:1;width:100%;max-width:420px;padding:20px}}.login-card{{background:rgba(26,29,39,.95);backdrop-filter:blur(20px);border:1px solid rgba(99,102,241,.2);border-radius:20px;padding:40px 36px;box-shadow:0 25px 60px rgba(0,0,0,.5),0 0 40px rgba(99,102,241,.1)}}
.logo{{text-align:center;margin-bottom:32px}}.logo-icon{{width:64px;height:64px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:16px;display:inline-flex;align-items:center;justify-content:center;margin-bottom:16px;box-shadow:0 8px 24px rgba(99,102,241,.4)}}
.logo-icon svg{{width:36px;height:36px}}.logo h1{{font-size:22px;font-weight:700;color:#e4e6eb;letter-spacing:-.5px}}.logo p{{font-size:13px;color:#8b8fa3;margin-top:6px}}
.form-group{{margin-bottom:20px}}.form-group label{{display:block;font-size:12px;font-weight:600;color:#8b8fa3;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}}
.input-wrap{{position:relative}}.input-wrap svg{{position:absolute;left:14px;top:50%;transform:translateY(-50%);width:18px;height:18px;color:#8b8fa3;pointer-events:none}}
input{{width:100%;padding:14px 14px 14px 44px;border-radius:12px;border:1.5px solid rgba(99,102,241,.2);background:rgba(15,17,23,.8);color:#e4e6eb;font-size:15px;outline:none;transition:all .2s}}input:focus{{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,.15)}}input::placeholder{{color:#4a4d5e}}
button{{width:100%;padding:14px;border-radius:12px;border:none;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;font-size:15px;font-weight:600;cursor:pointer;transition:all .3s;margin-top:8px;letter-spacing:.3px}}
button:hover{{transform:translateY(-2px);box-shadow:0 8px 24px rgba(99,102,241,.4)}}button:active{{transform:translateY(0)}}
.error{{color:#ef4444;font-size:13px;text-align:center;margin-top:16px;padding:10px;background:rgba(239,68,68,.1);border-radius:8px;border:1px solid rgba(239,68,68,.2)}}
.footer{{text-align:center;margin-top:24px;font-size:12px;color:#4a4d5e}}</style></head>
<body><div class="login-wrapper"><div class="login-card"><div class="logo"><div class="logo-icon"><svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg></div><h1>TG Emby Streamer</h1><p>Telegram 媒体流媒体服务器</p></div>
<form method="POST" action="/login?return={return_url}"><div class="form-group"><label>用户名</label><div class="input-wrap"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg><input name="username" placeholder="请输入用户名" required autofocus></div></div>
<div class="form-group"><label>密码</label><div class="input-wrap"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg><input name="password" type="password" placeholder="请输入密码" required></div></div>
<button type="submit">登 录</button></form></div><div class="footer">TG Emby Streamer v2.0</div></div></body></html>'''


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    return_url = request.query_params.get("return", "/dashboard")
    if username == settings.web.username and password == settings.web.password:
        token = secrets.token_hex(32)
        _session_tokens[token] = username
        response = RedirectResponse(url=return_url, status_code=302)
        response.set_cookie("session_token", token, httponly=True, max_age=7200, samesite="Lax")
        return response
    return HTMLResponse(_login_page(return_url).replace('</div><div class="footer">', f'<div class="error">用户名或密码错误</div></div><div class="footer">'), status_code=200)


app.add_middleware(AuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/diagnose")
async def diagnose(token: str = ""):
    import time
    import os
    from pathlib import Path
    
    result = {
        "proxy": {"status": "unknown"},
        "telegram": {"status": "unknown"},
        "stream_test": {"status": "unknown"},
        "cache": {},
        "errors": [],
    }
    
    # 1. Check proxy
    try:
        import socket
        proxy_host = settings.proxy.hostname or "127.0.0.1"
        proxy_port = settings.proxy.port or 10808
        start = time.time()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((proxy_host, int(proxy_port)))
        latency = int((time.time() - start) * 1000)
        s.close()
        result["proxy"] = {
            "status": "ok",
            "type": f"{settings.proxy.scheme}://{proxy_host}:{proxy_port}",
            "latency_ms": latency,
        }
    except Exception as e:
        result["proxy"] = {"status": "error", "detail": str(e)}
        result["errors"].append(f"代理连接失败: {e}")
    
    # 2. Check Telegram client
    try:
        from app.telegram.client import get_client, get_stream_client, _bot_pool
        client = await get_client()
        result["telegram"]["user_client"] = client.is_connected if client else False
        result["telegram"]["bot_pool"] = len(_bot_pool) if _bot_pool else 0
        result["telegram"]["status"] = "connected" if client and client.is_connected else "disconnected"
    except Exception as e:
        result["telegram"]["status"] = "error"
        result["telegram"]["detail"] = str(e)
        result["errors"].append(f"Telegram 连接失败: {e}")
    
    # 3. Test stream download
    try:
        from app.telegram.client import get_client
        from app.stream.range_stream import CachedFileDownloader
        from app.database import async_session
        from app.models import Media
        from sqlalchemy import select
        
        async with async_session() as session:
            q = select(Media).where(Media.recognized == True).limit(1)
            r = (await session.execute(q)).scalar_one_or_none()
        
        if r:
            client = await get_client()
            downloader = CachedFileDownloader(client, int(r.chat_id), r.message_id)
            start = time.time()
            size = await downloader.get_file_size()
            elapsed = time.time() - start
            
            # Test small download
            start2 = time.time()
            data = await downloader.download_chunk(0, 65536)
            dl_time = time.time() - start2
            speed = len(data) / dl_time / 1024 / 1024 if dl_time > 0 else 0
            
            result["stream_test"] = {
                "status": "ok",
                "file": r.file_name[:30] if r.file_name else "unknown",
                "file_size_mb": round(size / 1024 / 1024, 1),
                "speed_mbps": round(speed, 1),
                "bytes_downloaded": len(data),
            }
        else:
            result["stream_test"] = {"status": "no_media"}
    except Exception as e:
        result["stream_test"] = {"status": "error", "detail": str(e)}
        result["errors"].append(f"流媒体测试失败: {e}")
    
    # 4. Cache status
    try:
        cache_dir = Path(settings.cache.dir if hasattr(settings.cache, 'dir') else '/app/cache')
        if cache_dir.exists():
            total = sum(f.stat().st_size for f in cache_dir.rglob('*.chunk'))
            count = len(list(cache_dir.rglob('*.chunk')))
            result["cache"] = {
                "used_mb": round(total / 1024 / 1024, 1),
                "max_gb": settings.cache.disk_cache_max_gb if hasattr(settings.cache, 'disk_cache_max_gb') else 50,
                "files": count,
            }
    except Exception as e:
        result["cache"]["error"] = str(e)
    
    return result

app.include_router(stream_router)
app.include_router(dashboard_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/favicon.ico")
async def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#6366f1"/><text x="16" y="23" text-anchor="middle" fill="white" font-size="20" font-weight="bold">T</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/health")
async def health():
    from app.proxy.singbox import is_running as is_singbox_running
    singbox_status = get_singbox_status() if is_singbox_running() else None
    return {
        "status": "ok",
        "proxy": {
            "singbox_enabled": settings.singbox.enabled or is_singbox_running(),
            "singbox_running": singbox_status["running"] if singbox_status else False,
            "singbox_healthy": singbox_status["healthy"] if singbox_status else False,
            "external_proxy_enabled": settings.proxy.enabled,
        },
    }


@app.get("/proxy/status")
async def proxy_status():
    from app.proxy.singbox import is_running as is_singbox_running
    singbox_running = is_singbox_running()
    singbox_status = get_singbox_status() if singbox_running else None
    return {
        "singbox": singbox_status,
        "singbox_enabled": settings.singbox.enabled or singbox_running,
        "proxy_enabled": settings.proxy.enabled,
        "proxy_config": {
            "singbox": {
                "address": settings.singbox.address if settings.singbox.enabled else None,
                "port": settings.singbox.port if settings.singbox.enabled else None,
                "socks_port": settings.singbox.socks_port if settings.singbox.enabled else None,
            } if settings.singbox.enabled else None,
            "external": {
                "scheme": settings.proxy.scheme if settings.proxy.enabled else None,
                "hostname": settings.proxy.hostname if settings.proxy.enabled else None,
                "port": settings.proxy.port if settings.proxy.enabled else None,
            } if settings.proxy.enabled else None,
        },
        "telegram_proxy": _get_telegram_proxy_config(),
    }
