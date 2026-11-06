import os
import logging
import re
from pathlib import Path
from app.config import settings
from typing import Optional

logger = logging.getLogger(__name__)

STRM_BASE_DIR = Path(settings.strm.output_dir)


def build_stream_url(chat_id: str | int | None, message_id: int) -> str:
    """生成 STRM 内嵌的流媒体 URL。

    带有 chat_id 的 URL 可以在多频道同名 message_id 时准确定位；
    旧的 /stream/{message_id} 形式保留为兼容回退。
    """
    token = settings.web.stream_token
    token_param = f"?token={token}" if token else ""
    base_url = settings.stream.local_url.rstrip("/")
    if chat_id:
        return f"{base_url}/stream/{chat_id}/{message_id}{token_param}"
    return f"{base_url}/stream/{message_id}{token_param}"


def resolution_tag(width: int, height: int) -> str:
    """根据分辨率生成版本标识（用于多版本命名）。"""
    h = int(height or 0)
    w = int(width or 0)
    if h >= 4288 or w >= 7680:
        return "8K"
    if h >= 2140 or w >= 3840:
        return "2160p"
    if h >= 1400 or w >= 2560:
        return "1440p"
    if h >= 1040 or w >= 1920:
        return "1080p"
    if h >= 700 or w >= 1280:
        return "720p"
    if h > 0 or w > 0:
        return "SD"
    return ""


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


def compute_strm_path(
    message_id: int,
    category: str = None,
    title: str = None,
    season: int = None,
    episode: int = None,
    year: int = None,
    episode_digits: int = 2,
    strm_subdir: str = None,
) -> Optional[Path]:
    """计算 STRM 的目标目录与基础文件名（不含多版本后缀）。"""
    if not category:
        category = "unknown"

    strm_dir = STRM_BASE_DIR / (strm_subdir or category)
    safe_name = f"msg_{message_id}"

    if category in ("电视剧", "动漫", "海外剧", "18+"):
        if title:
            clean = _sanitize_filename(_clean_title(title))
            strm_dir = strm_dir / clean
            if season:
                strm_dir = strm_dir / f"Season {season:02d}"
            if season and episode:
                clean = _clean_title(title)
                ep_str = f"E{int(episode):0{episode_digits}d}"
                safe_name = f"{clean}-S{int(season):02d}{ep_str}"
            else:
                safe_name = _sanitize_filename(_clean_title(title))
    elif category == "电影":
        if title:
            clean = _clean_title(title)
            folder_name = f"{clean} ({year})" if year else clean
            safe_name = folder_name
            strm_dir = strm_dir / _sanitize_filename(folder_name)
    else:
        if title:
            strm_dir = strm_dir / _sanitize_filename(_clean_title(title))
            safe_name = _sanitize_filename(_clean_title(title))

    strm_dir.mkdir(parents=True, exist_ok=True)
    return strm_dir / f"{safe_name}.strm"


# 多版本候选后缀：先按分辨率分组（Emby/Jellyfin 同目录 `- 后缀` 命名会合并为多版本），
# 再退化为数字序号。
_VERSION_SUFFIXES = ["- 2", "- 3", "- 4", "- 5", "- 6", "- 7", "- 8", "- 9"]


def _candidate_names(base: str, resolution: str) -> list[str]:
    names = [base]
    if resolution:
        names.append(f"{base} - {resolution}")
    names.extend(f"{base} {s}" for s in _VERSION_SUFFIXES)
    if resolution:
        names.extend(f"{base} - {resolution} {n}" for n in
                     (" 2", " 3", " 4", " 5", " 6", " 7", " 8", " 9"))
    return names


def generate_strm(
    message_id: int,
    category: str = None,
    title: str = None,
    season: int = None,
    episode: int = None,
    year: int = None,
    episode_digits: int = 2,
    strm_subdir: str = None,
    chat_id: str | int = None,
    resolution: str = None,
    overwrite: bool = False,
) -> Optional[Path]:
    """生成 STRM 文件。

    多版本规则：
    - 目标文件已存在且内容（URL）一致 → 幂等复用，同一链接绝不重复入库；
    - 已存在但内容不同（另一条消息）→ 尝试 `名称 - 1080p.strm` 这类
      分辨率后缀，Emby/Jellyfin 会将同目录下的 `- 后缀` 文件识别为多版本；
    - 后缀也冲突 → 退化为 `- 2`、`- 3` 等序号，绝不覆盖已有版本。
    overwrite=True 时（重生成流程）不同内容会原地覆盖，由调用方保证路径归属。
    """
    try:
        if not category:
            category = "unknown"

        strm_dir = STRM_BASE_DIR / (strm_subdir or category)
        safe_name = f"msg_{message_id}"

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

        elif category == "电影":
            if title:
                clean = _clean_title(title)
                folder_name = f"{clean} ({year})" if year else clean
                safe_name = folder_name
                strm_dir = strm_dir / _sanitize_filename(folder_name)
            strm_dir.mkdir(parents=True, exist_ok=True)
        else:
            if title:
                strm_dir = strm_dir / _sanitize_filename(_clean_title(title))
            strm_dir.mkdir(parents=True, exist_ok=True)
            safe_name = _clean_title(title) if title else f"msg_{message_id}"

        safe_name = _sanitize_filename(safe_name)
        stream_url = build_stream_url(chat_id, message_id)

        candidates = _candidate_names(safe_name, resolution or "")
        chosen: Optional[Path] = None
        for name in candidates:
            candidate = strm_dir / f"{_sanitize_filename(name)}.strm"
            if not candidate.exists():
                chosen = candidate
                break
            try:
                if candidate.read_text(encoding="utf-8").strip() == stream_url.strip():
                    return candidate
            except Exception:
                pass
            if overwrite:
                chosen = strm_dir / f"{_sanitize_filename(safe_name)}.strm"
                break

        if chosen is None:
            import secrets
            chosen = strm_dir / f"{_sanitize_filename(safe_name)} - {secrets.token_hex(4)}.strm"

        chosen.write_text(stream_url, encoding="utf-8")
        logger.debug(f"Generated STRM: {chosen}")
        return chosen

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
