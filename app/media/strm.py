import os
import logging
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from app.config import settings
from app.media.jav import jav_code_conflict, resolve_jav_code
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


def stream_identity(url: str) -> tuple[str | None, int] | None:
    """比较实际来源，忽略 URL 的鉴权 Token；不接受其他路由。"""
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    match = re.fullmatch(r"/stream/(-?\d+)/(\d+)", path)
    if match:
        return match.group(1), int(match.group(2))
    match = re.fullmatch(r"/stream/(\d+)", path)
    return (None, int(match.group(1))) if match else None


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
    file_name: str = None,
    caption: str = None,
    chat_id: str | int = None,
) -> Optional[Path]:
    """计算 STRM 的目标目录与基础文件名（不含多版本后缀）。"""
    if not category:
        category = "unknown"

    strm_dir = STRM_BASE_DIR / (strm_subdir or category)
    safe_name = f"msg_{message_id}"

    if category == "18+":
        code = resolve_jav_code(file_name, caption, title)
        if code:
            return strm_dir / code / f"{code}.strm"
        if jav_code_conflict(file_name, caption):
            safe_name = _sanitize_filename(f"TG{chat_id or 'unknown'}-m{message_id}")
            return strm_dir / "未确认番号" / safe_name / f"{safe_name}.strm"

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

    return strm_dir / f"{_sanitize_filename(safe_name)}.strm"


# 多版本候选后缀：先按分辨率分组（Emby/Jellyfin 同目录 `- 后缀` 命名会合并为多版本），
# 再退化为数字序号。
_VERSION_SUFFIXES = ["- 2", "- 3", "- 4", "- 5", "- 6", "- 7", "- 8", "- 9"]


def _candidate_names(base: str, resolution: str) -> list[str]:
    names = [base]
    if resolution:
        names.append(f"{base} - {resolution}")
    names.extend(f"{base} {s}" for s in _VERSION_SUFFIXES)
    if resolution:
        names.extend(f"{base} - {resolution} {n}" for n in range(2, 10))
    return names


def _write_strm(path: Path, stream_url: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as file:
            temporary = Path(file.name)
            file.write(stream_url)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _jav_version_name(code: str, resolution: str, chat_id, message_id: int,
                      file_name: str | None, caption: str | None) -> str:
    labels = [_sanitize_filename(resolution, max_bytes=24) if resolution else "原画"]
    text = f"{file_name or ''}\n{caption or ''}"
    if re.search(r"中文字幕|中字|中文(?:硬|软)?字幕|CN[-_ ]?SUB", text, re.I):
        labels.append("中文字幕")
    if re.search(r"无码|無碼|破解|UNCENSORED|(?:最新)?流出", text, re.I):
        labels.append("无码")
    labels.append(f"TG{chat_id or 'unknown'}-m{message_id}")
    return f"{code} - {' '.join(labels)}"


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
    file_name: str = None,
    caption: str = None,
) -> Optional[Path]:
    """生成 STRM 文件。

    多版本规则：
    - JAV 按原始番号归入同一目录；第一版起统一为 `番号 - 版本 来源ID.strm`；
    - 目标文件已存在且内容（URL）一致 → 幂等复用，同一链接绝不重复入库；
    - 已存在但内容不同（另一条消息）→ 尝试 `名称 - 1080p.strm` 这类
      分辨率后缀，Emby/Jellyfin 会将同目录下的 `- 后缀` 文件识别为多版本；
    - 后缀也冲突 → 退化为 `- 2`、`- 3` 等序号，绝不覆盖已有版本。
    overwrite=True 时（重生成流程）允许更新 URL；JAV 仍需确认来源身份一致。
    """
    try:
        base_path = compute_strm_path(
            message_id=message_id, category=category, title=title, season=season,
            episode=episode, year=year, episode_digits=episode_digits,
            strm_subdir=strm_subdir, file_name=file_name, caption=caption, chat_id=chat_id,
        )
        strm_dir, safe_name = base_path.parent, base_path.stem
        strm_dir.mkdir(parents=True, exist_ok=True)
        stream_url = build_stream_url(chat_id, message_id)

        code = resolve_jav_code(file_name, caption, title) if category == "18+" else None
        if code:
            # 第一版也必须带「目录名 - 版本」前缀，Emby 才会自动合并。
            # 来源 ID 是稳定后缀：同分辨率、跨频道、超过 8 个版本都不会覆盖。
            name = _jav_version_name(code, resolution or "", chat_id, message_id, file_name, caption)
            chosen = strm_dir / f"{_sanitize_filename(name)}.strm"
            if chosen.exists():
                old_url = chosen.read_text(encoding="utf-8").strip()
                if old_url == stream_url.strip():
                    return chosen
                if stream_identity(old_url) != stream_identity(stream_url):
                    raise ValueError(f"STRM path belongs to another source: {chosen}")
                if not overwrite:
                    return chosen
            _write_strm(chosen, stream_url)
            return chosen

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

        _write_strm(chosen, stream_url)
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
                    # 文件名相近不代表重复；不同链接是需要保留的版本。
                    if f.read_text(encoding="utf-8").strip() != original.read_text(encoding="utf-8").strip():
                        continue
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
