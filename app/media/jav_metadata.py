"""通知使用的 JAV 字段：优先原消息文案，且不混用其他番号的 NFO。"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from app.media.jav import detect_jav_code


def _clean_title(title: str, code: str) -> str:
    prefix = re.escape(code).replace(r"\-", r"[-_ ]?")
    title = re.sub(rf"^\s*{prefix}\s*[:：\-]?\s*", "", title, flags=re.I)
    return re.sub(r"\.(mp4|mkv|avi|mov)$", "", title, flags=re.I).strip()


def _actors(value) -> list[str]:
    if isinstance(value, str):
        tags = re.findall(r"#([^\s#，,、/]+)", value)
        values = tags or re.split(r"[,，、;；/]+", value)
    elif isinstance(value, list):
        values = [v.get("name", "") if isinstance(v, dict) else v for v in value]
    else:
        return []
    return list(dict.fromkeys(v.strip().lstrip("#") for v in values if isinstance(v, str) and v.strip()))


def parse_caption(caption: str, code: str) -> dict:
    detected = detect_jav_code(caption)
    if detected and detected != code:
        return {}
    fields = {}
    for line in (caption or "").splitlines():
        match = re.search(r"(?:标题|標題|片名|作品名|Title)\s*[:：]\s*(.+)", line, re.I)
        if match:
            fields["title"] = _clean_title(match.group(1), code)
        match = re.search(r"(?:演员|演員|女优|女優|主演|Actors?)\s*[:：]\s*(.+)", line, re.I)
        if match:
            fields["actors"] = _actors(match.group(1))
    return fields


def read_nfo(strm_path: str | None, code: str) -> dict:
    if not strm_path:
        return {}
    path = Path(strm_path).with_suffix(".nfo")
    try:
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return {}
        root = ET.parse(path).getroot()
        title = root.findtext("title") or ""
        detected = detect_jav_code(title)
        if detected and detected != code:
            return {}
        return {
            "title": _clean_title(root.findtext("originaltitle") or title, code),
            "actors": _actors([node.text or "" for node in root.findall("actor/name")]),
        }
    except (OSError, ET.ParseError):
        return {}


def _filename_actor(file_name: str, code: str) -> list[str]:
    """仅接受常见的「番号 单个短演员名」文件名，不将整段片名猜成演员。"""
    if detect_jav_code(file_name) != code:
        return []
    value = _clean_title(file_name, code)
    if re.fullmatch(r"[A-Za-z]{3,16}|[\u3040-\u30ff\u4e00-\u9fff]{2,6}", value):
        if value.upper() not in {"HEVC", "HDR", "UNCENSORED", "LEAK", "SUB", "FHD", "VIDEO"}:
            return [value]
    return []


def notification_metadata(record, code: str, movie: dict | None = None) -> dict:
    movie = movie or {}
    caption = parse_caption(record.caption or "", code)
    nfo = read_nfo(record.strm_path, code)
    title = caption.get("title") or nfo.get("title") or movie.get("title")
    actors = (
        caption.get("actors") or nfo.get("actors") or _actors(movie.get("actors"))
        or _filename_actor(record.file_name or "", code)
    )
    return {
        "jav_code": code,
        "jav_title": title or _clean_title(record.title or record.file_name or "", code) or "未获取",
        "actors": actors,
    }
