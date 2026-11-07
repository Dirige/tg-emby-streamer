# TG Emby Streamer

Telegram 频道视频自动入库工具，通过 Pyrogram 连接 Telegram，监控指定频道的视频消息，智能解析标题/季/集/年份信息，自动生成 STRM 文件供 Emby/Jellyfin 刮削播放，并提供内嵌视频流代理实现实时转播。

基于 **FastAPI + Pyrogram + SQLAlchemy** 构建，配备现代化 Web 管理面板，支持多频道、多分类、自定义正则解析规则。

---

## 功能特点

- **Telegram 频道监控** — 基于 Pyrogram (MTProto) 连接 Telegram，实时扫描频道历史消息
- **正则标题解析** — 支持 `line:N`、`regex:...` 多种规则，灵活提取标题、集数、季数、年份
- **STRM 文件生成** — 自动按分类/剧集结构生成 `.strm` 文件，Emby/Jellyfin 可直接识别
- **视频流代理** — 内置分块流媒体服务器，支持 Range 请求、多 DC 负载均衡、断点续播
- **两级缓存** — 内存 LRU + 磁盘缓存，自动预取后续分片，播放流畅不卡顿
- **Web 管理面板** — 深色主题 SPA，在线管理频道规则、查看媒体库、手动扫描、缓存管理
- **多分类支持** — 电视剧、电影、动漫、综艺、纪录片、18+ 等多分类独立目录
- **代理支持** — 支持 SOCKS5/HTTP 代理连接（Telegram 封锁地区必备）
- **通知推送** — 刮削完成后带图通知；JAV 每个番号独立显示标题、演员和来源群组
- **全量入库** — 一键扫描所有已启用频道，自动跳过已入库记录

---

## 目录结构

```
tgemby/
├── config.yaml              # 主配置文件（支持环境变量覆盖）
├── docker-compose.yml       # Docker Compose 一键部署（可选；生产为 systemd + venv）
├── Dockerfile               # Docker 镜像构建
├── requirements.txt         # Python 依赖（锁定生产版本）
├── pytest.ini               # 测试配置
├── run.py                   # 应用启动入口
│
├── app/                     # 应用核心代码
│   ├── main.py              # FastAPI 应用入口、认证中间件（会话过期/登录限流）、登录页
│   ├── config.py            # 配置加载（YAML + 环境变量合并）
│   ├── dashboard.py         # Web API 路由（频道规则 CRUD、扫描、缓存管理）
│   ├── database.py          # SQLAlchemy 异步引擎、会话管理与唯一索引
│   ├── models.py            # 数据模型（Media(chat_id+message_id 唯一)、ChannelRule、Category）
│   │
│   ├── media/               # 媒体处理模块
│   │   ├── parser.py        # 标题/季/集/年份解析引擎（正则 + 行号）
│   │   ├── strm.py          # STRM 生成：多版本命名（`名称 - 1080p`）、入重幂等
│   │   ├── strm_regen.py    # 全量 STRM 重生成（Web 按钮与 scripts/regen_strm_all.py 共用）
│   │   ├── enrich_queue.py  # TMDB / JAV 刮削后台队列（完成后触发带图通知）
│   │   ├── enricher.py      # TMDB 匹配 / JAV 封面 / NFO
│   │   ├── metatube.py      # JAV 番号精确匹配与独立推送封面缓存
│   │   ├── jav_metadata.py  # 原消息 / NFO 的标题和演员字段
│   │   ├── nfo_generator.py # NFO 文件生成
│   │   ├── image_downloader.py # 海报/剧照下载
│   │   └── emby_notifier.py # Emby 媒体库刷新触发
│   │
│   ├── stream/              # 视频流代理模块
│   │   ├── router.py        # 流媒体路由（/stream/{chat_id}/{message_id} + 兼容旧路由）
│   │   ├── range_stream.py  # 基于 MTProto 的分块下载器，支持 FileMigrate
│   │   ├── cache.py         # 内存 LRU + 磁盘两级缓存（磁盘 I/O 不阻塞事件循环）
│   │   └── prefetch.py      # 智能预取后续分片（任务表自动回收）
│   │
│   ├── telegram/            # Telegram 客户端模块
│   │   ├── client.py        # Pyrogram 客户端管理（连接/重连/会话）
│   │   ├── monitor.py       # 频道扫描与入库（(chat_id, message_id) 唯一去重）
│   │   └── bot.py           # Bot 交互、手动入库、刮削完成带海报通知
│   │
│   ├── scan_state.py        # 扫描进度持久化
│   └── static/              # 前端静态资源
│       └── index.html       # 管理面板 SPA（纯 HTML/CSS/JS）
│
├── scripts/                 # 工具脚本
│   ├── generate_session.py  # 生成 Telegram Session String
│   ├── regen_strm_all.py    # 全量重生成 STRM（URL 格式升级后运行）
│   ├── init_all.py          # 初始化所有频道规则
│   ├── init_rules.py        # 初始化规则模板
│   ├── scan_all.py          # 全量扫描所有频道
│   ├── retry_scan.py        # 重试失败的扫描
│   ├── refresh_refs.py      # 刷新文件引用
│   ├── fix_media.py         # 修复媒体记录
│   └── extract_samples.py   # 提取样本用于调试
│
├── tests/                   # pytest 单元测试
├── samples/                 # 频道消息样本（用于调试解析规则）
├── data/                    # 运行时数据（自动生成）
│   ├── media.db             # SQLite 主数据库
│   ├── scan_state.json      # 扫描断点记录
│   ├── cache/               # 视频分块缓存
│   └── strm/                # STRM 文件输出目录
└── media/                   # 其他媒体资源目录
```

---

## 快速开始 — Docker 一键部署

### 前置条件

- **Docker** & **Docker Compose**（V2 及以上）
- **Telegram API 凭据** — 前往 https://my.telegram.org 注册应用获取 `api_id` 和 `api_hash`
- **一个 Telegram 账号**（用于登录并监控频道）

### 第 1 步：下载部署文件

```bash
mkdir tg-emby-streamer && cd tg-emby-streamer

# 下载 docker-compose.yml
wget -O docker-compose.yml https://raw.githubusercontent.com/dirige/tg-emby-streamer/main/docker-compose.yml

# 下载配置模板
wget -O config.example.yaml https://raw.githubusercontent.com/dirige/tg-emby-streamer/main/config.example.yaml

# 创建实际配置文件
cp config.example.yaml config.yaml
```

### 第 2 步：编辑配置文件 `config.yaml`

```yaml
# ============================================
# TG-Emby-Streamer 配置文件
# ============================================

telegram:
  # Telegram API 凭据，从 https://my.telegram.org 获取
  api_id: 12345
  api_hash: "your_api_hash_here"

  # Telegram 账号手机号（用于首次生成 Session String）
  phone: "+8612345678901"

  # Session String（首次运行前需通过脚本生成，填入后优先于 phone 登录）
  session_string: ""

  # 要监控的频道 ID，逗号分隔多个
  # 如何获取频道 ID：将频道消息转发到 @getidsbot 或 @userinfobot
  monitor_channels: "-1001668687022,-1002855500786"

proxy:
  # 是否启用代理（中国大陆等 Telegram 封锁地区必须开启）
  enabled: false
  scheme: socks5           # socks5 或 http
  host: 127.0.0.1
  port: 10808

server:
  # Web 服务监听地址
  host: 0.0.0.0
  port: 8001

  # 外部访问地址 — STRM 文件中会写入此地址作为流 URL
  # 如果通过反向代理或公网访问，请填写对应的域名
  base_url: "http://your-domain.com:8001"

  # 内部访问地址 — Docker 容器内或内网访问时使用的地址
  local_url: "http://192.168.1.100:8001"

  # 流媒体访问 Token（非空时，访问 /stream/* 需携带 ?token=xxx）
  # 用于防止 STRM 文件被泄露后任意播放
  stream_token: ""

storage:
  # 数据存储根目录（数据库、缓存、下载文件）
  data_dir: /app/data

  # STRM 文件输出目录 —— 将此目录挂载/映射到 Emby 媒体库
  strm_dir: /app/data/strm

  # 视频分块缓存目录
  cache_dir: /app/data/cache

  # 内存缓存大小（MB）
  memory_cache_size_mb: 256

  # 磁盘缓存上限（GB）
  disk_cache_max_gb: 50

database:
  # 数据库连接 URL（默认 SQLite）
  url: "sqlite+aiosqlite:////app/data/media.db"

auth:
  # Web 管理面板登录凭据
  username: admin
  password: "your_password"

notify:
  # 入库完成后是否发送 Telegram 通知
  enabled: false
  # 接收通知的 Telegram 用户 ID（数字，可在 @userinfobot 获取）
  target: "@username"
```

### 第 3 步：生成 Telegram Session String

首次使用需要生成 Session String（登录凭证），避免每次重启需要输入验证码：

```bash
# 方式一：使用 Docker 临时运行
docker run --rm -it \
  -v ./config.yaml:/app/config.yaml \
  ghcr.io/dirige/tg-emby-streamer:main \
  python3 scripts/generate_session.py

# 方式二：本地运行（需要 Python 3.12+）
pip install -r requirements.txt
python3 scripts/generate_session.py

# 按照提示输入验证码（如果账号开启了两步验证还需输入密码）
# 生成的 session_string 填入 config.yaml 的 telegram.session_string
```

### 第 4 步：一键启动

```bash
docker compose up -d
```

查看启动日志：

```bash
docker logs -f tg-emby-streamer
```

### 第 5 步：登录管理面板

打开浏览器访问 **http://your-server-ip:8001**，使用 `config.yaml` 中配置的 `auth.username` 和 `auth.password` 登录。

### 第 6 步：配置频道规则

在管理面板中：
1. 进入 **频道规则** 页面
2. 为每个频道添加解析规则（标题规则、集数规则、分类等）
3. 点击 **扫描** 按钮执行首次入库

---

## 环境变量（高级用法）

所有配置项均支持通过环境变量覆盖，适用于 Kubernetes、Docker Swarm 等场景：

| 环境变量 | 对应配置路径 | 说明 |
|----------|-------------|------|
| `TELEGRAM_API_ID` | `telegram.api_id` | Telegram API ID |
| `TELEGRAM_API_HASH` | `telegram.api_hash` | Telegram API Hash |
| `TELEGRAM_PHONE` | `telegram.phone` | 登录手机号 |
| `TELEGRAM_USER_ID` | `telegram.user_id` | 用户 ID |
| `TELEGRAM_SESSION_STRING` | `telegram.session_string` | Session 字符串 |
| `TELEGRAM_MONITOR_CHANNELS` | `telegram.monitor_channels` | 监控频道 ID（逗号分隔） |
| `PROXY_ENABLED` | `proxy.enabled` | 是否启用代理 |
| `PROXY_SCHEME` | `proxy.scheme` | 代理协议（socks5/http） |
| `PROXY_HOST` | `proxy.host` | 代理主机 |
| `PROXY_PORT` | `proxy.port` | 代理端口 |
| `STREAM_HOST` | `server.host` | 监听地址 |
| `STREAM_PORT` | `server.port` | 监听端口 |
| `BASE_URL` | `server.base_url` | 外部访问地址 |
| `STREAM_LOCAL_URL` | `server.local_url` | 内部访问地址 |
| `STREAM_TOKEN` | `server.stream_token` | 流媒体 Token |
| `STREAM_CONCURRENCY` | `server.concurrency` | 并发下载数 |
| `DATABASE_URL` | `database.url` | 数据库连接 URL |
| `CACHE_DIR` | `storage.cache_dir` | 缓存目录 |
| `MEMORY_CACHE_SIZE` | `storage.memory_cache_size_mb` | 内存缓存（MB） |
| `DISK_CACHE_MAX_GB` | `storage.disk_cache_max_gb` | 磁盘缓存（GB） |
| `STRM_OUTPUT_DIR` | `storage.strm_dir` | STRM 输出目录 |
| `WEB_USERNAME` | `auth.username` | 管理面板用户名 |
| `WEB_PASSWORD` | `auth.password` | 管理面板密码 |
| `NOTIFY_ENABLED` | `notify.enabled` | 启用通知 |
| `NOTIFY_TARGET` | `notify.target` | 通知接收者 |

使用 `.env` 文件配合 Docker Compose 自动加载：

```bash
# .env 文件位于 docker-compose.yml 同目录时会被自动加载
echo "TELEGRAM_API_ID=12345" >> .env
echo "TELEGRAM_API_HASH=abc123" >> .env
# ... 其他配置
docker compose up -d
```

---

## 频道规则配置指南

每个频道需要配置一条解析规则，系统根据规则从视频消息的 `caption`（文案）和 `filename`（文件名）中提取元数据。

### 规则字段详解

| 字段 | 必填 | 说明 |
|------|------|------|
| `channel_id` | ✅ | 频道 ID（如 `-1001234567890`） |
| `channel_name` | 可选 | 频道显示名称 |
| `category` | ✅ | 分类：`电视剧` / `电影` / `动漫` / `海外剧` / `18+` / `综艺` / `纪录片` |
| `title_rule` | 推荐 | 标题提取规则（见下方说明） |
| `episode_rule` | 可选 | 集数提取规则 |
| `season` | 可选 | 默认季号（如 `1`），当 caption 中未提取到季数时使用 |
| `season_rule` | 可选 | 从 caption 提取季数的正则 |
| `year_rule` | 可选 | 从 caption 提取年份的正则（用于电影） |
| `filename_rule` | 可选 | 从文件名提取标题的正则（用于 caption 为空的情况） |
| `enabled` | 可选 | 是否启用该规则 |

### 标题规则 `title_rule`

支持两种格式：

**`line:N`** — 取 caption 的第 N 行作为标题（从 0 开始）

```
示例 caption：
#千香 第11集 4K

规则：line:0  → 标题 = "#千香 第11集 4K"
规则：line:0 配合自动清洗 → 标题 = "千香"
```

**`regex:模式`** — 用正则从 caption 中提取标题

```yaml
# 提取 #号后的中文/英文作为标题
title_rule: "regex:#([^\s#]+?)(?:\s|$|第|\d)"

# 对于电影频道，"但丁之手 In the Hand of Dante (2025)" 取中英文标题
title_rule: "regex:^(.+?)\s+\("   # 提取 "但丁之手 In the Hand of Dante"
```

> **自动清洗**：提取到的标题会自动去除 `#tag`、`@mention`、`S01E01`、分辨率标识（4K/1080p）、方括号/圆括号内容等杂质。

### 集数规则 `episode_rule`

```yaml
# 匹配中文 "第11集" "第 28 集" "第100集"
episode_rule: "regex:第\s*(\d+)\s*集"

# 匹配 "EP01" "Ep12" "ep 5"
episode_rule: "regex:[Ee][Pp]\s*(\d+)"

# 匹配 S01E08 格式
episode_rule: "regex:[Ss]\d+[Ee](\d+)"
```

### 季数规则 `season_rule` 与默认 `season`

```yaml
# 默认季号（当未从 caption 提取到季数时使用）
season: 1

# 从 caption 提取季数（支持中文数字 "第一季" "第二季"）
season_rule: "regex:第\s*(\d+)\s*季"
season_rule: "regex:[Ss]eason\s*(\d+)"

# 动漫季数示例：匹配 "S2" 或 "第二季"
season_rule: "regex:[Ss](\d+)"
```

### 年份规则 `year_rule`（用于电影分类）

```yaml
# 匹配英文括号中的年份
year_rule: "regex:\((\d{4})\)"

# 从文件名匹配
year_rule: "regex:\.(\d{4})\."
```

### 文件名称则 `filename_rule`（用于 18+ / 空 caption 频道）

当 caption 为空时，可从文件名提取信息：

```yaml
# 从文件名 "Cloud_Whisper_S01E02_1080p.mp4" 提取标题
filename_rule: "regex:^([^_]+)"
```

### 典型配置示例

**电视剧频道**（caption 格式：`#剧名 第X集 4K`）：

```yaml
category: 电视剧
title_rule: "line:0"
episode_rule: "regex:第\s*(\d+)\s*集"
season: 1
```

**电影频道**（caption 格式：`电影名 (2024)\n简介...`）：

```yaml
category: 电影
title_rule: "regex:^(.+?)\s+\(\d{4}\)"
year_rule: "regex:\((\d{4})\)"
```

**动漫频道**（caption 格式：`#动漫名 第X话 S2`）：

```yaml
category: 动漫
title_rule: "line:0"
episode_rule: "regex:第\s*(\d+)\s*[话集]"
season_rule: "regex:[Ss](\d+)"
```

**海外剧频道**（文件名含 SxxExx）：

```yaml
category: 海外剧
title_rule: "regex:^([A-Za-z\s]+?)_(?=S\d+E\d+)"
episode_rule: "regex:[Ss]\d+[Ee](\d+)"
season: 1
```

### 常见 caption 示例与对应规则

| Caption 示例 | 频道类型 | 推荐 title_rule | 推荐 episode_rule |
|-------------|----------|-----------------|-------------------|
| `#千香 第11集 4K` | 电视剧 | `line:0` | `regex:第\s*(\d+)\s*集` |
| `#非份之罪 第2集 评论区更4K` | 电视剧 | `line:0` | `regex:第\s*(\d+)\s*集` |
| `#云深不知梦 特别篇：逐冥之役 第4集` | 动漫 | `line:0` | `regex:第\s*(\d+)\s*集` |
| `#极致欢愉保障 第8集` | 海外剧 | `line:0` | `regex:第\s*(\d+)\s*集` |
| `但丁之手 In the Hand of Dante (2025)` | 电影 | `regex:^(.+?)\s+\()` | 无 |
| `更多电影Telegram 影集 @yingji.mp4` | 电影 | `filename_rule` | 无 |
| `(空 caption)` | 电视剧 | `filename_rule:regex:^[#]?(.+?)(?:_\d|\.\w+$)` | 无 |

---

## STRM 文件结构

STRM 文件是纯文本文件，内容为视频流的 URL 地址（如 `http://server:8001/stream/12345`），Emby/Jellyfin 读取后直接播放该 URL，无需下载原文件到本地。

系统按分类自动组织目录结构：

```
strm/                                    # STRM 输出根目录（映射到 Emby 媒体库）
│
├── 电视剧/                              # 电视剧分类
│   ├── 剧集名称/                       # 自动清洗后的剧名
│   │   ├── Season 01/                  # 季目录
│   │   │   ├── 剧集名称-S01E01.strm    # 文件名：剧名-S{季}E{集数}
│   │   │   ├── 剧集名称-S01E02.strm
│   │   │   └── ...
│   │   └── Season 02/
│   │       └── ...
│   └── 另一部剧集/
│       └── Season 01/
│
├── 电影/                                # 电影分类
│   └── 电影名称 (2024)/                # 目录名：电影名 (年份)
│       └── 电影名称 (2024).strm        # 文件名同目录名
│
├── 动漫/                                # 动漫分类（类似电视剧结构）
│   ├── 动漫名称/
│   │   ├── Season 01/
│   │   │   ├── 动漫名称-S01E001.strm   # 动漫集数用 3 位数字（E001）
│   │   │   └── ...
│   │   └── Season 02/
│
├── 海外剧/                              # 海外剧分类
│   ├── Series Name/
│   │   └── Season 01/
│   │       ├── Series Name-S01E01.strm
│   │       └── ...
│
└── 18+/                                 # 成人内容
    └── ...
```

**STRM 文件内容示例**：

```
http://192.168.1.100:8001/stream/5734
```

如果配置了 `stream_token`，则为：

```
http://your-domain.com:8001/stream/5734?token=your_secure_token
```

---

## Emby / Jellyfin 集成

### 添加媒体库

在 Emby/Jellyfin 管理后台添加 STRM 输出目录作为媒体库：

1. 进入 **管理后台 → 媒体库**
2. 点击 **添加媒体库**

**电影库设置**：
- 内容类型：`电影`
- 文件夹：选择 `/path/to/strm/电影/`
- 元数据下载器：推荐安装 **MetaTube** 插件

**剧集库设置**：
- 内容类型：`电视剧`
- 文件夹：选择 `/path/to/strm/电视剧/`、`/path/to/strm/动漫/`、`/path/to/strm/海外剧/`
- 元数据下载器：推荐安装 **MetaTube** 插件

**18+ 内容**（可选）：
- 可单独作为一个媒体库，设置访问权限限制
- TG 推送的 JAV 封面和字段走 MetaTube，不依赖 TMDB API Key。
- MetaTube 服务默认 `http://127.0.0.1:8082`，可用 `METATUBE_URL` 环境变量调整。
- 推送封面按番号缓存在 `storage.cache_dir` 同级的 `jav_posters/`，与 Emby 的 STRM 海报隔离，避免共用图片或被插件覆盖。
- 通知格式：`刮削完成`、`📛 番号`、`🎬 标题`、`👤 演员`、`🔗 来源群组`。优先使用原消息中的中文片名/演员，缺失时取对应番号的 NFO / MetaTube；缺图明确提示，不挪用其他番号的图片。
- 补图脚本 `scripts/backfill_18plus_posters.py --media-id <ID>` 仅处理指定记录且不发送通知；请用与 systemd 服务相同的账号运行，避免生成 root 所有的目录。

### Docker 部署建议

如果 Emby 和 TG-Emby-Streamer 在同一台机器上运行，建议共享 STRM 目录：

```yaml
# docker-compose.yml 中 tg-emby-streamer 的 volumes
volumes:
  - ./data/strm:/app/data/strm        # STRM 输出
  - ./data/cache:/app/data/cache      # 视频缓存（可选共享）

# 在 Emby 容器中也挂载同一目录
# emby:
#   volumes:
#     - ./data/strm:/media/telegram   # TG STRM 目录映射到 Emby 媒体库路径
```

### 元数据刮削建议

- **MetaTube 插件**：专为中文 PT/BT 环境设计的刮削插件，支持 TMDB，强烈推荐
  - GitHub：https://github.com/metatube-community/jellyfin-plugin-metatube
- **TMDB 刮削器**：系统默认，适合英文内容

### 播放说明

- Emby 客户端直接播放时，流量经过 TG-Emby-Streamer 代理服务器
- 视频数据通过 Pyrogram MTProto 从 Telegram 下载，经过两级缓存后供给 Emby
- 支持拖拽进度条（Range 请求自动处理）
- 4K 原画播放，码率与 Telegram 源文件一致

---

## FAQ / 常见问题

### Q：如何获取 Telegram 频道 ID？

将频道中的任意消息转发给机器人 **@getidsbot** 或 **@userinfobot**，机器人会回复频道 ID（通常是以 `-100` 开头的数字）。

### Q：Session String 是什么？为什么需要它？

Session String 是 Pyrogram 的登录凭证字符串，包含了登录状态。使用 Session String 可以避免每次重启容器时都需要输入验证码。通过 `scripts/generate_session.py` 脚本生成。

### Q：连接 Telegram 报错 "PEER_ID_INVALID"

Telegram 客户端需要先获取对话列表缓存。解决方案：
1. 确保频道 ID 正确
2. 在 Web 面板中先访问 **频道列表** 页面加载缓存
3. 或者在配置中将频道 ID 加入 `TELEGRAM_MONITOR_CHANNELS`

### Q：播放时卡顿或缓冲

- 检查缓存配置：建议 `memory_cache_size_mb: 256`，`disk_cache_max_gb: 50`
- 默认预取 30 个分片（每个 1MB），可在 `config.py` 中调整 `prefetch_chunks`
- 如果 Telegram 连接速度慢，请检查代理
- 单个文件超过 2GB 时，Telegram 服务端限制分片大小需在 1024~524288 字节之间，系统已自动处理

### Q：STRM 文件已生成但 Emby 不显示

1. 在 Emby 中执行 **媒体库扫描**
2. 检查 STRM 文件内容中的 URL 是否可从 Emby 容器访问
3. 如果 Emby 和 TG-Emby-Streamer 在不同机器，确保 `base_url` 或 `local_url` 可被 Emby 访问
4. 安装 MetaTube 插件辅助刮削

### Q：如何更新配置？

直接编辑 `config.yaml` 后执行 `docker compose restart tg-emby-streamer`。或者通过 Web 面板的 **系统设置** 页面在线上修改（部分配置修改后自动生效）。

### Q：如何清理缓存？

Web 面板 → **缓存管理** → **清空缓存**。或者直接执行：

```bash
rm -rf ./data/cache/*
docker compose restart tg-emby-streamer
```

### Q：支持 Telegram 群组吗？

支持。如果是公开群组，直接用群组 ID（`-100...`）即可。如果是私密群组，需要先加入群组并确保客户端有权访问。

### Q：视频流 URL 泄露了怎么办？

在 `config.yaml` 中设置 `stream_token` 为随机字符串，然后重新生成 STRM 文件（Web 面板 → STRM 管理 → 重新生成）。旧 STRM 文件中的 URL 将失效。

### Q：扫描时提示 "NEED_LOGIN"

Session String 过期或无效。重新运行 `scripts/generate_session.py` 生成新的 Session String 并更新配置。

### Q：如何排查解析规则是否正确？

在 `samples/` 目录下有各频道的消息样本，可用 `scripts/extract_samples.py` 提取更多样本。也可直接查看 Web 面板中 **未识别媒体** 列表，对照实际 caption 调整正则规则。

---

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| Telegram 客户端 | Pyrogram (MTProto) |
| 数据库 | SQLite + SQLAlchemy (Async) |
| 缓存 | 内存 LRU + 磁盘文件 |
| 前端 | 纯 HTML/CSS/JS（无框架依赖） |
| 部署 | Docker / Docker Compose |
| 代理 | SOCKS5 / HTTP |

## License

MIT
