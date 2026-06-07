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
from app.media.parser import parse_media_info
from app.media.strm import remove_old_strm
from app.proxy.singbox import (
    get_status as get_singbox_status,
    start as start_singbox,
    stop as stop_singbox,
    generate_config,
    is_running as is_singbox_running,
)
from app.telegram.client import get_sub_nodes, get_current_node_idx, select_sub_node, get_proxy_mode
from sqlalchemy import select, delete

logger = logging.getLogger(__name__)
router = APIRouter()


def is_video_message(message) -> bool:
    if message.video:
        return True
    if message.document and message.document.mime_type:
        return message.document.mime_type.startswith("video/")
    return False


# 频道名缓存，避免每次都调用 Telegram API
_channel_name_cache: dict[int, str] = {}
_channel_cache_ts: dict[int, float] = {}
CHANNEL_CACHE_MAX_AGE = 600  # 缓存 10 分钟


async def _get_channel_name(client, channel_id: int) -> str:
    import time
    now = time.time()
    if channel_id in _channel_name_cache:
        if (now - _channel_cache_ts.get(channel_id, 0)) < CHANNEL_CACHE_MAX_AGE:
            return _channel_name_cache[channel_id]
    try:
        chat = await client.get_chat(channel_id)
        name = chat.title or str(channel_id)
        _channel_name_cache[channel_id] = name
        _channel_cache_ts[channel_id] = now
        return name
    except Exception:
        return _channel_name_cache.get(channel_id, str(channel_id))



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
            "protocol": settings.singbox.protocol,
            "cipher": settings.singbox.cipher,
        },
        "proxy": {
            "enabled": settings.proxy.enabled,
            "scheme": settings.proxy.scheme,
            "hostname": settings.proxy.hostname,
            "port": settings.proxy.port,
            "sub_url": settings.proxy.sub_url,
        },
        "stream": {
            "host": settings.stream.host,
            "port": settings.stream.port,
            "base_url": settings.stream.base_url,
            "local_url": settings.stream.local_url,
            "chunk_size": settings.stream.chunk_size,
        },
        "database": {"url": settings.database.url},
        "cache": {
            "dir": settings.cache.dir,
            "media_cache_size_mb": settings.cache.memory_cache_size_mb,
            "disk_cache_max_gb": settings.cache.disk_cache_max_gb,
            "prefetch_enabled": settings.cache.prefetch_enabled,
        },
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

        ca = body.get("cache", {})
        if ca.get("dir"):
            updates["CACHE_DIR"] = ca["dir"]
        if ca.get("memory_cache_size_mb") not in (None, "", 0):
            updates["MEMORY_CACHE_SIZE"] = str(_safe_int(ca["memory_cache_size_mb"], 256))
        if ca.get("disk_cache_max_gb") not in (None, "", 0):
            updates["DISK_CACHE_MAX_GB"] = str(_safe_int(ca["disk_cache_max_gb"], 50))

        px = body.get("proxy", {})
        if "sub_url" in px:
            updates["PROXY_SUB_URL"] = px["sub_url"] or ""

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
            "SINGBOX_PROTOCOL": body.get("protocol", "vless"),
            "SINGBOX_CIPHER": body.get("cipher", "auto"),
        })
        _stop_singbox_safe()
        await asyncio.sleep(2)
        generate_config(
            vless_address=body["address"], vless_port=_safe_int(body.get("port"), 443),
            vless_uuid=body["uuid"], vless_path=body.get("path", ""),
            vless_host=body.get("host", ""), vless_tls=True,
            vless_fp=body.get("fingerprint", "chrome"),
            socks_port=settings.singbox.socks_port,
            protocol=body.get("protocol", "vless"),
            cipher=body.get("cipher", "auto"),
        )
        await asyncio.to_thread(start_singbox, socks_port=settings.singbox.socks_port)
        return {"success": True, "message": "代理配置已保存并重启"}
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
                        "sni": obj.get("sni", ""),
                        "protocol": "vmess",
                    })
                except Exception:
                    continue
            elif line.startswith("ss://"):
                try:
                    from urllib.parse import unquote as _unquote
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
                        name = ""
                        if "#" in body:
                            name = _unquote(body.split("#")[-1])
                        nodes.append({
                            "name": name or f"{address}:{port}",
                            "address": address,
                            "port": port,
                            "uuid": password,
                            "host": "",
                            "path": "",
                            "fp": "",
                            "sni": "",
                            "protocol": "shadowsocks",
                            "cipher": method,
                        })
                except Exception:
                    continue
            elif line.startswith("vless://") or line.startswith("trojan://"):
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
                    protocol = "vless" if line.startswith("vless://") else "trojan"
                    nodes.append({
                        "name": name, "address": address, "port": port,
                        "uuid": uuid, "host": host or sni, "path": path,
                        "fp": fp, "sni": sni or host, "protocol": protocol,
                    })
                except Exception:
                    continue
        return {"success": True, "nodes": nodes}
    except Exception as e:
        return {"success": False, "message": f"获取失败: {e}"}


@router.get("/api/sub/nodes")
async def list_sub_nodes():
    nodes = get_sub_nodes()
    current = get_current_node_idx()
    return {
        "nodes": nodes,
        "current": current,
        "total": len(nodes),
        "sub_url": settings.proxy.sub_url,
        "proxy_mode": get_proxy_mode(),
    }


@router.post("/api/sub/select")
async def select_node(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    idx = body.get("index", -1)
    if idx < 0:
        return {"success": False, "message": "无效的节点索引"}
    if await asyncio.to_thread(select_sub_node, idx):
        return {"success": True, "message": f"已切换到节点 {idx+1}"}
    return {"success": False, "message": "切换失败，节点可能不可用"}


@router.get("/api/media")
async def list_media(page: int = 1, page_size: int = 20, category: str = ""):
    async with async_session() as session:
        base_query = select(Media).where(Media.recognized == True)
        if category:
            base_query = base_query.where(Media.category == category)

        count_result = await session.execute(base_query)
        total = len(count_result.scalars().all())

        query = base_query.order_by(Media.created_at.desc())
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        result = await session.execute(query)
        records = result.scalars().all()

    items = [{
        "id": r.id, "message_id": r.message_id, "chat_id": r.chat_id,
        "file_name": r.file_name, "size": r.size, "duration": r.duration,
        "mime_type": r.mime_type, "category": r.category,
        "display_name": r.display_name, "season": r.season, "episode": r.episode,
        "resolution": r.resolution, "strm_path": r.strm_path,
        "created_at": str(r.created_at) if r.created_at else None,
    } for r in records]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/api/media/unrecognized")
async def list_unrecognized(page: int = 1, page_size: int = 20):
    async with async_session() as session:
        count_result = await session.execute(select(Media).where(Media.recognized == False))
        total = len(count_result.scalars().all())

        query = select(Media).where(Media.recognized == False).order_by(Media.created_at.desc())
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        result = await session.execute(query)
        records = result.scalars().all()

    items = [{
        "id": r.id, "message_id": r.message_id, "chat_id": r.chat_id,
        "file_name": r.file_name, "size": r.size, "duration": r.duration,
        "mime_type": r.mime_type, "category": r.category,
        "display_name": r.display_name, "season": r.season, "episode": r.episode,
        "resolution": r.resolution, "strm_path": r.strm_path,
        "created_at": str(r.created_at) if r.created_at else None,
    } for r in records]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/api/adult")
async def list_adult(page: int = 1, page_size: int = 20, search: str = ""):
    async with async_session() as session:
        base_query = select(Media).where(Media.category == "cosplay")
        if search:
            base_query = base_query.where(Media.file_name.contains(search))
        count_result = await session.execute(base_query)
        total = len(count_result.scalars().all())
        query = base_query.order_by(Media.created_at.desc())
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        result = await session.execute(query)
        records = result.scalars().all()
    items = [{
        "id": r.id, "message_id": r.message_id, "chat_id": r.chat_id,
        "file_name": r.file_name, "size": r.size, "duration": r.duration,
        "mime_type": r.mime_type, "category": r.category,
        "display_name": r.display_name, "caption": r.caption,
        "strm_path": r.strm_path,
        "created_at": str(r.created_at) if r.created_at else None,
    } for r in records]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.post("/api/adult/update")
async def update_adult(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    media_id = body.get("id")
    if not media_id:
        return {"success": False, "message": "缺少 id"}
    try:
        async with async_session() as session:
            record = await session.get(Media, media_id)
            if not record:
                return {"success": False, "message": "记录不存在"}
            if "display_name" in body:
                record.display_name = body["display_name"]
            if "caption" in body:
                record.caption = body["caption"]
            await session.commit()
        return {"success": True, "message": "已更新"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.get("/api/adult/channels")
async def get_adult_channels():
    channels = settings.telegram.adult_channel_list
    result = []
    try:
        from app.telegram.client import get_client
        client = await get_client()
        for cid in channels:
            try:
                chat = await client.get_chat(cid)
                result.append({"id": cid, "name": chat.title or chat.username or str(cid)})
            except Exception:
                result.append({"id": cid, "name": str(cid)})
    except Exception:
        for cid in channels:
            result.append({"id": cid, "name": str(cid)})
    return {"channels": result}


@router.post("/api/adult/channels/add")
async def add_adult_channel(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    channel_id = str(body.get("id", "")).strip()
    if not channel_id:
        return {"success": False, "message": "频道 ID 不能为空"}
    current = settings.telegram.adult_channels
    if current:
        new_val = f"{current},{channel_id}"
    else:
        new_val = channel_id
    _update_env({"ADULT_CHANNELS": new_val})
    settings.telegram.adult_channels = new_val
    return {"success": True, "message": "频道已添加"}


@router.post("/api/adult/channels/delete")
async def delete_adult_channel(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    channel_id = str(body.get("id", "")).strip()
    ids = [c.strip() for c in settings.telegram.adult_channels.split(",") if c.strip() and c.strip() != channel_id]
    new_val = ",".join(ids)
    _update_env({"ADULT_CHANNELS": new_val})
    settings.telegram.adult_channels = new_val
    return {"success": True, "message": "频道已删除"}


@router.post("/api/media/scan")
async def scan_media(request: Request):
    try:
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    except Exception:
        body = {}
    limit = _safe_int(body.get("limit"), 0)

    try:
        from app.telegram.client import get_client
        from app.media.parser import parse_media_info
        from app.media.strm import generate_strm

        client = await get_client()
        channel_cat_map = settings.telegram.channel_category_map
        monitor_channels = settings.telegram.monitor_channel_list
        private_channel = settings.telegram.channel_id
        all_channels = list(set(monitor_channels + [private_channel]))
        total_count = 0

        for channel_id in all_channels:
            count = 0
            try:
                async for message in client.get_chat_history(channel_id):
                    if limit and total_count >= limit:
                        break
                    media = message.video or message.document
                    if not media:
                        continue
                    async with async_session() as session:
                        result = await session.execute(select(Media).where(Media.message_id == message.id))
                        existing = result.scalar_one_or_none()
                        if existing:
                            continue

                    file_name = media.file_name or f"video_{message.id}.mp4"
                    caption = message.caption or ""
                    forced_category = channel_cat_map.get(str(channel_id))
                    media_info = parse_media_info(file_name, caption=caption, channel_id=channel_id)
                    if media_info is None:
                        continue
                    if forced_category:
                        media_info["category"] = forced_category
                        media_info["recognized"] = True
                    strm_path = generate_strm(
                            message_id=message.id, file_name=file_name,
                            category=media_info.get("category"), title=media_info.get("title"),
                            season=media_info.get("season"), episode=media_info.get("episode"),
                            display_name=media_info.get("display_name"),
                        )
                    async with async_session() as session:
                        record = Media(
                            message_id=message.id, chat_id=str(channel_id), file_name=file_name,
                            caption=caption,
                            file_id=getattr(media, "file_id", None),
                            file_unique_id=getattr(media, "file_unique_id", None),
                            size=media.file_size or 0, duration=media.duration or 0,
                            mime_type=media.mime_type or "video/mp4",
                            width=media.width or 0, height=media.height or 0,
                            category=media_info.get("category"),
                            display_name=media_info.get("display_name") or media_info.get("title"),
                            season=media_info.get("season"),
                            episode=media_info.get("episode"), resolution=media_info.get("resolution"),
                            strm_path=str(strm_path) if strm_path else None,
                            recognized=media_info.get("recognized", False),
                        )
                        session.add(record)
                        await session.commit()
                        count += 1
            except Exception as e:
                logger.warning(f"Scan error in channel {channel_id}: {e}")
            if count:
                logger.info(f"Channel {channel_id}: scanned {count} videos")
            total_count += count
        return {"success": True, "message": f"扫描完成，新增 {total_count} 条记录"}
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
        channel_cat_map = settings.telegram.channel_category_map
        monitor_channels = settings.telegram.monitor_channel_list
        private_channel = settings.telegram.channel_id
        all_channels = list(set(monitor_channels + [private_channel]))
        total_count = 0

        for channel_id in all_channels:
            count = 0
            try:
                async for message in client.get_chat_history(channel_id):
                    if limit and total_count >= limit:
                        break
                    media = message.video or message.document
                    if not media:
                        continue

                    async with async_session() as session:
                        result = await session.execute(select(Media).where(Media.message_id == message.id))
                        existing = result.scalar_one_or_none()
                        if existing:
                            continue

                    file_name = media.file_name or f"video_{message.id}.mp4"
                    caption = message.caption or ""
                    forced_category = channel_cat_map.get(str(channel_id))
                    media_info = parse_media_info(file_name, caption=caption, channel_id=channel_id)
                    if media_info is None:
                        continue
                    if forced_category:
                        media_info["category"] = forced_category
                        media_info["recognized"] = True
                    strm_path = generate_strm(
                            message_id=message.id, file_name=file_name,
                            category=media_info.get("category"), title=media_info.get("title"),
                            season=media_info.get("season"), episode=media_info.get("episode"),
                            display_name=media_info.get("display_name"),
                        )
                    async with async_session() as session:
                        record = Media(
                            message_id=message.id, chat_id=str(channel_id), file_name=file_name,
                            caption=caption,
                            file_id=getattr(media, "file_id", None),
                            file_unique_id=getattr(media, "file_unique_id", None),
                            size=media.file_size or 0, duration=media.duration or 0,
                            mime_type=media.mime_type or "video/mp4",
                            width=media.width or 0, height=media.height or 0,
                            category=media_info.get("category"),
                            display_name=media_info.get("display_name") or media_info.get("title"),
                            season=media_info.get("season"),
                            episode=media_info.get("episode"), resolution=media_info.get("resolution"),
                            strm_path=str(strm_path) if strm_path else None,
                            recognized=media_info.get("recognized", False),
                        )
                        session.add(record)
                        await session.commit()
                        count += 1
            except Exception as e:
                logger.warning(f"Record error in channel {channel_id}: {e}")
            if count:
                logger.info(f"Channel {channel_id}: recorded {count} videos")
            total_count += count

        return {"success": True, "message": f"录入完成，新增 {total_count} 条记录"}
    except Exception as e:
        return {"success": False, "message": f"录入失败: {e}"}


@router.post("/api/scan/channel")
async def scan_single_channel(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    
    channel_id = body.get("channel_id")
    limit = _safe_int(body.get("limit"), 0)
    
    if not channel_id:
        return {"success": False, "message": "缺少频道ID"}
    
    try:
        from app.telegram.client import get_client, warmup_client
        from app.media.parser import parse_media_info
        from app.media.strm import generate_strm

        client = await get_client()
        await warmup_client(client)
        channel_cat_map = settings.telegram.channel_category_map
        
        try:
            chat = await client.get_chat(channel_id)
            ch_name = chat.title or str(channel_id)
        except Exception as e:
            return {"success": False, "message": f"无法访问频道: {e}"}
        
        count = 0
        async for message in client.get_chat_history(channel_id):
            if limit and count >= limit:
                break
            
            media = message.video or message.document
            if not media:
                continue
            
            async with async_session() as session:
                result = await session.execute(select(Media).where(Media.message_id == message.id))
                existing = result.scalar_one_or_none()
                if existing:
                    continue
            
            file_name = media.file_name or f"video_{message.id}.mp4"
            caption = message.caption or ""
            forced_category = channel_cat_map.get(str(channel_id))
            media_info = parse_media_info(file_name, caption=caption, channel_id=channel_id)
            if media_info is None:
                continue
            if forced_category:
                media_info["category"] = forced_category
                media_info["recognized"] = True
            
            is_adult = channel_id in settings.telegram.adult_channel_list
            strm_path = None
            if not is_adult:
                strm_path = generate_strm(
                    message_id=message.id, file_name=file_name,
                    category=media_info.get("category"), title=media_info.get("title"),
                    season=media_info.get("season"), episode=media_info.get("episode"),
                    display_name=media_info.get("display_name"),
                )
            
            async with async_session() as session:
                record = Media(
                    message_id=message.id, chat_id=str(channel_id), file_name=file_name,
                    caption=caption,
                    file_id=getattr(media, "file_id", None),
                    file_unique_id=getattr(media, "file_unique_id", None),
                    size=media.file_size or 0, duration=media.duration or 0,
                    mime_type=media.mime_type or "video/mp4",
                    width=media.width or 0, height=media.height or 0,
                    category=media_info.get("category"),
                    display_name=media_info.get("display_name") or media_info.get("title"),
                    season=media_info.get("season"),
                    episode=media_info.get("episode"), resolution=media_info.get("resolution"),
                    strm_path=str(strm_path) if strm_path else None,
                    recognized=media_info.get("recognized", False),
                )
                session.add(record)
                await session.commit()
                count += 1
        
        return {"success": True, "message": f"频道 {ch_name} 扫描完成，新增 {count} 条记录"}
    except Exception as e:
        logger.error(f"Scan channel error: {e}")
        return {"success": False, "message": f"扫描失败: {e}"}


@router.post("/api/forward/channel")
async def forward_single_channel(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    
    channel_id = body.get("channel_id")
    limit = _safe_int(body.get("limit"), 0)
    
    if not channel_id:
        return {"success": False, "message": "缺少频道ID"}
    
    try:
        from app.telegram.client import get_client, warmup_client

        client = await get_client()
        await warmup_client(client)
        private_channel = settings.telegram.channel_id
        
        try:
            chat = await client.get_chat(channel_id)
            ch_name = chat.title or str(channel_id)
        except Exception as e:
            return {"success": False, "message": f"无法访问频道: {e}"}
        
        count = 0
        async for message in client.get_chat_history(channel_id):
            if limit and count >= limit:
                break
            
            if not is_video_message(message):
                continue
            
            # 检查是否已入库（去重）
            async with async_session() as session:
                result = await session.execute(select(Media).where(Media.message_id == message.id))
                if result.scalar_one_or_none():
                    continue
            
            try:
                await client.forward_messages(
                    chat_id=private_channel,
                    from_chat_id=channel_id,
                    message_ids=message.id,
                )
                count += 1
            except Exception as e:
                err_str = str(e)
                if "CHAT_FORWARDS_RESTRICTED" in err_str:
                    # 转发受限，直接入库
                    media = message.video or message.document
                    fn = media.file_name or f"video_{message.id}.mp4"
                    caption = message.caption or ""
                    info = parse_media_info(fn, caption=caption, channel_id=channel_id)
                    if info:
                        await _direct_record_message(message, channel_id, info)
                        count += 1
                else:
                    logger.warning(f"Forward failed msg {message.id}: {e}")
            
            await asyncio.sleep(0.3)
        
        return {"success": True, "message": f"频道 {ch_name} 转发完成，共 {count} 条"}
    except Exception as e:
        logger.error(f"Forward channel error: {e}")
        return {"success": False, "message": f"转发失败: {e}"}


@router.post("/api/media/regen-strm")
async def regen_strm():
    try:
        from app.media.strm import generate_strm, remove_old_strm
        from app.media.parser import parse_media_info

        async with async_session() as session:
            result = await session.execute(select(Media))
            records = result.scalars().all()

        count = 0
        for r in records:
            try:
                remove_old_strm(r.strm_path)
                strm_path = generate_strm(
                    message_id=r.message_id,
                    file_name=r.file_name,
                    category=r.category,
                    title=r.display_name,
                    season=r.season,
                    episode=r.episode,
                    display_name=r.display_name,
                )
                async with async_session() as session:
                    record = await session.get(Media, r.id)
                    if record:
                        record.strm_path = str(strm_path) if strm_path else None
                        await session.commit()
                count += 1
            except Exception as e:
                logger.warning(f"Regen STRM failed for {r.file_name}: {e}")

        return {"success": True, "message": f"已重新生成 {count} 个 STRM 文件"}
    except Exception as e:
        logger.error(f"Regen STRM error: {e}")
        return {"success": False, "message": str(e)}


@router.delete("/api/media/{media_id}")
async def delete_media(media_id: int):
    try:
        async with async_session() as session:
            record = await session.get(Media, media_id)
            if not record:
                return {"success": False, "message": "记录不存在"}
            strm_path = record.strm_path
            await session.delete(record)
            await session.commit()

        if strm_path:
            remove_old_strm(strm_path)

        return {"success": True, "message": "已删除"}
    except Exception as e:
        logger.error(f"Delete media error: {e}")
        return {"success": False, "message": str(e)}


@router.post("/api/media/delete-batch")
async def delete_media_batch(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}

    ids = body.get("ids", [])
    if not ids:
        return {"success": False, "message": "未选择任何记录"}

    try:
        async with async_session() as session:
            result = await session.execute(
                select(Media).where(Media.id.in_(ids))
            )
            records = result.scalars().all()
            strm_paths = [r.strm_path for r in records if r.strm_path]

            await session.execute(
                delete(Media).where(Media.id.in_(ids))
            )
            await session.commit()

        for path in strm_paths:
            try:
                remove_old_strm(path)
            except Exception:
                pass

        return {"success": True, "message": f"已删除 {len(records)} 条记录"}
    except Exception as e:
        logger.error(f"Batch delete error: {e}")
        return {"success": False, "message": str(e)}


@router.post("/api/media/unrecognized/clear")
async def clear_unrecognized():
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Media).where(Media.recognized == False)
            )
            records = result.scalars().all()
            strm_paths = [r.strm_path for r in records if r.strm_path]

            await session.execute(
                delete(Media).where(Media.recognized == False)
            )
            await session.commit()

        for path in strm_paths:
            try:
                remove_old_strm(path)
            except Exception:
                pass

        return {"success": True, "message": f"已清除 {len(records)} 条未识别记录"}
    except Exception as e:
        logger.error(f"Clear unrecognized error: {e}")
        return {"success": False, "message": str(e)}


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
            protocol=sb.protocol, cipher=sb.cipher,
        )
        await asyncio.to_thread(start_singbox, socks_port=sb.socks_port)
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
        await asyncio.sleep(2)
        sb = settings.singbox
        generate_config(
            vless_address=sb.address, vless_port=sb.port, vless_uuid=sb.uuid,
            vless_path=sb.path, vless_host=sb.host, vless_tls=sb.tls,
            vless_fp=sb.fingerprint, socks_port=sb.socks_port,
            protocol=sb.protocol, cipher=sb.cipher,
        )
        await asyncio.to_thread(start_singbox, socks_port=sb.socks_port)
        return {"success": True, "message": "sing-box 已重启"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/test/telegram")
async def test_telegram():
    import socks as socks_mod
    import ssl

    from app.proxy.singbox import is_running as is_singbox_running
    if is_singbox_running():
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


@router.post("/api/test/forward")
async def test_forward():
    try:
        from app.telegram.client import get_client
        client = await get_client()
        channel_id = settings.telegram.channel_id
        try:
            chat = await client.get_chat(channel_id)
            return {"success": True, "detail": f"私有频道正常: {chat.title or chat.id}"}
        except Exception as e:
            return {"success": False, "detail": f"私有频道访问失败: {e}"}
    except Exception as e:
        return {"success": False, "detail": f"Telegram 连接失败: {e}"}


@router.get("/api/channels")
async def get_channels():
    monitor_ids = settings.telegram.monitor_channel_list
    private_id = settings.telegram.channel_id
    channels = {"monitor": [], "private": None}

    try:
        from app.telegram.client import get_client
        client = await get_client()

        # 并行获取所有频道名称（带缓存），大幅提速
        tasks = {}
        if private_id:
            tasks[("private", private_id)] = _get_channel_name(client, private_id)
        for cid in monitor_ids:
            tasks[("monitor", cid)] = _get_channel_name(client, cid)

        if tasks:
            keys = list(tasks.keys())
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for key, result in zip(keys, results):
                name = result if isinstance(result, str) else str(key[1])
                if key[0] == "private":
                    channels["private"] = {"id": key[1], "name": name}
                else:
                    channels["monitor"].append({"id": key[1], "name": name})
    except Exception:
        for cid in monitor_ids:
            channels["monitor"].append({"id": cid, "name": str(cid)})
        if private_id:
            channels["private"] = {"id": private_id, "name": str(private_id)}

    return channels


@router.post("/api/channels/add")
async def add_channel(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    channel_type = body.get("type", "monitor")
    channel_id = str(body.get("id", "")).strip()
    if not channel_id:
        return {"success": False, "message": "频道 ID 不能为空"}

    if channel_type == "private":
        _update_env({"TELEGRAM_CHANNEL_ID": channel_id})
        settings.telegram.channel_id = int(channel_id)
    else:
        current = settings.telegram.monitor_channels
        if current:
            new_val = f"{current},{channel_id}"
        else:
            new_val = channel_id
        _update_env({"TELEGRAM_MONITOR_CHANNELS": new_val})
        settings.telegram.monitor_channels = new_val

    return {"success": True, "message": "频道已添加"}


@router.post("/api/channels/delete")
async def delete_channel(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    channel_type = body.get("type", "monitor")
    channel_id = str(body.get("id", "")).strip()

    if channel_type == "private":
        _update_env({"TELEGRAM_CHANNEL_ID": ""})
        settings.telegram.channel_id = 0
    else:
        ids = [c.strip() for c in settings.telegram.monitor_channels.split(",") if c.strip() and c.strip() != channel_id]
        new_val = ",".join(ids)
        _update_env({"TELEGRAM_MONITOR_CHANNELS": new_val})
        settings.telegram.monitor_channels = new_val

    return {"success": True, "message": "频道已删除"}


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
