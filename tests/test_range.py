"""流路由 Range 解析测试。"""
import pytest

from app.stream.router import parse_range_header
from fastapi import HTTPException


def test_no_range_header():
    assert parse_range_header(None, 1000) == (0, None)


def test_simple_range():
    assert parse_range_header("bytes=0-499", 1000) == (0, 499)


def test_open_ended_range():
    start, end = parse_range_header("bytes=500-", 1000)
    assert start == 500
    assert end is None


def test_spaces_tolerated():
    assert parse_range_header("bytes= 100- 199", 1000) == (100, 199)


def test_end_clamped_to_filesize():
    assert parse_range_header("bytes=0-5000", 1000) == (0, 999)


def test_start_beyond_size_raises_416():
    with pytest.raises(HTTPException) as e:
        parse_range_header("bytes=1500-2000", 1000)
    assert e.value.status_code == 416


def test_malformed_header_falls_back_to_full():
    assert parse_range_header("bytes=abc", 1000) == (0, None)
