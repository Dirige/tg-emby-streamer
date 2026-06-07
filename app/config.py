"""
Telegram Emby Streamer - 配置管理

从 .env 文件加载配置，提供类型安全的配置访问。
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent


class TelegramSettings(BaseSettings):
    """Telegram 相关配置"""
    
    # Telegram API 凭据 (从 https://my.telegram.org 获取)
    api_id: int = Field(default=0, alias="TELEGRAM_API_ID")
    api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    
    # 手机号 (仅用于生成 Session String)
    phone: str = Field(default="", alias="TELEGRAM_PHONE")
    
    # 用户 ID
    user_id: int = Field(default=0, alias="TELEGRAM_USER_ID")
    
    # Bot Token (用于流媒体负载均衡，可选)
    bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    
    # Session String (运行 generate_session.py 生成)
    session_string: str = Field(default="", alias="TELEGRAM_SESSION_STRING")
    
    # 私有群聊/频道 ID (存储媒体的目标，负数)
    channel_id: int = Field(default=0, alias="TELEGRAM_CHANNEL_ID")
    
    # 监听频道列表 (逗号分隔的频道 ID，负数)
    monitor_channels: str = Field(default="", alias="TELEGRAM_MONITOR_CHANNELS")
    
    # 额外 Bot Token (用于流媒体负载均衡，逗号分隔，可选)
    bot_tokens: str = Field(default="", alias="TELEGRAM_BOT_TOKENS")
    
    # 频道分类映射 (channel_id=category，逗号分隔)
    channel_categories: str = Field(default="", alias="CHANNEL_CATEGORIES")
    
    # 18+ 频道列表 (逗号分隔)
    adult_channels: str = Field(default="", alias="ADULT_CHANNELS")

    @property
    def monitor_channel_list(self) -> list[int]:
        """获取监听频道 ID 列表"""
        if not self.monitor_channels:
            return []
        return [int(c.strip()) for c in self.monitor_channels.split(",") if c.strip()]

    @property
    def bot_token_list(self) -> list[str]:
        """获取 Bot Token 列表"""
        if not self.bot_tokens:
            return []
        return [t.strip() for t in self.bot_tokens.split(",") if t.strip()]

    @property
    def channel_category_map(self) -> dict[str, str]:
        """获取频道分类映射"""
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
        """获取 18+ 频道 ID 列表"""
        if not self.adult_channels:
            return []
        return [int(c.strip()) for c in self.adult_channels.split(",") if c.strip()]

    class Config:
        env_prefix = "TELEGRAM_"
        populate_by_name = True


class ProxySettings(BaseSettings):
    """外部代理配置"""
    
    # 是否启用外部代理
    enabled: bool = Field(default=False, alias="PROXY_ENABLED")
    
    # 代理协议 (socks5 / http)
    scheme: str = Field(default="socks5", alias="PROXY_SCHEME")
    
    # 代理地址
    hostname: str = Field(default="127.0.0.1", alias="PROXY_HOST")
    
    # 代理端口
    port: int = Field(default=10808, alias="PROXY_PORT")
    
    # 订阅地址 (自动获取代理节点，可选)
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
    """sing-box 内置代理配置"""
    
    # 是否启用 sing-box
    enabled: bool = Field(default=False, alias="SINGBOX_ENABLED")
    
    # 服务器地址
    address: str = Field(default="", alias="SINGBOX_ADDRESS")
    
    # 服务器端口
    port: int = Field(default=443, alias="SINGBOX_PORT")
    
    # UUID / 密码
    uuid: str = Field(default="", alias="SINGBOX_UUID")
    
    # 路径
    path: str = Field(default="/?ed=2048", alias="SINGBOX_PATH")
    
    # Host / SNI
    host: str = Field(default="", alias="SINGBOX_HOST")
    
    # 是否启用 TLS
    tls: bool = Field(default=True, alias="SINGBOX_TLS")
    
    # TLS 指纹
    fingerprint: str = Field(default="chrome", alias="SINGBOX_FINGERPRINT")
    
    # 本地 SOCKS5 端口
    socks_port: int = Field(default=10808, alias="SINGBOX_SOCKS_PORT")
    
    # 协议 (vless/trojan/vmess/shadowsocks)
    protocol: str = Field(default="vless", alias="SINGBOX_PROTOCOL")
    
    # 加密方式
    cipher: str = Field(default="auto", alias="SINGBOX_CIPHER")

    class Config:
        populate_by_name = True


class StreamSettings(BaseSettings):
    """流媒体服务器配置"""
    
    # 监听地址
    host: str = Field(default="0.0.0.0", alias="STREAM_HOST")
    
    # 监听端口
    port: int = Field(default=8001, alias="STREAM_PORT")
    
    # 外网访问地址 (用于 STRM 文件中的 URL)
    base_url: str = Field(default="http://localhost:8001", alias="BASE_URL")
    
    # 内网访问地址
    local_url: str = Field(default="http://localhost:8001", alias="STREAM_LOCAL_URL")
    
    # 分块大小 (1MB)
    chunk_size: int = 1048576
    
    # 并发下载数
    concurrency: int = Field(default=3, alias="STREAM_CONCURRENCY")
    
    # 最大重试次数
    max_retries: int = Field(default=3, alias="STREAM_MAX_RETRIES")
    
    # 重试延迟 (秒)
    retry_delay: float = Field(default=2.0, alias="STREAM_RETRY_DELAY")

    class Config:
        populate_by_name = True


class DatabaseSettings(BaseSettings):
    """数据库配置"""
    
    # 数据库 URL (默认使用 SQLite)
    url: str = Field(default="sqlite+aiosqlite:///./data/media.db", alias="DATABASE_URL")

    class Config:
        populate_by_name = True


class CacheSettings(BaseSettings):
    """缓存配置"""
    
    # 缓存目录
    dir: str = Field(default="./cache", alias="CACHE_DIR")
    
    # 媒体目录
    media_path: str = Field(default="./media", alias="MEDIA_PATH")
    
    # 内存缓存大小 (MB)
    memory_cache_size_mb: int = Field(default=256, alias="MEMORY_CACHE_SIZE")
    
    # 磁盘缓存上限 (GB)
    disk_cache_max_gb: int = Field(default=50, alias="DISK_CACHE_MAX_GB")
    
    # 是否启用预读取
    prefetch_enabled: bool = True
    
    # 预读取块数
    prefetch_chunks: int = 30

    class Config:
        populate_by_name = True

    @property
    def cache_path(self) -> Path:
        """获取缓存目录路径"""
        p = Path(self.dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_path_obj(self) -> Path:
        """获取媒体目录路径"""
        p = Path(self.media_path)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


class STRMSettings(BaseSettings):
    """STRM 文件输出配置"""
    
    # STRM 输出目录
    output_dir: str = Field(default="./strm", alias="STRM_OUTPUT_DIR")

    class Config:
        populate_by_name = True

    @property
    def output_path(self) -> Path:
        """获取 STRM 输出目录路径"""
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


class WebSettings(BaseSettings):
    """Web 面板配置"""
    
    # Web 面板用户名 (留空则无需登录)
    username: str = Field(default="", alias="WEB_USERNAME")
    
    # Web 面板密码
    password: str = Field(default="", alias="WEB_PASSWORD")

    class Config:
        populate_by_name = True


class Settings:
    """全局设置管理器"""
    
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


# 全局设置实例
settings = Settings()
