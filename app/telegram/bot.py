import re
import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from app.config import settings
from app.database import async_session
from app.models import Media, Category
from app.media.parser import parse_media_info
from app.media.jav import detect_jav_code, _JAV_PATTERNS
from app.media.strm import generate_strm
from app.telegram import jav_fetcher
from sqlalchemy import select

logger = logging.getLogger(__name__)

_bot_client: Client | None = None
_bot_started = False

# ── Conversation State Machine ──────────────────────────────────────

class ConvState(str, Enum):
    IDLE = "idle"
    COLLECTING = "collecting"           # collecting multi-video
    AWAITING_CONFIRM_START = "awaiting_confirm_start"  # user clicked start processing
    AWAITING_TITLE_CONFIRM = "awaiting_title_confirm"  # single: confirm/modify title
    AWAITING_TITLE_EDIT = "awaiting_title_edit"        # single: editing title
    AWAITING_SERIES_NAME = "awaiting_series_name"      # multi: enter series name
    AWAITING_CATEGORY = "awaiting_category"            # selecting category

@dataclass
class PendingVideo:
    message_id: int
    chat_id: int
    file_name: str
    file_id: str
    file_unique_id: str
    size: int
    duration: int
    mime_type: str
    width: int
    height: int
    caption: str
    source_link: str
    title: Optional[str] = None
    episode: Optional[int] = None
    season: Optional[int] = None
    year: Optional[int] = None
    is_jav: bool = False  # 标题来自番号自动识别

@dataclass
class Conversation:
    user_id: int
    state: ConvState = ConvState.IDLE
    videos: list[PendingVideo] = field(default_factory=list)
    series_name: Optional[str] = None
    category: Optional[str] = None
    per_video_titles: bool = False  # 多视频时保留各自的自动识别标题
    collect_task: Optional[asyncio.Task] = None

_conversations: dict[int, Conversation] = {}
_collect_lock = asyncio.Lock()

# ── Helpers ─────────────────────────────────────────────────────────

_BRACKET_RE = re.compile(r"[\(\[【（].*?[\)\]】）]")
_TAG_RE = re.compile(r"@\S+|#\S+")
_QUALITY_RE = re.compile(r"(?:4K|2160[pP]|1080[pP]|720[pP]|480[pP]|HEVC|H\.?264|H\.?265|x264|x265)", re.I)
_EXT_RE = re.compile(r"\.(mp4|mkv|avi|mov|wmv|flv|webm|ts)$", re.I)
_PART_RE = re.compile(r"[_.]\s*part\d+", re.I)
_SEP_RE = re.compile(r"[_\s]+")

# episode patterns (in order of priority)
_EP_PATTERNS = [
    re.compile(r"[Ss](\d+)[Ee](\d+)"),         # S01E02
    re.compile(r"第\s*(\d+)\s*[集话話]"),       # 第2集 / 第02话
    re.compile(r"[Ee][Pp]\s*(\d+)"),            # EP02 / Ep 3
    re.compile(r"[Ee](\d{2,3})"),               # E02 / E123
    re.compile(r"[-\s._](\d{2,3})\s*$"),        # _02 / -03 at end
]


def _guess_title_from_filename(filename: str) -> str:
    if not filename:
        return ""
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = _EXT_RE.sub("", name)
    name = _QUALITY_RE.sub("", name)
    name = _TAG_RE.sub("", name)
    name = _BRACKET_RE.sub("", name)
    name = _PART_RE.sub("", name)
    name = _SEP_RE.sub(" ", name)
    name = name.strip(" -_.")
    return name


def _guess_episode_from_filename(filename: str) -> Optional[int]:
    for pat in _EP_PATTERNS:
        m = pat.search(filename)
        if m:
            try:
                return int(m.group(1) if pat.groups == 1 else m.group(2))
            except (ValueError, IndexError):
                pass
    return None


def _guess_season_from_filename(filename: str) -> Optional[int]:
    m = re.search(r"[Ss](\d+)[Ee]", filename)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _source_link(chat_id: int | str, message_id: int) -> str:
    cid = str(chat_id).lstrip("-")
    return f"https://t.me/c/{cid}/{message_id}"


def _looks_like_jav(text: str) -> bool:
    """判断已识别的标题本身是否就是标准番号格式（避免重复改写）。"""
    for pattern, _fmt in _JAV_PATTERNS:
        m = pattern.fullmatch(text.strip())
        if m:
            code = _fmt(m)
            if code.split("-")[0] not in {"X264", "X265", "H264", "H265", "HEVC"}:
                return True
    return False


def _fmt_size(b: int) -> str:
    if not b:
        return "-"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f}{u}"
        b /= 1024
    return f"{b:.1f}PB"


async def _fetch_categories() -> list[str]:
    async with async_session() as session:
        result = await session.execute(
            select(Category).order_by(Category.sort_order, Category.id)
        )
        cats = result.scalars().all()
        return [c.name for c in cats]


async def _make_category_keyboard_async(suggest_18plus: bool = False) -> InlineKeyboardMarkup:
    cats = await _fetch_categories()
    if not cats:
        cats = ["电视剧", "电影", "动漫", "海外剧", "18+"]
    buttons: list[list[InlineKeyboardButton]] = []
    # 识别到番号时提供一键 18+ 入库
    if suggest_18plus:
        has_18 = any(c == "18+" for c in cats)
        if has_18:
            buttons.append([InlineKeyboardButton(
                "⚡ 全部按 18+ 入库（已识别番号）", callback_data="cat:18+")])
    buttons.extend([[InlineKeyboardButton(c, callback_data=f"cat:{c}")] for c in cats])
    buttons.append([InlineKeyboardButton("❌ 取消", callback_data="cat:cancel")])
    return InlineKeyboardMarkup(buttons)


def _display_name(v: PendingVideo) -> str:
    if v.season is not None and v.episode is not None:
        return f"{v.title}-S{int(v.season):02d}E{int(v.episode):02d}"
    if v.episode is not None:
        return f"{v.title}-E{int(v.episode):02d}"
    return v.title or v.file_name or "未知"


# ── DB Save + STRM ──────────────────────────────────────────────────

async def _save_video(
    v: PendingVideo,
    category: str,
    episode_digits: int = 2,
    record_ids: Optional[list] = None,   # 提供时暂不入刮削队列，id 收集后由调用方统一批量入队
) -> Optional[str]:
    from app.media.strm import resolution_tag

    strm_path = generate_strm(
        message_id=v.message_id,
        chat_id=str(v.chat_id),
        category=category,
        title=v.title,
        season=v.season,
        episode=v.episode,
        year=v.year,
        episode_digits=episode_digits,
        strm_subdir=category,
        resolution=resolution_tag(v.width, v.height),
    )
    result: Optional[str] = None
    async with async_session() as session:
        # 同一条消息（同频道同 message_id）不允许重复入库
        existing = await session.execute(
            select(Media).where(
                Media.chat_id == str(v.chat_id),
                Media.message_id == v.message_id,
            )
        )
        if existing.scalar_one_or_none():
            return None  # 已存在 → 调用方提示跳过
        record = Media(
            message_id=v.message_id,
            chat_id=str(v.chat_id),
            file_name=v.file_name,
            caption=v.caption,
            file_id=v.file_id,
            file_unique_id=v.file_unique_id,
            size=v.size,
            duration=v.duration,
            mime_type=v.mime_type or "video/mp4",
            width=v.width or 0,
            height=v.height or 0,
            category=category,
            title=_display_name(v),
            source_link=v.source_link,
            strm_path=str(strm_path) if strm_path else None,
        )
        session.add(record)
        try:
            await session.flush()
            mid = record.id
            await session.commit()
        except Exception:
            await session.rollback()
            return None
        if settings.enrich.enabled and settings.tmdb.api_key:
            from app.media.enrich_queue import enrich_queue
            if record_ids is not None:
                record_ids.append(mid)  # 批量模式：最后统一入队
            else:
                enrich_queue.enqueue_batch([mid], {
                    "ch_name": v.title or v.file_name or "手动入库",
                    "category": category,
                })
        result = str(strm_path) if strm_path else None
    return result


async def _save_videos(conv: Conversation) -> list[dict]:
    results = []
    category = conv.category or "电视剧"
    is_anime = category == "动漫"
    episode_digits = 3 if is_anime else 2

    for i, v in enumerate(conv.videos):
        # 独立标题模式（回复 "-"）：保留每条自动识别的标题，
        # 不统一改写为剧名 —— 适配番号或不同频道的不同命名
        if conv.series_name and not conv.per_video_titles:
            v.title = conv.series_name
        strm_path = await _save_video(v, category, episode_digits)
        results.append({
            "video": v,
            "strm_path": strm_path,
            "skipped": strm_path is None,  # 已存在（重复消息）或生成失败
        })
    return results


# ── 番号搜索取片（/jav、/javbatch）────────────────────────────────

JAV_CATEGORY = "18+"

_jav_batch: dict = {"task": None, "stop": False}


def _parse_code_list(text: str, cap: int = 300) -> list[str]:
    """从粘贴文本中解析番号列表（去重保序）。"""
    token_re = re.compile(r"(?:FC2[-_ ]?PPV[-_ ]?\d{6,10}|[A-Za-z]{2,6}[-_ ]?\d{2,5})")
    codes: list[str] = []
    seen: set[str] = set()
    for m in token_re.finditer(text):
        norm = detect_jav_code(m.group(0))
        if norm and norm not in seen:
            seen.add(norm)
            codes.append(norm)
            if len(codes) >= cap:
                break
    return codes


async def _code_in_library(code: str) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(Media.id).where(
                Media.category == JAV_CATEGORY,
                Media.title.ilike(f"%{code}%"),
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None


def _pending_from_search(code: str, chat_id: int, msg) -> PendingVideo:
    media = msg.video or msg.document
    fn = ""
    for attr in (getattr(media, "attributes", []) or []):
        if hasattr(attr, "file_name") and attr.file_name:
            fn = attr.file_name
    cap = jav_fetcher.safe_text(msg.caption) or fn
    # 标题取说明第一行（通常以「番号 片名」开头）；不以番号开头则直接用番号
    first_line = cap.strip().splitlines()[0].strip() if cap else ""
    title = first_line if first_line.upper().startswith(code.upper()) else code
    return PendingVideo(
        message_id=msg.id,
        chat_id=chat_id,
        file_name=fn or f"{code}.mp4",
        file_id=getattr(media, "file_id", "") or "",
        file_unique_id=getattr(media, "file_unique_id", "") or "",
        size=getattr(media, "file_size", 0) or 0,
        duration=getattr(msg.video, "duration", 0) if msg.video else 0,
        mime_type=getattr(media, "mime_type", "") or "video/mp4",
        width=getattr(msg.video, "width", 0) if msg.video else 0,
        height=getattr(msg.video, "height", 0) if msg.video else 0,
        caption=cap,
        source_link=_source_link(chat_id, msg.id),
        title=title,
        is_jav=True,
    )


async def _jav_ingest(code: str, record_ids: Optional[list] = None) -> dict:
    """搜索单个番号并入库。status: ok / in_library / not_found / duplicate / error"""
    code = code.strip().upper()
    try:
        if await _code_in_library(code):
            return {"code": code, "status": "in_library"}
        chat_id, msg = await jav_fetcher.find_video(code)
        if msg is None:
            return {"code": code, "status": "not_found"}
        v = _pending_from_search(code, chat_id, msg)
        strm_path = await _save_video(v, JAV_CATEGORY, record_ids=record_ids)
        if strm_path is None:
            return {"code": code, "status": "duplicate"}
        return {"code": code, "status": "ok", "strm": strm_path,
                "title": v.title, "source_link": v.source_link}
    except Exception as e:
        logger.warning(f"番号 {code} 检索入库失败: {e}")
        return {"code": code, "status": "error", "error": str(e)}


async def _run_jav_batch(user_id: int, codes: list[str], bot_client: Client):
    """后台批量检索入库；结束时统一入刮削队列 → 仅推送一条批量通知。"""
    _jav_batch["stop"] = False
    results: list[dict] = []
    record_ids: list[int] = []
    total = len(codes)
    try:
        for i, code in enumerate(codes):
            if _jav_batch["stop"]:
                break
            res = await _jav_ingest(code, record_ids=record_ids)
            results.append(res)
            if (i + 1) % 25 == 0:
                ok = sum(1 for r in results if r["status"] == "ok")
                try:
                    await bot_client.send_message(
                        user_id, f"⏳ 进度 {i + 1}/{total} · ✅ 已入库 {ok} 条")
                except Exception:
                    pass
            await asyncio.sleep(3)  # 检索间隔，避免触发搜索限流
    finally:
        ok = [r for r in results if r["status"] == "ok"]
        lib = [r["code"] for r in results if r["status"] == "in_library"]
        miss = [r["code"] for r in results if r["status"] == "not_found"]
        err = [r for r in results if r["status"] == "error"]
        try:
            lines = [
                f"📊 **番号批量检索{'中止' if _jav_batch['stop'] else '完成'}**\n",
                f"✅ 入库 **{len(ok)}** 条（刮削完成后自动推送海报通知）",
            ]
            if lib:
                lines.append(f"ℹ️ 已在库 {len(lib)} 条：{', '.join(lib[:15])}{' …' if len(lib) > 15 else ''}")
            if miss:
                lines.append(f"❌ 未找到 {len(miss)} 条：{', '.join(miss[:20])}{' …' if len(miss) > 20 else ''}")
            if err:
                err_view = ", ".join(f"`{r['code']}`" for r in err[:10])
                lines.append(f"⚠️ 失败 {len(err)} 条：{err_view}{' …' if len(err) > 10 else ''}")
            await bot_client.send_message(user_id, "\n".join(lines))
        except Exception as e:
            logger.warning(f"批量结果汇报失败: {e}")
        # 入库记录统一入刮削队列（一个批次 → 一条通知）
        if record_ids and settings.enrich.enabled and settings.tmdb.api_key:
            from app.media.enrich_queue import enrich_queue
            enrich_queue.enqueue_batch(
                record_ids, {"ch_name": "番号批量检索", "category": JAV_CATEGORY})
        _jav_batch["results"] = results


# ── Parse forwarded video ───────────────────────────────────────────

async def _parse_forwarded(message: Message) -> Optional[PendingVideo]:
    media = message.video or message.document
    if not media:
        return None

    fwd_chat = message.forward_from_chat
    fwd_msg_id = message.forward_from_message_id
    if not fwd_chat or not fwd_msg_id:
        return None

    chat_id = fwd_chat.id
    fn = media.file_name or f"video_{fwd_msg_id}.mp4"
    caption = message.caption or ""

    # ⚠️ 不同频道命名规则不同：优先按频道配置的 ChannelRule 解析
    rule = None
    try:
        from app.telegram.monitor import _get_rule_for_channel
        rule = await _get_rule_for_channel(int(chat_id))
    except Exception:
        rule = None

    info = parse_media_info(
        caption=caption,
        title_rule=rule.title_rule if rule else None,
        episode_rule=rule.episode_rule if rule else None,
        season=rule.season if rule else None,
        season_rule=rule.season_rule if rule else None,
        year_rule=rule.year_rule if rule else None,
        filename=fn,
        filename_rule=rule.filename_rule if rule else None,
    )

    # 番号识别：文件名优先，其次说明文字（18+ 内容唯一稳定的标识）
    jav_code = detect_jav_code(fn, media.file_name or "", caption)
    is_jav = False

    title = info.get("title") if info and info.get("title") else None
    if not title:
        title = _guess_title_from_filename(fn)

    if jav_code:
        # 番号比启发式命名更可靠：启发式结果与番号不符时以番号为准
        if title is None or not _looks_like_jav(title):
            title = jav_code
            is_jav = True

    episode = info.get("episode") if info else None
    if episode is None:
        episode = _guess_episode_from_filename(fn)

    season = info.get("season") if info else None
    if season is None:
        season = _guess_season_from_filename(fn)

    # 番号类视频没有剧集概念，避免误识别 E03 等
    if is_jav:
        episode = None
        season = None

    return PendingVideo(
        message_id=fwd_msg_id,
        chat_id=chat_id,
        file_name=fn,
        file_id=media.file_id,
        file_unique_id=media.file_unique_id,
        size=media.file_size or 0,
        duration=getattr(media, "duration", 0) or 0,
        mime_type=media.mime_type or "video/mp4",
        width=getattr(media, "width", 0) or 0,
        height=getattr(media, "height", 0) or 0,
        caption=caption,
        source_link=_source_link(chat_id, fwd_msg_id),
        title=title,
        episode=episode,
        season=season,
        is_jav=is_jav,
    )


# ── Notification builder ────────────────────────────────────────────

_PER_ITEM_CHARS = 200
_MAX_DETAIL_ITEMS = 10


def build_scan_notify(ch_name: str, count: int, category: str, items: list[dict]) -> str:
    lines = [f"📦 **{ch_name}** 新增 **{count}** 条入库\n"]
    for i, item in enumerate(items[:count]):
        if i >= _MAX_DETAIL_ITEMS:
            lines.append(f"\n⋯ 以及 {count - _MAX_DETAIL_ITEMS} 条")
            break
        title = item.get("title") or item.get("file_name") or "未知"
        cat = item.get("category") or category or ""
        link = item.get("source_link", "")
        n = i + 1
        lines.append(f"{n}. **{title}**")
        if item.get("episode") is not None:
            ep = f"E{int(item['episode']):02d}"
            lines[-1] += f" — {ep}"
        lines.append(f"   📂 {cat}")
        if link:
            lines.append(f"   🔗 [查看原消息]({link})")
    return "\n".join(lines)


def build_manual_notify_single(title: str, category: str, v: PendingVideo, strm_path: Optional[str] = None) -> str:
    lines = ["✅ **入库成功**\n"]
    lines.append(f"📺 **{title}**")
    if v.episode is not None:
        lines[-1] += f" — E{int(v.episode):02d}"
    if v.season is not None:
        if v.episode is not None:
            pass  # already shown in display name
        else:
            lines.append(f"   Season {int(v.season):02d}")
    lines.append(f"📂 {category}")
    lines.append(f"🔗 [查看原消息]({v.source_link})")
    if strm_path:
        lines.append(f"📁 `{strm_path}`")
    return "\n".join(lines)


def build_manual_notify_batch(series_name: str, category: str, results: list[dict]) -> str:
    lines = [f"✅ **批量入库完成**（共 {len(results)} 集）\n"]
    lines.append(f"📺 **{series_name}**")
    lines.append(f"📂 {category}")
    episodes = []
    for r in results:
        v = r["video"]
        ep = v.episode
        if ep is not None:
            episodes.append(f"E{int(ep):02d}")
    if episodes:
        lines.append(f"📋 {' '.join(episodes)}")
    lines.append("")
    for r in results[:10]:
        v = r["video"]
        ep_str = f"E{int(v.episode):02d}" if v.episode is not None else ""
        lines.append(f"🔗 [{ep_str}]({v.source_link})")
    if len(results) > 10:
        lines.append(f"\n⋯ 共 {len(results)} 集")
    return "\n".join(lines)


# ── Handlers ────────────────────────────────────────────────────────

def _is_admin(user_id: int) -> bool:
    admins = settings.bot.admin_users
    return not admins or user_id in admins


async def _start_processing(client: Client, user_id: int):
    conv = _conversations.get(user_id)
    if not conv or not conv.videos:
        return

    is_multi = len(conv.videos) > 1

    if is_multi:
        conv.state = ConvState.AWAITING_SERIES_NAME
        v = conv.videos[0]
        guessed = v.title or "未知"
        hint = ""
        titles = [x.title for x in conv.videos]
        if all(titles) and len(set(titles)) == len(titles):
            titles_view = "\n".join(f"  · `{t}`" for t in titles[:8])
            hint = (
                "\n\n各视频识别标题如下（各不相同 —— 如是番号或不同频道命名，"
                "可回复 **`-`** 保留每条各自标题独立入库）：\n"
                f"{titles_view}"
            )
            if len(conv.videos) > 8:
                hint += f"\n  ⋯ 共 {len(conv.videos)} 条"
        await client.send_message(
            user_id,
            f"📦 检测到 **{len(conv.videos)}** 个视频，自动识别为剧集模式\n\n"
            f"识别到剧名：`{guessed}`\n"
            f"如需修改请直接发送新名称，如需确认请发送：`{guessed}`\n"
            f"_或发送 **`-`** 保留各条独立标题；发送 /cancel 取消_{hint}",
        )
    else:
        v = conv.videos[0]
        await _send_single_confirm(client, conv)
        conv.state = ConvState.AWAITING_TITLE_CONFIRM


async def _send_single_confirm(client: Client, conv: Conversation):
    v = conv.videos[0]
    title = v.title or "（未能识别）"
    ep_str = f" — 第 {v.episode} 集" if v.episode is not None else ""
    jav_tag = "（番号自动识别）" if v.is_jav else ""
    text = (
        f"🎬 **识别结果**{jav_tag}\n\n"
        f"名称：`{title}`{ep_str}\n\n"
        f"是否修改名称？"
    )
    btns = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认", callback_data="title:confirm"),
         InlineKeyboardButton("✏️ 修改", callback_data="title:edit")],
        [InlineKeyboardButton("❌ 取消", callback_data="title:cancel")],
    ])
    await client.send_message(conv.user_id, text, reply_markup=btns)


BOT_FILES_DIR = Path(settings.cache.dir).parent / "bot_files"


async def _save_forwarded_document(client: Client, message: Message):
    """保存转发的文档（xlsx/css/csv 等）到 bot_files 目录，供服务器侧提取。"""
    user_id = message.from_user.id if message.from_user else 0
    if not _is_admin(user_id):
        return

    doc = message.document
    if not doc:
        return

    file_name = ""
    for attr in (getattr(doc, "attributes", []) or []):
        if hasattr(attr, "file_name") and attr.file_name:
            file_name = attr.file_name
            break
    if not file_name:
        file_name = f"doc_{message.id}"

    BOT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path = await message.download(file_name=str(BOT_FILES_DIR / file_name))
        await message.reply(
            f"📄 文档已保存：`{path}`\n"
            f"来源: [原消息]({_source_link(message.forward_from_chat.id, message.forward_from_message_id) if message.forward_from_chat else '未知'})"
        )
        logger.info(f"Bot saved forwarded document: {path}")
    except Exception as e:
        logger.error(f"保存转发文档失败: {e}")
        try:
            await message.reply(f"❌ 文档保存失败：{e}")
        except Exception:
            pass


# ── Pyrogram message handler: forwarded video ───────────────────────

async def _handle_forwarded_video(client: Client, message: Message):
    user_id = message.from_user.id
    if not _is_admin(user_id):
        await message.reply("⛔ 你没有权限使用此功能")
        return

    if settings.notify.bot_token:
        bot_user = await client.get_me()
        if message.forward_from and message.forward_from.id == bot_user.id:
            await message.reply("⚠️ 请转发来自频道的视频，而不是转发本 Bot 的消息")
            return

    video = message.video or message.document
    if not video:
        return

    if message.document and not (message.document.mime_type or "").startswith("video/"):
        await message.reply("⚠️ 请转发视频文件（不支持的格式）")
        return

    if not message.forward_from_chat:
        await message.reply("⚠️ 请转发来自频道的视频消息（需要保留原始转发信息）")
        return

    parsed = await _parse_forwarded(message)
    if not parsed:
        await message.reply("⚠️ 无法解析该消息")
        return

    async with _collect_lock:
        conv = _conversations.get(user_id)
        if not conv or conv.state == ConvState.IDLE:
            conv = Conversation(user_id=user_id, state=ConvState.COLLECTING)
            _conversations[user_id] = conv
            conv.collect_task = asyncio.create_task(_collect_timeout(client, user_id))
            conv.videos.append(parsed)
            await message.reply(
                f"📥 已收到视频：`{parsed.title or parsed.file_name}`\n"
                f"继续转发更多视频（同一剧集），或等待 15 秒自动开始处理",
            )
        elif conv.state == ConvState.COLLECTING:
            conv.videos.append(parsed)
            plural = f"（共 {len(conv.videos)} 个）" if len(conv.videos) > 1 else ""
            await message.reply(
                f"✅ 已添加：`{parsed.title or parsed.file_name}`{plural}\n"
                f"_继续转发或等待自动处理_",
            )
        else:
            await message.reply("⚠️ 请先完成当前操作")


async def _collect_timeout(client: Client, user_id: int):
    try:
        await asyncio.sleep(15)
        async with _collect_lock:
            conv = _conversations.get(user_id)
            if conv and conv.state == ConvState.COLLECTING:
                await _start_processing(client, user_id)
    except asyncio.CancelledError:
        pass


# ── Pyrogram message handler: text message ──────────────────────────

async def _handle_text_message(client: Client, message: Message):
    user_id = message.from_user.id
    if not _is_admin(user_id):
        return

    text = message.text.strip()
    if text == "/cancel":
        _conversations.pop(user_id, None)
        await message.reply("❌ 已取消")
        return

    conv = _conversations.get(user_id)
    if not conv or conv.state == ConvState.IDLE:
        return

    if text.startswith("/"):
        return  # 其他命令不进入会话状态机

    if conv.state == ConvState.AWAITING_SERIES_NAME:
        kb = await _make_category_keyboard_async(
            suggest_18plus=any(v.is_jav for v in conv.videos)
        )
        if text in ("-", "keep", "默认", "/auto"):
            # 保留每条视频各自识别出的标题（番号 / 不同频道命名）
            conv.per_video_titles = True
            conv.state = ConvState.AWAITING_CATEGORY
            preview = ", ".join(
                f"`{(v.title or v.file_name or '?')[:40]}`" for v in conv.videos[:5]
            )
            extra = f" 等 {len(conv.videos)} 条" if len(conv.videos) > 5 else ""
            await message.reply(
                f"✅ 将按各条独立标题入库：\n{preview}{extra}\n\n请选择分类：",
                reply_markup=kb,
            )
            return
        conv.series_name = text
        conv.state = ConvState.AWAITING_CATEGORY
        await message.reply(
            f"📺 剧集名称：**{text}**\n\n请选择分类：",
            reply_markup=kb,
        )

    elif conv.state == ConvState.AWAITING_TITLE_EDIT:
        v = conv.videos[0]
        v.title = text
        conv.state = ConvState.AWAITING_CATEGORY
        kb = await _make_category_keyboard_async(suggest_18plus=v.is_jav)
        await message.reply(
            f"✅ 名称已修改为：**{text}**\n\n请选择分类：",
            reply_markup=kb,
        )


# ── Pyrogram callback handler ───────────────────────────────────────

async def _handle_callback_query(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    conv = _conversations.get(user_id)
    if not conv:
        await callback.answer("会话已过期", show_alert=True)
        return

    if data == "title:confirm":
        conv.state = ConvState.AWAITING_CATEGORY
        kb = await _make_category_keyboard_async(
            suggest_18plus=any(v.is_jav for v in conv.videos)
        )
        await callback.message.edit_text(
            f"🎬 **{conv.videos[0].title}**\n\n请选择分类：",
            reply_markup=kb,
        )
        await callback.answer()

    elif data == "title:edit":
        conv.state = ConvState.AWAITING_TITLE_EDIT
        await callback.message.edit_text("✏️ 请发送新的名称：")
        await callback.answer()

    elif data == "title:cancel":
        _conversations.pop(user_id, None)
        await callback.message.edit_text("❌ 已取消")
        await callback.answer()

    elif data.startswith("cat:"):
        cat = data[4:]
        if cat == "cancel":
            _conversations.pop(user_id, None)
            await callback.message.edit_text("❌ 已取消")
            await callback.answer()
            return

        conv.category = cat
        await callback.message.edit_text(f"⏳ 正在入库，请稍候...")
        await callback.answer()

        try:
            results = await _save_videos(conv)
            saved = [r for r in results if r.get("strm_path")]
            if not saved and results:
                await callback.message.edit_text("⚠️ 选中的条目都已存在（同一消息不会重复入库）")
                await callback.answer()
                return
            # send notification
            if len(saved) == 1 and not [r for r in results if r.get("skipped")] == results:
                r = saved[0] if saved else results[0]
                v = r["video"]
                display = _display_name(v)
                notify_text = build_manual_notify_single(display, cat, v, r["strm_path"])
                await client.send_message(user_id, notify_text)
            else:
                notify_text = build_manual_notify_batch(
                    conv.series_name or conv.videos[0].title or "未知", cat, saved or results
                )
                if len(results) != len(saved):
                    notify_text += f"\n\n⚠️ {len(results) - len(saved)} 条已存在，自动跳过"
                await client.send_message(user_id, notify_text)

            # also send system notify if enabled
            if len(saved) == 1:
                v = saved[0]["video"]
                system_msg = (
                    f"📦 **手动入库**\n\n"
                    f"名称: {_display_name(v)}\n"
                    f"分类: {cat}\n"
                    f"来源: [消息]({v.source_link})"
                )
            else:
                system_msg = (
                    f"📦 **手动入库**（批量 {len(saved)} 集）\n\n"
                    f"剧名: {conv.series_name or conv.videos[0].title or '未知'}\n"
                    f"分类: {cat}"
                )
            await send_notify(system_msg)

            _conversations.pop(user_id, None)
        except Exception as e:
            logger.error(f"Save error: {e}")
            await client.send_message(user_id, f"❌ 入库失败：{e}")


# ── Enrichment-completion notifications (with poster images) ────────

def build_enriched_notify(meta: dict, results: list[dict]) -> str:
    """刮削完成后的入库通知文案（纯文字版，也用作相册 caption）。"""
    ch_name = meta.get("ch_name") or "媒体库"
    count = len(results)
    lines = [f"🎬 **{ch_name}** 新增 **{count}** 条 · 刮削完成\n"]
    for i, item in enumerate(results[:_MAX_DETAIL_ITEMS]):
        title = item.get("title") or item.get("file_name") or "未知"
        if item.get("tmdb_title") and item["tmdb_title"] != title:
            lines.append(f"{i + 1}. **{title}** → {item['tmdb_title']}")
        else:
            lines.append(f"{i + 1}. **{title}**")
        if item.get("episode") is not None:
            lines[-1] += f" — E{int(item['episode']):02d}"
        if item.get("category"):
            lines.append(f"   📂 {item['category']}")
        link = item.get("source_link", "")
        if link:
            lines.append(f"   🔗 [查看原消息]({link})")
        if not item.get("success") and not item.get("skipped"):
            reason = item.get("reason") or "识别失败"
            lines.append(f"   ⚠️ {reason}")
    if count > _MAX_DETAIL_ITEMS:
        lines.append(f"\n⋯ 共 {count} 条")
    return "\n".join(lines)


def _collect_posters(results: list[dict], max_photos: int = 10) -> list[str]:
    """本地海报去重收集（同一文件只发一次，上限 10 张/相册）。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in results:
        p = (item.get("poster_path") or "").strip()
        if p and p not in seen and p not in out:
            out.append(p)
        if len(out) >= max_photos:
            break
    return out


async def _resolve_target(target: str) -> str | int:
    target_clean = target.lstrip("@")
    try:
        return int(target_clean)
    except ValueError:
        return target_clean


async def _send_media_group(target, photos: list[str], caption: str) -> bool:
    """用本地海报文件发送相册；Bot 优先，Bot 不可用回退用户账号。"""
    from pyrogram.types import InputMediaPhoto
    media = [
        InputMediaPhoto(p, caption=caption if i == 0 else None)
        for i, p in enumerate(photos)
    ]
    client = await get_bot()
    if client is None:
        from app.telegram.client import get_client
        client = await get_client()
    await client.send_media_group(target, media=media)
    return True


async def send_enriched_batch_notify(meta: dict, results: list[dict]):
    """刮削批次完成后的通知：有本地海报发相册，无海报发纯文字。"""
    if not results:
        return
    results = sorted(results, key=lambda r: r.get("media_id", 0))
    text = build_enriched_notify(meta, results)

    target = settings.notify.target
    if not target:
        return

    photos: list[str] = []
    for p in _collect_posters(results):
        try:
            if Path(p).exists():
                photos.append(p)
            else:
                logger.debug(f"海报不存在，跳过: {p}")
        except Exception:
            continue

    if not settings.notify.enabled and not photos:
        return

    try:
        target_resolved = await _resolve_target(target)
        if photos:
            await _send_media_group(target_resolved, photos, text[:1024])
        else:
            await send_notify(text)
        logger.info(f"刮削完成通知已发送（{len(results)} 条，{len(photos)} 张海报）")
    except Exception as e:
        logger.warning(f"带图通知发送失败，改用纯文字: {e}")
        try:
            await send_notify(text)
        except Exception as e2:
            logger.warning(f"纯文字通知也失败: {e2}")
            await _send_via_user(text)


# ── Client management ─────────────────────────────────────────────────

async def _create_bot_client() -> Client:
    proxy = None
    if settings.proxy.enabled and settings.proxy.hostname:
        proxy = {
            "scheme": settings.proxy.scheme,
            "hostname": settings.proxy.hostname,
            "port": settings.proxy.port,
        }

    client = Client(
        name="tg_handler_bot",
        api_id=settings.telegram.api_id,
        api_hash=settings.telegram.api_hash,
        bot_token=settings.notify.bot_token,
        in_memory=True,
        proxy=proxy,
    )

    @client.on_message(filters.private & filters.command("jav"))
    async def jav_handler(_client: Client, message: Message):
        user_id = message.from_user.id
        if not _is_admin(user_id):
            return
        task = _jav_batch.get("task")
        if task and not task.done():
            await message.reply("⏳ 批量任务进行中（/javbatch stop 可中止），请稍后再试")
            return
        arg = message.text.split(maxsplit=1)[1].strip() if message.text and " " in message.text else ""
        code = detect_jav_code(arg) or detect_jav_code(arg.replace(" ", ""))
        if not code:
            await message.reply("用法：`/jav 番号`（如 `/jav SSIS-806`）")
            return
        _m = await message.reply(f"🔍 正在检索 **{code}** ...")
        res = await _jav_ingest(code)
        if res["status"] == "ok":
            await _m.edit_text(
                f"✅ **{code}** 已按 18+ 入库\n\n"
                f"📺 `{res['title']}`\n"
                f"🔗 [资源消息]({res['source_link']})\n\n"
                f"_元数据刮削完成后会自动推送海报通知_"
            )
        elif res["status"] == "in_library":
            await _m.edit_text(f"ℹ️ **{code}** 已在库中，跳过")
        elif res["status"] == "duplicate":
            await _m.edit_text(f"⚠️ **{code}** 同一来源消息已入库，跳过")
        else:
            await _m.edit_text(
                f"❌ **{code}** 资源频道未找到正片\n\n"
                f"可发送 `/javverify {code}` 向极搜验证番号有效性（单条，低频）")

    @client.on_message(filters.private & filters.command("javverify"))
    async def javverify_handler(_client: Client, message: Message):
        user_id = message.from_user.id
        if not _is_admin(user_id):
            return
        arg = message.text.split(maxsplit=1)[1].strip() if message.text and " " in message.text else ""
        code = detect_jav_code(arg) or detect_jav_code(arg.replace(" ", ""))
        if not code:
            await message.reply("用法：`/javverify 番号`")
            return
        _m = await message.reply(f"🔍 向极搜查询 **{code}** ...")
        r = await jav_fetcher.verify_via_jisou(code)
        if r["ok"]:
            await _m.edit_text(f"✅ 极搜已确认番号 **{code}** 有效（库内暂无正片）\n\n`{r['info'][:600]}`")
        else:
            await _m.edit_text(f"❌ 极搜验证失败：{r['reason']}")

    @client.on_message(filters.private & filters.command("javbatch"))
    async def javbatch_handler(_client: Client, message: Message):
        user_id = message.from_user.id
        if not _is_admin(user_id):
            return
        task = _jav_batch.get("task")
        arg = message.text.split(maxsplit=1)[1].strip() if message.text and " " in message.text else ""
        if arg.lower() in ("stop", "cancel", "取消"):
            if task and not task.done():
                _jav_batch["stop"] = True
                await message.reply("🛑 正在请求中止，当前条目完成后停止")
            else:
                await message.reply("没有正在运行的批量任务")
            return
        if task and not task.done():
            await message.reply("⏳ 已有批量任务在运行（/javbatch stop 中止）")
            return
        reply_text = message.reply_to_message.text if (message.reply_to_message and message.reply_to_message.text) else ""
        codes = _parse_code_list(arg) or _parse_code_list(reply_text)
        if not codes:
            await message.reply(
                "用法：`/javbatch 番号1 番号2 …`（一行多行均可，单条消息内最多 300 个）\n"
                "或回复一条含番号列表的消息发送此命令\n\n"
                "中止：`/javbatch stop`；进度每 25 条汇报一次")
            return
        await message.reply(f"🚀 开始批量检索 **{len(codes)}** 个番号（纯检索不向任何群发消息）…")
        _jav_batch["task"] = asyncio.create_task(
            _run_jav_batch(user_id, codes, _client))

    # 视频走原有入库流程；文档（非视频）单独保存
    @client.on_message(filters.forwarded & filters.video)
    async def fwd_handler(_client, message):
        await _handle_forwarded_video(_client, message)

    # 非视频文档（xlsx/xls/csv 等）也保存下来，供服务器侧提取
    @client.on_message(filters.forwarded & filters.document)
    async def doc_handler(_client, message):
        await _save_forwarded_document(_client, message)

    @client.on_message(filters.private & filters.command("start"))
    async def start_handler(_client, message):
        await message.reply(
            "👋 **TG Emby Streamer Bot**\n\n"
            "将我添加到频道，然后转发视频给我即可手动入库。\n\n"
            "**使用方法：**\n"
            "1. 转发单条视频 → 按频道规则/识别番号自动命名，交互式确认（可手动修改）\n"
            "2. 转发多条视频 → 进入剧集模式，输入剧名统一命名；\n"
            "   回复 **`-`** 则保留每条独立标题（适合番号/混合命名）\n"
            "3. 识别到番号（如 SSIS-806、FC2-PPV-1234567）时，可直接点击\n"
            "   「⚡ 全部按 18+ 入库」一键完成\n"
            "4. `/jav 番号` → 在资源频道搜索正片并自动入库（不发群消息）\n"
            "5. `/javbatch 番号1 番号2…` → 批量检索入库（后台执行，\n"
            "   完成后推送一条汇总 + 海报通知；/javbatch stop 中止）\n"
            "6. `/javverify 番号` → 通过极搜验证番号有效性\n"
            "7. 发送 /cancel → 取消当前操作"
        )

    @client.on_message(filters.private & filters.text & ~filters.command(["start", "help"]))
    async def text_handler(_client, message):
        await _handle_text_message(_client, message)

    @client.on_callback_query()
    async def cb_handler(_client, callback):
        await _handle_callback_query(_client, callback)

    return client


async def start_bot_handler(retries: int = 5):
    global _bot_client, _bot_started
    if _bot_started:
        return
    if not settings.notify.bot_token:
        logger.warning("Bot token not configured — bot handler not started")
        return
    # 重启/重建连接可能触发 Telegram 的 bot 登录限流（FLOOD_WAIT），
    # 自动等待并重试，避免人工再重启
    delay = 5.0
    for _ in range(max(1, retries)):
        try:
            _bot_client = await _create_bot_client()
            await _bot_client.start()
            _bot_started = True
            logger.info("Bot handler started successfully")
            return
        except Exception as e:
            logger.error(f"Bot handler start failed: {e}")
            wait = getattr(e, "value", None)
            if isinstance(e, FloodWait) and isinstance(wait, (int, float)) and wait > 0:
                delay = float(wait) + 5.0
            _bot_client = None
            if delay >= 600:
                logger.warning(f"Bot handler rate-limited until +{delay:.0f}s — retrying in background later")
                break
            await asyncio.sleep(delay)
            delay *= 2
    # 大延迟限流：后台延时重试，不阻塞启动
    if delay >= 600:
        async def _later():
            await asyncio.sleep(delay)
            global _bot_started
            _bot_started = False
            await start_bot_handler(retries=1)
        asyncio.create_task(_later())


async def stop_bot_handler():
    global _bot_client, _bot_started
    if _bot_client:
        try:
            await _bot_client.stop()
        except Exception:
            pass
        _bot_client = None
    _bot_started = False
    logger.info("Bot handler stopped")


async def get_bot() -> Client | None:
    if _bot_client is not None:
        try:
            if _bot_client.is_connected:
                return _bot_client
        except Exception:
            pass
    return None


async def _send_via_user(message: str) -> bool:
    try:
        from app.telegram.client import get_client
        client = await get_client()
        target_clean = settings.notify.target.lstrip("@")
        try:
            await client.send_message(int(target_clean), message)
        except ValueError:
            await client.send_message(target_clean, message)
        logger.info(f"通知已通过账号发送到 {settings.notify.target}")
        return True
    except Exception as e:
        logger.warning(f"账号发送通知失败: {e}")
        return False


async def send_notify(message: str) -> bool:
    try:
        if not settings.notify.enabled:
            return False
        target = settings.notify.target
        if not target:
            return False
        bot = await get_bot()
        if bot is None:
            return await _send_via_user(message)
        target_clean = target.lstrip("@")
        try:
            await bot.send_message(int(target_clean), message)
        except ValueError:
            await bot.send_message(target_clean, message)
        logger.info(f"通知已通过 Bot 发送到 {target}")
        return True
    except Exception as e:
        logger.warning(f"Bot发送通知失败，尝试账号发送: {e}")
        return await _send_via_user(message)


async def stop_bot():
    await stop_bot_handler()
