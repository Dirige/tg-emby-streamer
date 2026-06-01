# TG Emby Streamer

Telegram 频道视频自动入库 Emby/Jellyfin，支持 Range 流媒体播放、TMDB 刮削、sing-box 代理。

## 功能特性

- **自动监听** Telegram 频道视频，自动转发到私有频道
- **TMDB 刮削** 自动识别影片信息（中文文件名优先）
- **STRM 生成** 自动生成 Emby/Jellyfin 可识别的 STRM 文件
- **Range 流媒体** 支持 HTTP Range 请求，拖动进度条不卡顿
- **多级缓存** 内存 LRU + 磁盘缓存，减少 Telegram API 调用
- **sing-box 代理** 内置 VLESS 代理，自动路由 Telegram 流量
- **Web 管理面板** 可视化管理代理、媒体、缓存、配置
- **Docker 部署** 一键部署

## 架构

```
Emby/Jellyfin
    ↓
Cloudflare Worker (可选, CDN/鉴权)
    ↓
FastAPI Stream Server (Range 流媒体)
    ↓
sing-box (SOCKS5 → VLESS 代理)
    ↓
Telegram MTProto
```

## 快速开始

### 1. Docker 部署（推荐）

```bash
# 创建目录
mkdir tg-emby && cd tg-emby

# 下载配置文件
wget https://raw.githubusercontent.com/YOUR_USERNAME/tg-emby-streamer/main/docker-compose.yml
wget https://raw.githubusercontent.com/YOUR_USERNAME/tg-emby-streamer/main/.env.example

# 复制配置
cp .env.example .env
# 编辑 .env 填入你的配置

# 启动
docker-compose up -d
```

### 2. 本地部署

```bash
# 克隆仓库
git clone https://github.com/YOUR_USERNAME/tg-emby-streamer.git
cd tg-emby-streamer

# 安装依赖
pip install -r requirements.txt

# 配置
cp .env.example .env
# 编辑 .env 填入你的配置

# 生成 Session String（首次需要）
python generate_session.py

# 启动
python run.py
```

### 3. 访问 Web 面板

启动后访问：
```
http://localhost:8001
```

## 配置说明

### Telegram 配置

| 变量 | 说明 |
|------|------|
| `TELEGRAM_API_ID` | 从 https://my.telegram.org 获取 |
| `TELEGRAM_API_HASH` | 从 https://my.telegram.org 获取 |
| `TELEGRAM_SESSION_STRING` | 运行 `python generate_session.py` 生成 |
| `TELEGRAM_CHANNEL_ID` | 你的私有频道 ID（用于存储媒体） |
| `TELEGRAM_MONITOR_CHANNELS` | 要监听的频道 ID（逗号分隔） |

### 代理配置

支持三种模式：

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| **sing-box** | 内置 VLESS 代理 | 国内服务器 |
| **外部代理** | 使用 Clash/V2RayN 等 | 已有代理工具 |
| **直连** | 不使用代理 | 海外服务器 |

sing-box 配置：
```
SINGBOX_ENABLED=true
SINGBOX_ADDRESS=your_vless_server
SINGBOX_PORT=443
SINGBOX_UUID=your_uuid
SINGBOX_HOST=your_host
SINGBOX_PATH=/your_path
```

### 流媒体配置

| 变量 | 说明 |
|------|------|
| `STREAM_HOST` | 监听地址，默认 `0.0.0.0` |
| `STREAM_PORT` | 监听端口，默认 `8001` |
| `BASE_URL` | 外网访问地址（用于 STRM 文件） |
| `STREAM_LOCAL_URL` | 内网访问地址 |

### TMDB 配置

| 变量 | 说明 |
|------|------|
| `TMDB_API_KEY` | TMDB API Key |
| `TMDB_LANGUAGE` | 语言，默认 `zh-CN` |
| `TMDB_PROXY` | TMDB 代理地址（可选） |

## 使用流程

### 1. 生成 Session String

```bash
python generate_session.py
```

按提示输入手机号和验证码，完成后 Session String 会自动写入 `.env`。

### 2. 扫描历史消息

```bash
# 从监听频道转发到私有频道
python scan_history.py forward --limit 100

# 从私有频道录入数据库并生成 STRM
python scan_history.py record --limit 100
```

### 3. 重新生成 STRM 文件

```bash
python fix_strm.py
```

### 4. 配置 Emby/Jellyfin

1. 在 Emby 中添加媒体库
2. 选择 `strm/` 目录
3. 启用 Direct Play，禁用转码

## Web 面板功能

| 页面 | 功能 |
|------|------|
| 仪表盘 | 系统状态总览 |
| 代理管理 | sing-box 启停、节点切换、VLESS 配置 |
| Telegram | 连接状态、监听频道 |
| 媒体库 | 媒体列表、搜索、在线播放 |
| 扫描任务 | 历史消息转发、媒体录入 |
| 缓存管理 | 缓存统计、清空 |
| 系统设置 | 所有参数可编辑 |
| 运行日志 | sing-box 日志 |

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 重定向到面板 |
| `/dashboard` | GET | Web 管理面板 |
| `/health` | GET | 健康检查 |
| `/proxy/status` | GET | 代理状态 |
| `/stream/{id}` | GET | 流媒体播放 |
| `/api/media` | GET | 媒体列表 |
| `/api/config` | GET | 系统配置 |
| `/api/proxy/start` | POST | 启动代理 |
| `/api/proxy/stop` | POST | 停止代理 |
| `/api/proxy/restart` | POST | 重启代理 |
| `/api/test/telegram` | POST | 测试连接 |
| `/api/settings/save` | POST | 保存设置 |
| `/api/sub/fetch` | POST | 获取订阅节点 |
| `/api/scan/forward` | POST | 转发历史消息 |
| `/api/scan/record` | POST | 录入媒体库 |
| `/api/cache/clear` | POST | 清空缓存 |

## Docker 构建

```bash
# 本地构建
docker build -t tg-emby-streamer .

# 运行
docker run -d \
  --name tg-emby \
  -p 8001:8001 \
  -v ./data:/app/data \
  -v ./cache:/app/cache \
  -v ./strm:/app/strm \
  -v ./.env:/app/.env:ro \
  --env-file .env \
  tg-emby-streamer
```

## 许可证

MIT License
