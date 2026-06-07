"""
Telegram Emby Streamer - 数据模型
SQLAlchemy database models for media metadata storage.
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from app.database import Base


class Media(Base):
    """
    媒体记录表 / Media records table
    
    存储从 Telegram 频道解析的视频元数据。
    Stores video metadata parsed from Telegram channels.
    """
    __tablename__ = "media"

    # 自增主键 / Auto-increment primary key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Telegram 消息 ID / Telegram message ID
    message_id = Column(Integer, nullable=False, index=True)
    
    # 来源频道/群聊 ID / Source channel/group ID
    chat_id = Column(String, nullable=False, index=True)
    
    # 文件名 / File name
    file_name = Column(String, nullable=True)
    
    # Telegram 文件 ID (用于下载) / Telegram file ID (for downloading)
    file_id = Column(String, nullable=True)
    
    # Telegram 唯一文件 ID / Telegram unique file ID
    file_unique_id = Column(String, nullable=True)
    
    # 文件大小 (字节) / File size (bytes)
    size = Column(Integer, nullable=True)
    
    # 视频时长 (秒) / Video duration (seconds)
    duration = Column(Integer, nullable=True)
    
    # MIME 类型 / MIME type
    mime_type = Column(String, nullable=True)
    
    # 视频宽度 / Video width
    width = Column(Integer, nullable=True)
    
    # 视频高度 / Video height
    height = Column(Integer, nullable=True)
    
    # 解析后的显示名称 / Parsed display name
    display_name = Column(String, nullable=True)
    
    # 分类 (tv/movie/anime/dongman/cosplay 等)
    # Category (tv/movie/anime/dongman/cosplay etc.)
    category = Column(String, nullable=True)
    
    # 季数 / Season number
    season = Column(Integer, nullable=True)
    
    # 集数 / Episode number
    episode = Column(Integer, nullable=True)
    
    # 分辨率 (1080p/4K 等) / Resolution (1080p/4K etc.)
    resolution = Column(String, nullable=True)
    
    # STRM 文件路径 / STRM file path
    strm_path = Column(String, nullable=True)
    
    # 消息 caption / Message caption
    caption = Column(String, nullable=True)
    
    # 是否已识别 / Whether recognized by parser
    recognized = Column(Boolean, default=True, nullable=False)
    
    # 创建时间 / Creation time
    created_at = Column(DateTime, server_default=func.now())
    
    # 更新时间 / Update time
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
