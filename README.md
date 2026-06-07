# TG Emby Streamer

[English](#english) | [中文](#中文)

---

## English

### Introduction

TG Emby Streamer automatically monitors Telegram channels for video content, generates `.strm` files for Emby/Jellyfin, and provides HTTP Range-based streaming directly from Telegram. No need to download files locally — stream on demand!

### Features

- **Auto Monitor** — Monitor multiple Telegram channels, auto-forward to private group
- **Smart Parser** — Parse Chinese/English filenames to extract title, season, episode
- **STRM Generation** — Auto-generate Emby/Jellyfin compatible `.strm` files
- **HTTP Range Streaming** — Support seeking/progress bar without buffering
- **Multi-level Cache** — Memory LRU + disk cache, reduce Telegram API calls
- **Proxy Support** — Built-in sing-box (VLESS/Trojan/VMess), external SOCKS5/HTTP, subscription auto-proxy
- **Web Dashboard** — Visual management panel for proxy, media, cache, settings
- **Bot Pool** — Multiple bot tokens for load balancing streaming requests
- **Docker Ready** — One-click deployment with Docker

### Architecture

```
Emby/Jellyfin
    ↓
FastAPI Stream Server (HTTP Range)
    ↓
sing-box / External Proxy (SOCKS5)
    ↓
Telegram MTProto (Pyrogram)
```

### Quick Start

See [DEPLOY.md](DEPLOY.md) for detailed deployment guide.

```bash
# Docker (recommended)
docker-compose up -d

# Or local
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your config
python generate_session.py
python run.py
```

### Monitored Channels

This project monitors the following Telegram channels (examples):

| Channel | Type |
|---------|------|
| [@hantang](https://t.me/hantang) | Movie/TV 4K |
| [影视综合频道](https://t.me/+placeholder1) | TV Shows |
| [YunRuo动漫](https://t.me/+placeholder2) | Anime |
| [动漫频道](https://t.me/+placeholder3) | Anime/Dongman |

> Replace with your actual monitored channels

### License

MIT License

---

## 中文

### 简介

TG Emby Streamer 自动监听 Telegram 频道的视频内容，为 Emby/Jellyfin 生成 `.strm` 文件，并提供基于 HTTP Range 的流媒体播放，直接从 Telegram 按需串流，无需下载到本地！

### 功能特性

- **自动监听** — 监听多个 Telegram 频道，自动转发到私有群聊
- **智能解析** — 解析中英文文件名，自动提取标题、季数、集数
- **STRM 生成** — 自动生成 Emby/Jellyfin 兼容的 `.strm` 文件
- **HTTP Range 流媒体** — 支持拖动进度条，无需缓冲
- **多级缓存** — 内存 LRU + 磁盘缓存，减少 Telegram API 调用
- **代理支持** — 内置 sing-box（VLESS/Trojan/VMess）、外部 SOCKS5/HTTP、订阅自动代理
- **Web 管理面板** — 可视化管理代理、媒体、缓存、配置
- **Bot 池** — 多 Bot Token 负载均衡流媒体请求
- **Docker 部署** — 一键部署

### 架构

```
Emby/Jellyfin
    ↓
FastAPI 流媒体服务器 (HTTP Range)
    ↓
sing-box / 外部代理 (SOCKS5)
    ↓
Telegram MTProto (Pyrogram)
```

### 快速开始

详细部署教程请参阅 [DEPLOY.md](DEPLOY.md)。

```bash
# Docker 部署（推荐）
docker-compose up -d

# 或本地部署
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入你的配置
python generate_session.py
python run.py
```

### 监听频道

本项目监听以下 Telegram 频道（示例）：

| 频道 | 类型 |
|------|------|
| [@hantang](https://t.me/hantang) | 电影/电视剧 4K |
| [影视综合频道](https://t.me/+placeholder1) | 电视剧 |
| [YunRuo动漫](https://t.me/+placeholder2) | 动漫 |
| [动漫频道](https://t.me/+placeholder3) | 动漫/东漫 |

> 请替换为你实际监听的频道

### 许可证

MIT License
