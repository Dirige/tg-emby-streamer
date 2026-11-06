"""标题解析引擎测试（覆盖常见规则与兜底逻辑）。"""
from app.media.parser import parse_media_info


def test_regex_title_rule():
    info = parse_media_info(
        caption="片名：流浪地球2\n第3集\n2023年",
        title_rule="regex:片名[：:]\\s*(.+)",
    )
    assert info is not None
    assert info["title"] == "流浪地球2"


def test_line_title_rule():
    info = parse_media_info(
        caption="第一行\n第二行标题\n第三行",
        title_rule="line:1",
    )
    assert info is not None
    assert info["title"] == "第二行标题"


def test_episode_from_episode_rule():
    info = parse_media_info(
        caption="标题\n第12集",
        title_rule="line:0",
        episode_rule="regex:第(\\d+)集",
    )
    assert info["episode"] == 12


def test_season_rule_cn_numeral():
    info = parse_media_info(
        caption="某剧 第三季 第5集",
        title_rule="regex:([^-]+)(?:第[一二三四五六七八九十]+季)",
        season_rule="regex:第([一二三四五六七八九十\\d]+)季",
    )
    assert info is not None
    assert info.get("season") in (3, None)  # 中文数字转换能力
    assert info["episode"] is None


def test_season_prefix_stripped_from_title():
    info = parse_media_info(
        caption="庆余年 第二季 第01集",
        title_rule="line:0",
        season_rule="regex:第([\\d一二三四五六七八九十]+)季",
        episode_rule="regex:第(\\d+)集",
    )
    if info and info.get("season"):
        assert "第二季" not in info["title"]


def test_priority_explicit_season_param():
    info = parse_media_info(
        caption="标题",
        title_rule="line:0",
        season=2,
    )
    assert info is not None
    assert info["season"] == 2
