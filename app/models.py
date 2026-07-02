from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Boolean, func
from app.database import Base


class Media(Base):
    __tablename__ = "media"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, nullable=False, index=True)
    chat_id = Column(String, nullable=False, index=True)
    file_name = Column(String, nullable=True)
    file_id = Column(String, nullable=True)
    file_unique_id = Column(String, nullable=True)
    size = Column(BigInteger, nullable=True)
    duration = Column(Integer, nullable=True)
    mime_type = Column(String, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    category = Column(String, nullable=True)
    title = Column(String, nullable=True)
    caption = Column(String, nullable=True)
    source_link = Column(String, nullable=True)
    channel_rule_id = Column(Integer, nullable=True)
    strm_path = Column(String, nullable=True)
    parts = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    strm_dir = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class ChannelRule(Base):
    __tablename__ = "channel_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String, unique=True, nullable=False, index=True)
    channel_name = Column(String, nullable=True)
    category = Column(String, nullable=True)
    title_rule = Column(String, nullable=True)
    episode_rule = Column(String, nullable=True)
    season = Column(Integer, nullable=True)
    season_rule = Column(String, nullable=True)
    year_rule = Column(String, nullable=True)
    filename_rule = Column(String, nullable=True)
    filename_template = Column(String, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
