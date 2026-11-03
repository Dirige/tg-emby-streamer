import logging
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)


async def _download_image(url: str, dest: Path) -> bool:
    if not url or not dest:
        return False
    if dest.exists():
        return True
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                logger.info(f"Downloaded image: {dest}")
                return True
            logger.warning(f"Image download failed {r.status_code}: {url}")
            return False
    except Exception as e:
        logger.warning(f"Image download error {url}: {e}")
        return False


def _get_target_dir(strm_path: str | None, category: str, tmdb_type: str) -> Path | None:
    if not strm_path:
        return None
    p = Path(strm_path)
    parent = p.parent
    if tmdb_type == "tv" or category in ("电视剧", "动漫", "海外剧", "综艺"):
        while parent.parent != parent and not parent.name.startswith("Season"):
            if (parent.parent / "tvshow.nfo").exists() or parent.parent == parent:
                break
            parent = parent.parent
        if parent.name.startswith("Season") and parent.parent:
            parent = parent.parent
    return parent


async def download_images(
    strm_path: str | None = None,
    category: str = None,
    tmdb_type: str = "movie",
    poster_url: str = None,
    backdrop_url: str = None,
    logo_url: str = None,
) -> dict:
    target = _get_target_dir(strm_path, category, tmdb_type)
    if not target:
        return {"success": False}

    poster_file = target / "poster.jpg"
    backdrop_file = target / "fanart.jpg"
    logo_file = target / "logo.png"

    result = {}
    if poster_url:
        ok = await _download_image(poster_url, poster_file)
        if ok:
            result["poster_path"] = str(poster_file)

    if backdrop_url:
        ok = await _download_image(backdrop_url, backdrop_file)
        if ok:
            result["backdrop_path"] = str(backdrop_file)

    if logo_url:
        ok = await _download_image(logo_url, logo_file)
        if ok:
            result["logo_path"] = str(logo_file)

    result["success"] = True
    return result


async def download_episode_still(
    still_url: str = None,
    strm_path: str = None,
    season: int = 1,
    episode: int = 1,
) -> dict:
    if not still_url or not strm_path:
        return {"success": False}
    p = Path(strm_path)
    season_dir = p.parent
    still_file = season_dir / f"S{season:02d}E{episode:02d}-thumb.jpg"
    ok = await _download_image(still_url, still_file)
    return {"success": ok, "still_path": str(still_file) if ok else None}
