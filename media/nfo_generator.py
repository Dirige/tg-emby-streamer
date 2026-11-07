import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_strm_dir(strm_path: str | None) -> Path | None:
    if not strm_path:
        return None
    p = Path(strm_path)
    return p.parent


def _sanitize_xml(text: str) -> str:
    if not text:
        return ""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace("\"", "&quot;")
    text = text.replace("'", "&apos;")
    return text


def generate_movie_nfo(
    strm_path: str | None,
    tmdb_id: int,
    title: str,
    original_title: str = "",
    year: int = None,
    overview: str = "",
    poster_path: str = "",
    backdrop_path: str = "",
    vote_average: float = 0,
    genres: list[str] = None,
) -> dict:
    strm_dir = _get_strm_dir(strm_path)
    if not strm_dir:
        return {"success": False, "reason": "无 STRM 路径"}

    nfo_file = strm_dir / "movie.nfo"
    g = genres or []
    genre_xml = "\n".join(f"    <genre>{_sanitize_xml(gn)}</genre>" for gn in g) if g else ""

    xml = '<?xml version="1.0" encoding="utf-8"?>\n'
    xml += "<movie>\n"
    xml += f"  <title>{_sanitize_xml(title)}</title>\n"
    if original_title:
        xml += f"  <originaltitle>{_sanitize_xml(original_title)}</originaltitle>\n"
    if year:
        xml += f"  <year>{year}</year>\n"
    if overview:
        xml += f"  <plot>{_sanitize_xml(overview)}</plot>\n"
        xml += f"  <outline>{_sanitize_xml(overview)}</outline>\n"
    if tmdb_id:
        xml += f'  <uniqueid type="tmdb" default="true">{tmdb_id}</uniqueid>\n'
    if vote_average:
        xml += f"  <rating>{vote_average}</rating>\n"
    if poster_path:
        poster_file = strm_dir / "poster.jpg"
        xml += f'  <thumb aspect="poster">{_sanitize_xml(poster_file.name)}</thumb>\n'
    if backdrop_path:
        xml += "  <fanart>\n"
        fanart_file = strm_dir / "fanart.jpg"
        xml += f"    <thumb>{_sanitize_xml(fanart_file.name)}</thumb>\n"
        xml += "  </fanart>\n"
    if genre_xml:
        xml += f"{genre_xml}\n"
    xml += "</movie>"

    nfo_file.write_text(xml, encoding="utf-8")
    logger.info(f"Generated movie NFO: {nfo_file}")
    return {"success": True, "nfo_path": str(nfo_file)}


def generate_tvshow_nfo(
    strm_path: str | None,
    tmdb_id: int,
    title: str,
    original_title: str = "",
    year: int = None,
    overview: str = "",
    poster_path: str = "",
    backdrop_path: str = "",
    vote_average: float = 0,
    genres: list[str] = None,
) -> dict:
    strm_dir = _get_strm_dir(strm_path)
    if not strm_dir:
        return {"success": False, "reason": "无 STRM 路径"}
    show_dir = strm_dir
    while show_dir.parent != show_dir and not show_dir.name.startswith("Season"):
        if (show_dir.parent / "tvshow.nfo").exists() or show_dir.parent == show_dir:
            break
        show_dir = show_dir.parent
    if show_dir.name.startswith("Season") and show_dir.parent:
        show_dir = show_dir.parent

    nfo_file = show_dir / "tvshow.nfo"
    g = genres or []
    genre_xml = "\n".join(f"    <genre>{_sanitize_xml(gn)}</genre>" for gn in g) if g else ""

    xml = '<?xml version="1.0" encoding="utf-8"?>\n'
    xml += "<tvshow>\n"
    xml += f"  <title>{_sanitize_xml(title)}</title>\n"
    if original_title:
        xml += f"  <originaltitle>{_sanitize_xml(original_title)}</originaltitle>\n"
    if year:
        xml += f"  <year>{year}</year>\n"
    if overview:
        xml += f"  <plot>{_sanitize_xml(overview)}</plot>\n"
        xml += f"  <outline>{_sanitize_xml(overview)}</outline>\n"
    if tmdb_id:
        xml += f'  <uniqueid type="tmdb" default="true">{tmdb_id}</uniqueid>\n'
    if vote_average:
        xml += f"  <rating>{vote_average}</rating>\n"
    if poster_path:
        poster_file = show_dir / "poster.jpg"
        xml += f'  <thumb aspect="poster">{_sanitize_xml(poster_file.name)}</thumb>\n'
    if backdrop_path:
        xml += "  <fanart>\n"
        fanart_file = show_dir / "fanart.jpg"
        xml += f"    <thumb>{_sanitize_xml(fanart_file.name)}</thumb>\n"
        xml += "  </fanart>\n"
    if genre_xml:
        xml += f"{genre_xml}\n"
    xml += "</tvshow>"

    nfo_file.write_text(xml, encoding="utf-8")
    logger.info(f"Generated TV NFO: {nfo_file}")
    return {"success": True, "nfo_path": str(nfo_file)}


def generate_nfo(
    strm_path: str | None = None,
    category: str = None,
    tmdb_type: str = "movie",
    tmdb_id: int = None,
    title: str = "",
    original_title: str = "",
    year: int = None,
    overview: str = "",
    poster_path: str = "",
    backdrop_path: str = "",
    vote_average: float = 0,
    genres: list[str] = None,
) -> dict:
    if tmdb_type == "tv" or category in ("电视剧", "动漫", "海外剧", "综艺"):
        return generate_tvshow_nfo(
            strm_path, tmdb_id, title, original_title, year,
            overview, poster_path, backdrop_path, vote_average, genres,
        )
    return generate_movie_nfo(
        strm_path, tmdb_id, title, original_title, year,
        overview, poster_path, backdrop_path, vote_average, genres,
    )


def generate_episode_nfo(
    strm_path: str = None,
    tmdb_id: int = None,
    title: str = "",
    season: int = 1,
    episode: int = 1,
    overview: str = "",
    still_path: str = None,
) -> dict:
    if not strm_path:
        return {"success": False, "reason": "无 STRM 路径"}
    p = Path(strm_path)
    season_dir = p.parent
    nfo_file = season_dir / f"S{season:02d}E{episode:02d}.nfo"

    xml = '<?xml version="1.0" encoding="utf-8"?>\n'
    xml += "<episodedetails>\n"
    xml += f"  <title>{_sanitize_xml(title)}</title>\n"
    if tmdb_id:
        xml += f'  <uniqueid type="tmdb" default="true">{tmdb_id}</uniqueid>\n'
    xml += f"  <season>{season}</season>\n"
    xml += f"  <episode>{episode}</episode>\n"
    if overview:
        xml += f"  <plot>{_sanitize_xml(overview)}</plot>\n"
    if still_path:
        still_file = Path(still_path)
        xml += f'  <thumb aspect="thumb">{_sanitize_xml(still_file.name)}</thumb>\n'
    xml += "</episodedetails>"

    nfo_file.write_text(xml, encoding="utf-8")
    logger.info(f"Generated episode NFO: {nfo_file}")
    return {"success": True, "episode_nfo_path": str(nfo_file)}
