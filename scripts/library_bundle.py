#!/usr/bin/env python3
"""只读导出脱敏媒体库，或离线导入到空库；不修改生产服务。"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import yaml
from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
PORTABLE_STRM = Path("/app/strm")
PORTABLE_POSTERS = Path("/app/jav_posters")
PORTABLE_URL = "http://tgemby:8001"
RESOURCE_SUFFIXES = {".strm", ".nfo", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
RULE_FIELDS = ("id", "channel_id", "channel_name", "category", "title_rule", "episode_rule",
               "season", "season_rule", "year_rule", "filename_rule", "filename_template",
               "enabled", "strict_import")
SECRET_KEYS = {"api_hash", "session_string", "password", "stream_token", "api_key", "bot_token"}
STREAM_ROUTE = re.compile(r"/(?:stream|strm)/(-?\d+)/(\d+)")


def atomic_write(path: Path, data: bytes, mode: int = 0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def load_config(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for section in ("storage", "database", "server"):
        if section not in data:
            raise ValueError(f"配置缺少 {section} 段")
    return data


def database_path(data: dict) -> Path:
    url = make_url(data["database"]["url"])
    if url.get_backend_name() != "sqlite" or not url.database:
        raise ValueError("分享工具目前只支持文件型 SQLite 数据库")
    path = Path(url.database)
    return path if path.is_absolute() else BASE_DIR / path


def readonly_database(path: Path):
    conn = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def storage_paths(data: dict) -> tuple[Path, Path]:
    strm = Path(data["storage"]["strm_dir"])
    cache = Path(data["storage"]["cache_dir"])
    if not strm.is_absolute():
        strm = BASE_DIR / strm
    if not cache.is_absolute():
        cache = BASE_DIR / cache
    return strm, cache.parent / "jav_posters"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def secret_values(data: dict) -> list[str]:
    values = []
    for section in data.values():
        if isinstance(section, dict):
            for key, value in section.items():
                if key in SECRET_KEYS and value and len(str(value)) >= 6:
                    values.append(str(value))
    phone = data.get("telegram", {}).get("phone")
    if phone and len(str(phone)) >= 8:
        values.append(str(phone))
    return sorted(set(values), key=len, reverse=True)


def sanitize_text(text: str, data: dict) -> str:
    for value in secret_values(data):
        text = text.replace(value, "[redacted]")
    strm, posters = storage_paths(data)
    for old, new in ((str(strm), str(PORTABLE_STRM)), (str(posters), str(PORTABLE_POSTERS))):
        text = text.replace(old, new)
    for key in ("base_url", "local_url"):
        value = str(data["server"].get(key, "")).rstrip("/")
        if value:
            text = text.replace(value, PORTABLE_URL)
    return text


def portable_nfo_images(text: str, relative: Path) -> str:
    """图片引用使用相对路径，在 TGEmby /app 与 Emby /media 下都有效。"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("发现无效 NFO XML，未覆盖或分享该文件") from None
    changed = False
    parent = (PORTABLE_STRM / relative).parent
    for element in root.iter():
        value = (element.text or "").strip()
        if element.tag not in {"thumb", "filenameandpath", "path"}:
            continue
        if value.startswith(str(PORTABLE_STRM) + "/"):
            element.text = Path(os.path.relpath(Path(value), parent)).as_posix()
            changed = True
    if not changed:
        return text
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")


def stream_identity(value: str) -> tuple[str, int]:
    url = urlsplit(value.strip())
    match = STREAM_ROUTE.fullmatch(url.path)
    if not match or url.scheme not in {"http", "https"}:
        raise ValueError("发现无法确认频道/消息身份的 STRM；未生成不完整数据包")
    return match.group(1), int(match.group(2))


def stream_url(identity: tuple[str, int], base_url: str, token: str = "") -> str:
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("STRM 访问地址无效，或包含不应嵌入的用户名/密码")
    if url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("STRM 访问地址必须是无路径、无查询参数的 HTTP(S) 地址")
    _ = url.port
    chat, message = identity
    return f"{base_url.rstrip('/')}/stream/{chat}/{message}" + ("?token=" + quote(token, safe="") if token else "")


def channels_document(conn, data: dict, links: dict | None = None) -> dict:
    channels = data.get("telegram", {}).get("monitor_channels", []) or []
    if isinstance(channels, str):
        channels = [value.strip() for value in channels.split(",") if value.strip()]
    monitored = set(map(str, channels))
    public_links = {}
    for row in conn.execute("SELECT chat_id,source_link FROM media WHERE source_link LIKE 'https://t.me/%' ORDER BY id DESC"):
        match = re.match(r"https://t\.me/([A-Za-z][A-Za-z0-9_]+)/\d+", row["source_link"] or "")
        if match and match.group(1).lower() not in {"c", "s", "joinchat"}:
            public_links.setdefault(row["chat_id"], "https://t.me/" + match.group(1))
    for channel, link in (links or {}).items():
        parsed = urlsplit(link)
        if parsed.scheme != "https" or parsed.hostname != "t.me" or parsed.query or parsed.fragment:
            raise ValueError("补充的频道链接必须是无查询参数的 https://t.me/… 链接")
        public_links[str(channel)] = link
    counts = dict(conn.execute("SELECT chat_id,COUNT(*) FROM media GROUP BY chat_id").fetchall())
    available = {row["name"] for row in conn.execute("PRAGMA table_info(channel_rules)")}
    fields = [field for field in RULE_FIELDS if field in available]
    rules = []
    for row in conn.execute("SELECT " + ",".join(fields) + " FROM channel_rules ORDER BY id"):
        rule = dict(row)
        rule["enabled"] = bool(rule.get("enabled"))
        rule["strict_import"] = bool(rule.get("strict_import", False))
        rule.update({"monitored": rule["channel_id"] in monitored,
                     "media_count": counts.get(rule["channel_id"], 0),
                     "join_url": public_links.get(rule["channel_id"])})
        rules.append(rule)
    known = {rule["channel_id"] for rule in rules}
    extras = [{"channel_id": channel, "media_count": count, "join_url": public_links.get(channel)}
              for channel, count in counts.items() if channel not in known]
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "monitor_channels": sorted(monitored),
            "categories": [dict(row) for row in conn.execute("SELECT name,strm_dir,sort_order FROM categories ORDER BY sort_order,id")],
            "rules": rules, "media_only_channels": extras}


def channels_markdown(document: dict) -> str:
    lines = ["# 频道清单", "", f"生成时间：{document['generated_at']}", "",
             "> 当前资源主要为成人分类，仅供成年人在合法、获授权的范围内使用。", "",
             "使用你自己的 Telegram 账号加入有需要的来源频道，再登录 TGEmby。",
             "已入库的视频不代表公开可下载：私有频道必须获得有效邀请并加入，历史消息也可能已删除或对新成员不可见。",
             "`https://t.me/c/...` 是加入后才能打开的消息链接，**不是关注/邀请链接**。",
             "链接来自已有消息或维护者提供的信息，不保证永久有效；不要根据频道 ID 猜测邀请链接。", "",
             "## 已保留的全部频道规则", "",
             "| 频道 | 分类 | 频道 ID | 自动监控 | 规则启用 | 入库数 | 加入链接 |",
             "|---|---|---|---|---|---:|---|"]
    for rule in document["rules"]:
        name = (rule.get("channel_name") or "未命名").replace("|", "\\|").replace("\n", " ")
        link = f"[打开频道]({rule['join_url']})" if rule.get("join_url") else "未提供公开链接；请向管理员索取有效邀请"
        lines.append(f"| {name} | {rule.get('category') or ''} | `{rule['channel_id']}` | {'是' if rule['monitored'] else '否'} | {'是' if rule['enabled'] else '否'} | {rule['media_count']} | {link} |")
    if document["media_only_channels"]:
        lines.extend(["", "## 仅有历史入库记录的来源", "", "这些来源不在现有频道规则中，数据包仍保留对应记录；不自动新增监控规则。", ""])
        for channel in document["media_only_channels"]:
            link = channel.get("join_url") or "需要原频道管理员提供邀请链接"
            lines.append(f"- `{channel['channel_id']}`：{channel['media_count']} 条；{link}")
    lines.extend(["", "## 注意", "", "- 原规则的启用/停用状态保持不变，没有重新解释既有来源。",
                  "- 监控列表与规则开关是两项独立设置；停用规则的频道若仍在监控列表会产生提示，可按需移除。",
                  "- 没有加入全部频道时，可以先使用自己已加入频道的资源；其他条目不能凭数据包绕过访问权限。", ""])
    return "\n".join(lines)


def copy_resources(source: Path, target: Path, data: dict) -> dict:
    counts = Counter()
    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return dict(counts)
    for root, directories, files in os.walk(source, followlinks=False):
        directories.sort()
        for directory in directories:
            if Path(root, directory).is_symlink():
                raise ValueError("资源目录含符号链接，不能保证分享边界；请先审计")
        for name in sorted(files):
            path = Path(root, name)
            if path.is_symlink():
                raise ValueError("资源文件含符号链接，未复制指向外部的文件")
            if path.suffix.lower() not in RESOURCE_SUFFIXES:
                counts["excluded_other_files"] += 1
                continue
            relative = path.relative_to(source)
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() == ".strm":
                identity = stream_identity(path.read_text(encoding="utf-8"))
                dest.write_text(stream_url(identity, PORTABLE_URL), encoding="utf-8")
            elif path.suffix.lower() == ".nfo":
                text = sanitize_text(path.read_text(encoding="utf-8"), data)
                dest.write_text(portable_nfo_images(text, relative), encoding="utf-8")
            else:
                shutil.copyfile(path, dest)
            dest.chmod(0o644)
            counts[path.suffix.lower()] += 1
    target.mkdir(parents=True, exist_ok=True)
    return dict(counts)


def sanitize_database(path: Path, data: dict):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT COUNT(*) FROM media WHERE CAST(chat_id AS INTEGER)>0").fetchone()[0]:
            raise ValueError("媒体库含个人私聊来源，不能默认打入分享包；请先确认分享范围")
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        if set(tables) - {"media", "channel_rules", "categories", "unrecognized_media"}:
            raise ValueError("数据库含非媒体业务表，需审计后再分享")
        if "unrecognized_media" in tables:
            # Failed scans are diagnostics, not part of the portable media library.
            conn.execute("DELETE FROM unrecognized_media")
        for table in tables:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                raise ValueError("数据库包含非预期表名")
            columns = [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')
                       if any(kind in row["type"].upper() for kind in ("CHAR", "TEXT"))]
            if not columns:
                continue
            query = 'SELECT rowid AS _rowid,' + ','.join('"' + column + '"' for column in columns) + f' FROM "{table}"'
            cursor = conn.execute(query)
            while rows := cursor.fetchmany(1000):
                for row in rows:
                    changed = {}
                    for column in columns:
                        old = row[column]
                        if column in {"file_id", "file_unique_id"}:
                            new = None  # Fetch fresh references with the recipient's Telegram account.
                        elif isinstance(old, str):
                            new = sanitize_text(old, data)
                        else:
                            new = old
                        if new != old:
                            changed[column] = new
                    if changed:
                        update = ','.join('"' + column + '"=?' for column in changed)
                        conn.execute(f'UPDATE "{table}" SET {update} WHERE rowid=?',
                                     (*changed.values(), row["_rowid"]))
        conn.commit()
        conn.execute("VACUUM")  # Remove old credentials/paths from freed SQLite pages too.


def assert_no_secrets(root: Path, data: dict):
    needles = [value.encode() for value in secret_values(data)]
    strm, posters = storage_paths(data)
    needles.extend(value.encode() for value in (str(strm), str(posters)) if value not in {str(PORTABLE_STRM), str(PORTABLE_POSTERS)})
    for key in ("base_url", "local_url"):
        value = str(data["server"].get(key, "")).rstrip("/")
        if value and value != PORTABLE_URL:
            needles.append(value.encode())
    overlap = max((len(value) for value in needles), default=1)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        previous = b""
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                chunk = previous + block
                if any(value in chunk for value in needles):
                    raise ValueError("分享副本未通过敏感信息检查；不会生成最终压缩包")
                previous = chunk[-overlap:]


def archive_manifest(root: Path, summary: dict) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
    return {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary, "total_bytes": sum(file["size"] for file in files.values()), "files": files}


def export_bundle(config_path: Path, output: Path, *, emby_config: Path | None = None,
                  emby_path_prefix: str = "/media/tg", part_size_mb: int = 0,
                  channel_links: dict | None = None) -> Path:
    if part_size_mb < 0:
        raise ValueError("分卷大小不能为负数")
    data = load_config(config_path)
    source_db = database_path(data)
    source_strm, source_posters = storage_paths(data)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output.chmod(0o700)
    stage = output / ".payload"
    stage.mkdir(mode=0o700)
    db = stage / "config" / "media.db"
    db.parent.mkdir()
    with readonly_database(source_db) as source, sqlite3.connect(db) as target:
        source.backup(target, pages=1000)
    with readonly_database(db) as conn:
        document = channels_document(conn, data, channel_links)
        summary = {"media_count": conn.execute("SELECT COUNT(*) FROM media").fetchone()[0],
                   "rules_count": len(document["rules"]), "monitor_count": len(document["monitor_channels"]),
                   "categories": [dict(row) for row in conn.execute("SELECT category,COUNT(*) count FROM media GROUP BY category")],
                   "excluded": ["Telegram sessions/API credentials", "Web/stream tokens", "Emby accounts/watch history", "video/cache files"]}
    print(f"数据库快照：{summary['media_count']} 条媒体；开始复制 STRM/NFO/图片（生产服务保持运行）。", flush=True)
    sanitize_database(db, data)
    summary["strm_resources"] = copy_resources(source_strm, stage / "strm", data)
    summary["notification_posters"] = copy_resources(source_posters, stage / "jav_posters", data)
    if emby_config:
        from scripts.emby_portable import export_emby_metadata
        summary["emby_metadata"] = export_emby_metadata(emby_config, emby_path_prefix, db,
                                                        source_strm, stage, data, sanitize_text)
    # Metadata added by Emby may also contain author-machine image references.
    for path in (stage / "strm").rglob("*.nfo"):
        old = path.read_text(encoding="utf-8")
        new = portable_nfo_images(old, path.relative_to(stage / "strm"))
        if old != new:
            path.write_text(new, encoding="utf-8")
    atomic_write(stage / "channels.json", json.dumps(document, ensure_ascii=False, indent=2).encode())
    atomic_write(stage / "CHANNELS.md", channels_markdown(document).encode())
    assert_no_secrets(stage, data)
    with readonly_database(db) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("分享数据库未通过完整性检查")
        paths = {row[0] for row in conn.execute("SELECT strm_path FROM media")}
    for value in paths:
        if not value or not (stage / "strm" / Path(value).relative_to(PORTABLE_STRM)).is_file():
            raise ValueError("数据库快照存在缺失的 STRM 文件；未生成部分数据包")
    manifest = archive_manifest(stage, summary)
    atomic_write(stage / "manifest.json", json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode())
    archive = output / "tgemby-library.tar.gz"
    print(f"开始压缩：{len(manifest['files'])} 个文件，{manifest['total_bytes'] / 1024**3:.2f} GiB。", flush=True)
    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        for name in ["manifest.json", *manifest["files"]]:
            info = tar.gettarinfo(str(stage / name), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with (stage / name).open("rb") as file:
                tar.addfile(info, file)
    checksums = [f"{sha256_file(archive)}  {archive.name}"]
    if part_size_mb:
        if part_size_mb < 1:
            raise ValueError("分卷大小必须至少为 1 MiB")
        size = part_size_mb * 1024 * 1024
        with archive.open("rb") as file:
            index = 0
            while block := file.read(min(size, 1024 * 1024)):
                part = output / f"{archive.name}.part{index:03d}"
                remaining = size - len(block)
                with part.open("wb") as dest:
                    dest.write(block)
                    while remaining > 0:
                        chunk = file.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            break
                        dest.write(chunk)
                        remaining -= len(chunk)
                checksums.append(f"{sha256_file(part)}  {part.name}")
                index += 1
    atomic_write(output / "checksums.sha256", ("\n".join(checksums) + "\n").encode())
    atomic_write(output / "manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    shutil.copyfile(stage / "CHANNELS.md", output / "CHANNELS.md")
    shutil.copyfile(stage / "channels.json", output / "channels.json")
    shutil.rmtree(stage)  # Only this invocation's newly-created private staging directory.
    print(f"分享包已生成：{archive}（{archive.stat().st_size / 1024**3:.2f} GiB）；敏感信息检查通过。", flush=True)
    return archive


def safe_archive_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("数据包路径必须是文本")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name:
        raise ValueError("数据包包含不安全的路径")
    allowed = name in {"config/media.db", "channels.json", "CHANNELS.md", "emby_metadata/items.jsonl"}
    allowed |= bool(path.parts and path.parts[0] in {"strm", "jav_posters", "emby_metadata"}
                    and path.suffix.lower() in RESOURCE_SUFFIXES)
    if not allowed:
        raise ValueError("数据包包含非预期文件，拒绝导入")
    return name


def extract_verified(archive: Path, stage: Path, max_bytes: int = 100 * 1024**3) -> dict:
    with tarfile.open(archive, "r|gz") as tar:
        first = tar.next()
        if not first or first.name != "manifest.json" or not first.isfile() or first.size > 64 * 1024**2:
            raise ValueError("数据包缺少有效的首部 manifest.json")
        manifest = json.load(tar.extractfile(first))
        if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), dict):
            raise ValueError("不支持的数据包版本")
        files = manifest["files"]
        if len(files) > 2_000_000:
            raise ValueError("数据包文件数量超过安全上限")
        total = 0
        for name, info in files.items():
            safe_archive_name(name)
            if not isinstance(info, dict):
                raise ValueError("文件清单结构无效")
            size = info.get("size")
            if not isinstance(size, int) or size < 0 or size > 512 * 1024**2:
                raise ValueError("数据包包含过大的单个文件")
            if not re.fullmatch(r"[0-9a-f]{64}", str(info.get("sha256", ""))):
                raise ValueError("文件校验值无效")
            total += size
        if total != manifest.get("total_bytes") or total > max_bytes:
            raise ValueError("数据包解压大小无效或超过安全上限")
        if shutil.disk_usage(stage).free < 2 * total + 256 * 1024**2:
            raise ValueError("可用磁盘不足；解压校验及写入需要约两倍解压大小的空间")
        seen = set()
        tar.members.clear()
        for member in tar:
            if not member.isfile():
                raise ValueError("数据包含目录、链接或设备文件，拒绝导入")
            name = safe_archive_name(member.name)
            if name in seen or name not in files or member.size != files[name]["size"]:
                raise ValueError("数据包成员重复或与清单不一致")
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with tar.extractfile(member) as source, dest.open("xb") as target:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(block)
                    digest.update(block)
            dest.chmod(0o644)
            if digest.hexdigest() != files[name]["sha256"]:
                raise ValueError("数据包文件 SHA256 校验失败")
            seen.add(name)
            tar.members.clear()
        if seen != set(files):
            raise ValueError("数据包缺少清单中的文件")
    for required in ("config/media.db", "channels.json", "CHANNELS.md"):
        if required not in seen:
            raise ValueError("数据包缺少必要的数据库或频道清单")
    return manifest


def rebase_library(data: dict, *, strm_root: Path | None = None, db_path: Path | None = None) -> int:
    strm, posters = storage_paths(data)
    physical_strm = strm_root if strm_root is not None else strm
    # Do this before changing any files, including when the library is empty.
    stream_url(("-1001", 1), data["server"]["local_url"], str(data["server"].get("stream_token", "")))
    count = 0
    if physical_strm.exists():
        for path in physical_strm.rglob("*.strm"):
            if path.is_symlink():
                raise ValueError("STRM 中存在符号链接，拒绝批量更新")
            old = path.read_text(encoding="utf-8").strip()
            new = stream_url(stream_identity(old), data["server"]["local_url"],
                             str(data["server"].get("stream_token", "")))
            if new != old:
                atomic_write(path, new.encode())
            count += 1
        for path in physical_strm.rglob("*.nfo"):
            if path.is_symlink():
                raise ValueError("NFO 中存在符号链接，拒绝批量更新")
            old = path.read_text(encoding="utf-8")
            new = portable_nfo_images(old, path.relative_to(physical_strm))
            if new != old:
                atomic_write(path, new.encode())
    db = db_path if db_path is not None else database_path(data)
    if db.exists():
        with sqlite3.connect(db) as conn:
            fields = {row[1] for row in conn.execute("PRAGMA table_info(media)")}
            for field in ("strm_path", "nfo_path", "poster_path", "backdrop_path"):
                if field not in fields:
                    continue
                for old_root, new_root in ((PORTABLE_STRM, strm), (PORTABLE_POSTERS, posters)):
                    if old_root == new_root:
                        continue
                    prefix = str(old_root) + "/"
                    conn.execute(f"UPDATE media SET {field}=? || substr({field},?) WHERE substr({field},1,?)=?",
                                 (str(new_root) + "/", len(prefix) + 1, len(prefix), prefix))
    return count


def import_bundle(archive: Path, config_path: Path, *, expected_sha256: str | None = None,
                  replace_empty: bool = False) -> dict:
    data = load_config(config_path)
    if expected_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("SHA256 必须是 64 位十六进制校验值")
    target_db = database_path(data)
    strm, posters = storage_paths(data)
    if target_db.is_symlink():
        raise ValueError("目标数据库不能是符号链接")
    if (target_db.parent / "emby_metadata").exists():
        raise ValueError("目标目录已有 Emby 便携元数据，请使用新的空目录")
    if any(target_db.with_name(target_db.name + suffix).exists() for suffix in ("-wal", "-shm")):
        raise ValueError("目标数据库仍有 WAL/共享内存文件；请停止 TGEmby 后再导入")
    for path in (strm, posters):
        if path.is_symlink() or (path.exists() and any(path.iterdir())):
            raise ValueError("目标 STRM/封面目录不是空目录；请使用新目录，避免覆盖既有库")
    if target_db.exists():
        if not replace_empty:
            raise ValueError("目标数据库已存在；使用新目录，或明确指定 --replace-empty（只允许空媒体库且会备份）")
        with readonly_database(target_db) as conn:
            if conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]:
                raise ValueError("目标媒体库非空；分享包不会覆盖已有媒体库")
    if expected_sha256 and sha256_file(archive) != expected_sha256.lower():
        raise ValueError("压缩包 SHA256 与维护者提供的值不一致")
    target_db.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".library-import-", dir=target_db.parent) as directory:
        stage = Path(directory)
        manifest = extract_verified(archive, stage)
        with readonly_database(stage / "config/media.db") as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("数据库完整性检查失败")
            if conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] != manifest["summary"]["media_count"]:
                raise ValueError("数据库记录数与清单不一致")
            for row in conn.execute("SELECT chat_id,message_id,strm_path FROM media"):
                if not row["strm_path"]:
                    raise ValueError("数据库含空 STRM 路径")
                try:
                    relative = Path(row["strm_path"]).relative_to(PORTABLE_STRM)
                except ValueError:
                    raise ValueError("数据库含不属于分享库的 STRM 路径") from None
                safe_archive_name("strm/" + relative.as_posix())
                path = stage / "strm" / relative
                if not path.is_file() or stream_identity(path.read_text()) != (row["chat_id"], row["message_id"]):
                    raise ValueError("STRM 与数据库的原始来源身份不一致")
        document = json.loads((stage / "channels.json").read_text(encoding="utf-8"))
        if document.get("schema_version") != 1 or not isinstance(document.get("monitor_channels"), list):
            raise ValueError("频道清单结构无效")
        if not all(isinstance(value, str) and re.fullmatch(r"-100\d+", value)
                   for value in document["monitor_channels"]):
            raise ValueError("频道清单包含无效 ID")
        data.setdefault("telegram", {})["monitor_channels"] = ",".join(document["monitor_channels"])
        # Rewrite only the verified staging copy. A failed validation must not
        # leave a recipient with a half-imported database or invalid STRM URLs.
        count = rebase_library(data, strm_root=stage / "strm", db_path=stage / "config/media.db")
        if target_db.exists():
            backup = target_db.with_name("media-before-import-" + datetime.now().strftime("%Y%m%d%H%M%S") + ".db")
            with readonly_database(target_db) as source, sqlite3.connect(backup) as target:
                source.backup(target)
            backup.chmod(0o600)
        for source, destination in ((stage / "strm", strm), (stage / "jav_posters", posters)):
            destination.mkdir(parents=True, exist_ok=True)
            if source.exists():
                shutil.copytree(source, destination, dirs_exist_ok=True)
        temporary_db = target_db.with_name(".media-import.db")
        shutil.copyfile(stage / "config/media.db", temporary_db)
        temporary_db.chmod(0o600)
        os.replace(temporary_db, target_db)
        for name in ("CHANNELS.md", "channels.json"):
            shutil.copyfile(stage / name, target_db.parent / name)
        if (stage / "emby_metadata").exists():
            shutil.copytree(stage / "emby_metadata", target_db.parent / "emby_metadata", dirs_exist_ok=False)
        atomic_write(target_db.parent / "import-manifest.json", json.dumps(manifest, ensure_ascii=False).encode(), mode=0o600)
    # Keep the recipient's Telegram account, passwords and notification settings.
    atomic_write(config_path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode(), mode=0o600)
    print(f"导入成功：{manifest['summary']['media_count']} 条媒体、{count} 个 STRM；已保留接收者自己的登录配置。")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="只读取生产库，在新的私有目录生成分享包")
    export.add_argument("--config", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--emby-config", type=Path)
    export.add_argument("--emby-path-prefix", default="/media/tg")
    export.add_argument("--part-size-mb", type=int, default=0)
    export.add_argument("--channel-links", type=Path)
    restore = sub.add_parser("import", help="导入到空目录；不会覆盖非空媒体库")
    restore.add_argument("--config", type=Path, default=Path(os.environ.get("CONFIG_PATH", "/app/config/config.yaml")))
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--sha256")
    restore.add_argument("--replace-empty", action="store_true")
    channels = sub.add_parser("channels", help="生成不含凭据的频道规则/加入链接清单")
    channels.add_argument("--config", type=Path, required=True)
    channels.add_argument("--output", type=Path, required=True)
    channels.add_argument("--channel-links", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "export":
            links = json.loads(args.channel_links.read_text()) if args.channel_links else None
            export_bundle(args.config, args.output, emby_config=args.emby_config,
                          emby_path_prefix=args.emby_path_prefix, part_size_mb=args.part_size_mb,
                          channel_links=links)
        elif args.command == "import":
            import_bundle(args.input, args.config, expected_sha256=args.sha256,
                          replace_empty=args.replace_empty)
        else:
            data = load_config(args.config)
            links = json.loads(args.channel_links.read_text()) if args.channel_links else None
            with readonly_database(database_path(data)) as conn:
                document = channels_document(conn, data, links)
            args.output.mkdir(parents=True, exist_ok=True)
            atomic_write(args.output / "channels.json", json.dumps(document, ensure_ascii=False, indent=2).encode())
            atomic_write(args.output / "CHANNELS.md", channels_markdown(document).encode())
            print(f"已导出 {len(document['rules'])} 条频道规则及加入链接信息。")
    except Exception as exc:
        print(str(exc) if isinstance(exc, ValueError) else f"操作失败（{type(exc).__name__}）；检查路径、权限与可用磁盘。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
