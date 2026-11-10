"""JAV 库定向修复：默认只审计；不检索 Telegram、不通知、不全库重刮。"""
import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import update

from app.config import settings
from app.database import async_session
from app.models import Media
from app.media import metatube
from app.media.emby_versions import (
    fetch_movies, index_records, item_records, load_jav_records, merge_group,
    plan_version_groups, record_code, merge_user_states,
)
from app.media.image_downloader import _download_image, _is_image
from app.media.jav import detect_jav_code
from app.media.jav_metadata import notification_metadata
from app.media.nfo_generator import generate_jav_nfo
from app.media.strm import stream_identity


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def host_path(path: str, prefix: str) -> Path | None:
    try:
        relative = Path(path).relative_to(prefix)
    except ValueError:
        return None
    return Path(settings.strm.output_dir) / relative


async def correct_item(client, user, item, record, movie, output: Path):
    code = record_code(record)
    if not code or not record.strm_path or stream_identity(Path(record.strm_path).read_text().strip()) != (record.chat_id, record.message_id):
        raise ValueError(f"Source identity check failed for media {record.id}")
    response = await client.get(f"Users/{user['Id']}/Items/{item['Id']}")
    response.raise_for_status()
    original = response.json()
    save_json(output / f"before-item-{item['Id']}.json", original)
    metadata = notification_metadata(record, code, movie)
    title = metadata.get("jav_title") or ""
    if title == "未获取" or (detect_jav_code(title) and detect_jav_code(title) != code):
        title = ""
    name = title if title.startswith(code) else f"{code} {title}".strip()
    poster = metatube.poster_path(code)
    has_poster = False
    if movie:
        has_poster = await _download_image(movie["cover"], poster)
        if not has_poster and movie.get("cover_fallback"):
            has_poster = await _download_image(movie["cover_fallback"], poster)
    elif poster.is_file():
        has_poster = _is_image(poster.read_bytes()[:16])

    body = dict(original)
    for field in ("UserData", "MediaSources", "MediaStreams", "ImageTags", "BackdropImageTags", "ExternalUrls", "Etag"):
        body.pop(field, None)
    release = (movie or {}).get("release_date") or ""
    body.update({
        "Name": name, "OriginalTitle": (movie or {}).get("title") or name,
        "SortName": code, "ForcedSortName": code,
        "ProviderIds": {"jav": code}, "LockData": True,
        "LockedFields": sorted(set(original.get("LockedFields") or []) | {
            "Name", "OriginalTitle", "SortName", "Cast", "Overview", "Genres", "Studios", "Tags"}),
        "People": [{"Name": actor, "Type": "Actor"} for actor in metadata.get("actors") or []],
        "Overview": (movie or {}).get("overview") or "",
        "ProductionYear": int(release[:4]) if release[:4].isdigit() else None,
        "PremiereDate": release or None, "OfficialRating": "JP-18+",
        "Genres": (movie or {}).get("genres") or [],
        "Studios": ([{"Name": movie["studio"]}] if movie and movie.get("studio") else []),
        "Tags": [], "Taglines": [], "RemoteTrailers": [],
        "CommunityRating": None, "CriticRating": None,
    })
    if movie and movie.get("provider") and movie.get("id"):
        if detect_jav_code(movie.get("number")) != code:
            raise ValueError(f"MetaTube detail number mismatch for {code}")
        body["ProviderIds"]["MetaTube"] = f"{movie['provider']}:{movie['id']}"
    response = await client.post(f"Items/{item['Id']}", json=body)
    response.raise_for_status()
    images = await client.get(f"Items/{item['Id']}/Images")
    images.raise_for_status()
    existing = images.json()
    for image_type in ("Primary", "Thumb", "Backdrop"):
        if has_poster:
            response = await client.post(f"Items/{item['Id']}/Images/{image_type}",
                                         content=base64.b64encode(poster.read_bytes()),
                                         headers={"Content-Type": "image/jpeg"}, params={"Index": 0})
            response.raise_for_status()
        else:
            for image in existing:
                if image.get("ImageType") == image_type:
                    response = await client.delete(f"Items/{item['Id']}/Images/{image_type}", params={"Index": image.get("ImageIndex", 0)})
                    response.raise_for_status()
    nfo = generate_jav_nfo(record.strm_path, code, title, metadata.get("actors"), movie,
                           str(poster) if has_poster else "")
    if not nfo["success"]:
        raise OSError(f"NFO write failed for media {record.id}")
    async with async_session() as session:
        await session.execute(update(Media).where(Media.id == record.id, Media.strm_path == record.strm_path).values(
            nfo_path=nfo["nfo_path"], poster_path=str(poster) if has_poster else None,
        ))
        await session.commit()
    response = await client.get(f"Users/{user['Id']}/Items/{item['Id']}")
    response.raise_for_status()
    after = response.json()
    if detect_jav_code(after["Name"]) != code or after.get("ProviderIds", {}).get("jav") != code:
        raise ValueError(f"Emby did not retain the correct number for {code}")
    desired = merge_user_states([original.get("UserData") or {}, after.get("UserData") or {}])
    if desired and any((after.get("UserData") or {}).get(key) != value for key, value in desired.items()):
        response = await client.post(f"Users/{user['Id']}/Items/{item['Id']}/UserData", json=desired)
        response.raise_for_status()
    save_json(output / f"after-item-{item['Id']}.json", after)
    return {"item_id": item["Id"], "media_id": record.id, "code": code, "has_poster": has_poster,
            "metatube_id": body["ProviderIds"].get("MetaTube"), "nfo_path": nfo["nfo_path"]}


async def run(args):
    os.umask(0o077)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.merge or args.fix_numbering:
        if not args.backup_dir:
            raise ValueError("Writes require --backup-dir with a verified pre-repair backup")
        backup = Path(args.backup_dir)
        for name in ("tg-media.db", "emby-library.db", "strm-18plus.tar.gz", "emby-metadata.tar.gz"):
            if not (backup / name).is_file() or (backup / name).stat().st_size == 0:
                raise ValueError(f"Required backup is missing: {name}")
    if not settings.emby.url or not settings.emby.api_key:
        raise ValueError("Emby is not configured")
    records = await load_jav_records()
    codes = {detect_jav_code(code) for code in args.code} if args.code else None
    if codes is not None and None in codes:
        raise ValueError("Invalid --code")
    async with httpx.AsyncClient(base_url=f"{settings.emby.url.rstrip('/')}/emby/",
                                 headers={"X-Emby-Token": settings.emby.api_key}, timeout=90) as client:
        items = await fetch_movies(client)
        by_source = index_records(records)
        groups = plan_version_groups(items, records, codes)
        candidates = [group for group in groups.values() if len(group.items) > 1]
        wrong = []
        for item in items:
            refs = item_records(item, by_source)
            if not refs:
                continue
            code = record_code(refs[0])
            named = detect_jav_code(item.get("Name"))
            path = host_path(item.get("Path") or "", args.emby_path_prefix)
            # 只纠正实际存在的来源 STRM，跳过已归档的多余引用和来源歧义。
            if named and named != code and (codes is None or code in codes) and path and path.is_file():
                wrong.append((item, refs[0]))
        plan = {
            "movie_items": len(items), "multi_version_groups": len(candidates),
            "already_merged": sum(group.already_merged for group in candidates),
            "merge_needed": sum(not group.already_merged and group.complete for group in candidates),
            "incomplete_codes": [group.code for group in candidates if not group.complete],
            "wrong_number_items": [{"item_id": item["Id"], "code": record_code(record), "media_id": record.id} for item, record in wrong],
            "source_conflict_ids": [record.id for record in records if not record_code(record) and detect_jav_code(record.file_name) and detect_jav_code(record.caption)],
            "groups": [{"code": group.code, "item_ids": group.item_ids, "source_count": len(group.source_keys),
                        "already_merged": group.already_merged, "complete": group.complete} for group in candidates],
        }
        save_json(output / "plan.json", plan)
        print(json.dumps({key: value for key, value in plan.items() if key != "groups"}, ensure_ascii=False), flush=True)
        if not args.merge and not args.fix_numbering:
            return
        response = await client.get("Users")
        response.raise_for_status()
        users = response.json()
        admin = next(user for user in users if user.get("Policy", {}).get("IsAdministrator"))
        results = []
        if args.fix_numbering:
            movie_cache = {}
            # 用户报告的串号优先处理；其余仅纠正已查实的错号，不重刮正确条目。
            wrong.sort(key=lambda pair: (detect_jav_code(pair[0].get("Name")) != "SKMJ-567", pair[0]["Id"]))
            for item, record in wrong:
                code = record_code(record)
                if code not in movie_cache:
                    movie_cache[code] = await metatube.find_movie(code)
                result = await correct_item(client, admin, item, record, movie_cache[code], output)
                results.append(result)
                with (output / "corrections.jsonl").open("a") as log:
                    log.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(json.dumps(result, ensure_ascii=False), flush=True)
            save_json(output / "correction-summary.json", {"corrected": len(results), "with_poster": sum(result["has_poster"] for result in results)})
        if args.merge:
            # 元数据写入后重读一次，不能使用旧的分组关系。
            items = await fetch_movies(client)
            groups = plan_version_groups(items, records, codes)
            results = []
            for group in sorted(groups.values(), key=lambda group: group.code):
                if len(group.items) < 2 or group.already_merged:
                    continue
                result = await merge_group(client, group, users)
                results.append(result)
                with (output / "merges.jsonl").open("a") as log:
                    log.write(json.dumps(result, ensure_ascii=False) + "\n")
                if len(results) % 100 == 0 or len(group.items) > 8:
                    print(json.dumps({"merged_groups": len(results), "last": result}, ensure_ascii=False), flush=True)
            save_json(output / "merge-summary.json", {"merged_groups": len(results), "sources": sum(result["source_count"] for result in results)})
            print(json.dumps({"merged_groups": len(results), "all_verified": True}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backup-dir")
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--emby-path-prefix", default="/media/tg")
    parser.add_argument("--merge", action="store_true", help="合并已验证的同番号多版本（不移动源文件）")
    parser.add_argument("--fix-numbering", action="store_true", help="仅纠正已查实的 Emby 串号")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
