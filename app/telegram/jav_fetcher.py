"""番号搜索取片 —— 全程只「读」不「发」。

机制（2026-11 实测验证）：
- 资源频道（av8plus 等）的正片说明文字以番号开头，TG 官方搜索 API
  `search_messages(chat, code)` 可直接命中 1~2GB 完整正片；
- 全过程不向任何群/频道发送消息，批量检索无反垃圾风险；
- 极搜群（JISOU）回复的是 GIF 预览 + 外链文本，不含正片，
  仅作为单条查询失败时的番号有效性验证兜底（单条低频，不会刷群）。
"""
import asyncio
import logging
import re

from pyrogram.errors import PeerIdInvalid, FloodWait

from app.telegram.client import get_client
from app.media.jav import detect_jav_code

logger = logging.getLogger(__name__)

# 极搜官方群（番号验证兜底用）
JISOU_CHAT_ID = -1002252096596

# 正片资源频道，按优先级排列（说明文字以番号开头、发布完整版正片）
SEARCH_CHANNELS: list[int] = [
    -1001981480055,  # av8plus 🎥 色情云|番号资源库（实测最全，正片 1~2GB）
    -1001556987893,  # AV无码原片
    -1003998056356,  # avmm3388
    -1001313648085,  # diyisec 水果派
]

_peer_cache_warmed = False


def safe_text(text: str | None) -> str:
    """Telegram 文本可能含未配对代理字节，统一清洗避免后续编码崩溃。"""
    if not text:
        return ""
    try:
        return text.encode("utf-16", "surrogatepass").decode("utf-16")
    except Exception:
        return text


def _matches_code(msg, code: str) -> bool:
    media = msg.video or msg.document
    if not media:
        return False
    fn = ""
    for attr in (getattr(media, "attributes", []) or []):
        if hasattr(attr, "file_name") and attr.file_name:
            fn = attr.file_name
    cap = msg.caption or ""
    # 任一文本的番号识别结果与目标一致才算命中（避免搜到无关消息）
    return detect_jav_code(safe_text(cap)) == code or detect_jav_code(fn) == code


async def _warm_peer_cache(client) -> None:
    """in-memory 会话的 peer 缓存可能不含未被监听的频道；遍历一次对话列表预热。"""
    global _peer_cache_warmed
    if _peer_cache_warmed:
        return
    try:
        async for _ in client.get_dialogs(limit=200):
            pass
        _peer_cache_warmed = True
    except Exception as e:
        logger.warning(f"预热 peer 缓存失败: {e}")


async def find_video(code: str, per_channel_limit: int = 10):
    """在资源频道中搜索番号对应的完整正片。

    返回 (chat_id, message)；未找到返回 (None, None)。
    同一 file_unique_id 的重发只保留一条，命中最高优先级频道即停。
    """
    if not code:
        return None, None
    client = await get_client()
    await _warm_peer_cache(client)
    candidates: dict[str, tuple[int, object]] = {}
    for ch_id in SEARCH_CHANNELS:
        # 先校验 peer 可访问（不是成员/未缓存时直接跳过该频道）
        try:
            chat = await client.get_chat(ch_id)
        except PeerIdInvalid:
            logger.info(f"搜索跳过频道 {ch_id}: 无权访问/未缓存")
            continue
        except Exception as e:
            logger.info(f"搜索跳过频道 {ch_id}: {e}")
            continue
        try:
            async for msg in client.search_messages(chat.id, query=code, limit=per_channel_limit):
                if not _matches_code(msg, code):
                    continue
                media = msg.video or msg.document
                uid = getattr(media, "file_unique_id", "") or f"{msg.id}"
                prev = candidates.get(uid)
                if prev is None or msg.id > prev[1].id:
                    candidates[uid] = (ch_id, msg)
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.warning(f"搜索频道 {ch_id} 失败: {e}")
        if candidates:
            break  # 最高优先级频道命中即可
    if not candidates:
        return None, None
    # 多版本取体积最大的一条（通常为完整版）
    def _size(cm):
        media = cm[1].video or cm[1].document
        return getattr(media, "file_size", 0) or 0
    chat_id, msg = max(candidates.values(), key=_size)
    return chat_id, msg


async def verify_via_jisou(code: str, timeout: float = 25.0) -> dict:
    """向极搜群发一条番号验证有效性（单条接口，批量勿用）。

    返回 {"ok": bool, "info": str, "reason": str}。
    """
    client = await get_client()
    try:
        try:
            await client.send_message(JISOU_CHAT_ID, code)
        except PeerIdInvalid:
            await _warm_peer_cache(client)
            await client.send_message(JISOU_CHAT_ID, code)
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        try:
            await client.send_message(JISOU_CHAT_ID, code)
        except Exception as ex:
            return {"ok": False, "info": "", "reason": f"极搜发送失败: {ex}"}
    except Exception as e:
        return {"ok": False, "info": "", "reason": f"极搜发送失败: {e}"}

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(3)
        try:
            async for m in client.get_chat_history(JISOU_CHAT_ID, limit=8):
                if m.from_user and not m.from_user.is_bot:
                    continue
                text = safe_text(m.text or m.caption or "")
                if not text:
                    continue
                # 回复形如 "您查询的番号 PIYO-027 信息如下：..."
                if text.startswith("您查询的番号") or text.startswith("未查询到"):
                    if detect_jav_code(text[:30]) == code or "未查询到" in text[:20]:
                        return {"ok": "未查询到" not in text[:20], "info": text[:1500]}
        except PeerIdInvalid:
            return {"ok": False, "info": "", "reason": "极搜群 peer 未缓存"}
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception:
            pass
    return {"ok": False, "info": "", "reason": "超时无回复（可能限流，稍后重试）"}
