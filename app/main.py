import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import init_db
from app.stream.router import router as stream_router
from app.dashboard import router as dashboard_router
from app.telegram.client import stop_client, init_bot_pool
from app.telegram.monitor import start_monitor
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
    if settings.singbox.enabled:
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

    if settings.singbox.enabled:
        logger.info("sing-box proxy is enabled")
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
            )
            start_singbox(socks_port=sb.socks_port)
            _singbox_started = True
            logger.info(f"sing-box started on 127.0.0.1:{sb.socks_port}")
        except Exception as e:
            logger.error(f"sing-box failed to start: {e}")
            _singbox_started = False
    elif settings.proxy.enabled:
        logger.info(
            f"External proxy: {settings.proxy.scheme}://"
            f"{settings.proxy.hostname}:{settings.proxy.port}"
        )
    else:
        logger.warning("No proxy configured - Telegram connections may fail")

    logger.info("Starting Telegram monitor...")
    try:
        await start_monitor()
        _monitor_started = True
        logger.info("Telegram monitor started")
    except Exception as e:
        logger.warning(f"Telegram monitor failed to start: {e}")

    logger.info("Initializing bot pool...")
    try:
        await init_bot_pool()
    except Exception as e:
        logger.warning(f"Bot pool initialization failed: {e}")

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stream_router)
app.include_router(dashboard_router)


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")


@app.get("/health")
async def health():
    singbox_status = get_singbox_status() if settings.singbox.enabled else None
    return {
        "status": "ok",
        "proxy": {
            "singbox_enabled": settings.singbox.enabled,
            "singbox_running": singbox_status["running"] if singbox_status else False,
            "singbox_healthy": singbox_status["healthy"] if singbox_status else False,
            "external_proxy_enabled": settings.proxy.enabled,
        },
    }


@app.get("/proxy/status")
async def proxy_status():
    singbox_status = get_singbox_status() if settings.singbox.enabled else None
    return {
        "singbox": singbox_status,
        "singbox_enabled": settings.singbox.enabled,
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
