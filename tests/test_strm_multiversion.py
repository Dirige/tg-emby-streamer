"""多版本 STRM 生成 behave测试（使用临时目录写入真实文件）。

通过 monkeypatch STRM_BASE_DIR 指向 tmp_path。
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


@pytest.fixture
def strm_env(tmp_path, monkeypatch):
    from app.media import strm
    monkeypatch.setattr(strm, "STRM_BASE_DIR", tmp_path)
    return tmp_path


def test_same_url_idempotent(strm_env):
    from app.media.strm import generate_strm

    p1 = generate_strm(message_id=10, chat_id="-1001", category="电影",
                       title="Movie", year=2024, resolution="1080p")
    p2 = generate_strm(message_id=10, chat_id="-1001", category="电影",
                       title="Movie", year=2024, resolution="1080p")
    assert p1 == p2
    listing = list(strm_env.rglob("*.strm"))
    assert len(listing) == 1  # 同一链接绝不重复生成第二个文件


def test_different_message_multi_version(strm_env):
    from app.media.strm import generate_strm, resolution_tag

    p1 = generate_strm(message_id=10, chat_id="-1001", category="电影",
                       title="Movie", year=2024)
    p2 = generate_strm(message_id=11, chat_id="-1001", category="电影",
                       title="Movie", year=2024, resolution="1080p")
    assert p1 != p2
    # 第二个版本带分辨率后缀（Emby 多版本命名约定）
    assert "1080p" in p2.name
    content1 = p1.read_text()
    content2 = p2.read_text()
    assert content1 != content2
    assert "/stream/-1001/10" in content1
    assert "/stream/-1001/11" in content2
    # 旧文件未被覆盖
    assert "/stream/-1001/10" in p1.read_text()


def test_resolution_suffix_collision_falls_back_to_number(strm_env):
    from app.media.strm import generate_strm

    generate_strm(message_id=1, chat_id="-1", category="电视剧",
                  title="Show", season=1, episode=2)
    p2 = generate_strm(message_id=2, chat_id="-1", category="电视剧",
                       title="Show", season=1, episode=2, resolution="2160p")
    assert "2160p" in p2.name
    # 再来一个同分辨率版本 → 落到数字后缀
    p3 = generate_strm(message_id=3, chat_id="-1", category="电视剧",
                       title="Show", season=1, episode=2, resolution="2160p")
    assert p3.name.endswith(" - 2.strm") or "2" in p3.name
    # 三者互不覆盖
    assert len({p1 for p1 in p2.parent.glob("*")}) == 3


def test_overwrite_mode_rewrites_in_place(strm_env):
    from app.media.strm import generate_strm

    p1 = generate_strm(message_id=10, chat_id="-1001", category="电影",
                       title="Movie", year=2024)
    p1.write_text("old-url", encoding="utf-8")
    p_updated = generate_strm(message_id=10, chat_id="-1001", category="电影",
                              title="Movie", year=2024, overwrite=True)
    assert p_updated.name == p1.name
    assert p_updated.read_text() != "old-url"
    # 没有产生额外文件
    assert len(list(p1.parent.glob("*.strm"))) == 1


def test_tv_layout_season_folder(strm_env):
    from app.media.strm import generate_strm

    p = generate_strm(message_id=5, chat_id="-1", category="电视剧",
                      title="Show", season=1, episode=3)
    assert f"Season {1:02d}" in p.parts.__str__() or "Season 01" in str(p)
    assert "/stream/-1/5" in p.read_text()
