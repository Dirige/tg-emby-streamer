#!/usr/bin/env python3
"""生成干净源码发布包并检查凭据；不复制生产数据、虚拟环境或 Git 历史。"""

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

ROOT_FILES = {"Dockerfile", ".dockerignore", ".gitignore", ".env.example", "docker-compose.yml",
              "config.example.yaml", "requirements.txt", "pytest.ini", "run.py", "LICENSE",
              "README.md", "DEPLOY.md", "DATA_SHARE.md", "CHANNELS.md", "channels.json", "RULES.md"}
DIRECTORIES = {"app", "scripts", "tests", "docker", ".github"}
SUFFIXES = {".py", ".sh", ".html", ".css", ".js", ".json", ".yaml", ".yml", ".md",
            ".png", ".jpg", ".svg", ".ini", ".txt"}


def scrub(root: Path):
    path = root / "app/static/index.html"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    pattern = r'(<input\b[^>]*\bid="s_tmdb_key"[^>]*\bplaceholder=")[^"]*(")'
    cleaned, count = re.subn(pattern, r'\1请填写你自己的 TMDB API Key\2', text)
    if count and cleaned != text:
        path.write_text(cleaned, encoding="utf-8")
        print("已清除发布副本中的历史 TMDB 示例值。")


def scan(root: Path, private_config: Path | None = None):
    needles = []
    if private_config:
        import yaml
        data = yaml.safe_load(private_config.read_text(encoding="utf-8")) or {}
        for section in data.values():
            if isinstance(section, dict):
                needles.extend(str(value).encode() for key, value in section.items()
                               if key in {"api_hash", "session_string", "password", "stream_token", "api_key", "bot_token", "phone"}
                               and value and len(str(value)) >= 6)
    docker_token = re.compile(rb"\bdckr_pat_[A-Za-z0-9_-]{12,}\b")
    failures = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.is_symlink():
            failures.append(path.relative_to(root).as_posix())
            continue
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("源码发布树含异常大文件，拒绝发布")
        data = path.read_bytes()
        if docker_token.search(data) or any(needle in data for needle in needles):
            failures.append(path.relative_to(root).as_posix())
    if failures:
        raise ValueError("发布副本含敏感值，拒绝发布。涉及文件：" + ", ".join(failures))
    print("源码发布副本敏感信息检查通过。")


def bundle(source: Path, output: Path, private_config: Path | None = None):
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "tgemby-source.tar.gz"
    if archive.exists():
        raise ValueError("源码包已存在，未覆盖；请选择新的输出目录")
    with tempfile.TemporaryDirectory(prefix=".source-", dir=output) as temporary:
        root = Path(temporary) / "tgemby"
        root.mkdir()
        paths = [source / name for name in ROOT_FILES if (source / name).is_file()]
        for directory in DIRECTORIES:
            paths.extend(path for path in (source / directory).rglob("*")
                         if path.is_file() and path.suffix in SUFFIXES and "__pycache__" not in path.parts)
        for path in paths:
            if path.is_symlink():
                raise ValueError("源码含符号链接，需审计后再发布")
            destination = root / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            destination.chmod(0o755 if path.suffix == ".sh" else 0o644)
        scrub(root)
        scan(root, private_config)
        with tarfile.open(archive, "w:gz") as tar:
            def normalize(info):
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                return info
            tar.add(root, arcname="tgemby", filter=normalize)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "source-checksums.sha256").write_text(f"{digest}  {archive.name}\n")
    print(json.dumps({"archive": str(archive), "size": archive.stat().st_size, "sha256": digest}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["scrub", "scan", "bundle"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--private-config", type=Path, help="只用于本地检查，不复制到输出或镜像")
    args = parser.parse_args()
    try:
        if args.command == "scrub":
            scrub(args.root)
        elif args.command == "scan":
            scan(args.root, args.private_config)
        else:
            if not args.output:
                raise ValueError("bundle 需要 --output 目录")
            bundle(args.root, args.output, args.private_config)
    except Exception as exc:
        print(str(exc) if isinstance(exc, ValueError) else f"发布检查失败（{type(exc).__name__}）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
