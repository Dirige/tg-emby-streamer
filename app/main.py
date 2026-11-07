import asyncio
import html as _html
import logging
import secrets
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings
from app.database import init_db, async_session
from app.stream.router import router as stream_router
from app.dashboard import router as dashboard_router
from app.telegram.client import stop_client
from app.telegram.bot import start_bot_handler, stop_bot_handler
from app.stream.prefetch import prefetch_manager
from app.models import ChannelRule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


_monitor_task: asyncio.Task | None = None


async def _auto_monitor():
    await asyncio.sleep(30)
    while True:
        try:
            from app.telegram.monitor import scan_channel
            from sqlalchemy import select

            # 频道来源 = 数据库里启用的 channel_rules
            # （Web 面板添加/启停频道后，自动监控立即跟随；不再与配置文件两套来源）
            channels: list[str] = []
            try:
                async with async_session() as session:
                    rows = (
                        await session.execute(
                            select(ChannelRule.channel_id)
                            .where(ChannelRule.enabled == True)  # noqa: E712
                        )
                    )
                    channels = [r[0] for r in rows.all()]
            except Exception as e:
                logger.warning(f"读取频道规则失败，回退配置文件列表: {e}")
            if not channels:
                channels = list(settings.telegram.monitor_channels)
            if not channels:
                interval = max(1, settings.telegram.monitor_interval_minutes) * 60
                await asyncio.sleep(interval)
                continue
            seen = set()
            for ch_id in channels:
                try:
                    cid = int(ch_id)
                except (TypeError, ValueError):
                    continue
                if cid in seen:
                    continue
                seen.add(cid)
                try:
                    r = await scan_channel(cid, limit=50)
                    if r.get("success"):
                        logger.info(f"自动监控 {cid}: {r.get('message', '')}")
                    else:
                        message = r.get("message", "")
                        logger.warning(f"自动监控 {cid}: {message}")
                        if "未登录" in message or "登录已失效" in message:
                            break
                except ConnectionError as e:
                    if "NEED_LOGIN" in str(e):
                        logger.warning("自动监控跳过: 未登录")
                        break
                except Exception as e:
                    logger.warning(f"自动监控 {cid} 失败: {e}")
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"自动监控异常: {e}")
        interval = max(1, settings.telegram.monitor_interval_minutes) * 60
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor_task
    logger.info("Initializing database...")
    await init_db()
    _monitor_task = asyncio.create_task(_auto_monitor())
    # Start bot handler
    _bot_handler = asyncio.create_task(start_bot_handler())
    logger.info("Web UI ready!")
    yield
    logger.info("Shutting down...")
    if _monitor_task:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
    prefetch_manager.cancel_all()
    try:
        await stop_client()
    except Exception as e:
        logger.warning(f"Error stopping client: {e}")
    try:
        await stop_bot_handler()
    except Exception as e:
        logger.warning(f"Error stopping bot: {e}")


app = FastAPI(
    title="TG Emby Streamer",
    description="Telegram media streamer with per-channel rules",
    version="3.0.0",
    lifespan=lifespan,
)

_session_tokens: dict[str, str] = {}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            if not settings.web.username or not settings.web.password:
                return await call_next(request)

            path = request.url.path
            if path in ("/login", "/favicon.ico", "/health", "/logo.svg", "/logo.png") or path.startswith("/api/health") or path.startswith("/api/auth/"):
                return await call_next(request)

            session_token = request.cookies.get("session_token")
            if session_token and session_token in _session_tokens:
                return await call_next(request)

            # stream/strm 两个路由前缀都暴露流接口，token 校验必须同时覆盖
            # （否则 /strm/xxx 会被踩进登录页，Emby 拿到 HTML 而不是视频）
            if path.startswith("/stream/") or path.startswith("/strm/"):
                if settings.web.stream_token:
                    req_token = request.query_params.get("token", "")
                    if req_token == settings.web.stream_token:
                        return await call_next(request)
                    return Response(content='{"detail":"Unauthorized"}', status_code=401, media_type="application/json")
                return await call_next(request)

            if path.startswith("/api/"):
                return Response(content='{"detail":"Unauthorized"}', status_code=401, media_type="application/json")

            return HTMLResponse(_login_page(path), status_code=200)
        except Exception as e:
            logger.error(f"Auth middleware error: {e}")
            return Response(content='{"detail":"Internal auth error"}', status_code=500, media_type="application/json")


def _safe_return_url(raw: str | None) -> str:
    """return URL 必须是站内相对路径，防开放重定向 / 反射注入"""
    raw = raw or "/dashboard"
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or "\n" in raw or "\r" in raw:
        return "/dashboard"
    return raw


def _login_page(return_url: str = "/dashboard") -> str:
    return_url = _safe_return_url(return_url)
    return_url_escaped = _html.escape(return_url, quote=True)
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TG Emby Streamer</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#0a0b0f;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;overflow:hidden}}
body::before{{content:'';position:fixed;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle at 30% 20%,rgba(99,102,241,.15) 0%,transparent 50%),radial-gradient(circle at 70% 80%,rgba(139,92,246,.1) 0%,transparent 50%);animation:float 20s ease-in-out infinite}}@keyframes float{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(-20px,-20px)}}}}
.login-wrapper{{position:relative;z-index:1;width:100%;max-width:420px;padding:20px}}.login-card{{background:rgba(26,29,39,.95);backdrop-filter:blur(20px);border:1px solid rgba(99,102,241,.2);border-radius:20px;padding:40px 36px;box-shadow:0 25px 60px rgba(0,0,0,.5),0 0 40px rgba(99,102,241,.1)}}
.logo{{text-align:center;margin-bottom:32px}}.logo-icon{{width:64px;height:64px;margin:0 auto 16px}}
.logo-icon{{position:relative}}.logo-icon svg{{width:40px;height:40px;display:block}}.logo h1{{font-size:22px;font-weight:700;color:#e4e6eb;letter-spacing:-.5px}}.logo p{{font-size:13px;color:#8b8fa3;margin-top:6px}}
.form-group{{margin-bottom:20px}}.form-group label{{display:block;font-size:12px;font-weight:600;color:#8b8fa3;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}}
.input-wrap{{position:relative}}.input-wrap svg{{position:absolute;left:14px;top:50%;transform:translateY(-50%);width:18px;height:18px;color:#8b8fa3;pointer-events:none}}
input{{width:100%;padding:14px 14px 14px 44px;border-radius:12px;border:1.5px solid rgba(99,102,241,.2);background:rgba(15,17,23,.8);color:#e4e6eb;font-size:15px;outline:none;transition:all .2s}}input:focus{{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,.15)}}input::placeholder{{color:#4a4d5e}}
button{{width:100%;padding:14px;border-radius:12px;border:none;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;font-size:15px;font-weight:600;cursor:pointer;transition:all .3s;margin-top:8px;letter-spacing:.3px}}
button:hover{{transform:translateY(-2px);box-shadow:0 8px 24px rgba(99,102,241,.4)}}button:active{{transform:translateY(0)}}
.error{{color:#ef4444;font-size:13px;text-align:center;margin-top:16px;padding:10px;background:rgba(239,68,68,.1);border-radius:8px;border:1px solid rgba(239,68,68,.2)}}
.footer{{text-align:center;margin-top:24px;font-size:12px;color:#4a4d5e}}</style></head>
<body><div class="login-wrapper"><div class="login-card"><div class="logo"><img class="logo-icon" src="/logo.png" alt="TG Emby Streamer"><h1>TG Emby Streamer</h1><p>Telegram 媒体流媒体服务器</p></div>
<form method="POST" action="/login?return={return_url_escaped}"><div class="form-group"><label>用户名</label><div class="input-wrap"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg><input name="username" placeholder="请输入用户名" required autofocus></div></div>
<div class="form-group"><label>密码</label><div class="input-wrap"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg><input name="password" type="password" placeholder="请输入密码" required></div></div>
<button type="submit">登 录</button></form></div><div class="footer">TG Emby Streamer v3.0</div></div></body></html>'''


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    return_url = _safe_return_url(request.query_params.get("return", "/dashboard"))
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

app.include_router(stream_router)
app.include_router(dashboard_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/favicon.ico")
async def favicon():
    p = Path(__file__).parent / "static" / "favicon.png"
    if p.exists():
        return Response(content=p.read_bytes(), media_type="image/png")
    return Response(status_code=404)


@app.get("/logo.svg")
@app.get("/logo.png")
async def logo_img():
    p = Path(__file__).parent / "static" / "logo.png"
    if p.exists():
        return Response(content=p.read_bytes(), media_type="image/png")
    return Response(status_code=404)


@app.get("/health")
async def health():
    return {"status": "ok"}
