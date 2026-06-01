from sqlalchemy import Column, Integer, String, DateTime, func
from app.database import Base


class Media(Base):
    __tablename__ = "media"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(Integer, nullable=False, index=True)
    chat_id = Column(String, nullable=False, index=True)
    file_name = Column(String, nullable=True)
    file_id = Column(String, nullable=True)
    file_unique_id = Column(String, nullable=True)
    size = Column(Integer, nullable=True)
    duration = Column(Integer, nullable=True)
    mime_type = Column(String, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    tmdb_id = Column(String, nullable=True)
    tmdb_name = Column(String, nullable=True)
    category = Column(String, nullable=True)
    season = Column(Integer, nullable=True)
    episode = Column(Integer, nullable=True)
    resolution = Column(String, nullable=True)
    strm_path = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
