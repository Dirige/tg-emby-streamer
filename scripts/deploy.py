#!/usr/bin/env python3
"""初始化、修改地址和代理；不会使用或分发作者的凭据。"""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def atomic_write(path: Path, data: bytes, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def validate_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("访问地址必须是 http://服务器IP:端口 或 https://域名")
    if url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("访问地址不能含用户名、密码、路径、查询参数或片段")
    _ = url.port
    return value.rstrip("/")


def channel_seed() -> dict:
    return json.loads((BASE_DIR / "channels.json").read_text(encoding="utf-8"))


def update_access_file(path: Path, data: dict):
    auth = data.get("auth", {})
    atomic_write(path.parent / "access.txt", (
        f"TGEmby 地址：{data['server']['base_url']}\n"
        f"STRM 地址：{data['server']['local_url']}\n"
        f"用户名：{auth.get('username', '')}\n密码：{auth.get('password', '')}\n"
        "这是你自己的私密凭据文件，不要分享或上传。\n"
    ).encode())


def init_config(path: Path, public_url: str | None = None, uid: int | None = None,
                gid: int | None = None) -> bool:
    path = path.absolute()
    if public_url:
        public_url = validate_url(public_url)
    if path.exists():
        print("配置已存在，保留现有配置和密码。")
        return False
    data = copy.deepcopy(yaml.safe_load((BASE_DIR / "config.example.yaml").read_text(encoding="utf-8")))
    # Paths are derived from the config file location so the same code is
    # correct on a host install (…/runtime/config) and in the container (/app/config).
    config_parent = path.parent
    root = config_parent.parent
    data["storage"].update({"data_dir": str(config_parent), "strm_dir": str(root / "strm"),
                            "cache_dir": str(root / "cache"), "media_path": str(root / "cache/media")})
    data["database"]["url"] = "sqlite+aiosqlite:///" + str(config_parent / "media.db")
    data["auth"]["password"] = secrets.token_urlsafe(24)
    data["server"]["stream_token"] = secrets.token_urlsafe(32)
    if public_url:
        data["server"]["base_url"] = public_url
        data["server"]["local_url"] = public_url
    seed = channel_seed()
    data["telegram"]["monitor_channels"] = ",".join(seed["monitor_channels"])
    data["deployment"] = {"channels_initialized": True}
    for directory in (path.parent, root / "strm", root / "cache", root / "jav_posters"):
        directory.mkdir(parents=True, exist_ok=True)
        if uid is not None and gid is not None:
            os.chown(directory, uid, gid)
    atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode())
    access = path.parent / "access.txt"
    update_access_file(path, data)
    if uid is not None and gid is not None:
        os.chown(path, uid, gid)
        os.chown(access, uid, gid)
    print("已创建配置和随机密码；登录信息保存在 runtime/config/access.txt。")
    return True


def seed_database(config_path: Path):
    # Import only after config creation. No Telegram client is started here.
    os.environ["CONFIG_PATH"] = str(config_path.absolute())
    from app.database import engine, init_db
    from app.config import settings
    from sqlalchemy.engine import make_url

    database = Path(make_url(settings.database.url).database)
    database.parent.mkdir(parents=True, exist_ok=True)
    async def initialize():
        try:
            await init_db()
        finally:
            await engine.dispose()

    asyncio.run(initialize())
    database.chmod(0o600)
    with sqlite3.connect(database) as conn:
        if conn.execute("SELECT COUNT(*) FROM channel_rules").fetchone()[0]:
            return
        if conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]:
            return
        seed = channel_seed()
        for category in seed["categories"]:
            conn.execute("INSERT OR IGNORE INTO categories (name,strm_dir,sort_order) VALUES (?,?,?)",
                         (category["name"], category["strm_dir"], category.get("sort_order", 0)))
        fields = ("channel_id", "channel_name", "category", "title_rule", "episode_rule", "season",
                  "season_rule", "year_rule", "filename_rule", "filename_template", "enabled", "strict_import")
        sql = "INSERT OR IGNORE INTO channel_rules (" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")"
        for rule in seed["rules"]:
            conn.execute(sql, tuple(rule.get(field) for field in fields))


def rebase(path: Path, force: bool = False):
    from scripts.library_bundle import load_config, rebase_library

    data = load_config(path)
    signature = hashlib.sha256(json.dumps({
        "url": data["server"]["local_url"], "token": data["server"].get("stream_token", ""),
        "strm_dir": data["storage"]["strm_dir"], "cache_dir": data["storage"]["cache_dir"],
        "database": data["database"]["url"],
    }, sort_keys=True).encode()).hexdigest()
    state = path.parent / ".library-paths.json"
    if not force and state.exists():
        try:
            if json.loads(state.read_text())["signature"] == signature:
                return
        except (KeyError, ValueError):
            pass
    count = rebase_library(data)
    atomic_write(state, json.dumps({"signature": signature, "strm_count": count}).encode())
    print(f"已核对/更新 {count} 个 STRM 的访问地址；未重命名来源或重新刮削。")


def configure_proxy(path: Path, scheme: str, host: str, port: int,
                    username: str = "", password: str = "", enabled: bool = True):
    if scheme not in {"socks5", "http"} or not host or not 1 <= port <= 65535:
        raise ValueError("代理须使用 socks5/http、有效主机及 1–65535 端口")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.setdefault("proxy", {}).update({"enabled": enabled, "scheme": scheme, "host": host,
                                         "port": port, "username": username, "password": password})
    atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode())
    print("Telegram 代理配置已保存；重启 TGEmby 后生效。")


def proxy_from_url(value: str) -> dict:
    try:
        url = urlsplit(value.strip())
        port = url.port
    except ValueError:
        raise ValueError("代理地址格式无效") from None
    if url.scheme not in {"socks5", "http"} or not url.hostname or not port:
        raise ValueError("代理格式须为 http://主机:端口 或 socks5://主机:端口")
    if url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("代理地址不能有路径、查询参数或片段")
    return {"scheme": url.scheme, "host": url.hostname, "port": port,
            "username": unquote(url.username or ""), "password": unquote(url.password or "")}


def set_url(path: Path, public_url: str | None, local_url: str | None):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    from scripts.library_bundle import rebase_library
    if public_url:
        data.setdefault("server", {})["base_url"] = validate_url(public_url)
    if local_url:
        data.setdefault("server", {})["local_url"] = validate_url(local_url)
    # Validate both addresses before modifying anything.
    validate_url(data["server"]["base_url"])
    validate_url(data["server"]["local_url"])
    count = rebase_library(data)
    atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode())
    update_access_file(path, data)
    print(f"访问地址已更新（对外 {data['server'].get('base_url', '')}，STRM 用 {data['server']['local_url']}）；已重写 {count} 个 STRM 文件。")


def rotate_token(path: Path):
    from scripts.library_bundle import rebase_library

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["server"]["stream_token"] = secrets.token_urlsafe(32)
    count = rebase_library(data)
    atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode())
    print(f"已更换播放 Token 并更新 {count} 个 STRM；重启 TGEmby 后旧链接失效。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "bootstrap", "rebase", "proxy", "seturl", "rotate-token"])
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("CONFIG_PATH", str(BASE_DIR / "config.yaml"))))
    parser.add_argument("--url")
    parser.add_argument("--local-url")
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    parser.add_argument("--scheme", choices=["socks5", "http"], default="socks5")
    parser.add_argument("--host", default="proxy")
    parser.add_argument("--port", type=int, default=7890)
    parser.add_argument("--proxy-url-stdin", action="store_true", help="从标准输入读取代理 URL，避免凭据出现在命令参数中")
    parser.add_argument("--disable", action="store_true", help="停用 Telegram 代理")
    args = parser.parse_args()
    args.config = args.config.absolute()
    os.environ["CONFIG_PATH"] = str(args.config)
    try:
        if args.command == "init":
            init_config(args.config, args.url, args.uid, args.gid)
        elif args.command == "bootstrap":
            init_config(args.config)
            data = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
            if not data.get("auth", {}).get("username") or not data.get("auth", {}).get("password"):
                raise ValueError("Web 用户名和密码不能为空。请先完成初始化。")
            seed_database(args.config)
            rebase(args.config)
        elif args.command == "rebase":
            rebase(args.config, force=True)
        elif args.command == "proxy":
            proxy = proxy_from_url(sys.stdin.readline()) if args.proxy_url_stdin else {
                "scheme": args.scheme, "host": args.host, "port": args.port}
            configure_proxy(args.config, **proxy, enabled=not args.disable)
        elif args.command == "seturl":
            if not args.url and not args.local_url:
                raise ValueError("至少要提供 --url 或 --local-url")
            set_url(args.config, args.url, args.local_url)
        elif args.command == "rotate-token":
            rotate_token(args.config)
    except Exception as exc:
        # Do not print URLs with credentials, config contents or exception bodies unless debugging.
        if os.environ.get("TGEMBY_DEBUG"):
            traceback.print_exc()
        print(f"部署操作失败（{type(exc).__name__}）：{exc}" if isinstance(exc, ValueError)
               else f"部署操作失败（{type(exc).__name__}），请检查路径、权限和配置。",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
