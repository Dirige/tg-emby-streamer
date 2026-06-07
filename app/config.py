"""
Telegram Emby Streamer - 配置管理
Configuration management using Pydantic Settings.

从 .env 文件加载配置，提供类型安全的配置访问。
Load configuration from .env file with type-safe access.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

# 加载 .env 文件 / Load .env file
load_dotenv()

# 项目根目录 / Project root directory
BASE_DIR = Path(__file__).resolve().parent.parent


class TelegramSettings(BaseSettings):
    """Telegram 相关配置 / Telegram configuration"""
    
    # Telegram API 凭据 (从 https://my.telegram.org 获取)
    # Telegram API credentials (get from https://my.telegram.org)
    api_id: int = Field(default=0, alias="TELEGRAM_API_ID")
    api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    
    # 手机号 (用于生成 Session) / Phone number (for session generation)
    phone: str = Field(default="", alias="TELEGRAM_PHONE")
    
    # 用户 ID / User ID
    user_id: int = Field(default=0, alias="TELEGRAM_USER_ID")
    
    # Bot Token (用于流媒体下载) / Bot Token (for media streaming)
    bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    
    # Session String (运行 generate_session.py 生成)
    # Session String (run generate_session.py to generate)
    session_string: str = Field(default="", alias="TELEGRAM_SESSION_STRING")
    
    # 私有群聊/频道 ID (存储媒体的目标)
    # Private group/channel ID (target for media storage)
    channel_id: int = Field(default=0, alias="TELEGRAM_CHANNEL_ID")
    
    # 监听频道列表 (逗号分隔)
    # Monitor channels (comma-separated)
    monitor_channels: str = Field(default="", alias="TELEGRAM_MONITOR_CHANNELS")
    
    # 额外 Bot Token (用于负载均衡，逗号分隔)
    # Additional Bot Tokens (for load balancing, comma-separated)
    bot_tokens: str = Field(default="", alias="TELEGRAM_BOT_TOKENS")
    
    # 频道分类映射 (channel_id=category, 逗号分隔)
    # Channel category mapping (channel_id=category, comma-separated)
    channel_categories: str = Field(default="", alias="CHANNEL_CATEGORIES")
    
    # 18+ 频道列表 (逗号分隔)
    # Adult channel list (comma-separated)
    adult_channels: str = Field(default="", alias="ADULT_CHANNELS")

    @property
    def monitor_channel_list(self) -> list[int]:
        """获取监听频道 ID 列表 / Get monitor channel ID list"""
        if not self.monitor_channels:
            return []
        return [int(c.strip()) for c in self.monitor_channels.split(",") if c.strip()]

    @property
    def bot_token_list(self) -> list[str]:
        """获取 Bot Token 列表 / Get bot token list"""
        if not self.bot_tokens:
            return []
        return [t.strip() for t in self.bot_tokens.split(",") if t.strip()]

    @property
    def channel_category_map(self) -> dict[str, str]:
        """获取频道分类映射 / Get channel category mapping"""
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
        """获取 18+ 频道 ID 列表 / Get adult channel ID list"""
        if not self.adult_channels:
            return []
        return [int(c.strip()) for c in self.adult_channels.split(",") if c.strip()]

    class Config:
        env_prefix = "TELEGRAM_"
        populate_by_name = True


class ProxySettings(BaseSettings):
    """外部代理配置 / External proxy configuration"""
    
    # 是否启用外部代理 / Enable external proxy
    enabled: bool = Field(default=False, alias="PROXY_ENABLED")
    
    # 代理协议 (socks5 / http) / Proxy scheme (socks5 / http)
    scheme: str = Field(default="socks5", alias="PROXY_SCHEME")
    
    # 代理地址 / Proxy hostname
    hostname: str = Field(default="127.0.0.1", alias="PROXY_HOST")
    
    # 代理端口 / Proxy port
    port: int = Field(default=10808, alias="PROXY_PORT")
    
    # 订阅地址 (自动获取代理节点) / Subscription URL (auto-fetch proxy nodes)
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
    """sing-box 内置代理配置 / sing-box built-in proxy configuration"""
    
    # 是否启用 sing-box / Enable sing-box
    enabled: bool = Field(default=False, alias="SINGBOX_ENABLED")
    
    # VLESS/Trojan/VMess 服务器地址 / Server address
    address: str = Field(default="", alias="SINGBOX_ADDRESS")
    
    # 服务器端口 / Server port
    port: int = Field(default=443, alias="SINGBOX_PORT")
    
    # UUID / 密码 / UUID / Password
    uuid: str = Field(default="", alias="SINGBOX_UUID")
    
    # 路径 / Path
    path: str = Field(default="/?ed=2048", alias="SINGBOX_PATH")
    
    # Host / SNI
    host: str = Field(default="", alias="SINGBOX_HOST")
    
    # 是否启用 TLS / Enable TLS
    tls: bool = Field(default=True, alias="SINGBOX_TLS")
    
    # TLS 指纹 / TLS fingerprint
    fingerprint: str = Field(default="chrome", alias="SINGBOX_FINGERPRINT")
    
    # 本地 SOCKS5 端口 / Local SOCKS5 port
    socks_port: int = Field(default=10808, alias="SINGBOX_SOCKS_PORT")
    
    # 协议 (vless/trojan/vmess/shadowsocks) / Protocol
    protocol: str = Field(default="vless", alias="SINGBOX_PROTOCOL")
    
    # 加密方式 / Cipher
    cipher: str = Field(default="auto", alias="SINGBOX_CIPHER")

    class Config:
        populate_by_name = True


class StreamSettings(BaseSettings):
    """流媒体服务器配置 / Stream server configuration"""
    
    # 监听地址 / Listen host
    host: str = Field(default="0.0.0.0", alias="STREAM_HOST")
    
    # 监听端口 / Listen port
    port: int = Field(default=8001, alias="STREAM_PORT")
    
    # 外网访问地址 (用于 STRM 文件) / External URL (for STRM files)
    base_url: str = Field(default="http://localhost:8001", alias="BASE_URL")
    
    # 内网访问地址 / Internal URL
    local_url: str = Field(default="http://localhost:8001", alias="STREAM_LOCAL_URL")
    
    # 分块大小 (1MB) / Chunk size (1MB)
    chunk_size: int = 1048576
    
    # 并发下载数 / Concurrent downloads
    concurrency: int = Field(default=3, alias="STREAM_CONCURRENCY")
    
    # 最大重试次数 / Max retry attempts
    max_retries: int = Field(default=3, alias="STREAM_MAX_RETRIES")
    
    # 重试延迟 (秒) / Retry delay (seconds)
    retry_delay: float = Field(default=2.0, alias="STREAM_RETRY_DELAY")

    class Config:
        populate_by_name = True


class DatabaseSettings(BaseSettings):
    """数据库配置 / Database configuration"""
    
    # 数据库 URL / Database URL
    url: str = Field(default="sqlite+aiosqlite:///./data/media.db", alias="DATABASE_URL")

    class Config:
        populate_by_name = True


class CacheSettings(BaseSettings):
    """缓存配置 / Cache configuration"""
    
    # 缓存目录 / Cache directory
    dir: str = Field(default="./cache", alias="CACHE_DIR")
    
    # 媒体目录 / Media directory
    media_path: str = Field(default="./media", alias="MEDIA_PATH")
    
    # 内存缓存大小 (MB) / Memory cache size (MB)
    memory_cache_size_mb: int = Field(default=256, alias="MEMORY_CACHE_SIZE")
    
    # 磁盘缓存上限 (GB) / Disk cache limit (GB)
    disk_cache_max_gb: int = Field(default=50, alias="DISK_CACHE_MAX_GB")
    
    # 是否启用预读取 / Enable prefetch
    prefetch_enabled: bool = True
    
    # 预读取块数 / Prefetch chunks
    prefetch_chunks: int = 30

    class Config:
        populate_by_name = True

    @property
    def cache_path(self) -> Path:
        """获取缓存目录路径 / Get cache directory path"""
        p = Path(self.dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_path_obj(self) -> Path:
        """获取媒体目录路径 / Get media directory path"""
        p = Path(self.media_path)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


class STRMSettings(BaseSettings):
    """STRM 文件输出配置 / STRM file output configuration"""
    
    # STRM 输出目录 / STRM output directory
    output_dir: str = Field(default="./strm", alias="STRM_OUTPUT_DIR")

    class Config:
        populate_by_name = True

    @property
    def output_path(self) -> Path:
        """获取 STRM 输出目录路径 / Get STRM output directory path"""
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


class WebSettings(BaseSettings):
    """Web 面板配置 / Web dashboard configuration"""
    
    # Web 面板用户名 / Web dashboard username
    username: str = Field(default="", alias="WEB_USERNAME")
    
    # Web 面板密码 / Web dashboard password
    password: str = Field(default="", alias="WEB_PASSWORD")

    class Config:
        populate_by_name = True


class Settings:
    """全局设置管理器 / Global settings manager"""
    
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
        """重新加载 .env 并刷新配置 / Reload .env and refresh settings"""
        load_dotenv(override=True)
        self.telegram = TelegramSettings()
        self.proxy = ProxySettings()
        self.singbox = SingboxSettings()
        self.stream = StreamSettings()
        self.database = DatabaseSettings()
        self.cache = CacheSettings()
        self.strm = STRMSettings()
        self.web = WebSettings()


# 全局设置实例 / Global settings instance
settings = Settings()
