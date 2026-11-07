import re
import logging

logger = logging.getLogger(__name__)

CN_NUM = "[一二三四五六七八九十]+"
CN_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _apply_regex_rule(text: str, rule: str | None, group_num: int = 1) -> str | None:
    if not rule or not text:
        return None
    if rule.startswith("regex:"):
        m = re.search(rule[6:], text)
        if m:
            return m.group(group_num).strip() if m.lastindex and m.lastindex >= group_num else m.group(0).strip()
    return None


def _apply_line_rule(text: str, rule: str | None, line_num: int = 0) -> str | None:
    if not rule or not text:
        return None
    if rule.startswith("line:"):
        parts = rule.split(":", 1)
        n = int(parts[1]) if len(parts) > 1 else line_num
        lines = text.split("\n")
        if n < len(lines):
            return lines[n].strip()
    return None


def _apply_title_rule(caption: str, rule: str | None) -> str | None:
    return _apply_regex_rule(caption, rule) or _apply_line_rule(caption, rule)


def _apply_episode_rule(caption: str, rule: str | None) -> int | None:
    val = _apply_regex_rule(caption, rule)
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return None


def _apply_year_rule(caption: str, rule: str | None) -> int | None:
    val = _apply_regex_rule(caption, rule)
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return None


def _apply_filename_rule(filename: str, rule: str | None) -> str | None:
    return _apply_regex_rule(filename, rule)


def _parse_cn_season(text: str) -> int | None:
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    if text in CN_MAP:
        return CN_MAP[text]
    if text.startswith("十"):
        if len(text) == 1:
            return 10
        rest = text[1:]
        base = 10
        if rest in CN_MAP:
            return base + CN_MAP[rest]
    return None


def _apply_season_rule(caption: str, rule: str | None) -> dict:
    if not rule or not caption:
        return {"season": None, "season_text": None}
    if rule.startswith("regex:"):
        pattern = rule[6:]
        # Try original pattern
        m = re.search(pattern, caption)
        if m:
            val = m.group(1).strip() if m.lastindex else m.group(0).strip()
            season = _parse_cn_season(val)
            if season is not None:
                return {"season": season, "season_text": m.group(0).strip()}
        # Try with Chinese numerals (replace \d+ with CN_NUM)
        cn_pattern = pattern.replace(r"\d+", CN_NUM).replace("\\d+", CN_NUM)
        if cn_pattern != pattern:
            m2 = re.search(cn_pattern, caption)
            if m2:
                val2 = m2.group(1).strip() if m2.lastindex else m2.group(0).strip()
                season2 = _parse_cn_season(val2)
                if season2 is not None:
                    return {"season": season2, "season_text": m2.group(0).strip()}
    return {"season": None, "season_text": None}


def _strip_season_from_title(title: str, season_text: str | None) -> str:
    if not title or not season_text:
        return title
    safe = re.escape(season_text)
    for sep in [" ", "　"]:
        cleaned = re.sub(rf"\s*{safe}\s*", sep, title).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned != title:
            return cleaned
    return title


TITLE_PREFIXES = [
    r"^片名[：:]?\s*",
    r"^电影[：:]?\s*",
    r"^#\S*\s+",
]

VIDEO_EXT = re.compile(r"\.(mp4|mkv|avi|mov|wmv|flv|webm)$", re.I)


def _clean_title(title: str) -> str:
    if not title:
        return title
    for prefix in TITLE_PREFIXES:
        title = re.sub(prefix, "", title)
    title = re.sub(r"\s+", " ", title).strip()
    title = VIDEO_EXT.sub("", title)
    title = re.sub(r"[_.]\s*part\d+\s*", " ", title, flags=re.I).strip()
    title = re.sub(r"\s+", " ", title).strip()
    return title


def parse_media_info(
    caption: str,
    title_rule: str | None = None,
    episode_rule: str | None = None,
    season: int | None = None,
    season_rule: str | None = None,
    year_rule: str | None = None,
    category: str | None = None,
    filename: str | None = None,
    filename_rule: str | None = None,
) -> dict | None:
    title = _apply_title_rule(caption, title_rule)
    if not title and filename_rule and filename:
        title = _apply_filename_rule(filename, filename_rule)

    episode = _apply_episode_rule(caption, episode_rule)
    year = _apply_year_rule(caption, year_rule)
    if year is None and filename and year_rule:
        year = _apply_year_rule(filename, year_rule)

    season_result = _apply_season_rule(caption, season_rule)
    extracted_season = season_result["season"]
    season_text = season_result["season_text"]

    final_season = extracted_season if extracted_season is not None else season

    if title and season_text:
        title = _strip_season_from_title(title, season_text)

    title = _clean_title(title)

    return {
        "title": title,
        "episode": episode,
        "season": final_season,
        "year": year,
        "category": category,
        "recognized": title is not None,
    }
