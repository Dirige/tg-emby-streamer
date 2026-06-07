"""
文件名解析模块

提供从 Telegram 文件名解析媒体信息的功能。
支持识别剧集、电影、动漫等类型，提取标题、季数、集数等信息。
"""

import re
from typing import Optional


def parse_media_info(file_name: str) -> dict:
    """
    解析文件名，提取媒体信息
    
    Args:
        file_name: 文件名（可能包含路径）
        
    Returns:
        dict: 包含以下键的字典：
            - category: 媒体类型 (tv/movie/anime/dongman/overseas/variety/documentary/cosplay)
            - title: 标题
            - season: 季数（如果有）
            - episode: 集数（如果有）
            - resolution: 分辨率
            - tmdb_id: TMDB ID（如果有）
            - tmdb_name: TMDB 名称（如果有）
    """
    # 移除路径，只保留文件名
    file_name = file_name.split("/")[-1].split("\\")[-1]
    
    # 移除扩展名
    if "." in file_name:
        file_name = file_name.rsplit(".", 1)[0]
    
    result = {
        "category": None,
        "title": file_name,
        "season": None,
        "episode": None,
        "resolution": None,
        "tmdb_id": None,
        "tmdb_name": None,
    }
    
    # 解析分辨率
    resolution = _extract_resolution(file_name)
    if resolution:
        result["resolution"] = resolution
    
    # 解析 TMDB ID
    tmdb_info = _extract_tmdb_id(file_name)
    if tmdb_info:
        result["tmdb_id"] = tmdb_info["id"]
        result["tmdb_name"] = tmdb_info["name"]
    
    # 解析季数和集数
    season_episode = _extract_season_episode(file_name)
    if season_episode:
        result["season"] = season_episode["season"]
        result["episode"] = season_episode["episode"]
    
    # 判断媒体类型
    category = _guess_category(file_name, result["season"], result["episode"])
    if category:
        result["category"] = category
    
    # 提取标题
    title = _extract_title(file_name, result["season"], result["episode"], tmdb_info)
    if title:
        result["title"] = title
    
    return result


def _extract_resolution(file_name: str) -> Optional[str]:
    """提取分辨率"""
    patterns = [
        r'(?:^|[^a-zA-Z])(\d{3,4})[pP](?:[^a-zA-Z]|$)',  # 1080p, 720p
        r'(?:^|[^a-zA-Z])(4[Kk])?(?:[^a-zA-Z]|$)',         # 4K
        r'(?:^|[^a-zA-Z])([48][Kk])(?:[^a-zA-Z]|$)',      # 4K, 8K
        r'(?:^|[^a-zA-Z])(HD|SD|FHD|UHD)(?:[^a-zA-Z]|$)', # HD, SD, FHD, UHD
    ]
    
    for pattern in patterns:
        match = re.search(pattern, file_name, re.IGNORECASE)
        if match:
            res = match.group(1) if match.lastindex else match.group()
            return res.upper()
    
    return None


def _extract_tmdb_id(file_name: str) -> Optional[dict]:
    """提取 TMDB ID"""
    patterns = [
        r'tmdb[_-]?(\d+)',
        r'tmdb[_-]?(id)?[_-]?(\d+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, file_name, re.IGNORECASE)
        if match:
            tmdb_id = match.group(1) or match.group(2)
            # 尝试从文件名中提取 TMDB 名称
            name_part = re.sub(r'tmdb[_-]?(id)?[_-]?\d+', '', file_name, flags=re.IGNORECASE)
            name_part = re.sub(r'[._-]+', ' ', name_part).strip()
            return {
                "id": int(tmdb_id),
                "name": name_part if name_part else None
            }
    
    return None


def _extract_season_episode(file_name: str) -> Optional[dict]:
    """提取季数和集数"""
    # 标准格式: S01E01, s01e01, Season 1 Episode 1
    patterns = [
        r'[Ss](\d{1,2})[Ee](\d{1,2})',           # S01E01
        r'[Ss]eason\s*(\d{1,2})\s*[Ee]p?(\d{1,2})',  # Season 1 Episode 1
        r'[Ss](\d{1,2})[Xx](\d{1,2})',           # S01x01
        r'第?(\d{1,2})[季集]第?(\d{1,2})[集话]?',  # 第1季第1集
        r'[^\d](\d{1,2})x(\d{1,2})[^\d]',        # 01x01
        r'[^\d](\d{1,2})-(\d{1,2})[^\d]',        # 01-01 (需要更严格的判断)
    ]
    
    for pattern in patterns:
        match = re.search(pattern, file_name)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            # 合理范围检查
            if 0 < season <= 30 and 0 < episode <= 200:
                return {"season": season, "episode": episode}
    
    # 中文格式: 第1季 第01集
    chinese_pattern = r'第(\d+)季'
    chinese_ep_pattern = r'第(\d+)集'
    
    season_match = re.search(chinese_pattern, file_name)
    episode_match = re.search(chinese_ep_pattern, file_name)
    
    if season_match and episode_match:
        return {
            "season": int(season_match.group(1)),
            "episode": int(episode_match.group(1))
        }
    
    return None


def _guess_category(file_name: str, season: Optional[int], episode: Optional[int]) -> Optional[str]:
    """根据文件名猜测媒体类型"""
    file_lower = file_name.lower()
    
    # 成人内容
    adult_keywords = ['adult', '18+', 'xxx', 'porn', 'cosplay', '色情', '成人']
    for keyword in adult_keywords:
        if keyword in file_lower:
            return 'cosplay'
    
    # 动漫
    anime_keywords = ['anime', '动漫', 'dongman', 'bdrip', 'rip', 'webrip']
    for keyword in anime_keywords:
        if keyword in file_lower:
            return 'anime'
    
    # 海外剧
    overseas_keywords = ['overseas', '海外']
    for keyword in overseas_keywords:
        if keyword in file_lower:
            return 'overseas'
    
    # 综艺
    variety_keywords = ['variety', '综艺', 'show']
    for keyword in variety_keywords:
        if keyword in file_lower:
            return 'variety'
    
    # 纪录片
    documentary_keywords = ['doc', 'documentary', '纪录', '记录']
    for keyword in documentary_keywords:
        if keyword in file_lower:
            return 'documentary'
    
    # 如果有季数和集数，判定为电视剧
    if season is not None and episode is not None:
        return 'tv'
    
    # 默认电影
    return 'movie'


def _extract_title(file_name: str, season: Optional[int], episode: Optional[int], 
                   tmdb_info: Optional[dict]) -> str:
    """提取标题"""
    title = file_name
    
    # 移除 TMDB ID
    title = re.sub(r'tmdb[_-]?(id)?[_-]?\d+', '', title, flags=re.IGNORECASE)
    
    # 移除分辨率
    title = re.sub(r'\d{3,4}[pP]', '', title)
    title = re.sub(r'(4[Kk]|8[Kk]|HD|SD|FHD|UHD)', '', title, flags=re.IGNORECASE)
    
    # 移除季数和集数信息
    title = re.sub(r'[Ss]\d{1,2}[Ee]\d{1,2}', '', title)
    title = re.sub(r'[Ss]eason\s*\d{1,2}\s*[Ee]p?\d{1,2}', '', title, flags=re.IGNORECASE)
    title = re.sub(r'[Ss]\d{1,2}[Xx]\d{1,2}', '', title)
    title = re.sub(r'第\d+季第\d+集', '', title)
    title = re.sub(r'第\d+季', '', title)
    title = re.sub(r'第\d+集', '', title)
    title = re.sub(r'\d{1,2}x\d{1,2}', '', title)
    
    # 移除扩展名和常见后缀
    title = re.sub(r'\.(mp4|mkv|avi|mov|wmv|flv|webm|m4v)$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'(bdrip|webrip|dvdrip|hdrip|hdtv|bluray|blu-ray|web-dl)$', '', title, flags=re.IGNORECASE)
    
    # 清理特殊字符
    title = re.sub(r'[._-]+', ' ', title)
    title = re.sub(r'\s+', ' ', title)
    title = title.strip()
    
    # 如果使用 TMDB 名称
    if tmdb_info and tmdb_info.get("name"):
        return tmdb_info["name"]
    
    return title or file_name
