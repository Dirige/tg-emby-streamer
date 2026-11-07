import json
import time
import logging
from pathlib import Path
from app.config import settings

logger = logging.getLogger(__name__)

SCAN_STATE_PATH = Path("data/scan_state.json")
if not SCAN_STATE_PATH.is_absolute():
    from app.config import BASE_DIR
    SCAN_STATE_PATH = BASE_DIR / SCAN_STATE_PATH


class ScanState:
    def __init__(self):
        self._state: dict[str, int] = {}
        self._dirty = False
        self._last_save = 0.0
        self._load()

    def _load(self):
        if SCAN_STATE_PATH.exists():
            try:
                self._state = json.loads(SCAN_STATE_PATH.read_text())
                logger.info(f"Scan state loaded: {len(self._state)} channels")
            except Exception as e:
                logger.warning(f"Failed to load scan state: {e}")

    def get_checkpoint(self, chat_id: int) -> int:
        return self._state.get(str(chat_id), 0)

    def save_checkpoint(self, chat_id: int, message_id: int):
        key = str(chat_id)
        if self._state.get(key, 0) < message_id:
            self._state[key] = message_id
            self._dirty = True

    def save(self):
        if not self._dirty:
            return
        try:
            SCAN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            SCAN_STATE_PATH.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False)
            )
            self._dirty = False
            self._last_save = time.time()
        except Exception as e:
            logger.warning(f"Failed to save scan state: {e}")

    def auto_save(self):
        interval = getattr(settings.telegram, "scan_save_interval", 60)
        if self._dirty and (time.time() - self._last_save) >= interval:
            self.save()
