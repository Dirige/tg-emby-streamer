import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


async def refresh_library(jav_codes=None) -> dict:
    url = settings.emby.url
    api_key = settings.emby.api_key
    if not url or not api_key:
        return {"success": False, "reason": "Emby 未配置"}
    try:
        async with httpx.AsyncClient(timeout=30, headers={"X-Emby-Token": api_key}) as client:
            r = await client.post(
                f"{url}/emby/Library/Refresh",
            )
            if r.status_code in (200, 204):
                logger.info("Emby library refresh triggered")
                if jav_codes:
                    from app.media.emby_versions import schedule_version_reconcile
                    schedule_version_reconcile(jav_codes)
                return {"success": True, "message": "已通知 Emby 刷新媒体库"}
            logger.warning(f"Emby refresh failed {r.status_code}: {r.text[:200]}")
            return {"success": False, "reason": f"Emby 返回 {r.status_code}"}
    except Exception as e:
        logger.warning(f"Emby refresh error: {e}")
        return {"success": False, "reason": str(e)}
