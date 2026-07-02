import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml
    _has_yaml = True
except ImportError:
    _has_yaml = False

try:
    from ruamel.yaml import YAML
    _has_ruamel = True
except ImportError:
    _has_ruamel = False

BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_PATH = os.environ.get("CONFIG_PATH") or str(BASE_DIR / "config.yaml")

ENV_TO_KEY = {
    "TELEGRAM_API_ID": "telegram.api_id",
    "TELEGRAM_API_HASH": "telegram.api_hash",
    "TELEGRAM_PHONE": "telegram.phone",
    "TELEGRAM_USER_ID": "telegram.user_id",
    "TELEGRAM_SESSION_STRING": "telegram.session_string",
    "PROXY_ENABLED": "proxy.enabled",
    "PROXY_SCHEME": "proxy.scheme",
    "PROXY_HOST": "proxy.host",
    "PROXY_PORT": "proxy.port",
    "STREAM_HOST": "server.host",
    "STREAM_PORT": "server.port",
    "BASE_URL": "server.base_url",
    "STREAM_LOCAL_URL": "server.local_url",
    "STREAM_CONCURRENCY": "server.concurrency",
    "STREAM_TOKEN": "server.stream_token",
    "DATABASE_URL": "database.url",
    "CACHE_DIR": "storage.cache_dir",
    "MEMORY_CACHE_SIZE": "storage.memory_cache_size_mb",
    "DISK_CACHE_MAX_GB": "storage.disk_cache_max_gb",
    "STRM_OUTPUT_DIR": "storage.strm_dir",
    "WEB_USERNAME": "auth.username",
    "WEB_PASSWORD": "auth.password",
    "NOTIFY_ENABLED": "notify.enabled",
    "NOTIFY_TARGET": "notify.target",
    "NOTIFY_BOT_TOKEN": "notify.bot_token",
    "TELEGRAM_MONITOR_CHANNELS": "telegram.monitor_channels",
    "MEDIA_PATH": "storage.media_path",
}

KEY_TO_ENV = {v: k for k, v in ENV_TO_KEY.items()}


def _deep_get(data: dict, key_path: str) -> Any:
    keys = key_path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def _deep_set(data: dict, key_path: str, value: Any):
    keys = key_path.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _deep_delete(data: dict, key_path: str):
    keys = key_path.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            return
        current = current[key]
    current.pop(keys[-1], None)


def _load_yaml_data() -> dict:
    path = Path(CONFIG_PATH)
    if not path.exists():
        return {}
    try:
        if _has_ruamel:
            yaml_inst = YAML(typ="safe")
            with open(path, encoding="utf-8") as f:
                return yaml_inst.load(f) or {}
        elif _has_yaml:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        else:
            return _simple_yaml_parse(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_yaml_data(data: dict):
    path = Path(CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _has_ruamel:
        yaml_inst = YAML()
        yaml_inst.indent(mapping=2, sequence=4, offset=2)
        yaml_inst.default_flow_style = False
        with open(path, "w", encoding="utf-8") as f:
            yaml_inst.dump(data, f)
    elif _has_yaml:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
    else:
        _simple_yaml_write(data, path)


def _simple_yaml_parse(text: str) -> dict:
    result = {}
    current = result
    stack = []
    current_indent = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.endswith(":"):
            key = stripped[:-1].strip()
            if indent <= current_indent and stack:
                depth = (current_indent - indent) // 2
                for _ in range(min(depth, len(stack) - 1)):
                    stack.pop()
                current = stack[-1] if stack else result
            node = {}
            current[key] = node
            stack.append(node)
            current = node
            current_indent = indent
        elif ":" in stripped:
            key, _, val_str = stripped.partition(":")
            key = key.strip()
            val_str = val_str.strip().strip('"').strip("'")
            if val_str.lower() == "true":
                value = True
            elif val_str.lower() == "false":
                value = False
            elif val_str.lower() in ("none", "null", "~"):
                value = None
            elif val_str.isdigit() or (val_str.startswith("-") and val_str[1:].isdigit()):
                value = int(val_str)
            elif re.match(r"^-?\d+\.\d*$", val_str):
                value = float(val_str)
            else:
                value = val_str
            if indent <= current_indent:
                stack.clear()
                current = result
            current[key] = value
            current_indent = indent
    return result


def _simple_yaml_write(data: dict, path: Path, indent: int = 0):
    lines = []
    for key, value in data.items():
        prefix = "  " * indent
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.extend(_simple_yaml_serialize(value, indent + 1))
        elif isinstance(value, bool):
            lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{prefix}{key}: null")
        else:
            lines.append(f"{prefix}{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _simple_yaml_serialize(data: dict, indent: int) -> list[str]:
    lines = []
    for key, value in data.items():
        prefix = "  " * indent
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.extend(_simple_yaml_serialize(value, indent + 1))
        elif isinstance(value, bool):
            lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{prefix}{key}: null")
        else:
            lines.append(f"{prefix}{key}: {value}")
    return lines


def get_config_value(key_path: str):
    data = _load_yaml_data()
    return _deep_get(data, key_path)


def save_config_value(key_path: str, value):
    data = _load_yaml_data()
    if value is None or value == "":
        _deep_delete(data, key_path)
    else:
        _deep_set(data, key_path, value)
    _write_yaml_data(data)


def save_config(updates: dict):
    data = _load_yaml_data()
    for key, value in updates.items():
        if key in ENV_TO_KEY:
            key = ENV_TO_KEY[key]
        if value is None or value == "":
            _deep_delete(data, key)
        else:
            _deep_set(data, key, value)
    _write_yaml_data(data)


def _coerce_type(value: str, existing):
    if existing is None:
        return value
    if isinstance(existing, bool):
        return value.lower() in ("true", "1", "yes", "on")
    if isinstance(existing, int):
        try:
            return int(value)
        except ValueError:
            return value
    if isinstance(existing, float):
        try:
            return float(value)
        except ValueError:
            return value
    return value


class TelegramSettings:
    def __init__(self, data: dict):
        d = data.get("telegram", {})
        self.api_id: int = int(d.get("api_id", 0))
        self.api_hash: str = str(d.get("api_hash", ""))
        self.phone: str = str(d.get("phone", ""))
        self.user_id: int = int(d.get("user_id", 0))
        self.session_string: str = str(d.get("session_string", ""))
        self.monitor_interval_minutes: int = int(d.get("monitor_interval_minutes", 10))
        raw_channels = d.get("monitor_channels", "")
        self.monitor_channels: list[str] = [
            c.strip() for c in raw_channels.split(",") if c.strip()
        ] if isinstance(raw_channels, str) else (raw_channels or [])


class ProxySettings:
    def __init__(self, data: dict):
        d = data.get("proxy", {})
        self.enabled: bool = bool(d.get("enabled", False))
        self.scheme: str = str(d.get("scheme", "socks5"))
        self.hostname: str = str(d.get("host", "127.0.0.1"))
        self.port: int = int(d.get("port", 10808))

    def to_dict(self) -> dict | None:
        if not self.enabled:
            return None
        return {
            "scheme": self.scheme,
            "hostname": self.hostname,
            "port": self.port,
        }


class StreamSettings:
    def __init__(self, data: dict):
        d = data.get("server", {})
        self.host: str = str(d.get("host", "0.0.0.0"))
        self.port: int = int(d.get("port", 8001))
        self.base_url: str = str(d.get("base_url", "http://localhost:8001"))
        self.local_url: str = str(d.get("local_url", "http://localhost:8001"))
        self.concurrency: int = int(d.get("concurrency", 3))


class DatabaseSettings:
    def __init__(self, data: dict):
        d = data.get("database", {})
        self.url: str = str(d.get("url", "sqlite+aiosqlite:////app/data/media.db"))


class CacheSettings:
    def __init__(self, data: dict):
        d = data.get("storage", {})
        self.dir: str = str(d.get("cache_dir", "./cache"))
        self.media_path: str = str(d.get("media_path", "./media"))
        self.memory_cache_size_mb: int = int(d.get("memory_cache_size_mb", 256))
        self.disk_cache_max_gb: int = int(d.get("disk_cache_max_gb", 50))
        self.prefetch_enabled: bool = True
        self.prefetch_chunks: int = 30

    @property
    def cache_path(self) -> Path:
        p = Path(self.dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_path_obj(self) -> Path:
        p = Path(self.media_path)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


class STRMSettings:
    def __init__(self, data: dict):
        d = data.get("storage", {})
        self.output_dir: str = str(d.get("strm_dir", "./strm"))

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


class WebSettings:
    def __init__(self, data: dict):
        auth = data.get("auth", {})
        server = data.get("server", {})
        self.username: str = str(auth.get("username", ""))
        self.password: str = str(auth.get("password", ""))
        self.stream_token: str = str(server.get("stream_token", ""))


class NotificationSettings:
    def __init__(self, data: dict):
        d = data.get("notify", {})
        self.enabled: bool = bool(d.get("enabled", False))
        self.target: str = str(d.get("target", ""))
        self.bot_token: str = str(d.get("bot_token", ""))


class Settings:
    def __init__(self):
        self._raw: dict = {}
        self._load()

    def _load(self):
        data = _load_yaml_data()
        for env_key, key_path in ENV_TO_KEY.items():
            env_val = os.environ.get(env_key)
            if env_val is not None:
                existing = _deep_get(data, key_path)
                _deep_set(data, key_path, _coerce_type(env_val, existing))
        self._raw = data
        self.telegram = TelegramSettings(data)
        self.proxy = ProxySettings(data)
        self.stream = StreamSettings(data)
        self.database = DatabaseSettings(data)
        self.cache = CacheSettings(data)
        self.strm = STRMSettings(data)
        self.web = WebSettings(data)
        self.notify = NotificationSettings(data)

    def reload(self):
        self._load()

    def reload_from_file(self):
        self._load()


settings = Settings()
