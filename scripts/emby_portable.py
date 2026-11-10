"""Read only Emby's media fields/cache and materialize portable local metadata.

No authentication/users database, watch history or Emby server configuration is
copied. Existing source identity and NFO fields take precedence over Emby data.
"""

import hashlib
import json
import shutil
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

from app.media.jav import detect_jav_code, resolve_jav_code


def image_type(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            data = file.read(16)
        return (data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a"))
                or (data.startswith(b"RIFF") and data[8:12] == b"WEBP"))
    except OSError:
        return False


def provider_ids(value: str | None) -> dict:
    providers = {}
    for part in (value or "").split("|"):
        key, separator, data = part.partition("=")
        if separator and key.lower() in {"jav", "metatube", "tmdb", "imdb", "telegram"}:
            providers[key.lower()] = unquote(data)
    return providers


def images(value: str | None) -> list[tuple[str, str, int]]:
    result = []
    for part in (value or "").split("|"):
        fields = part.split("*")
        if len(fields) >= 3:
            result.append((fields[0], fields[2], sum(entry[1] == fields[2] for entry in result)))
    return result


def cache_directory(config: Path, guid) -> Path | None:
    if not isinstance(guid, bytes) or len(guid) != 16:
        return None
    for value in (uuid.UUID(bytes_le=guid).hex, guid.hex()):
        directory = config / "metadata" / "library" / value[:2] / value / "imagecache"
        if directory.is_dir():
            return directory
    return None


def local_image(config: Path, strm_root: Path, prefix: str, value: str,
                cache: Path | None, kind: str, index: int) -> Path | None:
    path = None
    if value.startswith(prefix.rstrip("/") + "/"):
        path = strm_root / value[len(prefix.rstrip("/")) + 1:]
    elif value.startswith("/config/"):
        path = config / value[len("/config/"):]
    if path and path.is_file() and not path.is_symlink() and image_type(path):
        return path
    if cache:
        names = {"Primary": ["poster", "folder"], "Backdrop": ["backdrop", "fanart"],
                 "Thumb": ["thumb", "landscape"], "Logo": ["logo", "clearlogo"]}.get(kind, [])
        for name in names:
            for suffix in (".jpg", ".png", ".webp", ".jpeg"):
                candidate = cache / (name + (str(index) if index else "") + suffix)
                if candidate.is_file() and image_type(candidate):
                    return candidate
        # Emby may keep the original per-version image filename in imagecache.
        matches = [path for path in cache.iterdir() if path.is_file()
                   and path.suffix.lower() in {".jpg", ".png", ".webp", ".jpeg"}
                   and (kind == "Primary" and "poster" in path.stem.lower())]
        if len(matches) == 1 and image_type(matches[0]):
            return matches[0]
    return None


def trusted_identity(record: dict, item: dict, nfo: ET.Element | None) -> str | None:
    providers = provider_ids(item.get("ProviderIds"))
    if record["category"] == "18+":
        code = resolve_jav_code(record.get("file_name"), record.get("caption"), record.get("title"))
        if not code or providers.get("jav") != code:
            return None
        named = detect_jav_code(item.get("Name"))
        if named and named != code:
            return None
        if nfo is not None:
            source_ids = {entry.get("type", "").lower(): entry.text for entry in nfo.findall("uniqueid")}
            if source_ids.get("jav") not in {None, code}:
                return None
            if providers.get("metatube") and source_ids.get("metatube") != providers["metatube"]:
                return None
        return code
    if record["category"] in {"国产", "无码", "水果派"}:
        identity = f"{record['chat_id']}:{record['message_id']}"
        return identity if providers.get("telegram") == identity else None
    return f"{record['chat_id']}:{record['message_id']}"


def export_emby_metadata(config: Path, prefix: str, portable_db: Path,
                         original_strm: Path, stage: Path, source_config: dict,
                         sanitize_text) -> dict:
    database = config / "data" / "library.db"
    if not database.is_file():
        raise ValueError("指定的 Emby 目录中没有 data/library.db")
    uri = "file:" + quote(str(database), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    conn.row_factory = sqlite3.Row
    required = {"Id", "guid", "Path", "Name", "Images", "ProviderIds"}
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(MediaItems)")}
    if not required <= columns:
        conn.close()
        raise ValueError("Emby 数据库格式不受此导出器支持；没有猜测字段或复制内部数据库")
    fields = [field for field in ("Id", "guid", "Path", "Name", "OriginalTitle", "Overview",
                                 "ProductionYear", "CommunityRating", "OfficialRating", "Images", "ProviderIds")
              if field in columns]
    items = [dict(row) for row in conn.execute("SELECT " + ",".join(fields) + " FROM MediaItems WHERE Path LIKE ? AND Path LIKE '%.strm'",
                                             (prefix.rstrip("/") + "/%",))]
    with sqlite3.connect(portable_db) as media:
        media.row_factory = sqlite3.Row
        records = {str(Path(row["strm_path"]).relative_to("/app/strm")): dict(row)
                   for row in media.execute("SELECT chat_id,message_id,strm_path,category,title,caption,file_name FROM media")}
    accepted = []
    owners = defaultdict(set)
    counts = Counter()
    for item in items:
        relative = item["Path"][len(prefix.rstrip("/")) + 1:]
        record = records.get(relative)
        if not record:
            counts["unmapped_items_skipped"] += 1
            continue
        nfo_path = (stage / "strm" / relative).with_suffix(".nfo")
        root = None
        if nfo_path.is_file():
            try:
                root = ET.parse(nfo_path).getroot()
            except ET.ParseError:
                raise ValueError("已存在的 NFO XML 无效，未覆盖原文件")
        identity = trusted_identity(record, item, root)
        if not identity:
            counts["unverified_identity_skipped"] += 1
            continue
        for value, kind, index in images(item.get("Images")):
            owners[value].add(identity)
        accepted.append((item, relative, record, nfo_path, root, identity))
    people = defaultdict(list)
    if "ItemPeople2" in {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        ids = {item["Id"] for item, *_ in accepted}
        for row in conn.execute("SELECT p.ItemId,m.Name,p.Role,m.Path,m.guid,m.Images FROM ItemPeople2 p JOIN MediaItems m ON m.Id=p.PersonId ORDER BY p.ListOrder"):
            if row["ItemId"] in ids:
                people[row["ItemId"]].append(dict(row))
    output = stage / "emby_metadata"
    output.mkdir()
    with (output / "items.jsonl").open("w", encoding="utf-8") as snapshot:
        for item, relative, record, nfo_path, root, identity in accepted:
            root = root if root is not None else ET.Element("movie")
            # Source identity fields are never replaced with Emby's display title.
            for tag, value in (("title", record.get("title") or item.get("Name")),
                               ("originaltitle", item.get("OriginalTitle")),
                               ("plot", item.get("Overview")), ("year", item.get("ProductionYear")),
                               ("rating", item.get("CommunityRating")), ("mpaa", item.get("OfficialRating"))):
                if root.find(tag) is None and value not in {None, ""}:
                    ET.SubElement(root, tag).text = sanitize_text(str(value), source_config)
            if root.find("uniqueid") is None:
                kind = "jav" if record["category"] == "18+" else "telegram"
                ET.SubElement(root, "uniqueid", type=kind, default="true").text = identity
            cache = cache_directory(config, item.get("guid"))
            image_assets = {}
            parent = nfo_path.parent
            for value, kind, index in images(item.get("Images")):
                if len(owners[value]) > 1:
                    counts["shared_or_wrong_images_skipped"] += 1
                    continue
                source = local_image(config, original_strm, prefix, value, cache, kind, index)
                if not source:
                    counts["images_not_cached_locally"] += 1
                    continue
                stem = {"Primary": "poster", "Backdrop": "fanart", "Thumb": "landscape", "Logo": "logo"}.get(kind)
                if not stem:
                    continue
                if record["category"] in {"国产", "无码", "水果派"}:
                    stem = nfo_path.stem + "-" + stem
                dest = parent / (stem + (str(index) if index else "") + source.suffix.lower())
                if not dest.exists():
                    shutil.copyfile(source, dest)
                    dest.chmod(0o644)
                    counts["additional_cached_images"] += 1
                image_assets[f"{kind}:{index}"] = dest.relative_to(stage).as_posix()
                if kind == "Primary" and root.find("thumb[@aspect='poster']") is None:
                    ET.SubElement(root, "thumb", aspect="poster").text = dest.name
                elif kind == "Backdrop" and root.find("fanart") is None:
                    ET.SubElement(ET.SubElement(root, "fanart"), "thumb").text = dest.name
            actors = []
            for person in people[item["Id"]]:
                name = sanitize_text(person["Name"] or "", source_config)
                if not name:
                    continue
                actors.append({"name": name, "role": sanitize_text(person["Role"] or "", source_config)})
                actor = next((actor for actor in root.findall("actor") if actor.findtext("name") == name), None)
                if actor is None:
                    actor = ET.SubElement(root, "actor")
                    ET.SubElement(actor, "name").text = name
                    if person["Role"]:
                        ET.SubElement(actor, "role").text = sanitize_text(person["Role"], source_config)
                if actor.find("thumb") is not None:
                    continue
                for value, kind, index in images(person["Images"]):
                    if kind != "Primary":
                        continue
                    photo = local_image(config, original_strm, prefix, value,
                                        cache_directory(config, person["guid"]), kind, index)
                    if not photo:
                        continue
                    filename = hashlib.sha256(name.encode()).hexdigest()[:24] + photo.suffix.lower()
                    dest = stage / "strm" / ".actors" / filename
                    dest.parent.mkdir(exist_ok=True)
                    if not dest.exists():
                        shutil.copyfile(photo, dest)
                        dest.chmod(0o644)
                        counts["actor_images"] += 1
                    import os
                    ET.SubElement(actor, "thumb").text = Path(os.path.relpath(dest, nfo_path.parent)).as_posix()
                    break
            ET.indent(root, space="  ")
            text = ET.tostring(root, encoding="unicode")
            nfo_path.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + sanitize_text(text, source_config), encoding="utf-8")
            dto = {"strm_path": relative, "identity": identity,
                   "name": sanitize_text(item.get("Name") or "", source_config),
                   "original_title": sanitize_text(item.get("OriginalTitle") or "", source_config),
                   "overview": sanitize_text(item.get("Overview") or "", source_config),
                   "year": item.get("ProductionYear"), "rating": item.get("CommunityRating"),
                   "provider_ids": provider_ids(item.get("ProviderIds")), "people": actors,
                   "local_images": image_assets}
            snapshot.write(json.dumps(dto, ensure_ascii=False, separators=(",", ":")) + "\n")
            counts["portable_items"] += 1
    conn.rollback()
    conn.close()
    print(f"Emby 资料转出：{counts['portable_items']} 条已核对来源；未复制账户或观看记录。", flush=True)
    return dict(counts)
