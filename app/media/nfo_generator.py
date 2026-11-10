import logging
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)
SOURCE_ONLY_CATEGORIES = {"无码", "国产", "水果派"}


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def generate_jav_nfo(
    strm_path: str | None,
    code: str,
    title: str = "",
    actors: list[str] | None = None,
    movie: dict | None = None,
    poster_path: str = "",
) -> dict:
    """番号作为身份；每个 STRM 独立 NFO，同目录的同番号共享一张封面。"""
    from app.media.jav import detect_jav_code
    from app.media.image_downloader import _is_image

    if not strm_path or detect_jav_code(code) != code:
        return {"success": False, "reason": "无 STRM 路径或有效番号"}
    movie = movie or {}
    if movie and detect_jav_code(movie.get("number")) != code:
        return {"success": False, "reason": "MetaTube 番号与来源不一致"}
    path = Path(strm_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element("movie")
        title = (title or "").strip()
        if title == "未获取" or (detect_jav_code(title) and detect_jav_code(title) != code):
            title = ""
        name = title if title.startswith(code) else f"{code} {title}".strip()
        ET.SubElement(root, "title").text = name
        ET.SubElement(root, "sorttitle").text = code
        original = movie.get("title") or name
        if detect_jav_code(original) and detect_jav_code(original) != code:
            original = name
        ET.SubElement(root, "originaltitle").text = original
        ET.SubElement(root, "uniqueid", type="jav", default="true").text = code
        if movie.get("provider") and movie.get("id"):
            ET.SubElement(root, "uniqueid", type="MetaTube", default="false").text = f"{movie['provider']}:{movie['id']}"
        ET.SubElement(root, "mpaa").text = "JP-18+"
        ET.SubElement(root, "lockdata").text = "true"
        ET.SubElement(root, "lockedfields").text = "Name|OriginalTitle|SortName|Overview|Cast"
        for actor in dict.fromkeys(actors or []):
            if actor:
                ET.SubElement(ET.SubElement(root, "actor"), "name").text = actor
        if movie.get("overview"):
            ET.SubElement(root, "plot").text = movie["overview"]
        release = movie.get("release_date") or ""
        if release[:4].isdigit():
            ET.SubElement(root, "year").text = release[:4]
            ET.SubElement(root, "premiered").text = release[:10]
        if movie.get("studio"):
            ET.SubElement(root, "studio").text = movie["studio"]
        for genre in movie.get("genres") or []:
            if isinstance(genre, str) and genre:
                ET.SubElement(root, "genre").text = genre
        library_poster = None
        if poster_path:
            data = Path(poster_path).read_bytes()
            if _is_image(data[:16]):
                # 平铺旧目录不能共用 poster.jpg；标准番号目录则统一一张。
                library_poster = path.parent / ("poster.jpg" if path.parent.name == code else f"{path.stem}-poster.jpg")
                if not library_poster.is_file() or library_poster.read_bytes() != data:
                    _atomic_write(library_poster, data)
                ET.SubElement(root, "thumb", aspect="poster").text = library_poster.name
        nfo = path.with_suffix(".nfo")
        ET.indent(root, space="  ")
        content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        _atomic_write(nfo, content)
        return {"success": True, "nfo_path": str(nfo),
                "library_poster_path": str(library_poster) if library_poster else ""}
    except OSError as exc:
        logger.warning("JAV NFO write failed %s: %s", path, exc)
        return {"success": False, "reason": "JAV NFO/封面写入失败"}


def generate_jav_record_nfo(record, movie: dict | None = None, poster_path: str = "") -> dict:
    from app.media.jav import jav_code_conflict, resolve_jav_code
    from app.media.jav_metadata import notification_metadata

    code = resolve_jav_code(record.file_name, record.caption, record.title)
    if not code:
        return {"success": False, "reason": "原文件名和说明的番号冲突" if jav_code_conflict(record.file_name, record.caption) else "未识别到番号"}
    metadata = notification_metadata(record, code, movie)
    return generate_jav_nfo(record.strm_path, code, metadata.get("jav_title"), metadata.get("actors"), movie, poster_path)


def generate_source_record_nfo(record, poster_path: str = "") -> dict:
    """自定义成人分类使用已核对的原消息信息，不模糊匹配普通电影。"""
    from app.media.image_downloader import _is_image

    if record.category not in SOURCE_ONLY_CATEGORIES or not record.strm_path or not record.title:
        return {"success": False, "reason": "无已确认来源标题或 STRM 路径"}
    path = Path(record.strm_path)
    try:
        root = ET.Element("movie")
        ET.SubElement(root, "title").text = record.title
        ET.SubElement(root, "sorttitle").text = record.title
        ET.SubElement(root, "originaltitle").text = record.title
        ET.SubElement(root, "uniqueid", type="telegram", default="true").text = f"{record.chat_id}:{record.message_id}"
        ET.SubElement(root, "genre").text = record.category
        ET.SubElement(root, "mpaa").text = "18+"
        ET.SubElement(root, "lockdata").text = "true"
        ET.SubElement(root, "lockedfields").text = "Name|OriginalTitle|SortName|Overview|Cast|Genres"
        if record.caption:
            ET.SubElement(root, "plot").text = record.caption
        if poster_path:
            poster = Path(poster_path)
            if poster.parent == path.parent and poster.is_file() and _is_image(poster.read_bytes()[:16]):
                ET.SubElement(root, "thumb", aspect="poster").text = poster.name
        nfo = path.with_suffix(".nfo")
        ET.indent(root, space="  ")
        _atomic_write(nfo, ET.tostring(root, encoding="utf-8", xml_declaration=True))
        return {"success": True, "nfo_path": str(nfo), "source_metadata_only": True}
    except OSError:
        return {"success": False, "reason": "来源 NFO 写入失败"}


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
