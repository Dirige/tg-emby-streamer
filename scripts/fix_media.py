import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# Override STRM base path to actual host path (Docker maps /app/strm -> /vol1/1000/SSD/影视/Strm)
HOST_STRM_BASE = Path("/vol1/1000/SSD/影视/Strm")
OLD_STRM_PREFIX = "/app/strm"  # Docker-internal path in existing DB records

from dotenv import load_dotenv
load_dotenv(override=True)

import sqlite3
import logging
import app.media.strm
from app.media.strm import generate_strm

# Override the module-level STRM_BASE_DIR after import
app.media.strm.STRM_BASE_DIR = HOST_STRM_BASE

from app.config import settings
settings.reload()

from app.media.parser import parse_media_info

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_media")

DB_PATH = PROJECT_DIR / "data" / "media.db"


def resolve_strm_path(db_path: str | None) -> Path | None:
    """Translate Docker-internal /app/strm paths to host paths"""
    if not db_path:
        return None
    p = Path(db_path)
    if str(p).startswith("/app/strm"):
        p = HOST_STRM_BASE / str(p.relative_to("/app/strm"))
    return p


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM media ORDER BY id")
    rows = cursor.fetchall()
    total = len(rows)
    logger.info(f"Total records: {total}")

    fixed = 0
    strm_fixed = 0
    old_strms_removed = 0

    for row in rows:
        record = dict(row)
        file_name = record["file_name"]
        caption = record.get("caption") or ""
        chat_id = int(record["chat_id"]) if record["chat_id"] else 0
        old_display = record.get("display_name")
        old_strm = record.get("strm_path")
        old_category = record.get("category")
        old_season = record.get("season")
        old_episode = record.get("episode")

        info = parse_media_info(file_name, caption=caption, channel_id=chat_id)
        if info is None:
            continue

        new_display = info.get("display_name") or info.get("title")
        new_category = info.get("category")
        new_season = info.get("season")
        new_episode = info.get("episode")

        changes = []
        if new_display and new_display != old_display:
            changes.append(f"display: {old_display} -> {new_display}")
        if new_category != old_category:
            changes.append(f"cat: {old_category} -> {new_category}")
        if new_season != old_season:
            changes.append(f"S: {old_season} -> {new_season}")
        if new_episode != old_episode:
            changes.append(f"E: {old_episode} -> {new_episode}")

        new_strm = generate_strm(
            message_id=record["message_id"],
            file_name=file_name,
            category=info.get("category"),
            title=info.get("title"),
            season=info.get("season"),
            episode=info.get("episode"),
            display_name=info.get("display_name"),
        )

        old_strm_host = resolve_strm_path(old_strm)
        new_strm_host = resolve_strm_path(str(new_strm)) if new_strm else None
        strm_changed = bool(new_strm_host and str(new_strm_host) != str(old_strm_host)) if old_strm_host else bool(new_strm_host)

        if changes or strm_changed:
            update_fields = {}
            if new_display and new_display != old_display:
                update_fields["display_name"] = new_display
            if new_category != old_category:
                update_fields["category"] = new_category
            if new_season != old_season:
                update_fields["season"] = new_season
            if new_episode != old_episode:
                update_fields["episode"] = new_episode
            if new_strm:
                update_fields["strm_path"] = str(new_strm)
            if info.get("recognized") is not None:
                update_fields["recognized"] = 1 if info["recognized"] else 0

            if update_fields:
                set_clause = ", ".join(f"{k} = ?" for k in update_fields)
                vals = list(update_fields.values()) + [record["id"]]
                cursor.execute(f"UPDATE media SET {set_clause} WHERE id = ?", vals)
                fixed += 1

            if strm_changed:
                strm_fixed += 1
                if old_strm_host and old_strm_host.exists():
                    old_strm_host.unlink()
                    logger.info(f"Removed old STRM: {old_strm_host}")
                    parent = old_strm_host.parent
                    while parent != HOST_STRM_BASE:
                        try:
                            if not any(parent.iterdir()):
                                parent.rmdir()
                            else:
                                break
                        except Exception:
                            break
                        parent = parent.parent
                    old_strms_removed += 1

            if changes:
                logger.info(f"  [{record['id']}] {', '.join(changes)}")
            if strm_changed:
                logger.info(f"  [{record['id']}] STRM: {old_strm_host or 'N/A'} -> {new_strm_host}")

    conn.commit()
    conn.close()

    logger.info(f"\nDone! Fixed: {fixed}, STRM regenerated: {strm_fixed}, Old STRMs removed: {old_strms_removed}")


if __name__ == "__main__":
    main()
