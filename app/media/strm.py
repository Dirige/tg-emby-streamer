"""
STRM 文件生成模块

提供生成 Emby/Jellyfin 兼容的 STRM 文件的功能。
支持按类型分类存储（电视剧、电影、动漫等）。
"""

import os
import re
from pathlib import Path
from typing import Optional
from app.config import settings


def generate_strm(
    message_id: int,
    file_name: str,
    category: Optional[str] = None,
    title: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    tmdb_id: Optional[int] = None,
    tmdb_name: Optional[str] = None,
) -> Optional[Path]:
    """
    生成 STRM 文件
    
    Args:
        message_id: Telegram 消息 ID
        file_name: 原始文件名
        category: 媒体类型 (tv/movie/anime/dongman/overseas/variety/documentary/cosplay)
        title: 标题
        season: 季数
        episode: 集数
        tmdb_id: TMDB ID
        tmdb_name: TMDB 名称
        
    Returns:
        Path: STRM 文件路径，失败返回 None
    """
    try:
        # 获取 STRM 目录
        strm_dir = Path("strm")
        strm_dir.mkdir(exist_ok=True)
        
        # 确定分类目录
        category_dir = _get_category_dir(category)
        category_path = strm_dir / category_dir
        category_path.mkdir(exist_ok=True)
        
        # 生成文件名
        strm_file_name = _generate_strm_file_name(
            title=title,
            category=category,
            season=season,
            episode=episode,
            tmdb_id=tmdb_id,
            tmdb_name=tmdb_name,
        )
        
        # 完整路径
        strm_path = category_path / strm_file_name
        
        # 确保文件名唯一（避免冲突）
        counter = 1
        base_name = strm_path.stem
        while strm_path.exists():
            strm_path = category_path / f"{base_name}_{counter}.strm"
            counter += 1
        
        # 生成 STRM 内容
        strm_url = _generate_strm_url(message_id)
        
        # 写入文件
        strm_path.write_text(strm_url, encoding='utf-8')
        
        return strm_path
        
    except Exception as e:
        print(f"生成 STRM 文件失败: {e}")
        return None


def remove_old_strm(strm_path: str) -> bool:
    """
    删除旧的 STRM 文件
    
    Args:
        strm_path: STRM 文件路径（可以是绝对路径或相对路径）
        
    Returns:
        bool: 是否成功删除
    """
    try:
        path = Path(strm_path)
        if path.exists() and path.is_file():
            path.unlink()
            return True
        return False
    except Exception as e:
        print(f"删除 STRM 文件失败: {e}")
        return False


def _get_category_dir(category: Optional[str]) -> str:
    """获取分类目录名"""
    category_map = {
        'tv': '电视剧',
        'movie': '电影',
        'anime': '动漫',
        'dongman': '东漫',
        'overseas': '海外剧',
        'variety': '综艺',
        'documentary': '纪录片',
        'cosplay': '18+',
    }
    
    return category_map.get(category, '电影')


def _generate_strm_file_name(
    title: Optional[str] = None,
    category: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    tmdb_id: Optional[int] = None,
    tmdb_name: Optional[str] = None,
) -> str:
    """生成 STRM 文件名"""
    # 使用标题
    if tmdb_name:
        name = tmdb_name
    elif title:
        name = title
    else:
        name = "未命名"
    
    # 清理文件名
    name = _sanitize_file_name(name)
    
    # 电视剧添加季数和集数
    if category in ['tv', 'anime', 'dongman', 'overseas']:
        if season is not None and episode is not None:
            return f"{name}-S{season:02d}E{episode:02d}.strm"
        elif season is not None:
            return f"{name}-S{season:02d}.strm"
    
    # 电影和其他类型
    return f"{name}.strm"


def _sanitize_file_name(name: str) -> str:
    """清理文件名，移除非法字符"""
    # Windows 和 Linux 文件名非法字符
    illegal_chars = r'[<>:"/\\|?*\x00-\x1f]'
    name = re.sub(illegal_chars, '', name)
    
    # 移除首尾空格和点
    name = name.strip(' .')
    
    # 限制长度
    if len(name) > 200:
        name = name[:200]
    
    return name


def _generate_strm_url(message_id: int) -> str:
    """生成 STRM 文件中的 URL"""
    base_url = getattr(settings, 'base_url', 'http://localhost:8001')
    stream_path = '/stream'
    
    # 移除 URL 末尾的斜杠
    base_url = base_url.rstrip('/')
    
    return f"{base_url}{stream_path}/{message_id}"
