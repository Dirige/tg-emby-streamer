"""STRM 命名/多版本/URL 构建测试（纯函数，不落盘）。"""
from app.media.strm import (
    _candidate_names,
    _sanitize_filename,
    build_stream_url,
    resolution_tag,
)


def test_sanitize_invalid_chars():
    assert _sanitize_filename('a<b>c:"d/e\\f|g?*h') == "a_b_c__d_e_f_g__h"


def test_sanitize_max_bytes():
    name = "长" * 300
    out = _sanitize_filename(name, max_bytes=200)
    assert len(out.encode("utf-8")) <= 200


def test_resolution_tag():
    assert resolution_tag(3840, 2160) == "2160p"
    assert resolution_tag(1920, 1080) == "1080p"
    assert resolution_tag(1280, 720) == "720p"
    assert resolution_tag(0, 0) == ""
    assert resolution_tag(640, 480) == "SD"


def test_candidate_names_contains_resolution_and_numbers():
    names = _candidate_names("Movie", "1080p")
    assert names[0] == "Movie"
    assert "Movie - 1080p" in names
    assert "Movie - 2" in names
    # 高分辨率优先出现在序号后备之前
    assert names.index("Movie - 1080p") < names.index("Movie - 2")


def test_build_stream_url_with_token_and_chat():
    from app.config import settings
    url = build_stream_url("-100123", 42)
    base = settings.stream.local_url.rstrip("/")
    if settings.web.stream_token:
        assert url == f"{base}/stream/-100123/42?token={settings.web.stream_token}"
    else:
        assert url == f"{base}/stream/-100123/42"
    legacy = build_stream_url(None, 42)
    assert "/stream/42" in legacy
