import re
import json
import logging
import httpx
from sqlalchemy import select
from app.config import settings
from app.database import async_session
from app.models import Media, UnrecognizedMedia
from app.media.nfo_generator import generate_nfo, generate_episode_nfo
from app.media.image_downloader import download_images, download_episode_still

logger = logging.getLogger(__name__)

CATEGORY_TO_TMDB_TYPE = {
    "电影": "movie",
    "电视剧": "tv",
    "动漫": "tv",
    "海外剧": "tv",
    "综艺": "tv",
    "纪录片": "movie",
}


def _build_api_url(path: str) -> str:
    base = settings.tmdb.api_base.rstrip("/")
    return f"{base}{path}"


def _build_image_url(path: str, size: str = "w500") -> str:
    base = settings.tmdb.image_base.rstrip("/")
    return f"{base}/{size}{path}"


async def _tmdb_get(path: str, params: dict = None) -> dict | None:
    api_key = settings.tmdb.api_key
    if not api_key:
        return None
    url = _build_api_url(path)
    p = {"api_key": api_key}
    if params:
        p.update(params)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=p)
            if r.status_code == 200:
                return r.json()
            logger.warning(f"TMDB API {r.status_code} for {url}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.warning(f"TMDB request failed: {e}")
        return None


async def search_movie(title: str, year: int = None) -> dict | None:
    params = {"query": title, "language": "zh-CN"}
    data = await _tmdb_get("/search/movie", params)
    if not data or not data.get("results"):
        params["language"] = "en-US"
        data = await _tmdb_get("/search/movie", params)
    if data and data.get("results"):
        results = data["results"]
        if year:
            for r in results:
                r_year = None
                rd = r.get("release_date", "")
                if rd:
                    try:
                        r_year = int(rd[:4])
                    except (ValueError, IndexError):
                        pass
                if r_year == year:
                    return r
        return results[0] if results else None
    return None


async def search_tv(title: str, year: int = None) -> dict | None:
    params = {"query": title, "language": "zh-CN"}
    data = await _tmdb_get("/search/tv", params)
    if not data or not data.get("results"):
        params["language"] = "en-US"
        data = await _tmdb_get("/search/tv", params)
    if data and data.get("results"):
        results = data["results"]
        if year:
            for r in results:
                r_year = None
                fd = r.get("first_air_date", "")
                if fd:
                    try:
                        r_year = int(fd[:4])
                    except (ValueError, IndexError):
                        pass
                if r_year == year:
                    return r
        return results[0] if results else None
    return None


async def get_movie_details(tmdb_id: int) -> dict | None:
    data = await _tmdb_get(f"/movie/{tmdb_id}", {"language": "zh-CN"})
    if not data:
        data = await _tmdb_get(f"/movie/{tmdb_id}", {"language": "en-US"})
    return data


async def get_tv_details(tmdb_id: int) -> dict | None:
    data = await _tmdb_get(f"/tv/{tmdb_id}", {"language": "zh-CN"})
    if not data:
        data = await _tmdb_get(f"/tv/{tmdb_id}", {"language": "en-US"})
    return data


async def get_movie_images(tmdb_id: int) -> dict | None:
    return await _tmdb_get(f"/movie/{tmdb_id}/images", {"include_image_language": "zh,en,null"})


async def get_tv_images(tmdb_id: int) -> dict | None:
    return await _tmdb_get(f"/tv/{tmdb_id}/images", {"include_image_language": "zh,en,null"})


def _clean_search_title(title: str) -> str:
    if not title:
        return title
    t = re.sub(r"[Ss]\d{1,2}[Ee]\d{1,3}", "", title)
    t = re.sub(r"\s*-\s*$", "", t)
    t = re.sub(r"[_.]\s*part\d+", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(" -_")
    return t if t else title


def _parse_year_from_title(title: str) -> int | None:
    m = re.search(r"\(?(\d{4})\)?$", title)
    if m:
        return int(m.group(1))
    return None


async def enrich_media(media_id: int) -> dict:
    if not settings.tmdb.api_key:
        return {"success": False, "reason": "TMDB API Key 未配置，请在「元数据」页面设置"}

    async with async_session() as session:
        record = await session.get(Media, media_id)
        if not record:
            return {"success": False, "reason": "记录不存在"}
        if record.tmdb_id:
            tmdb_type = record.tmdb_type or CATEGORY_TO_TMDB_TYPE.get(record.category or "", "movie")
            if tmdb_type == "tv" and record.strm_path and record.title:
                ep_result = await enrich_tv_episode(int(record.tmdb_id), record.strm_path, record.title)
                await session.commit()
                return {"success": True, "message": "已存在 TMDB 元数据", "episode": ep_result}
            return {"success": True, "message": "已存在 TMDB 元数据"}

        category = record.category or ""
        # 18+ 由 Emby MetaTube 插件刮削，无需 TMDB 处理
        if category == "18+":
            return {"success": True, "message": "18+ 跳过 TMDB 由 MetaTube 处理", "skipped": True}

        title = record.title or record.file_name or ""
        if not title:
            return {"success": False, "reason": "缺少标题"}

        year = record.tmdb_year
        if not year and record.title:
            year = _parse_year_from_title(_clean_search_title(record.title))

        search_title = _clean_search_title(title)
        tmdb_type = CATEGORY_TO_TMDB_TYPE.get(category, "movie")
        result = None
        if tmdb_type == "movie":
            result = await search_movie(search_title, year)
            if not result:
                result = await search_movie(_clean_search_title(record.file_name or search_title), year)
        else:
            result = await search_tv(search_title, year)
            if not result:
                result = await search_tv(_clean_search_title(record.file_name or search_title), year)

        if not result:
            return {"success": False, "reason": "TMDB 未匹配到结果"}

        tmdb_id = result["id"]
        confidence = result.get("vote_average", 0)

        details = None
        images = None
        if tmdb_type == "movie":
            details = await get_movie_details(tmdb_id)
            images = await get_movie_images(tmdb_id)
        else:
            details = await get_tv_details(tmdb_id)
            images = await get_tv_images(tmdb_id)

        if not details:
            return {"success": False, "reason": "TMDB 详情获取失败"}

        tmdb_title = details.get("title") or details.get("name") or result.get("title") or result.get("name", "")
        tmdb_year = None
        date_str = details.get("release_date") or details.get("first_air_date", "")
        if date_str:
            try:
                tmdb_year = int(date_str[:4])
            except (ValueError, IndexError):
                pass

        record.tmdb_id = tmdb_id
        record.tmdb_type = tmdb_type
        record.tmdb_title = tmdb_title
        record.tmdb_year = tmdb_year
        record.tmdb_data_dict = details

        poster = backdrop = logo = None
        if images:
            posters = images.get("posters", [])
            backdrops = images.get("backdrops", [])
            logos = images.get("logos", [])
            if posters:
                poster = _build_image_url(posters[0]["file_path"], "w500")
            if backdrops:
                backdrop = _build_image_url(backdrops[0]["file_path"], "original")
            if logos:
                logo = _build_image_url(logos[0]["file_path"], "w500")

        nfo_result = generate_nfo(
            strm_path=record.strm_path,
            category=category,
            tmdb_type=tmdb_type,
            tmdb_id=tmdb_id,
            title=tmdb_title,
            original_title=details.get("original_title") or details.get("original_name", ""),
            year=tmdb_year,
            overview=details.get("overview", ""),
            poster_path=poster,
            backdrop_path=backdrop,
            vote_average=details.get("vote_average", 0),
            genres=[g.get("name", "") for g in (details.get("genres") or [])],
        )

        if nfo_result.get("nfo_path"):
            record.nfo_path = str(nfo_result["nfo_path"])

        image_result = await download_images(
            strm_path=record.strm_path,
            category=category,
            tmdb_type=tmdb_type,
            poster_url=poster,
            backdrop_url=backdrop,
            logo_url=logo,
        )

        if image_result.get("poster_path"):
            record.poster_path = str(image_result["poster_path"])
        if image_result.get("backdrop_path"):
            record.backdrop_path = str(image_result["backdrop_path"])

        episode_result = None
        if tmdb_type == "tv" and record.strm_path and record.title:
            episode_result = await enrich_tv_episode(tmdb_id, record.strm_path, record.title)

        await session.commit()

        return {
            "success": True,
            "tmdb_id": tmdb_id,
            "tmdb_type": tmdb_type,
            "tmdb_title": tmdb_title,
            "confidence": confidence,
            "nfo": nfo_result,
            "images": image_result,
            "episode": episode_result,
        }


async def enrich_media_safe(media_id: int) -> dict:
    try:
        return await enrich_media(media_id)
    except Exception as e:
        logger.error(f"Enrich media {media_id} error: {e}")
        return {"success": False, "reason": str(e)}


async def enrich_tv_episode(tmdb_id: int, strm_path: str, title: str) -> dict:
    m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", title)
    if not m:
        return {"success": False}

    season = int(m.group(1))
    episode = int(m.group(2))

    details = await _tmdb_get(f"/tv/{tmdb_id}/season/{season}/episode/{episode}", {"language": "zh-CN"})
    if not details:
        details = await _tmdb_get(f"/tv/{tmdb_id}/season/{season}/episode/{episode}", {"language": "en-US"})

    ep_title = ""
    ep_overview = ""
    if details:
        ep_title = details.get("name", "")
        ep_overview = details.get("overview", "")

    still_url = None
    images = await _tmdb_get(f"/tv/{tmdb_id}/season/{season}/episode/{episode}/images", {"include_image_language": "zh,en,null"})
    if images and images.get("stills"):
        still_path = images["stills"][0]["file_path"]
        still_url = _build_image_url(still_path, "w342")

    img_result = await download_episode_still(still_url, strm_path, season, episode)

    nfo_result = generate_episode_nfo(
        strm_path=strm_path,
        tmdb_id=tmdb_id,
        title=ep_title or f"S{season:02d}E{episode:02d}",
        season=season,
        episode=episode,
        overview=ep_overview,
        still_path=img_result.get("still_path"),
    )

    return {
        "success": True,
        "season": season,
        "episode": episode,
        "still": img_result,
        "nfo": nfo_result,
    }


async def add_to_unrecognized(media_id: int, reason: str = None):
    async with async_session() as session:
        record = await session.get(Media, media_id)
        if not record:
            return
        existing = await session.execute(
            select(UnrecognizedMedia).where(
                UnrecognizedMedia.message_id == record.message_id,
                UnrecognizedMedia.chat_id == record.chat_id,
            )
        )
        if existing.scalar_one_or_none():
            return
        ur = UnrecognizedMedia(
            message_id=record.message_id,
            chat_id=record.chat_id,
            file_name=record.file_name,
            caption=record.caption,
            source_link=record.source_link,
            category=record.category,
            parsed_title=record.title,
            reason=reason,
        )
        session.add(ur)
        await session.commit()
