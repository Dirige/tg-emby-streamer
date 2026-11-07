import os
import re
import json
import logging
import shutil
import asyncio
import time
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.config import settings, BASE_DIR, save_config, save_config_value, get_config_value
from app.database import async_session
from app.models import Media, ChannelRule, Category, UnrecognizedMedia
from app.media.strm import remove_old_strm
from app.telegram.monitor import scan_channel, is_video_message
from app.telegram.client import get_client, stop_client, test_connection, send_code, verify_code, check_password, get_auth_status
from sqlalchemy import select, delete

logger = logging.getLogger(__name__)
router = APIRouter()

_channel_name_cache: dict[str, str] = {}
_channel_cache_ts: dict[str, float] = {}
CHANNEL_CACHE_MAX_AGE = 600


async def _resolve_channel_name(client, channel_id: str) -> str:
    now = time.time()
    if channel_id in _channel_name_cache:
        if (now - _channel_cache_ts.get(channel_id, 0)) < CHANNEL_CACHE_MAX_AGE:
            return _channel_name_cache[channel_id]
    try:
        chat = await client.get_chat(int(channel_id))
        name = chat.title or str(channel_id)
        _channel_name_cache[channel_id] = name
        _channel_cache_ts[channel_id] = now
        return name
    except Exception:
        return _channel_name_cache.get(channel_id, str(channel_id))


# ========== Dashboard ==========

def _dashboard_html() -> str:
    html_path = Path(__file__).parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(_dashboard_html())


# 兼容旧版入口路径 /web/index.html（同页），避免书签 404
@router.get("/web", response_class=HTMLResponse, include_in_schema=False)
@router.get("/web/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/web/index.html", response_class=HTMLResponse, include_in_schema=False)
async def web_index():
    return HTMLResponse(_dashboard_html())


@router.get("/api/health")
async def health():
    return {
        "status": "ok",
        "proxy": {
            "enabled": settings.proxy.enabled,
            "scheme": settings.proxy.scheme if settings.proxy.enabled else "",
            "hostname": settings.proxy.hostname if settings.proxy.enabled else "",
            "port": settings.proxy.port if settings.proxy.enabled else 0,
        },
    }


@router.get("/api/config")
async def get_config():
    return {
        "telegram": {
            "api_id": settings.telegram.api_id,
            "api_hash": settings.telegram.api_hash,
            "phone": settings.telegram.phone,
            "user_id": settings.telegram.user_id,
            "session_string": settings.telegram.session_string,
            "monitor_interval_minutes": settings.telegram.monitor_interval_minutes,
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
            "token": settings.web.stream_token,
        },
        "auth": {
            "username": settings.web.username,
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
            updates["telegram.api_id"] = tg["api_id"]
        if tg.get("api_hash"):
            updates["telegram.api_hash"] = tg["api_hash"]
        if tg.get("session_string"):
            updates["telegram.session_string"] = tg["session_string"]
        if tg.get("monitor_interval") not in (None, "", 0):
            updates["telegram.monitor_interval_minutes"] = int(tg["monitor_interval"])
        st = body.get("stream", {})
        if st.get("host"):
            updates["server.host"] = st["host"]
        if st.get("port") not in (None, "", 0):
            updates["server.port"] = int(st["port"])
        if st.get("base_url"):
            updates["server.base_url"] = st["base_url"]
        if st.get("local_url"):
            updates["server.local_url"] = st["local_url"]
        if "token" in st:
            updates["server.stream_token"] = st["token"] or ""
        ca = body.get("storage", {})
        if ca.get("cache_dir"):
            updates["storage.cache_dir"] = ca["cache_dir"]
        if ca.get("strm_dir"):
            updates["storage.strm_dir"] = ca["strm_dir"]
        px = body.get("proxy", {})
        if "enabled" in px:
            updates["proxy.enabled"] = px["enabled"]
        if "scheme" in px:
            updates["proxy.scheme"] = px["scheme"]
        if "hostname" in px:
            updates["proxy.host"] = px["hostname"]
        if "port" in px:
            updates["proxy.port"] = int(px["port"])
        save_config(updates)
        settings.reload()
        return {"success": True, "message": "设置已保存"}
    except Exception as e:
        return {"success": False, "message": f"保存失败: {e}"}


# ========== Telegram Connection ==========

@router.post("/api/test/telegram")
async def test_telegram():
    return await test_connection()


# ========== Telegram Auth (phone login) ==========

@router.get("/api/auth/status")
async def auth_status():
    return await get_auth_status()


@router.post("/api/auth/send-code")
async def auth_send_code(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    phone = body.get("phone", "")
    if not phone:
        return {"success": False, "message": "手机号不能为空"}
    return await send_code(phone)


@router.post("/api/auth/verify-code")
async def auth_verify_code(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    phone = body.get("phone", "")
    code = body.get("code", "")
    if not phone or not code:
        return {"success": False, "message": "手机号和验证码不能为空"}
    return await verify_code(phone, code)


@router.post("/api/auth/check-password")
async def auth_check_password(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    password = body.get("password", "")
    if not password:
        return {"success": False, "message": "密码不能为空"}
    return await check_password(password)


@router.post("/api/auth/logout")
async def auth_logout():
    from app.telegram.client import stop_client
    from app.config import save_config_value
    save_config_value("telegram.session_string", "")
    settings.reload()
    await stop_client()
    return {"success": True, "message": "已退出登录"}


# ========== Channel Rules CRUD ==========

@router.get("/api/channel-rules")
async def list_channel_rules():
    async with async_session() as session:
        result = await session.execute(select(ChannelRule).order_by(ChannelRule.channel_id))
        rules = result.scalars().all()
    return {
        "rules": [{
            "id": r.id,
            "channel_id": r.channel_id,
            "channel_name": r.channel_name or "",
            "category": r.category or "",
            "title_rule": r.title_rule or "",
            "episode_rule": r.episode_rule or "",
            "season": r.season,
            "season_rule": r.season_rule or "",
            "year_rule": r.year_rule or "",
            "filename_rule": r.filename_rule or "",
            "filename_template": r.filename_template or "",
            "enabled": r.enabled,
        } for r in rules]
    }


@router.get("/api/channel-rule")
async def get_channel_rule(channel_id: str):
    async with async_session() as session:
        result = await session.execute(
            select(ChannelRule).where(ChannelRule.channel_id == channel_id)
        )
        r = result.scalar_one_or_none()
    if not r:
        return {"rule": None}
    return {
        "rule": {
            "id": r.id,
            "channel_id": r.channel_id,
            "channel_name": r.channel_name or "",
            "category": r.category or "",
            "title_rule": r.title_rule or "",
            "episode_rule": r.episode_rule or "",
            "season": r.season,
            "season_rule": r.season_rule or "",
            "year_rule": r.year_rule or "",
            "filename_rule": r.filename_rule or "",
            "filename_template": r.filename_template or "",
            "enabled": r.enabled,
        }
    }


@router.post("/api/channel-rule/save")
async def save_channel_rule(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    channel_id = str(body.get("channel_id", "")).strip()
    if not channel_id:
        return {"success": False, "message": "频道 ID 不能为空"}
    try:
        async with async_session() as session:
            result = await session.execute(
                select(ChannelRule).where(ChannelRule.channel_id == channel_id)
            )
            rule = result.scalar_one_or_none()
            if not rule:
                rule = ChannelRule(channel_id=channel_id)
                session.add(rule)
            rule.channel_name = body.get("channel_name", "")
            rule.category = body.get("category", "")
            rule.title_rule = body.get("title_rule", "")
            rule.episode_rule = body.get("episode_rule", "")
            if "season" in body and body["season"] is not None:
                try:
                    rule.season = int(body["season"])
                except (ValueError, TypeError):
                    rule.season = None
            else:
                rule.season = None
            rule.year_rule = body.get("year_rule", "")
            rule.season_rule = body.get("season_rule", "")
            rule.filename_rule = body.get("filename_rule", "")
            rule.filename_template = body.get("filename_template", "")
            rule.enabled = body.get("enabled", True)
            await session.commit()
        return {"success": True, "message": "规则已保存"}
    except Exception as e:
        return {"success": False, "message": f"保存失败: {e}"}


@router.post("/api/channel-rule/delete")
async def delete_channel_rule(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    channel_id = str(body.get("channel_id", "")).strip()
    if not channel_id:
        return {"success": False, "message": "频道 ID 不能为空"}
    try:
        async with async_session() as session:
            await session.execute(
                delete(ChannelRule).where(ChannelRule.channel_id == channel_id)
            )
            await session.commit()
        return {"success": True, "message": "规则已删除"}
    except Exception as e:
        return {"success": False, "message": f"删除失败: {e}"}


# ========== Category CRUD ==========

@router.get("/api/categories")
async def list_categories():
    async with async_session() as session:
        result = await session.execute(
            select(Category).order_by(Category.sort_order, Category.id)
        )
        cats = result.scalars().all()
    return {
        "categories": [{
            "id": c.id,
            "name": c.name,
            "strm_dir": c.strm_dir,
            "sort_order": c.sort_order,
        } for c in cats]
    }


@router.post("/api/category/save")
async def save_category(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    name = str(body.get("name", "")).strip()
    if not name:
        return {"success": False, "message": "分类名称不能为空"}
    strm_dir = str(body.get("strm_dir", "")).strip()
    if not strm_dir:
        strm_dir = name
    try:
        async with async_session() as session:
            cat_id = body.get("id")
            if cat_id:
                result = await session.execute(
                    select(Category).where(Category.id == int(cat_id))
                )
                cat = result.scalar_one_or_none()
                if not cat:
                    return {"success": False, "message": "分类不存在"}
            else:
                result = await session.execute(
                    select(Category).where(Category.name == name)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    return {"success": False, "message": "分类已存在"}
                cat = Category(name=name)
                session.add(cat)
            cat.name = name
            cat.strm_dir = strm_dir
            if "sort_order" in body:
                cat.sort_order = int(body["sort_order"])
            await session.commit()
        return {"success": True, "message": "分类已保存"}
    except Exception as e:
        return {"success": False, "message": f"保存失败: {e}"}


@router.delete("/api/category/{category_id}")
async def delete_category(category_id: int):
    try:
        async with async_session() as session:
            cat = await session.get(Category, category_id)
            if not cat:
                return {"success": False, "message": "分类不存在"}
            await session.delete(cat)
            await session.commit()
        return {"success": True, "message": "分类已删除"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ========== Media CRUD ==========

CATEGORY_MAP = {
    "tv": "电视剧", "movie": "电影", "anime": "动漫",
    "dongman": "动漫", "variety": "综艺", "documentary": "纪录片",
}


@router.get("/api/media")
async def list_media(page: int = 1, page_size: int = 20, category: str = ""):
    async with async_session() as session:
        query = select(Media).order_by(Media.created_at.desc())
        if category:
            db_cat = CATEGORY_MAP.get(category, category)
            query = query.where(Media.category == db_cat)
        count_result = await session.execute(query)
        all_records = count_result.scalars().all()
        total = len(all_records)
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        result = await session.execute(query)
        records = result.scalars().all()
    items = []
    for r in records:
        ch_name = _channel_name_cache.get(r.chat_id, r.chat_id or "")
        items.append({
            "id": r.id,
            "message_id": r.message_id,
            "chat_id": r.chat_id,
            "file_name": r.file_name,
            "size": r.size,
            "duration": r.duration,
            "mime_type": r.mime_type,
            "category": r.category,
            "title": r.title,
            "caption": r.caption,
            "source_link": r.source_link,
            "strm_path": r.strm_path,
            "created_at": str(r.created_at) if r.created_at else None,
            "channel_name": ch_name,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/api/media/unrecognized")
async def list_unrecognized(page: int = 1, page_size: int = 20):
    async with async_session() as session:
        query = select(UnrecognizedMedia).order_by(UnrecognizedMedia.created_at.desc())
        count_result = await session.execute(query)
        total = len(count_result.scalars().all())
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)
        result = await session.execute(query)
        records = result.scalars().all()
    items = [{
        "id": r.id,
        "message_id": r.message_id,
        "chat_id": r.chat_id,
        "file_name": r.file_name,
        "category": r.category,
        "parsed_title": r.parsed_title,
        "caption": r.caption,
        "source_link": r.source_link,
        "reason": r.reason,
        "created_at": str(r.created_at) if r.created_at else None,
    } for r in records]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/api/media/{media_id}")
async def get_media(media_id: int):
    async with async_session() as session:
        r = await session.get(Media, media_id)
    if not r:
        return {"success": False, "message": "记录不存在"}
    return {
        "success": True,
        "item": {
            "id": r.id,
            "message_id": r.message_id,
            "chat_id": r.chat_id,
            "file_name": r.file_name,
            "size": r.size,
            "category": r.category,
            "title": r.title,
            "caption": r.caption,
            "source_link": r.source_link,
            "strm_path": r.strm_path,
            "tmdb_id": r.tmdb_id,
            "tmdb_type": r.tmdb_type,
            "tmdb_title": r.tmdb_title,
            "tmdb_year": r.tmdb_year,
            "nfo_path": r.nfo_path,
            "poster_path": r.poster_path,
            "backdrop_path": r.backdrop_path,
        }
    }


@router.post("/api/media/update")
async def update_media(request: Request):
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
            if "title" in body:
                record.title = body["title"]
            if "category" in body:
                record.category = body["category"]
            if "caption" in body:
                record.caption = body["caption"]
            await session.commit()
        return {"success": True, "message": "已更新"}
    except Exception as e:
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
            result = await session.execute(select(Media).where(Media.id.in_(ids)))
            records = result.scalars().all()
            strm_paths = [r.strm_path for r in records if r.strm_path]
            await session.execute(delete(Media).where(Media.id.in_(ids)))
            await session.commit()
        for path in strm_paths:
            try:
                remove_old_strm(path)
            except Exception:
                pass
        return {"success": True, "message": f"已删除 {len(records)} 条记录"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/media/unrecognized/clear")
async def clear_unrecognized():
    try:
        async with async_session() as session:
            result = await session.execute(select(UnrecognizedMedia))
            records = result.scalars().all()
            await session.execute(delete(UnrecognizedMedia))
            await session.commit()
        return {"success": True, "message": f"已清除 {len(records)} 条未识别记录"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/unrecognized/retry")
async def retry_unrecognized(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    ur_id = body.get("id")
    if not ur_id:
        return {"success": False, "message": "缺少 ID"}
    try:
        async with async_session() as session:
            ur = await session.get(UnrecognizedMedia, ur_id)
            if not ur:
                return {"success": False, "message": "记录不存在"}
            result = await session.execute(
                select(Media).where(
                    Media.message_id == ur.message_id,
                    Media.chat_id == ur.chat_id,
                )
            )
            media = result.scalar_one_or_none()
            if not media:
                return {"success": False, "message": "关联的媒体记录不存在"}
        from app.media.enricher import enrich_media_safe
        enrich_result = await enrich_media_safe(media.id)
        if enrich_result.get("success"):
            async with async_session() as session:
                ur = await session.get(UnrecognizedMedia, ur_id)
                if ur:
                    await session.delete(ur)
                    await session.commit()
            return {"success": True, "message": "TMDB 匹配成功，已从未识别列表移除"}
        return {"success": False, "message": f"重试失败: {enrich_result.get('reason', '未知')}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/unrecognized/delete")
async def delete_unrecognized(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    ur_id = body.get("id")
    if not ur_id:
        return {"success": False, "message": "缺少 ID"}
    try:
        async with async_session() as session:
            ur = await session.get(UnrecognizedMedia, ur_id)
            if ur:
                await session.delete(ur)
                await session.commit()
        return {"success": True, "message": "已删除"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.get("/api/tmdb/config")
async def get_tmdb_config():
    return {
        "tmdb": {
            "api_key": settings.tmdb.api_key,
            "api_base": settings.tmdb.api_base,
            "image_base": settings.tmdb.image_base,
        },
        "emby": {
            "url": settings.emby.url,
            "api_key": settings.emby.api_key,
        },
        "enrich": {
            "enabled": settings.enrich.enabled,
        },
    }


@router.post("/api/tmdb/save")
async def save_tmdb_config(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    try:
        tmdb = body.get("tmdb", {})
        if "api_key" in tmdb:
            save_config_value("tmdb.api_key", tmdb["api_key"])
        if "api_base" in tmdb and tmdb["api_base"]:
            save_config_value("tmdb.api_base", tmdb["api_base"])
        if "image_base" in tmdb and tmdb["image_base"]:
            save_config_value("tmdb.image_base", tmdb["image_base"])
        emby = body.get("emby", {})
        if "url" in emby:
            save_config_value("emby.url", emby["url"])
        if "api_key" in emby:
            save_config_value("emby.api_key", emby["api_key"])
        if "enabled" in body.get("enrich", {}):
            save_config_value("enrich.enabled", body["enrich"]["enabled"])
        settings.reload()
        return {"success": True, "message": "TMDB/Emby 设置已保存"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/api/tmdb/enrich/{media_id}")
async def manual_enrich(media_id: int):
    from app.media.enricher import enrich_media_safe
    result = await enrich_media_safe(media_id)
    return result


@router.post("/api/tmdb/test")
async def test_tmdb_connection(request: Request):
    """服务端代理测试 TMDB：浏览器直连会被 CORS/被墙拦截，必须走服务端。"""
    import httpx
    try:
        body = await request.json()
    except Exception:
        body = {}
    tmdb = body.get("tmdb") or {}
    api_key = tmdb.get("api_key") or settings.tmdb.api_key
    api_base = (tmdb.get("api_base") or settings.tmdb.api_base or "").rstrip("/")
    if not api_key:
        return {"success": False, "message": "未配置 API Key"}
    if not api_base:
        return {"success": False, "message": "未配置 API 地址"}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{api_base}/configuration?api_key={api_key}")
        if r.status_code == 200:
            return {"success": True, "message": "TMDB 连接正常"}
        if r.status_code == 401:
            return {"success": False, "message": "TMDB 返回 401：API Key 无效"}
        return {"success": False, "message": f"TMDB 返回 {r.status_code}，检查反代地址"}
    except Exception as e:
        return {"success": False, "message": f"TMDB 连接失败: {e}"}


@router.post("/api/emby/test")
async def test_emby_connection(request: Request):
    """服务端代理测试 Emby：浏览器连 127.0.0.1 指向的是用户本机，必须走服务端。"""
    import httpx
    try:
        body = await request.json()
    except Exception:
        body = {}
    emby = body.get("emby") or {}
    url = (emby.get("url") or settings.emby.url or "").rstrip("/")
    api_key = emby.get("api_key") or settings.emby.api_key
    if not url or not api_key:
        return {"success": False, "message": "未配置 Emby 地址或 API Key"}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{url}/emby/System/Info", params={"api_key": api_key})
        if r.status_code == 200:
            name = r.json().get("ServerName") or "Emby"
            return {"success": True, "message": f"Emby 连接正常: {name}"}
        if r.status_code == 401:
            return {"success": False, "message": "Emby 返回 401：API Key 无效"}
        return {"success": False, "message": f"Emby 返回 {r.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"Emby 连接失败: {e}"}


@router.post("/api/emby/refresh")
async def manual_emby_refresh():
    from app.media.emby_notifier import refresh_library
    return await refresh_library()


@router.post("/api/tmdb/unrecognized/retry-all")
async def retry_all_unrecognized():
    try:
        async with async_session() as session:
            result = await session.execute(select(UnrecognizedMedia))
            records = result.scalars().all()
        success_count = 0
        for ur in records:
            try:
                result = await session.execute(
                    select(Media).where(
                        Media.message_id == ur.message_id,
                        Media.chat_id == ur.chat_id,
                    )
                )
                media = result.scalar_one_or_none()
                if not media:
                    continue
                from app.media.enricher import enrich_media_safe
                enrich_result = await enrich_media_safe(media.id)
                if enrich_result.get("success"):
                    async with async_session() as s:
                        u = await s.get(UnrecognizedMedia, ur.id)
                        if u:
                            await s.delete(u)
                            await s.commit()
                    success_count += 1
            except Exception:
                continue
        return {"success": True, "message": f"重试完成，成功 {success_count}/{len(records)}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ========== Scan ==========

@router.post("/api/scan/channel")
async def api_scan_channel(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    channel_id = body.get("channel_id")
    limit = int(body.get("limit", 200))
    if not channel_id:
        return {"success": False, "message": "缺少频道 ID"}
    try:
        channel_id = int(channel_id)
    except ValueError:
        return {"success": False, "message": "频道 ID 格式错误"}
    result = await scan_channel(channel_id, limit)
    return result





# ========== Full Import (sequential, one channel per call) ==========

_scan_state: dict = {
    "running": False,
    "rules": [],
    "index": 0,
    "total": 0,
    "done": False,
    "new_records": 0,
    "errors": [],
    "current_channel": "",
    "current_result": "",
}


@router.post("/api/scan/full-import/start")
async def full_import_start():
    from app.media.strm import cleanup_duplicate_strms
    cleanup_duplicate_strms()

    async with async_session() as session:
        result = await session.execute(
            select(ChannelRule).where(ChannelRule.enabled == True)
        )
        rules = result.scalars().all()

    if not rules:
        return {"success": False, "message": "没有已启用的规则"}

    _scan_state["rules"] = [
        (r.channel_name or str(r.channel_id), int(r.channel_id)) for r in rules
    ]
    _scan_state["total"] = len(_scan_state["rules"])
    _scan_state["index"] = 0
    _scan_state["done"] = False
    _scan_state["running"] = True
    _scan_state["new_records"] = 0
    _scan_state["errors"] = []
    _scan_state["current_channel"] = ""
    _scan_state["current_result"] = ""

    return {"success": True, "total": _scan_state["total"]}


@router.post("/api/scan/full-import")
async def full_import_next():
    if not _scan_state.get("running"):
        return {"success": True, "done": True, "message": "未开始"}

    idx = _scan_state["index"]
    rules = _scan_state["rules"]

    if idx >= len(rules):
        _scan_state["done"] = True
        _scan_state["running"] = False
        msg = f"全量入库完成，共新增 {_scan_state['new_records']} 条记录"
        if _scan_state["errors"]:
            msg += f"，失败 {len(_scan_state['errors'])} 个"
        return {"success": True, "done": True, "message": msg}

    name, ch_id = rules[idx]
    _scan_state["current_channel"] = name
    _scan_state["index"] = idx + 1
    _scan_state["current_result"] = ""

    try:
        r = await scan_channel(ch_id, limit=0, notify_detailed=False)
        if r.get("success"):
            match = re.search(r"新增 (\d+) 条", r.get("message", ""))
            added = int(match.group(1)) if match else 0
            _scan_state["new_records"] += added
            _scan_state["current_result"] = r.get("message", "完成")
        else:
            _scan_state["errors"].append({name: r.get("message", "")})
            _scan_state["current_result"] = f"❌ {r.get('message', '')}"
    except Exception as e:
        _scan_state["errors"].append({name: str(e)})
        _scan_state["current_result"] = f"❌ {e}"

    _done = _scan_state["index"] >= _scan_state["total"]
    if _done:
        _scan_state["done"] = True
        _scan_state["running"] = False

    return {
        "success": True,
        "done": _done,
        "completed": _scan_state["index"],
        "total": _scan_state["total"],
        "new_records": _scan_state["new_records"],
        "current": _scan_state["current_channel"],
        "result": _scan_state["current_result"],
    }


@router.get("/api/scan/full-import/status")
async def full_import_status():
    return {
        "running": _scan_state.get("running", False),
        "done": _scan_state.get("done", False),
        "completed": _scan_state.get("index", 0),
        "total": _scan_state.get("total", 0),
        "new_records": _scan_state.get("new_records", 0),
        "current": _scan_state.get("current_channel", ""),
        "result": _scan_state.get("current_result", ""),
        "errors": _scan_state.get("errors", [])[-3:],
    }


# ========== Channels (for rule management) ==========

@router.get("/api/channels")
async def get_channels():
    try:
        client = await get_client()
        monitor_ids = list(settings.telegram.monitor_channels)

        # Also get channels from rules
        async with async_session() as session:
            result = await session.execute(
                select(ChannelRule.channel_id).where(ChannelRule.enabled == True)
            )
            rule_channel_ids = [r[0] for r in result.fetchall()]

        all_ids = list(set(monitor_ids + rule_channel_ids))
        channels = []
        for cid in all_ids:
            try:
                name = await _resolve_channel_name(client, cid)
                channels.append({"id": cid, "name": name})
            except Exception:
                channels.append({"id": cid, "name": cid})
        return {"channels": channels}
    except Exception as e:
        logger.error(f"Get channels error: {e}")
        return {"channels": []}


# ========== Cache ==========

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


# ========== STRM ==========

@router.post("/api/media/regen-strm")
async def regen_strm():
    try:
        from app.media.strm_regen import regen_all
        stats = await regen_all()
        return {
            "success": True,
            "message": (
                f"已重新生成 {stats['regenerated']} 个 STRM 文件"
                f"（多版本 {stats['versioned']} 个，"
                f"清理孤儿 {stats['orphans_removed']} 个，"
                f"失败 {stats['errors']} 个）"
            ),
        }
    except Exception as e:
        logger.error(f"Regen STRM error: {e}")
        return {"success": False, "message": str(e)}


# ========== Notification Settings ==========

@router.post("/api/notification/test")
async def test_media_notification(request: Request):
    """只测试指定的已有记录，使用运行中的 Telegram 连接发送到配置的接收人。"""
    try:
        body = await request.json()
        media_id = int(body.get("media_id", 0))
        if media_id <= 0:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        return {"success": False, "message": "请指定有效的 media_id"}
    if not settings.notify.enabled or not settings.notify.target:
        return {"success": False, "message": "通知未开启或未设置接收人"}
    from app.media.enrich_queue import enrich_queue
    from app.media.enricher import enrich_media_safe
    from app.telegram.bot import send_enriched_batch_notify

    if not await enrich_queue._load_record(media_id):
        return {"success": False, "message": "记录不存在"}
    result = await enrich_media_safe(media_id)
    info = await enrich_queue._load_record(media_id)
    sent = await send_enriched_batch_notify(
        {"ch_name": "通知格式测试"}, [{"media_id": media_id, **result, **info}]
    )
    return {
        "success": sent,
        "media_id": media_id,
        "jav_code": result.get("jav_code", ""),
        "has_poster": bool(info.get("poster_path")),
        "enrich_reason": result.get("reason", ""),
        "message": (
            ("带图测试通知已发送" if info.get("poster_path") else "文字测试通知已发送（未取得封面）")
            if sent else "通知发送失败，请查看服务日志"
        ),
    }


@router.get("/api/notification/settings")
async def get_notification_settings():
    return {
        "notify_enabled": "true" if settings.notify.enabled else "false",
        "notify_target": settings.notify.target,
        "bot_token": bool(settings.notify.bot_token),
    }


@router.post("/api/notification/save")
async def save_notification_settings(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"success": False, "message": "请求解析失败"}
    if "notify_enabled" in body:
        save_config_value("notify.enabled", body["notify_enabled"])
    if "notify_target" in body:
        save_config_value("notify.target", str(body["notify_target"]))
    if "bot_token" in body:
        save_config_value("notify.bot_token", str(body["bot_token"]))
    settings.reload()
    return {"success": True, "message": "通知设置已保存"}
