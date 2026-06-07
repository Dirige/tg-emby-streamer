# TG Emby Streamer

<p align="center">
  <strong>Telegram 频道视频 → Emby/Jellyfin 自动入库</strong><br>
  <sub>STRM 生成 · HTTP Range 流媒体 · 代理穿透 · Web 管理面板</sub>
</p>

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **自动监听** | 监听多个 Telegram 频道，新视频自动转发到私有群聊 |
| **智能解析** | 自动解析中英文文件名，提取标题、季数、集数、分辨率 |
| **STRM 生成** | 自动生成 Emby/Jellyfin 兼容的 `.strm` 文件，按类型分目录 |
| **HTTP Range 流媒体** | 支持拖动进度条、断点续播，无需缓冲 |
| **多级缓存** | 内存 LRU + 磁盘缓存，减少 Telegram API 调用 |
| **代理支持** | 内置 sing-box（VLESS/Trojan/VMess）、外部 SOCKS5/HTTP、订阅自动代理 |
| **Web 管理面板** | 可视化管理代理、媒体、缓存、配置，支持在线播放 |
| **Bot 负载均衡** | 多 Bot Token 分散发媒体请求，避免限速（可选） |
| **18+ 内容分离** | 成人内容独立管理，不与普通媒体混在一起 |
| **Docker 部署** | 一键部署，开箱即用 |

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                      Emby / Jellyfin                    │
│                         ▼                               │
│              FastAPI 流媒体服务器                         │
│           (HTTP Range, 分块下载, 缓存)                    │
│                         ▼                               │
│          sing-box / 外部代理 (SOCKS5)                    │
│                         ▼                               │
│             Telegram MTProto (Pyrogram)                  │
│                    (频道监听/下载)                        │
└─────────────────────────────────────────────────────────┘
```

## 快速开始

### Docker 部署（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/Dirige/tg-emby-streamer.git
cd tg-emby-streamer

# 2. 配置
cp .env.example .env
# 编辑 .env 填入你的配置（详见下方配置说明）

# 3. 启动服务
docker-compose up -d
```

### 本地部署

```bash
# 1. 克隆仓库
git clone https://github.com/Dirige/tg-emby-streamer.git
cd tg-emby-streamer

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置
cp .env.example .env
# 编辑 .env 填入你的配置（详见下方配置说明）

# 4. 生成 Session（首次需要）
python generate_session.py

# 5. 启动
python run.py
```

### sing-box 代理配置

项目内置 sing-box 代理支持，配置文件位于 `sing-box/config.json`。

**下载 sing-box：**
- 官方下载：https://github.com/SagerNet/sing-box/releases
- Windows: `sing-box-windows-amd64.exe`
- Linux: `sing-box-linux-amd64`

**使用方法：**
1. 下载对应平台的 sing-box 可执行文件
2. 放入 `sing-box/` 目录
3. 编辑 `sing-box/config.json` 配置你的代理节点
4. 在 `.env` 中设置 `SINGBOX_ENABLED=true`

> 详细部署教程请参阅 **[DEPLOY.md](DEPLOY.md)**

## 配置说明

所有配置项都在 `.env` 文件中，详见 [.env.example](.env.example)。

### Telegram 配置

| 变量 | 说明 | 获取方式 |
|------|------|----------|
| `TELEGRAM_API_ID` | API ID | https://my.telegram.org |
| `TELEGRAM_API_HASH` | API Hash | https://my.telegram.org |
| `TELEGRAM_SESSION_STRING` | Session String | 运行 `python generate_session.py` |
| `TELEGRAM_CHANNEL_ID` | 私有群聊 ID | 创建私有群聊，获取其负数 ID |
| `TELEGRAM_MONITOR_CHANNELS` | 监听频道列表 | 逗号分隔的频道 ID |
| `CHANNEL_CATEGORIES` | 频道分类映射 | `频道ID=分类`，逗号分隔 |
| `ADULT_CHANNELS` | 18+ 频道列表 | 逗号分隔的频道 ID |
| `CAPTION_ONLY_CHANNELS` | 仅从 caption 提取标题的频道 | 文件名无意义的频道 |

### 代理配置

支持三种模式，根据你的服务器位置选择：

| 模式 | 适用场景 | 配置方式 |
|------|----------|----------|
| **sing-box** | 国内服务器，使用 VLESS/Trojan/VMess 节点 | `SINGBOX_ENABLED=true`，填写服务器信息 |
| **外部代理** | 已有 Clash/V2RayN 等代理工具 | `PROXY_ENABLED=true`，填写代理地址 |
| **订阅代理** | 有订阅链接，自动获取节点 | `PROXY_SUB_URL=你的订阅地址` |
| **直连** | 海外服务器，无需代理 | 以上都设为 `false` |

### 流媒体配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `STREAM_PORT` | 监听端口 | `8001` |
| `BASE_URL` | 外网访问地址（用于 STRM 文件） | `http://你的IP:8001` |
| `STREAM_CONCURRENCY` | 并发下载数 | `3` |

### Web 面板配置

| 变量 | 说明 |
|------|------|
| `WEB_USERNAME` | 登录用户名（留空则无需登录） |
| `WEB_PASSWORD` | 登录密码 |

## Web 管理面板

启动后访问 `http://localhost:8001`，功能页面：

| 页面 | 功能 |
|------|------|
| **仪表盘** | 系统状态总览、代理状态、连接状态 |
| **代理管理** | sing-box 启停、节点切换、订阅管理 |
| **Telegram** | 连接状态、监听频道管理、手动扫描/转发 |
| **媒体库** | 媒体列表、搜索、在线播放、批量操作 |
| **18+** | 成人内容独立管理 |
| **缓存管理** | 缓存统计、清空 |
| **系统设置** | 所有参数可在线编辑 |

## STRM 目录结构

自动生成的 STRM 文件按以下结构组织：

```
strm/
├── 电视剧/          # tv 类型
│   └── 剧名/
│       ├── 剧名-S01E01.strm
│       └── 剧名-S01E02.strm
├── 电影/            # movie 类型
│   └── 片名/
│       └── 片名.strm
├── 动漫/            # anime/dongman 类型
│   └── 动漫名/
│       └── 动漫名-S01E01.strm
├── 海外剧/          # overseas 类型
└── 18+/            # cosplay 类型
    └── 标题/
        └── 标题.strm
```

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

或在 Web 面板的 **Telegram** 页面点击「扫描」/「转发」按钮。

### 3. 配置 Emby/Jellyfin

1. 打开 Emby/Jellyfin 管理面板
2. 添加新媒体库，选择 `strm/` 目录
3. 启用**直接播放**，禁用转码
4. 扫描媒体库

### 4. 重新生成 STRM（可选）

```bash
python fix_strm.py
```

## 常见问题

<details>
<summary><b>Q: 无法连接 Telegram</b></summary>

国内服务器必须使用代理才能访问 Telegram。检查 `.env` 中的代理配置：
- 确保 `PROXY_ENABLED=true` 或 `SINGBOX_ENABLED=true`
- 确保代理地址和端口正确
- 确保代理服务正在运行
</details>

<details>
<summary><b>Q: STRM 文件无法播放</b></summary>

1. 确保 `BASE_URL` 从 Emby 服务器可以访问
2. 检查防火墙是否开放了端口
3. 确保 Emby 启用了直接播放，禁用了转码
</details>

<details>
<summary><b>Q: 视频未被检测到</b></summary>

1. 检查 `TELEGRAM_CHANNEL_ID` 和 `TELEGRAM_MONITOR_CHANNELS` 是否正确（负数）
2. 确保 Bot 或你的账号在频道/群聊中
3. 检查 Web 面板的 Telegram 连接状态
</details>

<details>
<summary><b>Q: 如何获取频道/群聊 ID？</b></summary>

- 转发频道消息到 @userinfobot
- 或使用 @RawDataBot
- 或在 Web 面板的 Telegram 页面查看
</details>

<details>
<summary><b>Q: 文件名都是乱码或相同怎么办？</b></summary>

对于文件名无意义的频道，在 `.env` 中添加：
```
CAPTION_ONLY_CHANNELS=-100xxxxxxxxxx
```
这样会从消息 caption 中提取标题。
</details>

## 许可证

MIT License
