import asyncio
import os
import sys

if sys.version_info >= (3, 14):
    asyncio.set_event_loop(asyncio.new_event_loop())

import uvicorn
from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.stream.host,
        port=settings.stream.port,
        reload=False,
        log_level="info",
        # 容器默认关闭 URL 访问日志，避免 STRM 查询参数中的播放 Token 落入日志。
        access_log=os.environ.get("TGEMBY_ACCESS_LOG", "true").lower() in {"true", "1", "yes"},
    )
