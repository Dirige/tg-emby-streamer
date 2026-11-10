#!/usr/bin/env python3
"""安全读写部署用 .env，不把内容当 shell 脚本执行。仅依赖 Python 标准库。"""

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def get_value(path: Path, key: str, default: str = "") -> str:
    if not path.exists():
        return default
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\s*" + re.escape(key) + r"\s*=", line):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            return value[1:-1].replace("\\'", "'").replace("\\\\", "\\")
        if len(value) >= 2 and value[0] == value[-1] == '"':
            return value[1:-1]
        return value.split(" #", 1)[0].rstrip()
    return default


def set_value(path: Path, key: str, value: str):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(char in value for char in "\r\n\0"):
        raise ValueError("环境变量名或内容无效")
    if key == "SCRAPER_PROXY" and value:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.query or url.fragment:
            raise ValueError("刮削代理须是有效 HTTP(S) 代理地址，不含查询参数或片段")
        _ = url.port
    if path.is_symlink():
        raise ValueError(".env 不能是符号链接")
    encoded = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    replacement = f"{key}={encoded}"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found = False
    for index, line in enumerate(lines):
        if re.match(r"^\s*" + re.escape(key) + r"\s*=", line):
            lines[index] = replacement
            found = True
    if not found:
        lines.append(replacement)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as file:
            temporary = Path(file.name)
            file.write("\n".join(lines) + "\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["get", "set"])
    parser.add_argument("key")
    parser.add_argument("--file", type=Path, default=Path(".env"))
    parser.add_argument("--default", default="")
    args = parser.parse_args()
    try:
        if args.command == "get":
            print(get_value(args.file, args.key, args.default))
        else:
            set_value(args.file, args.key, sys.stdin.readline().rstrip("\r\n"))
    except Exception:
        print("环境文件操作失败，请检查路径、权限和输入格式。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
