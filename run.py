import asyncio
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
    )
