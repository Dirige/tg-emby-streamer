"""
Telegram Emby Streamer - 数据模型

SQLAlchemy 数据库模型，用于存储媒体元数据。
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from app.database import Base


class Media(Base):
    """
    媒体记录表
    
    存储从 Telegram 频道解析的视频元数据。
    """
    __tablename__ = "media"

    # 自增主键
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Telegram 消息 ID
    message_id = Column(Integer, nullable=False, index=True)
    
    # 来源频道/群聊 ID
    chat_id = Column(String, nullable=False, index=True)
    
    # 文件名
    file_name = Column(String, nullable=True)
    
    # Telegram 文件 ID (用于下载)
    file_id = Column(String, nullable=True)
    
    # Telegram 唯一文件 ID
    file_unique_id = Column(String, nullable=True)
    
    # 文件大小 (字节)
    size = Column(Integer, nullable=True)
    
    # 视频时长 (秒)
    duration = Column(Integer, nullable=True)
    
    # MIME 类型
    mime_type = Column(String, nullable=True)
    
    # 视频宽度
    width = Column(Integer, nullable=True)
    
    # 视频高度
    height = Column(Integer, nullable=True)
    
    # 解析后的显示名称
    display_name = Column(String, nullable=True)
    
    # 分类 (tv/movie/anime/dongman/cosplay 等)
    category = Column(String, nullable=True)
    
    # 季数
    season = Column(Integer, nullable=True)
    
    # 集数
    episode = Column(Integer, nullable=True)
    
    # 分辨率 (1080p/4K 等)
    resolution = Column(String, nullable=True)
    
    # STRM 文件路径
    strm_path = Column(String, nullable=True)
    
    # 消息 caption
    caption = Column(String, nullable=True)
    
    # 是否已识别
    recognized = Column(Boolean, default=True, nullable=False)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
