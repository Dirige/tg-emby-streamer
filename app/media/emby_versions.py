"""仅按 Telegram 原始番号合并 Emby 多版本，不按片名、海报或 ProviderId 合并。"""
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.media.jav import resolve_jav_code
from app.media.strm import stream_identity
from app.models import Media

logger = logging.getLogger(__name__)
IDENTITY_LOCK_FIELDS = {"Name", "OriginalTitle", "SortName", "Overview", "Cast"}


def value(record, name: str, default=None):
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def record_code(record) -> str | None:
    if value(record, "category", "18+") != "18+":
        return None
    return resolve_jav_code(value(record, "file_name"), value(record, "caption"), value(record, "title"))


def index_records(records: list) -> dict[tuple[str, int], object]:
    return {(str(value(record, "chat_id")), int(value(record, "message_id"))): record
            for record in records if value(record, "category", "18+") == "18+"}


def item_records(item: dict, by_source: dict) -> list | None:
    """所有可播放来源都必须已知且番号一致；未知或冲突的来源一律跳过。"""
    if item.get("Type") != "Movie" or not item.get("MediaSources") or (item.get("PartCount") or 1) > 1:
        return None
    records = []
    for source in item["MediaSources"]:
        key = stream_identity(source.get("Path") or "")
        record = by_source.get(key)
        if not record or not record_code(record):
            return None
        records.append(record)
    if len({record_code(record) for record in records}) != 1:
        return None
    return records


@dataclass
class VersionGroup:
    code: str
    items: list[dict] = field(default_factory=list)
    records: list = field(default_factory=list)
    source_keys: set[tuple[str, int]] = field(default_factory=set)

    @property
    def item_ids(self) -> list[str]:
        # 已有正确番号的卡片优先，避免错误刮削卡片成为主版本。
        from app.media.jav import detect_jav_code
        ordered = sorted(self.items, key=lambda item: (
            detect_jav_code(item.get("Name")) != self.code,
            not (item.get("ImageTags") or {}).get("Primary"),
            not (item.get("Path") or "").endswith(f"/{self.code}/{self.code}.strm"),
            not item["Id"].isdigit(),
            int(item["Id"]) if item["Id"].isdigit() else 0,
            item["Id"],
        ))
        return [item["Id"] for item in ordered]

    @property
    def expected_keys(self) -> set[tuple[str, int]]:
        return {(str(value(record, "chat_id")), int(value(record, "message_id"))) for record in self.records}

    @property
    def complete(self) -> bool:
        return self.expected_keys <= self.source_keys

    @property
    def already_merged(self) -> bool:
        keys = {item.get("PresentationUniqueKey") for item in self.items}
        return len(self.items) < 2 or (len(keys) == 1 and bool(next(iter(keys))))


def plan_version_groups(items: list[dict], records: list, codes: set[str] | None = None) -> dict[str, VersionGroup]:
    by_source = index_records(records)
    groups: dict[str, VersionGroup] = {}
    owners: dict[str, set[str | None]] = defaultdict(set)
    for item in items:
        refs = item_records(item, by_source)
        code = record_code(refs[0]) if refs else None
        presentation = item.get("PresentationUniqueKey")
        if presentation:
            owners[presentation].add(code)
        if not code or (codes is not None and code not in codes):
            continue
        group = groups.setdefault(code, VersionGroup(code))
        group.items.append(item)
        group.source_keys.update((str(value(record, "chat_id")), int(value(record, "message_id"))) for record in refs)
    # 即使列表只返回默认来源，也不能把已与别的番号混合的组继续扩大。
    for code, group in list(groups.items()):
        if any(len(owners[item["PresentationUniqueKey"]]) > 1 for item in group.items if item.get("PresentationUniqueKey")):
            del groups[code]
    for record in records:
        code = record_code(record)
        if code in groups:
            groups[code].records.append(record)
    return groups


async def load_jav_records() -> list[Media]:
    async with async_session() as session:
        return list((await session.execute(select(Media).where(Media.category == "18+").order_by(Media.id))).scalars().all())


async def fetch_movies(client: httpx.AsyncClient) -> list[dict]:
    """不传 UserId：必须读到各原始版本，而不是只读到用户界面的主卡。"""
    items = []
    while True:
        response = await client.get("Items", params={
            "Recursive": "true", "IncludeItemTypes": "Movie",
            "Fields": "Path,ProviderIds,MediaSources,PresentationUniqueKey",
            "EnableImages": "true", "EnableUserData": "false",
            "StartIndex": len(items), "Limit": 1000,
        })
        response.raise_for_status()
        data = response.json()
        page = data.get("Items", [])
        items.extend(page)
        if not page or len(items) >= data.get("TotalRecordCount", len(items)):
            return items


def merge_user_states(states: list[dict]) -> dict | None:
    """同一电影保留收藏、已看及最新播放位置，不让主版本的空状态覆盖历史。"""
    meaningful = [state for state in states if any(state.get(key) for key in (
        "Played", "PlayCount", "IsFavorite", "PlaybackPositionTicks", "LastPlayedDate"))]
    if not meaningful:
        return None
    latest = max(meaningful, key=lambda state: (state.get("LastPlayedDate") or "", state.get("PlaybackPositionTicks") or 0))
    result = {
        "Played": any(state.get("Played", False) for state in meaningful),
        "PlayCount": max(state.get("PlayCount") or 0 for state in meaningful),
        "IsFavorite": any(state.get("IsFavorite", False) for state in meaningful),
        "PlaybackPositionTicks": latest.get("PlaybackPositionTicks") or 0,
    }
    if latest.get("LastPlayedDate"):
        result["LastPlayedDate"] = latest["LastPlayedDate"]
    return result


def identity_is_locked(item: dict, code: str) -> bool:
    return ((item.get("ProviderIds") or {}).get("jav") == code and item.get("LockData") is True
            and IDENTITY_LOCK_FIELDS <= set(item.get("LockedFields") or []))


async def lock_item_identity(client: httpx.AsyncClient, item: dict, code: str,
                             admin_id: str, by_source: dict) -> tuple[dict, bool]:
    """只保护已核对的来源身份；不改片名、媒体信息、来源关系或观看记录。"""
    refs = item_records(item, by_source)
    if not refs or any(record_code(record) != code for record in refs):
        raise ValueError(f"{code}: mixed or unknown source in item {item['Id']}")
    if identity_is_locked(item, code):
        return item, False
    body = dict(item)
    for name in ("UserData", "MediaSources", "MediaStreams", "ImageTags", "BackdropImageTags",
                 "ExternalUrls", "Etag", "RunTimeTicks", "Chapters", "PartCount"):
        body.pop(name, None)
    providers = {key: data for key, data in (item.get("ProviderIds") or {}).items() if key.lower() != "jav"}
    body.update({"ProviderIds": {**providers, "jav": code}, "LockData": True,
                 "LockedFields": sorted(set(item.get("LockedFields") or []) | IDENTITY_LOCK_FIELDS)})
    response = await client.post(f"Items/{item['Id']}", json=body)
    response.raise_for_status()
    response = await client.get(f"Users/{admin_id}/Items/{item['Id']}")
    response.raise_for_status()
    saved = response.json()
    saved_refs = item_records(saved, by_source)
    keys = {(str(value(record, "chat_id")), int(value(record, "message_id"))) for record in refs}
    saved_keys = {(str(value(record, "chat_id")), int(value(record, "message_id"))) for record in (saved_refs or [])}
    if saved.get("Id") != item["Id"] or saved_keys != keys or not identity_is_locked(saved, code):
        raise ValueError(f"{code}: source identity protection was not retained")
    for name in ("Name", "OriginalTitle", "RunTimeTicks", "PresentationUniqueKey"):
        if saved.get(name) != item.get(name):
            raise ValueError(f"{code}: identity protection changed {name}")
    before_state, after_state = item.get("UserData") or {}, saved.get("UserData") or {}
    if any(before_state.get(name) != after_state.get(name) for name in
           ("Played", "PlayCount", "IsFavorite", "PlaybackPositionTicks", "LastPlayedDate")):
        raise ValueError(f"{code}: identity protection changed viewing state")
    return saved, True


async def merge_group(client: httpx.AsyncClient, group: VersionGroup, users: list[dict]) -> dict:
    if not group.complete:
        raise ValueError(f"{group.code}: some source versions are not indexed yet")
    admin = next(user for user in users if user.get("Policy", {}).get("IsAdministrator"))
    ids = group.item_ids
    before = {}
    for item_id in ids:
        response = await client.get(f"Users/{admin['Id']}/Items/{item_id}")
        response.raise_for_status()
        before[item_id] = response.json()
    actual = set()
    by_source = index_records(group.records)
    for item in before.values():
        refs = item_records(item, by_source)
        if not refs:
            raise ValueError(f"{group.code}: mixed or unknown source in item {item['Id']}")
        actual.update((str(value(record, "chat_id")), int(value(record, "message_id"))) for record in refs)
    if actual != group.source_keys:
        raise ValueError(f"{group.code}: source identity changed since the audit")
    keys = {item.get("PresentationUniqueKey") for item in before.values()}
    already_merged = len(ids) < 2 or (len(keys) == 1 and bool(next(iter(keys))))

    states = {}
    for user in users:
        if already_merged or user.get("Policy", {}).get("IsDisabled"):
            continue
        if user["Id"] == admin["Id"]:
            states[user["Id"]] = [item.get("UserData") or {} for item in before.values()]
        else:
            user_states = []
            for item_id in ids:
                response = await client.get(f"Users/{user['Id']}/Items/{item_id}")
                response.raise_for_status()
                user_states.append(response.json().get("UserData") or {})
            states[user["Id"]] = user_states

    protected = []
    # Emby 合卡会复制主版本元数据；必须先锁定每个版本，已合卡也不能直接跳过。
    for item_id in ids:
        before[item_id], changed = await lock_item_identity(client, before[item_id], group.code, admin["Id"], by_source)
        if changed:
            protected.append(item_id)
    if already_merged:
        return {"code": group.code, "already_merged": True, "source_count": len(actual),
                "main_item_id": ids[0], "protected_item_ids": protected}

    response = await client.post("Videos/MergeVersions", params={"Ids": ",".join(ids)})
    response.raise_for_status()
    response = await client.get(f"Users/{admin['Id']}/Items/{ids[0]}")
    response.raise_for_status()
    merged = response.json()
    refs = item_records(merged, by_source)
    after = {(str(value(record, "chat_id")), int(value(record, "message_id"))) for record in (refs or [])}
    if after != actual:
        raise ValueError(f"{group.code}: not all source versions survived the merge")
    visible = await client.get("Items", params={"Ids": ",".join(ids), "UserId": admin["Id"], "GroupItems": "true"})
    visible.raise_for_status()
    cards = visible.json().get("Items", [])
    if len(cards) != 1:
        raise ValueError(f"{group.code}: expected one Emby movie card, got {len(cards)}")
    main_id = cards[0]["Id"]
    for item_id in ids:
        if item_id == merged["Id"]:
            current = merged
        else:
            response = await client.get(f"Users/{admin['Id']}/Items/{item_id}")
            response.raise_for_status()
            current = response.json()
        _, changed = await lock_item_identity(client, current, group.code, admin["Id"], by_source)
        if changed and item_id not in protected:
            protected.append(item_id)
    restored = []
    for user_id, old_states in states.items():
        response = await client.get(f"Users/{user_id}/Items/{main_id}")
        response.raise_for_status()
        current = response.json().get("UserData") or {}
        desired = merge_user_states([*old_states, current])
        if desired and any(current.get(key) != data for key, data in desired.items()):
            response = await client.post(f"Users/{user_id}/Items/{main_id}/UserData", json=desired)
            response.raise_for_status()
            response = await client.get(f"Users/{user_id}/Items/{main_id}")
            response.raise_for_status()
            saved = response.json().get("UserData") or {}
            if any(saved.get(key) != data for key, data in desired.items()):
                raise ValueError(f"{group.code}: viewing state was not preserved")
            restored.append(user_id)
    return {"code": group.code, "main_item_id": main_id, "source_count": len(after),
            "version_item_ids": ids, "restored_user_ids": restored, "already_merged": False,
            "protected_item_ids": protected}


async def reconcile_versions(codes: set[str]) -> dict:
    records = await load_jav_records()
    async with httpx.AsyncClient(base_url=f"{settings.emby.url.rstrip('/')}/emby/",
                                 headers={"X-Emby-Token": settings.emby.api_key}, timeout=60) as client:
        items = await fetch_movies(client)
        groups = plan_version_groups(items, records, codes)
        candidates = [group for group in groups.values() if group.complete]
        users = []
        if candidates:
            response = await client.get("Users")
            response.raise_for_status()
            users = response.json()
        merged = 0
        protected = 0
        for group in candidates:
            result = await merge_group(client, group, users)
            merged += not result["already_merged"]
            protected += len(result["protected_item_ids"])
            logger.info("Emby 番号 %s 已合并为一张卡（%s 个来源）", group.code, result["source_count"])
        return {"merged": merged, "identity_protected": protected,
                "completed": {code for code, group in groups.items() if group.complete}}


_pending: dict[str, int] = {}
_task: asyncio.Task | None = None


def schedule_version_reconcile(codes) -> None:
    """库扫描是异步的：合并请求去重排队，等新版本入 Emby 后再核对。"""
    global _task
    for code in codes:
        if code and resolve_jav_code(title=code) == code:
            _pending[code] = _pending.get(code, 0) + 1
    if _pending and (_task is None or _task.done()):
        _task = asyncio.create_task(_reconcile_pending())


async def _reconcile_pending():
    global _task
    snapshot = {}
    cancelled = False
    try:
        for delay in (5, 15, 30, 60):
            await asyncio.sleep(delay)
            snapshot = dict(_pending)
            if not snapshot:
                break
            try:
                result = await reconcile_versions(set(snapshot))
                for code in result["completed"]:
                    if _pending.get(code) == snapshot.get(code):
                        _pending.pop(code, None)
            except Exception as exc:
                logger.warning("Emby 多版本核对失败: %s", type(exc).__name__)
            if not _pending:
                break
        overdue = [code for code, generation in snapshot.items() if _pending.get(code) == generation]
        if overdue:
            logger.warning("Emby 尚未完成这些番号的索引，未强行合并: %s", ", ".join(overdue))
            for code in overdue:
                _pending.pop(code, None)
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        _task = None
        if _pending and not cancelled:
            _task = asyncio.create_task(_reconcile_pending())


async def stop_version_reconcile() -> None:
    global _task
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None
    _pending.clear()
