# Deployment Guide / 部署教程

[English](#english) | [中文](#中文)

---

## English

### Requirements

- Python 3.11+ (or Docker)
- Telegram API credentials (from https://my.telegram.org)
- A Telegram Bot Token (from @BotBotFather)
- A private Telegram group/channel for media storage
- (Optional) A proxy server if in China

### Option 1: Docker (Recommended)

```bash
# 1. Create project directory
mkdir tg-emby && cd tg-emby

# 2. Download files
wget https://raw.githubusercontent.com/YOUR_USERNAME/tg-emby-streamer/main/docker-compose.yml
wget https://raw.githubusercontent.com/YOUR_USERNAME/tg-emby-streamer/main/.env.example

# 3. Configure
cp .env.example .env
nano .env  # Edit with your settings

# 4. Generate session (first time only)
docker-compose run --rm app python generate_session.py

# 5. Start
docker-compose up -d
```

### Option 2: Local Installation

```bash
# 1. Clone repository
git clone https://github.com/YOUR_USERNAME/tg-emby-streamer.git
cd tg-emby-streamer

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
nano .env  # Edit with your settings

# 4. Generate session (first time only)
python generate_session.py

# 5. Start
python run.py
```

### Configuration Guide

#### Telegram Settings

| Variable | Description | How to Get |
|----------|-------------|------------|
| `TELEGRAM_API_ID` | API ID | https://my.telegram.org |
| `TELEGRAM_API_HASH` | API Hash | https://my.telegram.org |
| `TELEGRAM_SESSION_STRING` | Session | Run `python generate_session.py` |
| `TELEGRAM_CHANNEL_ID` | Private group ID | Create a private group, get its ID |
| `TELEGRAM_MONITOR_CHANNELS` | Channel IDs to monitor | Comma-separated, e.g. `-100111,-100222` |

#### Proxy Settings

Three proxy modes are supported:

| Mode | When to Use | Configuration |
|------|-------------|---------------|
| **sing-box** | Built-in VLESS/Trojan/VMess | Set `SINGBOX_ENABLED=true` |
| **External** | Use Clash/V2RayN etc. | Set `PROXY_ENABLED=true` |
| **Direct** | Overseas server | Set both to `false` |

#### Stream Settings

| Variable | Description |
|----------|-------------|
| `STREAM_PORT` | Listen port (default: 8001) |
| `BASE_URL` | External URL for STRM files (e.g. `http://your-ip:8001`) |

### Emby/Jellyfin Setup

1. Open Emby/Jellyfin admin panel
2. Add a new media library
3. Select the `strm/` directory as the content folder
4. Enable Direct Play, disable transcoding
5. Scan the library

### Common Issues

**Q: Cannot connect to Telegram**
A: Check your proxy settings. In China, you need a proxy to access Telegram.

**Q: STRM files not playing**
A: Make sure `BASE_URL` is accessible from your Emby server. Check firewall settings.

**Q: Videos not being detected**
A: Check that `TELEGRAM_CHANNEL_ID` and `TELEGRAM_MONITOR_CHANNELS` are correct (negative numbers).

---

## 中文

### 环境要求

- Python 3.11+（或 Docker）
- Telegram API 凭据（从 https://my.telegram.org 获取）
- Telegram Bot Token（从 @BotFather 获取）
- 一个私有 Telegram 群聊/频道用于存储媒体
- （可选）如果在国内，需要代理服务器

### 方式一：Docker 部署（推荐）

```bash
# 1. 创建项目目录
mkdir tg-emby && cd tg-emby

# 2. 下载文件
wget https://raw.githubusercontent.com/YOUR_USERNAME/tg-emby-streamer/main/docker-compose.yml
wget https://raw.githubusercontent.com/YOUR_USERNAME/tg-emby-streamer/main/.env.example

# 3. 配置
cp .env.example .env
nano .env  # 编辑配置

# 4. 生成 Session（首次需要）
docker-compose run --rm app python generate_session.py

# 5. 启动
docker-compose up -d
```

### 方式二：本地部署

```bash
# 1. 克隆仓库
git clone https://github.com/YOUR_USERNAME/tg-emby-streamer.git
cd tg-emby-streamer

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置
cp .env.example .env
nano .env  # 编辑配置

# 4. 生成 Session（首次需要）
python generate_session.py

# 5. 启动
python run.py
```

### 配置说明

#### Telegram 配置

| 变量 | 说明 | 获取方式 |
|------|------|----------|
| `TELEGRAM_API_ID` | API ID | https://my.telegram.org |
| `TELEGRAM_API_HASH` | API Hash | https://my.telegram.org |
| `TELEGRAM_SESSION_STRING` | Session | 运行 `python generate_session.py` |
| `TELEGRAM_CHANNEL_ID` | 私有群聊 ID | 创建私有群聊，获取其 ID |
| `TELEGRAM_MONITOR_CHANNELS` | 监听频道 ID | 逗号分隔，如 `-100111,-100222` |

#### 代理配置

支持三种代理模式：

| 模式 | 适用场景 | 配置方式 |
|------|----------|----------|
| **sing-box** | 内置 VLESS/Trojan/VMess | 设置 `SINGBOX_ENABLED=true` |
| **外部代理** | 使用 Clash/V2RayN 等 | 设置 `PROXY_ENABLED=true` |
| **直连** | 海外服务器 | 两者都设为 `false` |

#### 流媒体配置

| 变量 | 说明 |
|------|------|
| `STREAM_PORT` | 监听端口（默认：8001） |
| `BASE_URL` | STRM 文件中的外网地址（如 `http://你的IP:8001`） |

### Emby/Jellyfin 配置

1. 打开 Emby/Jellyfin 管理面板
2. 添加新媒体库
3. 选择 `strm/` 目录作为内容文件夹
4. 启用直接播放，禁用转码
5. 扫描媒体库

### 常见问题

**Q: 无法连接 Telegram**
A: 检查代理配置。国内服务器必须使用代理才能访问 Telegram。

**Q: STRM 文件无法播放**
A: 确保 `BASE_URL` 从 Emby 服务器可以访问。检查防火墙设置。

**Q: 视频未被检测到**
A: 检查 `TELEGRAM_CHANNEL_ID` 和 `TELEGRAM_MONITOR_CHANNELS` 是否正确（负数）。

**Q: 如何获取频道 ID？**
A: 转发频道消息到 @userinfobot，或使用 @RawDataBot。

**Q: 如何获取群聊 ID？**
A: 将机器人添加到群聊，发送 /start，或使用 @RawDataBot。
