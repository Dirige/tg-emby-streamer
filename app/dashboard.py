import os
import re
import json
import base64
import logging
import shutil
import asyncio
import socket
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.config import settings, BASE_DIR
from app.database import async_session
from app.models import Media
from app.proxy.singbox import (
    get_status as get_singbox_status,
    start as start_singbox,
    stop as stop_singbox,
    generate_config,
    is_running as is_singbox_running,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


def _update_env(updates: dict):
    env_path = BASE_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for key, val in updates.items():
        val_str = str(val) if val is not None else ""
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={val_str}\n"
                found = True
                break
        if not found:
            lines.append(f"{key}={val_str}\n")
    env_path.write_text("".join(lines), encoding="utf-8")


def _safe_int(val, default=0):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _stop_singbox_safe():
    try:
        stop_singbox()
    except Exception:
        pass


@router.get("/api/config")
async def get_config():
    return {
        "telegram": {
            "api_id": settings.telegram.api_id,
            "phone": settings.telegram.phone,
            "user_id": settings.telegram.user_id,
            "channel_id": settings.telegram.channel_id,
            "monitor_channels": settings.telegram.monitor_channels,
        },
        "singbox": {
            "enabled": settings.singbox.enabled,
            "address": settings.singbox.address,
            "port": settings.singbox.port,
            "uuid": settings.singbox.uuid,
            "path": settings.singbox.path,
            "host": settings.singbox.host,
            "tls": settings.singbox.tls,
            "fingerprint": settings.singbox.fingerprint,
            "socks_port": settings.singbox.socks_port,
        },
        "proxy": {
            "enabled": settings.proxy.enabled,
            "scheme": settings.proxy.scheme,
            "hostname": settings.proxy.hostname,
            "port": settings.proxy.port,
        },
        "stream": {
            "host": settings.stream.host,
            "port": settings.stream.port,
            "base_url": settings.stream.base_url,
            "local_url": settings.stream.local_url,
            "chunk_size": settings.stream.chunk_size,
        },
        "tmdb": {
            "api_key": settings.tmdb.api_key,
            "language": settings.tmdb.language,
            "proxy": settings.tmdb.proxy,
            "image_cdn": settings.tmdb.image_cdn,
        },
        "database": {"url": settings.database.url},
        "cache": {
            "dir": settings.cache.dir,
            "media_cache_size_mb": settings.cache.memory_cache_size_mb,
            "disk_cache_max_gb": settings.cache.disk_cache_max_gb,
            "prefetch_enabled": settings.cache.prefetch_enabled,
        },
        "worker": {"url": settings.worker.url},
        "emby": {"host": settings.emby.host},
    }


@router.post("/api/settings/save")
async def save_settings(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        return {"success": False, "message": f"请求解析失败: {e}"}

    try:
        updates = {}

        tg = body.get("telegram", {})
        if tg.get("api_id") not in (None, "", 0):
            updates["TELEGRAM_API_ID"] = str(tg["api_id"])
        if tg.get("api_hash"):
            updates["TELEGRAM_API_HASH"] = tg["api_hash"]
        if tg.get("session_string"):
            updates["TELEGRAM_SESSION_STRING"] = tg["session_string"]
        if tg.get("channel_id") not in (None, ""):
            updates["TELEGRAM_CHANNEL_ID"] = str(tg["channel_id"])
        if "monitor_channels" in tg:
            updates["TELEGRAM_MONITOR_CHANNELS"] = tg["monitor_channels"]

        st = body.get("stream", {})
        if st.get("host"):
            updates["STREAM_HOST"] = st["host"]
        if st.get("port") not in (None, "", 0):
            updates["STREAM_PORT"] = str(_safe_int(st["port"], 8001))
        if st.get("base_url"):
            updates["BASE_URL"] = st["base_url"]
        if st.get("local_url"):
            updates["STREAM_LOCAL_URL"] = st["local_url"]

        tm = body.get("tmdb", {})
        if "api_key" in tm:
            updates["TMDB_API_KEY"] = tm["api_key"]
        if "language" in tm and tm["language"]:
            updates["TMDB_LANGUAGE"] = tm["language"]
        if "proxy" in tm:
            updates["TMDB_PROXY"] = tm["proxy"]
        if "image_cdn" in tm:
            updates["TMDB_IMAGE_CDN"] = tm["image_cdn"]

        ca = body.get("cache", {})
        if ca.get("dir"):
            updates["CACHE_DIR"] = ca["dir"]
        if ca.get("memory_cache_size_mb") not in (None, "", 0):
            updates["MEMORY_CACHE_SIZE"] = str(_safe_int(ca["memory_cache_size_mb"], 256))
        if ca.get("disk_cache_max_gb") not in (None, "", 0):
            updates["DISK_CACHE_MAX_GB"] = str(_safe_int(ca["disk_cache_max_gb"], 50))

        cf = body.get("worker", {})
        if "url" in cf:
            updates["CF_WORKER_URL"] = cf["url"]
        if cf.get("secret"):
            updates["CF_WORKER_SECRET"] = cf["secret"]

        em = body.get("emby", {})
        if "host" in em:
            updates["EMBY_HOST"] = em["host"]
        if em.get("api_key"):
            updates["EMBY_API_KEY"] = em["api_key"]

        _update_env(updates)
        return {"success": True, "message": "设置已保存，部分设置需重启生效"}
    except Exception as e:
        logger.error(f"Save settings error: {e}")
        return {"success": False, "message": f"保存失败: {e}"}


@router.post("/api/settings/vless")
async def save_vless(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        return {"success": False, "message": f"请求解析失败: {e}"}
    try:
        _update_env({
            "SINGBOX_ENABLED": "true",
            "SINGBOX_ADDRESS": body.get("address", ""),
            "SINGBOX_PORT": str(_safe_int(body.get("port"), 443)),
            "SINGBOX_UUID": body.get("uuid", ""),
            "SINGBOX_HOST": body.get("host", ""),
            "SINGBOX_PATH": body.get("path", ""),
            "SINGBOX_FINGERPRINT": body.get("fingerprint", "chrome"),
        })
        _stop_singbox_safe()
        time.sleep(2)
        generate_config(
            vless_address=body["address"], vless_port=_safe_int(body.get("port"), 443),
            vless_uuid=body["uuid"], vless_path=body.get("path", ""),
            vless_host=body.get("host", ""), vless_tls=True,
            vless_fp=body.get("fingerprint", "chrome"),
            socks_port=settings.singbox.socks_port,
        )
        start_singbox(socks_port=settings.singbox.socks_port)
        return {"success": True, "message": "VLESS 配置已保存并重启代理"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/settings/proxy-mode")
async def save_proxy_mode(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        return {"success": False, "message": f"请求解析失败: {e}"}

    mode = body.get("mode", "singbox")
    try:
        if mode == "singbox":
            _update_env({"SINGBOX_ENABLED": "true", "PROXY_ENABLED": "false"})
            return {"success": True, "message": "已切换为 sing-box 代理模式，需重启生效"}

        elif mode == "external":
            _update_env({
                "SINGBOX_ENABLED": "false", "PROXY_ENABLED": "true",
                "PROXY_SCHEME": body.get("scheme", "socks5"),
                "PROXY_HOST": body.get("hostname", "127.0.0.1"),
                "PROXY_PORT": str(_safe_int(body.get("port"), 7890)),
            })
            return {"success": True, "message": "已切换为外部代理模式，需重启生效"}

        elif mode == "direct":
            _stop_singbox_safe()
            _update_env({"SINGBOX_ENABLED": "false", "PROXY_ENABLED": "false"})
            return {"success": True, "message": "已切换为直连模式（海外服务器），代理已关闭"}

    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/sub/fetch")
async def fetch_sub(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        return {"success": False, "message": f"请求解析失败: {e}"}

    url = body.get("url", "")
    if not url:
        return {"success": False, "message": "订阅地址为空"}
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = resp.read().decode("utf-8").strip()

        nodes = []
        for line in data.splitlines():
            line = line.strip()
            if not line.startswith("vless://"):
                continue
            try:
                parsed = urlparse(line)
                params = parse_qs(parsed.query)
                uuid = parsed.username
                address = parsed.hostname
                port = parsed.port or 443
                host = params.get("host", [""])[0]
                sni = params.get("sni", [""])[0]
                fp = params.get("fp", ["chrome"])[0]
                path = unquote(params.get("path", [""])[0])
                name = unquote(parsed.fragment) if parsed.fragment else f"{address}:{port}"
                nodes.append({
                    "name": name, "address": address, "port": port,
                    "uuid": uuid, "host": host or sni, "path": path,
                    "fp": fp, "sni": sni or host,
                })
            except Exception:
                continue
        return {"success": True, "nodes": nodes}
    except Exception as e:
        return {"success": False, "message": f"获取失败: {e}"}


@router.get("/api/media")
async def list_media(limit: int = 0, category: str = ""):
    async with async_session() as session:
        query = select(Media).order_by(Media.created_at.desc())
        if category:
            query = query.where(Media.category == category)
        if limit:
            query = query.limit(limit)
        result = await session.execute(query)
        records = result.scalars().all()

    items = [{
        "id": r.id, "message_id": r.message_id, "chat_id": r.chat_id,
        "file_name": r.file_name, "size": r.size, "duration": r.duration,
        "mime_type": r.mime_type, "category": r.category, "tmdb_id": r.tmdb_id,
        "tmdb_name": r.tmdb_name, "season": r.season, "episode": r.episode,
        "resolution": r.resolution, "strm_path": r.strm_path,
        "created_at": str(r.created_at) if r.created_at else None,
    } for r in records]
    return {"total": len(items), "items": items}


@router.post("/api/media/scan")
async def scan_media():
    try:
        from app.telegram.client import get_client
        from app.media.parser import parse_media_info
        from app.media.strm import generate_strm

        client = await get_client()
        channel_id = settings.telegram.channel_id
        count = 0

        async for message in client.get_chat_history(channel_id):
            media = message.video or message.document
            if not media:
                continue
            async with async_session() as session:
                existing = await session.get(Media, message.id)
                if existing:
                    continue

            file_name = media.file_name or f"video_{message.id}.mp4"
            media_info = parse_media_info(file_name)
            strm_path = generate_strm(
                message_id=message.id, file_name=file_name,
                category=media_info.get("category"), title=media_info.get("title"),
                season=media_info.get("season"), episode=media_info.get("episode"),
                tmdb_id=media_info.get("tmdb_id"), tmdb_name=media_info.get("tmdb_name"),
            )
            async with async_session() as session:
                record = Media(
                    message_id=message.id, chat_id=str(channel_id), file_name=file_name,
                    file_id=getattr(media, "file_id", None),
                    file_unique_id=getattr(media, "file_unique_id", None),
                    size=media.file_size or 0, duration=media.duration or 0,
                    mime_type=media.mime_type or "video/mp4",
                    width=media.width or 0, height=media.height or 0,
                    category=media_info.get("category"), tmdb_id=media_info.get("tmdb_id"),
                    tmdb_name=media_info.get("tmdb_name") or media_info.get("title"), season=media_info.get("season"),
                    episode=media_info.get("episode"), resolution=media_info.get("resolution"),
                    strm_path=str(strm_path) if strm_path else None,
                )
                session.add(record)
                await session.commit()
                count += 1
        return {"success": True, "message": f"扫描完成，新增 {count} 条记录"}
    except Exception as e:
        logger.error(f"Scan error: {e}")
        return {"success": False, "message": f"扫描失败: {e}"}


@router.post("/api/scan/forward")
async def scan_forward(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    limit = _safe_int(body.get("limit"), 0)
    try:
        from app.telegram.client import get_client, warmup_client

        client = await get_client()
        await warmup_client(client)
        monitor_channels = settings.telegram.monitor_channel_list
        private_channel = settings.telegram.channel_id
        total = 0

        for chat_id in monitor_channels:
            try:
                chat = await client.get_chat(chat_id)
                ch_name = chat.title or str(chat_id)
            except Exception as e:
                logger.warning(f"Cannot access {chat_id}: {e}")
                continue

            count = 0
            async for message in client.get_chat_history(chat_id):
                if limit and count >= limit:
                    break
                media = message.video or message.document
                if not media:
                    continue
                if message.document and not (message.document.mime_type and message.document.mime_type.startswith("video/")):
                    continue
                try:
                    await client.forward_messages(
                        chat_id=private_channel, from_chat_id=chat_id, message_ids=message.id,
                    )
                    count += 1
                    total += 1
                except Exception as e:
                    logger.error(f"Forward failed: {e}")
                await asyncio.sleep(0.5)

            logger.info(f"Channel {ch_name}: forwarded {count}")

        return {"success": True, "message": f"转发完成，共转发 {total} 个视频到私有频道"}
    except Exception as e:
        return {"success": False, "message": f"转发失败: {e}"}


@router.post("/api/scan/record")
async def scan_record(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    limit = _safe_int(body.get("limit"), 0)
    try:
        from app.telegram.client import get_client, warmup_client
        from app.media.parser import parse_media_info
        from app.media.strm import generate_strm

        client = await get_client()
        await warmup_client(client)
        channel_id = settings.telegram.channel_id
        count = 0

        async for message in client.get_chat_history(channel_id):
            if limit and count >= limit:
                break
            media = message.video or message.document
            if not media:
                continue

            async with async_session() as session:
                existing = await session.get(Media, message.id)
                if existing:
                    continue

            file_name = media.file_name or f"video_{message.id}.mp4"
            media_info = parse_media_info(file_name)
            strm_path = generate_strm(
                message_id=message.id, file_name=file_name,
                category=media_info.get("category"), title=media_info.get("title"),
                season=media_info.get("season"), episode=media_info.get("episode"),
                tmdb_id=media_info.get("tmdb_id"), tmdb_name=media_info.get("tmdb_name"),
            )
            async with async_session() as session:
                record = Media(
                    message_id=message.id, chat_id=str(channel_id), file_name=file_name,
                    file_id=getattr(media, "file_id", None),
                    file_unique_id=getattr(media, "file_unique_id", None),
                    size=media.file_size or 0, duration=media.duration or 0,
                    mime_type=media.mime_type or "video/mp4",
                    width=media.width or 0, height=media.height or 0,
                    category=media_info.get("category"), tmdb_id=media_info.get("tmdb_id"),
                    tmdb_name=media_info.get("tmdb_name") or media_info.get("title"), season=media_info.get("season"),
                    episode=media_info.get("episode"), resolution=media_info.get("resolution"),
                    strm_path=str(strm_path) if strm_path else None,
                )
                session.add(record)
                await session.commit()
                count += 1

        return {"success": True, "message": f"录入完成，新增 {count} 条记录"}
    except Exception as e:
        return {"success": False, "message": f"录入失败: {e}"}


@router.post("/api/proxy/start")
async def proxy_start():
    try:
        if is_singbox_running():
            return {"success": True, "message": "sing-box 已在运行"}
        sb = settings.singbox
        generate_config(
            vless_address=sb.address, vless_port=sb.port, vless_uuid=sb.uuid,
            vless_path=sb.path, vless_host=sb.host, vless_tls=sb.tls,
            vless_fp=sb.fingerprint, socks_port=sb.socks_port,
        )
        start_singbox(socks_port=sb.socks_port)
        return {"success": True, "message": "sing-box 已启动"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/proxy/stop")
async def proxy_stop():
    try:
        _stop_singbox_safe()
        return {"success": True, "message": "sing-box 已停止"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/proxy/restart")
async def proxy_restart():
    try:
        _stop_singbox_safe()
        time.sleep(2)
        sb = settings.singbox
        generate_config(
            vless_address=sb.address, vless_port=sb.port, vless_uuid=sb.uuid,
            vless_path=sb.path, vless_host=sb.host, vless_tls=sb.tls,
            vless_fp=sb.fingerprint, socks_port=sb.socks_port,
        )
        start_singbox(socks_port=sb.socks_port)
        return {"success": True, "message": "sing-box 已重启"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/test/telegram")
async def test_telegram():
    import socks as socks_mod
    import ssl

    if settings.singbox.enabled:
        port = settings.singbox.socks_port
    elif settings.proxy.enabled:
        port = settings.proxy.port
    else:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect(("api.telegram.org", 443))
            ctx = ssl.create_default_context()
            ss = ctx.wrap_socket(s, server_hostname="api.telegram.org")
            ss.send(b"GET /bot123:fake/getMe HTTP/1.1\r\nHost: api.telegram.org\r\nConnection: close\r\n\r\n")
            resp = ss.recv(4096)
            ss.close()
            status = resp.split(b"\r\n")[0].decode()
            return {"success": True, "detail": f"直连成功\n{status}"}
        except Exception as e:
            return {"success": False, "detail": f"直连失败: {e}"}

    try:
        s = socks_mod.socksocket()
        s.set_proxy(socks_mod.SOCKS5, "127.0.0.1", port)
        s.settimeout(15)
        s.connect(("api.telegram.org", 443))
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(s, server_hostname="api.telegram.org")
        ss.send(b"GET /bot123:fake/getMe HTTP/1.1\r\nHost: api.telegram.org\r\nConnection: close\r\n\r\n")
        resp = ss.recv(4096)
        ss.close()
        status = resp.split(b"\r\n")[0].decode()
        return {"success": True, "detail": f"SOCKS5({port}) 连接成功\n{status}"}
    except Exception as e:
        return {"success": False, "detail": str(e)}


@router.get("/api/cache")
async def get_cache():
    cache_dir = settings.cache.cache_path
    total_size = 0
    file_count = 0
    media_count = 0
    details = []
    if cache_dir.exists():
        media_dirs = [d for d in cache_dir.iterdir() if d.is_dir()]
        media_count = len(media_dirs)
        for md in media_dirs:
            chunks = list(md.glob("*.chunk"))
            dir_size = sum(f.stat().st_size for f in chunks)
            total_size += dir_size
            file_count += len(chunks)
            if chunks:
                details.append({"message_id": md.name, "file_count": len(chunks), "size": dir_size})
    details.sort(key=lambda x: x["size"], reverse=True)
    return {"total_size": total_size, "file_count": file_count, "media_count": media_count, "details": details[:50]}


@router.post("/api/cache/clear")
async def clear_cache():
    cache_dir = settings.cache.cache_path
    try:
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
        return {"success": True, "message": "缓存已清空"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.get("/api/logs")
async def get_logs():
    log_file = BASE_DIR / "sing-box" / "sing-box.log"
    logs = []
    if log_file.exists():
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            logs = lines[-200:]
        except Exception:
            pass
    return {"logs": logs}
