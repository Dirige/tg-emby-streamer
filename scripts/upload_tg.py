#!/usr/bin/env python3
"""通过 Telegram 发送大文件 / 媒体数据包（分卷）给指定会话。

要求：本机 TGEMBY 已完成 Telegram 登录（config.yaml 内有 session_string）。

用法：
  # 发送单个文件（完整压缩包或某个分卷）
  python scripts/upload_tg.py --input share/tgemby-library.tar.gz --to me

  # 一次发送数据包目录中需要分享的全部文件
  # （自动收集 manifest.json / checksums.sha256 / channels.json / CHANNELS.md
  #   以及 tgemby-library.tar.gz 与它的全部分卷 .partNNN）
  python scripts/upload_tg.py --input share --to me

  --to 支持 @用户名、chat_id 数字，或 me（发送到自己的收藏/Saved Messages）。
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TELEGRAM_MAX_PART = 2_000_000_000  # 保守低于免费账号上限；推荐每卷 1800 MiB。

MIME = {
    ".tar.gz": "application/gzip",
    ".gz": "application/gzip",
    ".json": "application/json",
    ".md": "text/plain",
    ".sha256": "text/plain",
    ".txt": "text/plain",
}


def guess_mime(path: Path) -> str:
    name = path.name.lower()
    for suffix, mime in MIME.items():
        if name.endswith(suffix):
            return mime
    return "application/octet-stream"


def read_checksums(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})  ([^/\\\r\n]+)", line)
        if not match or match.group(2) in result:
            raise ValueError("checksums.sha256 格式无效或含重复文件名")
        result[match.group(2)] = match.group(1).lower()
    return result


def pick_files(path: Path) -> list[Path]:
    """决定要发送的文件清单；目录模式按分享包的惯例顺序收集。"""
    if path.is_file():
        return [path]
    candidates: list[Path] = []
    for name in ("manifest.json", "checksums.sha256", "channels.json", "CHANNELS.md"):
        candidate = path / name
        if candidate.is_file():
            candidates.append(candidate)
    archives: dict[str, Path] = {}
    for item in list(path.glob("*.tar.gz")) + list(path.glob("*.tar.gz.part*")):
        if not item.is_file():
            continue
        archives[item.name] = item
    parts = sorted(name for name in archives if ".part" in name)
    full = sorted(name for name in archives if ".part" not in name)
    if parts:
        # 存在分卷的同名整包不再上传，避免重复
        stems = {name.split(".part")[0] for name in parts}
        for stem in stems:
            numbers = []
            for name in parts:
                if name.split(".part")[0] != stem:
                    continue
                match = re.fullmatch(re.escape(stem) + r"\.part(\d{3,})", name)
                if not match:
                    raise ValueError("分卷名称须为 .part000、.part001 等数字后缀")
                numbers.append(int(match.group(1)))
            if sorted(numbers) != list(range(len(numbers))):
                raise ValueError("分卷不连续，拒绝发送缺卷的数据包")
        full = [name for name in full if name not in stems]
        expected = read_checksums(path / "checksums.sha256")
        for stem in stems:
            if any(name.startswith(stem + ".part") and name not in archives for name in expected):
                raise ValueError("缺少校验清单中的分卷（包括最后一卷），拒绝发送")
    selected = full + parts
    candidates.extend(archives[archive_name] for archive_name in selected)
    return candidates


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def upload_config(path: Path) -> dict:
    """只读配置，在内存中复用已登录会话，不触碰生产 Session 文件。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    telegram = data.get("telegram", {})
    if not telegram.get("api_id") or not telegram.get("api_hash") or not telegram.get("session_string"):
        raise ValueError("缺少已登录的 Telegram 会话；请先完成 generate_session.py 登录")
    options = {"api_id": int(telegram["api_id"]), "api_hash": telegram["api_hash"],
               "session_string": telegram["session_string"]}
    proxy = data.get("proxy", {})
    if proxy.get("enabled"):
        options["proxy"] = {"scheme": proxy.get("scheme", "socks5"), "hostname": proxy["host"],
                            "port": int(proxy["port"])}
        if proxy.get("username"):
            options["proxy"].update(username=proxy["username"], password=proxy.get("password", ""))
    return options


def prepare_files(files: list[Path]) -> dict[str, str]:
    """先检查全部分卷及校验和，避免发到一半才发现大文件或损坏。"""
    hashes = {}
    checksums_by_directory = {}
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("只允许上传普通文件，不上传符号链接")
        if path.stat().st_size > TELEGRAM_MAX_PART:
            raise ValueError("存在超过 2GB 的单文件，请先用 --part-size-mb 1800 分卷")
        digest = sha256_file(path)
        if path.parent not in checksums_by_directory:
            checksums_by_directory[path.parent] = read_checksums(path.parent / "checksums.sha256")
        expected = checksums_by_directory[path.parent].get(path.name)
        if expected and expected != digest:
            raise ValueError("待上传文件与 checksums.sha256 不一致，拒绝发送")
        hashes[str(path)] = digest
    return hashes


async def run(target: str | int, files: list[Path], caption: str, *, config: Path,
              state_path: Path, hashes: dict[str, str]) -> int:
    from pyrogram import Client
    from pyrogram.errors import FloodWait
    from scripts.library_bundle import atomic_write

    kwargs = upload_config(config)
    if state_path.is_symlink():
        raise ValueError("上传进度文件不能是符号链接")
    state = json.loads(state_path.read_text()) if state_path.exists() else {"schema_version": 1, "sent": {}}
    if state.get("schema_version") != 1 or not isinstance(state.get("sent"), dict):
        raise ValueError("上传进度文件无效；请使用新的 --state 路径")
    sent = 0
    logging.getLogger("pyrogram").setLevel(logging.ERROR)
    async with Client("tgemby-share-upload", in_memory=True, no_updates=True, **kwargs) as client:
        chat = await client.get_chat(target)
        for file in files:
            digest = hashes[str(file)]
            key = f"{chat.id}:{file.name}:{digest}"
            if key in state["sent"]:
                print(f"跳过已上传文件：{file.name}", flush=True)
                continue
            size_mb = file.stat().st_size / 1024**2
            note = (caption if len(files) == 1 else f"{caption} · {file.name}")[:900]
            print(f"发送 {file.name}（{size_mb:.1f} MB）……", flush=True)
            started = time.monotonic()
            last = -10

            async def progress(current, total):
                nonlocal last
                percent = int(current * 100 / max(total, 1))
                if percent >= last + 10 or percent == 100:
                    elapsed = max(time.monotonic() - started, 0.1)
                    print(f"  {percent}% · {current / 1024**2 / elapsed:.1f} MiB/s", flush=True)
                    last = percent

            while True:
                try:
                    message = await client.send_document(
                        # Pyrogram 2.x infers MIME internally; send_document has no mime_type argument.
                        chat.id, document=str(file), caption=note,
                        force_document=True, progress=progress)
                    break
                except FloodWait as exc:
                    print(f"Telegram 限速，等待 {exc.value} 秒后继续。", flush=True)
                    await asyncio.sleep(exc.value)
            if not message or not message.document or message.document.file_size != file.stat().st_size:
                raise ValueError("Telegram 未确认完整文件，进度没有标记为成功")
            state["sent"][key] = {"name": file.name, "size": file.stat().st_size,
                                  "sha256": digest, "message_id": message.id}
            atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode(), mode=0o600)
            sent += 1
            print("  ✓ 已发出", flush=True)
    return sent


def main():
    parser = argparse.ArgumentParser(description="通过 Telegram 发送分享文件（不打印任何凭据）")
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", "/app/config/config.yaml"))
    parser.add_argument("--input", required=True, help="要发送的文件或数据包目录")
    parser.add_argument("--to", required=True, help='目标：@用户名 / chat_id / me（发给自己收藏）')
    parser.add_argument("--caption", default="TGEmby 媒体数据包")
    parser.add_argument("--state", type=Path, help="上传进度文件；默认放在数据包旁，重跑会跳过已成功发送的文件")
    parser.add_argument("--dry-run", action="store_true", help="校验并列出文件，不连接 Telegram")
    args = parser.parse_args()

    path = Path(args.input).absolute()
    if not path.exists():
        raise SystemExit(f"找不到：{path}")
    try:
        files = pick_files(path)
        if not files:
            raise ValueError("目录里没有可分享的文件（需要 manifest.json / checksums.sha256 / 压缩包或分卷）")
        hashes = prepare_files(files)
        if args.dry_run:
            for file in files:
                print(f"{file.name} · {file.stat().st_size} 字节 · SHA256 {hashes[str(file)]}")
            print("校验通过；未连接 Telegram。")
            return 0
        target = int(args.to) if args.to.lstrip("-").isdigit() else args.to
        state_path = args.state or (path if path.is_dir() else path.parent) / ".upload-state.json"
        count = asyncio.run(run(target, files, args.caption, config=Path(args.config),
                                state_path=state_path, hashes=hashes))
        print(f"完成，本次发送 {count} 个文件（之前上传的文件已自动跳过）。")
    except Exception as exc:
        print(str(exc) if isinstance(exc, ValueError) else
              f"上传失败（{type(exc).__name__}），检查会话和网络；重跑可续传已完成的文件。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
