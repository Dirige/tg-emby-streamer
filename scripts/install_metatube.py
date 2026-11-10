#!/usr/bin/env python3
"""下载官方 Emby 版 MetaTube 插件，校验 SHA256 后安装；不复制任何作者配置。"""

import argparse
import hashlib
import io
import json
import os
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/metatube-community/jellyfin-plugin-metatube/releases/latest"


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "TGEmby-deploy", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("官方插件下载大小异常")
    return data


def install(directory: Path, uid: int | None = None, gid: int | None = None):
    release = json.loads(download(RELEASE_API))
    assets = [asset for asset in release.get("assets", [])
              if asset["name"].startswith("Emby.MetaTube@") and asset["name"].endswith(".zip")]
    if len(assets) != 1:
        raise ValueError("找不到唯一的 Emby 版插件；请勿使用 Jellyfin 版 DLL")
    asset = assets[0]
    expected = asset.get("digest", "")
    if not expected.startswith("sha256:"):
        raise ValueError("官方未提供 SHA256，拒绝自动安装；请手动检查来源")
    archive = download(asset["browser_download_url"])
    if hashlib.sha256(archive).hexdigest() != expected.split(":", 1)[1]:
        raise ValueError("官方插件校验失败，未安装")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        names = [name for name in zipped.namelist() if Path(name).name == "MetaTube.dll"]
        if len(names) != 1 or zipped.getinfo(names[0]).file_size > 10 * 1024 * 1024:
            raise ValueError("压缩包内没有唯一、有效的 MetaTube.dll")
        data = zipped.read(names[0])
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "MetaTube.dll"
    if destination.is_symlink():
        raise ValueError("插件目标不能是符号链接")
    if destination.exists():
        if destination.read_bytes() == data:
            print("MetaTube 插件已经是该版本，未重复安装。")
            return
        backup = destination.with_name("MetaTube.dll.backup-" + datetime.now().strftime("%Y%m%d%H%M%S%f"))
        backup.write_bytes(destination.read_bytes())
        backup.chmod(0o600)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as file:
        temporary = Path(file.name)
        file.write(data)
    try:
        temporary.chmod(0o644)
        if uid is not None and gid is not None:
            os.chown(temporary, uid, gid)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"已安装官方 Emby 版 MetaTube：{release['tag_name']}；在插件设置填写 http://metatube:8080。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    args = parser.parse_args()
    try:
        install(args.directory, args.uid, args.gid)
    except Exception as exc:
        print(str(exc) if isinstance(exc, ValueError) else f"插件下载/安装失败（{type(exc).__name__}），请检查 GitHub 连通性和目录权限。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
