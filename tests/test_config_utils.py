"""config 工具函数测试。"""
from app.config import _deep_get, _deep_set, _deep_delete, _coerce_type


DATA = {"telegram": {"api_id": 1}, "auth": {"username": "u"}}


def test_deep_get():
    assert _deep_get(DATA, "telegram.api_id") == 1
    assert _deep_get(DATA, "auth.username") == "u"
    assert _deep_get(DATA, "missing.key") is None


def test_deep_set_overwrite_and_nested_create():
    d = {"telegram": {"api_id": 1}}
    _deep_set(d, "telegram.api_id", 2)
    assert d["telegram"]["api_id"] == 2
    _deep_set(d, "a.b.c", "x")
    assert _deep_get(d, "a.b.c") == "x"


def test_deep_delete():
    d = {"a": {"b": {"c": 1}, "keep": 2}}
    _deep_delete(d, "a.b.c")
    assert _deep_get(d, "a.b.c") is None
    assert _deep_get(d, "a.keep") == 2
    _deep_delete(d, "a.missing.x")  # 不应抛异常


def test_coerce_type():
    assert _coerce_type("true", True) is True
    assert _coerce_type("false", True) is False
    assert _coerce_type("42", 10) == 42
    assert _coerce_type("3.5", 1.0) == 3.5
    assert _coerce_type("whatever", "orig") == "whatever"
    assert _coerce_type("raw", None) == "raw"
