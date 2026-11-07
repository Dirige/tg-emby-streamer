"""Metatube 18+ 封面/元数据获取。

本地 metatube 容器 (http://127.0.0.1:8082) 的 API：
- 搜索:  GET /v1/movies/search?q=<番号>&limit=6
- 详情:  GET /v1/movies/{provider}/{id}
- 封面:  GET /v1/images/primary/{provider}/{id}  (公开无需鉴权，返回图片本体)

TMDB 没有 18+ 内容，18+ 的海报全走这里。
"""
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from app.config import settings
from app.media.jav import detect_jav_code

logger = logging.getLogger(__name__)

BASE = os.environ.get("METATUBE_URL", "http://127.0.0.1:8082").rstrip("/") + "/v1"


def poster_path(code: str) -> Path:
    """推送封面按番号独立缓存，不读取可能被 Emby 改写的 STRM 海报。"""
    if detect_jav_code(code) != code:
        raise ValueError("Invalid JAV code")
    return Path(settings.cache.dir).parent / "jav_posters" / f"{code}.jpg"


def _norm(s: str | None) -> str:
    return re.sub(r"[-_\s]", "", s or "").upper()


async def _get(url: str, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, params=params)
        if r.status_code != 200:
            logger.debug(f"metatube {url} -> {r.status_code}")
            return None
        return r.json().get("data")
    except Exception as e:
        logger.debug(f"metatube request failed {url}: {e}")
        return None


async def find_movie(code: str) -> dict | None:
    """按番号搜索 metatube。

    返回番号、标题、演员和对应封面地址；未命中返回 None。
    cover 用 metatube 本地代理地址（稳定、无需处理防盗链）。
    """
    if not code:
        return None
    hits = await _get(f"{BASE}/movies/search", {"q": code, "limit": 6}) or []
    if not isinstance(hits, list):
        return None
    for item in hits:
        if not isinstance(item, dict):
            continue
        if _norm(item.get("number")) != _norm(code):
            continue
        provider = item.get("provider")
        mid = item.get("id")
        if not provider or not mid:
            continue
        path = f"{quote(str(provider), safe='')}/{quote(str(mid), safe='')}"
        info = await _get(f"{BASE}/movies/{path}") or {}
        if not isinstance(info, dict):
            info = {}
        # 搜索和详情都必须属于同一番号，不能将近似结果的图/演员配给本片。
        if info.get("number") and _norm(info["number"]) != _norm(code):
            logger.warning("MetaTube detail number mismatch for %s (%s)", code, path)
            continue
        cover = (
            info.get("big_cover_url")
            or info.get("cover_url")
            or item.get("cover_url")
            or ""
        )
        previews = info.get("preview_images") or []
        backdrop = previews[0] if previews else ""
        return {
            "number": item.get("number") or code,
            "title": info.get("title") or item.get("title") or "",
            "actors": info.get("actors") or [],
            "provider": provider,
            "id": mid,
            # 首选 metatube 本地代理封面（稳定、无防盗链），失败可用原生 URL 兜底
            "cover": f"{BASE}/images/primary/{path}",
            "cover_fallback": cover,
            "backdrop": backdrop or cover,
        }
    return None
