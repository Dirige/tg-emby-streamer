import os
import logging
import re
from pathlib import Path
from app.config import settings
from typing import Optional

logger = logging.getLogger(__name__)

STRM_BASE_DIR = Path(settings.strm.output_dir)


def _clean_title(title: str) -> str:
    if not title:
        return "unknown"
    title = re.sub(r"\[.*?\]", "", title)
    title = re.sub(r"[\(\[【（].*?[\)\]】）]", "", title)
    title = re.sub(r"@[a-zA-Z0-9_]+", "", title)
    title = re.sub(r"#\S+", "", title)
    title = re.sub(r"[Ss]\d{1,2}[Ee]\d{1,3}", "", title)
    title = re.sub(r"(?:4K|2160[pP]|1080[pP]|720[pP]|480[pP])", "", title)
    title = re.sub(r"_", " ", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" -_\n")
    return title if title else "unknown"


def generate_strm(
    message_id: int,
    category: str = None,
    title: str = None,
    season: int = None,
    episode: int = None,
    year: int = None,
    episode_digits: int = 2,
    strm_subdir: str = None,
) -> Optional[Path]:
    try:
        if not category:
            category = "unknown"

        strm_dir = STRM_BASE_DIR / (strm_subdir or category)

        if category in ("电视剧", "动漫", "海外剧", "18+"):
            if title:
                clean = _sanitize_filename(_clean_title(title))
                strm_dir = strm_dir / clean
                if season:
                    strm_dir = strm_dir / f"Season {season:02d}"
                strm_dir.mkdir(parents=True, exist_ok=True)
                if season and episode:
                    clean = _clean_title(title)
                    ep_str = f"E{int(episode):0{episode_digits}d}"
                    safe_name = f"{clean}-S{int(season):02d}{ep_str}"
                else:
                    safe_name = _clean_title(title)
            else:
                strm_dir.mkdir(parents=True, exist_ok=True)
                safe_name = f"msg_{message_id}"

        elif category == "电影":
            if title:
                clean = _clean_title(title)
                folder_name = f"{clean} ({year})" if year else clean
                safe_name = folder_name
                strm_dir = strm_dir / _sanitize_filename(folder_name)
            strm_dir.mkdir(parents=True, exist_ok=True)
            if not title:
                safe_name = f"msg_{message_id}"
        else:
            if title:
                clean = _sanitize_filename(_clean_title(title))
                strm_dir = strm_dir / clean
            strm_dir.mkdir(parents=True, exist_ok=True)
            safe_name = _clean_title(title) if title else f"msg_{message_id}"

        safe_name = _sanitize_filename(safe_name)
        strm_file = strm_dir / f"{safe_name}.strm"

        if strm_file.exists():
            logger.debug(f"STRM already exists, reusing: {strm_file}")
            return strm_file

        token = settings.web.stream_token
        token_param = f"?token={token}" if token else ""
        base_url = settings.stream.local_url.rstrip("/")
        stream_url = f"{base_url}/stream/{message_id}{token_param}"
        strm_file.write_text(stream_url, encoding="utf-8")

        logger.debug(f"Generated STRM: {strm_file}")
        return strm_file

    except Exception as e:
        logger.error(f"Failed to generate STRM: {e}")
        return None


def cleanup_duplicate_strms():
    count = 0
    for f in STRM_BASE_DIR.rglob("*_*.strm"):
        m = re.match(r"^(.+)_\d+\.strm$", f.name)
        if m:
            base_name = m.group(1)
            original = f.parent / f"{base_name}.strm"
            if original.exists():
                try:
                    f.unlink()
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to remove duplicate {f}: {e}")
    if count:
        logger.info(f"Cleaned up {count} duplicate STRM files")
    return count


def remove_old_strm(strm_path) -> None:
    if not strm_path:
        return
    try:
        path = Path(strm_path) if isinstance(strm_path, str) else strm_path
        if path.exists() and path.suffix == ".strm":
            path.unlink()
            parent = path.parent
            while parent != STRM_BASE_DIR:
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                    else:
                        break
                except Exception:
                    break
                parent = parent.parent
    except Exception as e:
        logger.warning(f"Failed to remove STRM {strm_path}: {e}")


def _sanitize_filename(name: str, max_bytes: int = 200) -> str:
    if not name:
        return "unknown"
    invalid_chars = "<>:\"/\\|?*"
    for char in invalid_chars:
        name = name.replace(char, "_")
    name = name.strip(". ")
    encoded = name.encode("utf-8")
    if len(encoded) > max_bytes:
        while len(encoded) > max_bytes:
            name = name[:-1]
            encoded = name.encode("utf-8")
    return name if name else "unknown"
