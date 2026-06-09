import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class TelegramSettings(BaseSettings):
    api_id: int = Field(default=0, alias="TELEGRAM_API_ID")
    api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    phone: str = Field(default="", alias="TELEGRAM_PHONE")
    user_id: int = Field(default=0, alias="TELEGRAM_USER_ID")
    bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    session_string: str = Field(default="", alias="TELEGRAM_SESSION_STRING")
    channel_id: int = Field(default=0, alias="TELEGRAM_CHANNEL_ID")
    monitor_channels: str = Field(default="", alias="TELEGRAM_MONITOR_CHANNELS")
    bot_tokens: str = Field(default="", alias="TELEGRAM_BOT_TOKENS")
    channel_categories: str = Field(default="", alias="CHANNEL_CATEGORIES")
    adult_channels: str = Field(default="-1001491440122", alias="ADULT_CHANNELS")

    @property
    def monitor_channel_list(self) -> list[int]:
        if not self.monitor_channels:
            return []
        return [int(c.strip()) for c in self.monitor_channels.split(",") if c.strip()]

    @property
    def bot_token_list(self) -> list[str]:
        if not self.bot_tokens:
            return []
        return [t.strip() for t in self.bot_tokens.split(",") if t.strip()]

    @property
    def channel_category_map(self) -> dict[str, str]:
        result = {}
        if not self.channel_categories:
            return result
        for item in self.channel_categories.split(","):
            item = item.strip()
            if "=" in item:
                cid, cat = item.split("=", 1)
                result[cid.strip()] = cat.strip()
        return result

    @property
    def adult_channel_list(self) -> list[int]:
        if not self.adult_channels:
            return []
        return [int(c.strip()) for c in self.adult_channels.split(",") if c.strip()]

    class Config:
        env_prefix = "TELEGRAM_"
        populate_by_name = True


class ProxySettings(BaseSettings):
    enabled: bool = Field(default=False, alias="PROXY_ENABLED")
    scheme: str = Field(default="socks5", alias="PROXY_SCHEME")
    hostname: str = Field(default="127.0.0.1", alias="PROXY_HOST")
    port: int = Field(default=10808, alias="PROXY_PORT")
    sub_url: str = Field(default="", alias="PROXY_SUB_URL")

    class Config:
        populate_by_name = True

    def to_dict(self) -> dict | None:
        if not self.enabled:
            return None
        return {
            "scheme": self.scheme,
            "hostname": self.hostname,
            "port": self.port,
        }


class SingboxSettings(BaseSettings):
    enabled: bool = Field(default=False, alias="SINGBOX_ENABLED")
    address: str = Field(default="", alias="SINGBOX_ADDRESS")
    port: int = Field(default=443, alias="SINGBOX_PORT")
    uuid: str = Field(default="", alias="SINGBOX_UUID")
    path: str = Field(default="/?ed=2048", alias="SINGBOX_PATH")
    host: str = Field(default="", alias="SINGBOX_HOST")
    tls: bool = Field(default=True, alias="SINGBOX_TLS")
    fingerprint: str = Field(default="chrome", alias="SINGBOX_FINGERPRINT")
    socks_port: int = Field(default=10808, alias="SINGBOX_SOCKS_PORT")
    protocol: str = Field(default="vless", alias="SINGBOX_PROTOCOL")
    cipher: str = Field(default="auto", alias="SINGBOX_CIPHER")

    class Config:
        populate_by_name = True


class StreamSettings(BaseSettings):
    host: str = Field(default="0.0.0.0", alias="STREAM_HOST")
    port: int = Field(default=8000, alias="STREAM_PORT")
    base_url: str = Field(default="http://localhost:8000", alias="BASE_URL")
    local_url: str = Field(default="http://localhost:8000", alias="STREAM_LOCAL_URL")
    chunk_size: int = 1048576  # 1MB
    concurrency: int = Field(default=3, alias="STREAM_CONCURRENCY")
    max_retries: int = Field(default=3, alias="STREAM_MAX_RETRIES")
    retry_delay: float = Field(default=2.0, alias="STREAM_RETRY_DELAY")

    class Config:
        populate_by_name = True


class DatabaseSettings(BaseSettings):
    url: str = Field(default="sqlite+aiosqlite:///./data/media.db", alias="DATABASE_URL")

    class Config:
        populate_by_name = True


class CacheSettings(BaseSettings):
    dir: str = Field(default="./cache", alias="CACHE_DIR")
    media_path: str = Field(default="./media", alias="MEDIA_PATH")
    memory_cache_size_mb: int = Field(default=256, alias="MEMORY_CACHE_SIZE")
    disk_cache_max_gb: int = Field(default=50, alias="DISK_CACHE_MAX_GB")
    prefetch_enabled: bool = True
    prefetch_chunks: int = 30

    class Config:
        populate_by_name = True

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


class STRMSettings(BaseSettings):
    output_dir: str = Field(default="./strm", alias="STRM_OUTPUT_DIR")

    class Config:
        populate_by_name = True

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


class WebSettings(BaseSettings):
    username: str = Field(default="", alias="WEB_USERNAME")
    password: str = Field(default="", alias="WEB_PASSWORD")
    stream_token: str = Field(default="", alias="STREAM_TOKEN")

    class Config:
        populate_by_name = True


class Settings:
    def __init__(self):
        self.telegram = TelegramSettings()
        self.proxy = ProxySettings()
        self.singbox = SingboxSettings()
        self.stream = StreamSettings()
        self.database = DatabaseSettings()
        self.cache = CacheSettings()
        self.strm = STRMSettings()
        self.web = WebSettings()

    def reload(self):
        """重新加载 .env 并刷新配置"""
        load_dotenv(override=True)
        self.telegram = TelegramSettings()
        self.proxy = ProxySettings()
        self.singbox = SingboxSettings()
        self.stream = StreamSettings()
        self.database = DatabaseSettings()
        self.cache = CacheSettings()
        self.strm = STRMSettings()
        self.web = WebSettings()


settings = Settings()
