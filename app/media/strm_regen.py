"""STRM 全量重生成。

用途：
- 流媒体 URL 格式升级（/stream/{chat_id}/{message_id} + ?token=...）后，
  重写所有已入库条目的 STRM 文件；
- 多版本归属：同目录同基础名的多个条目，第一条保留基础名，
  其余条目自动落到 ` - 分辨率` / ` - N` 后缀文件，绝不覆盖其它版本；
- JAV 始终按原始番号归目录，第一版也使用 `番号 - 版本 来源ID.strm`；
- 清理不再被任何记录引用的孤儿 STRM 文件。

可由 Web 面板 (/api/media/regen-strm) 或 scripts/regen_strm_all.py 调用。
"""
import logging
import re
from pathlib import Path

from sqlalchemy import select, update

from app.database import async_session
from app.models import Media, Category
from app.media.strm import (
    STRM_BASE_DIR,
    compute_strm_path,
    generate_strm,
    resolution_tag,
)
from app.media.jav import resolve_jav_code
from app.media.nfo_generator import generate_jav_record_nfo

logger = logging.getLogger(__name__)

SEASON_EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")


def _parse_season_episode(record: Media) -> tuple[int | None, int | None]:
    if record.category in ("电视剧", "动漫", "海外剧", "18+"):
        m = SEASON_EP_RE.search(record.title or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


async def regen_all() -> dict:
    stats = {"regenerated": 0, "versioned": 0, "orphans_removed": 0, "errors": 0}

    async with async_session() as session:
        cats = (await session.execute(select(Category))).scalars().all()
        subdir_map = {c.name: c.strm_dir for c in cats if c.strm_dir}
        records = (await session.execute(select(Media).order_by(Media.id))).scalars().all()

    # 1. 按"基础目标路径"分组：同片名/同集的多个记录 → 多版本分配
    groups: dict[str, list[Media]] = {}
    parsed: dict[int, tuple[int | None, int | None]] = {}
    for r in records:
        season, episode = _parse_season_episode(r)
        parsed[r.id] = (season, episode)
        base = compute_strm_path(
            message_id=r.message_id,
            category=r.category,
            title=r.title,
            season=season,
            episode=episode,
            year=r.tmdb_year,
            episode_digits=3 if r.category == "动漫" else 2,
            strm_subdir=subdir_map.get(r.category),
            file_name=r.file_name, caption=r.caption, chat_id=r.chat_id,
        )
        if base is None:
            stats["errors"] += 1
            continue
        groups.setdefault(str(base), []).append(r)

    # 2. 组内第一名获得者重写基础名文件，其余自动落到多版本后缀
    updates: list[dict] = []
    referenced: set[str] = set()
    for base_s, recs in groups.items():
        base_path = Path(base_s)
        # 先移除旧基础名文件（内容是旧 URL，必然变化），让第一条干净重写
        try:
            is_jav = recs[0].category == "18+" and resolve_jav_code(recs[0].file_name, recs[0].caption, recs[0].title)
            if base_path.exists() and not is_jav:
                base_path.unlink()
        except OSError:
            pass

        for i, r in enumerate(recs):
            season, episode = parsed[r.id]
            try:
                strm_path = generate_strm(
                    message_id=r.message_id,
                    chat_id=r.chat_id,
                    category=r.category,
                    title=r.title,
                    season=season,
                    episode=episode,
                    year=r.tmdb_year,
                    episode_digits=3 if r.category == "动漫" else 2,
                    strm_subdir=subdir_map.get(r.category),
                    resolution=resolution_tag(r.width, r.height),
                    file_name=r.file_name, caption=r.caption,
                    overwrite=bool(is_jav),
                )
            except Exception as e:
                logger.warning(f"Regen STRM failed record {r.id}: {e}")
                stats["errors"] += 1
                continue
            if strm_path is None:
                stats["errors"] += 1
                continue
            final_s = str(strm_path)
            referenced.add(final_s)
            updates.append({"id": r.id, "strm_path": final_s})
            if is_jav:
                r.strm_path = final_s
                nfo = generate_jav_record_nfo(r)
                if nfo.get("success"):
                    updates[-1]["nfo_path"] = nfo["nfo_path"]
            if i > 0 and final_s != str(base_path):
                stats["versioned"] += 1
            stats["regenerated"] += 1

    # 3. 批量回写 DB
    if updates:
        async with async_session() as session:
            await session.execute(update(Media), updates)
            await session.commit()

    # 4. 清理孤儿 STRM 文件 + 空目录
    if STRM_BASE_DIR.exists():
        for f in list(STRM_BASE_DIR.rglob("*.strm")):
            if str(f) not in referenced:
                try:
                    f.unlink()
                    stats["orphans_removed"] += 1
                except OSError:
                    pass
        # 自底向上删除空目录（只到分类层为止，不删分类目录本身）
        for d in sorted(STRM_BASE_DIR.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if d.is_dir():
                try:
                    if not any(d.iterdir()):
                        d.rmdir()
                except OSError:
                    pass

    logger.info(f"STRM regen done: {stats}")
    return stats
