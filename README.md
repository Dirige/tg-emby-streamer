# TG Emby Streamer

Telegram 频道视频自动入库 Emby/Jellyfin，支持 HTTP Range 流媒体播放、代理穿透、Web 管理面板。

## 功能特性

- **自动监听** — 监听多个 Telegram 频道，自动转发到私有群聊
- **智能解析** — 解析中英文文件名，自动提取标题、季数、集数
- **STRM 生成** — 自动生成 Emby/Jellyfin 兼容的 `.strm` 文件
- **HTTP Range 流媒体** — 支持拖动进度条，无需缓冲
- **多级缓存** — 内存 LRU + 磁盘缓存，减少 Telegram API 调用
- **代理支持** — 内置 sing-box（VLESS/Trojan/VMess）、外部 SOCKS5/HTTP、订阅自动代理
- **Web 管理面板** — 可视化管理代理、媒体、缓存、配置
- **Bot 负载均衡** — 多 Bot Token 分散发媒体请求（可选）
- **Docker 部署** — 一键部署

## 架构

```
Emby/Jellyfin
    ↓
FastAPI 流媒体服务器 (HTTP Range)
    ↓
sing-box / 外部代理 (SOCKS5)
    ↓
Telegram MTProto (Pyrogram)
```

## 快速开始

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

## 配置说明

所有配置项都在 `.env` 文件中，详见 [.env.example](.env.example) 和 [DEPLOY.md](DEPLOY.md)。

### 核心配置

| 变量 | 说明 |
|------|------|
| `TELEGRAM_API_ID` | Telegram API ID（从 https://my.telegram.org 获取） |
| `TELEGRAM_API_HASH` | Telegram API Hash |
| `TELEGRAM_SESSION_STRING` | Session String（运行 `python generate_session.py` 生成） |
| `TELEGRAM_CHANNEL_ID` | 私有群聊 ID（存储媒体的目标） |
| `TELEGRAM_MONITOR_CHANNELS` | 监听频道 ID（逗号分隔） |

### 代理配置

支持三种模式：

| 模式 | 适用场景 | 配置方式 |
|------|----------|----------|
| **sing-box** | 内置 VLESS/Trojan/VMess | 设置 `SINGBOX_ENABLED=true` |
| **外部代理** | 使用 Clash/V2RayN 等 | 设置 `PROXY_ENABLED=true` |
| **直连** | 海外服务器 | 两者都设为 `false` |

## Web 管理面板

启动后访问 `http://localhost:8001`，默认用户名密码在 `.env` 中配置。

功能页面：
- **仪表盘** — 系统状态总览
- **代理管理** — sing-box 启停、节点切换、订阅管理
- **Telegram** — 连接状态、监听频道管理、手动扫描/转发
- **媒体库** — 媒体列表、搜索、在线播放
- **18+** — 成人内容管理
- **缓存管理** — 缓存统计、清空
- **系统设置** — 所有参数可编辑

## 使用流程

### 1. 生成 Session String

```bash
python generate_session.py
```

按提示输入手机号和验证码，完成后 Session String 会自动写入 `.env`。

### 2. 扫描历史消息

```bash
# 从监听频道转发到私有群聊
python scan_history.py forward --limit 100

# 从私有群聊录入数据库并生成 STRM
python scan_history.py record --limit 100
```

### 3. 重新生成 STRM 文件

```bash
python fix_strm.py
```

### 4. 配置 Emby/Jellyfin

1. 在 Emby 中添加媒体库
2. 选择 `strm/` 目录
3. 启用直接播放，禁用转码

## 监听频道示例

在 `.env` 中配置 `TELEGRAM_MONITOR_CHANNELS`：

```
TELEGRAM_MONITOR_CHANNELS=-1001687077497,-1001668687022,-1002855500786
```

## 常见问题

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

## 许可证

MIT License
