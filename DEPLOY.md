# 部署教程

本文档提供详细的部署步骤，帮助你从零开始搭建 TG Emby Streamer。

---

## 目录

- [环境要求](#环境要求)
- [方式一：Docker 部署（推荐）](#方式一docker-部署推荐)
- [方式二：本地部署](#方式二本地部署)
- [配置详解](#配置详解)
- [Emby/Jellyfin 配置](#embyjellyfin-配置)
- [常用命令](#常用命令)
- [常见问题](#常见问题)

---

## 环境要求

| 项目 | 要求 |
|------|------|
| Python | 3.11+（本地部署） |
| Docker | 20.10+（Docker 部署） |
| Telegram API | 从 https://my.telegram.org 获取 |
| Bot Token | 从 @BotFather 获取（可选，用于负载均衡） |
| 私有群聊 | 用于存储媒体的 Telegram 群聊 |
| 代理 | 国内服务器需要，海外服务器不需要 |

---

## 方式一：Docker 部署（推荐）

### 1. 创建项目目录

```bash
mkdir tg-emby && cd tg-emby
```

### 2. 下载配置文件

```bash
# 下载 docker-compose.yml
wget https://raw.githubusercontent.com/Dirige/tg-emby-streamer/main/docker-compose.yml

# 下载配置模板
wget https://raw.githubusercontent.com/Dirige/tg-emby-streamer/main/.env.example
```

### 3. 创建配置文件

```bash
cp .env.example .env
```

### 4. 编辑配置

```bash
nano .env
```

**必须填写的配置：**

```env
# Telegram API（从 https://my.telegram.org 获取）
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash_here

# 私有群聊 ID（负数）
TELEGRAM_CHANNEL_ID=-1001234567890

# 监听频道（逗号分隔）
TELEGRAM_MONITOR_CHANNELS=-1001111111111,-1002222222222

# 流媒体外网地址
BASE_URL=http://你的服务器IP:8001

# Web 面板登录（可选）
WEB_USERNAME=admin
WEB_PASSWORD=your_password
```

### 5. 生成 Session（首次需要）

```bash
docker-compose run --rm app python generate_session.py
```

按提示输入手机号和验证码。

### 6. 启动服务

```bash
docker-compose up -d
```

### 7. 访问 Web 面板

打开浏览器访问 `http://你的服务器IP:8001`

---

## 方式二：本地部署

### 1. 克隆仓库

```bash
git clone https://github.com/Dirige/tg-emby-streamer.git
cd tg-emby-streamer
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置

```bash
cp .env.example .env
nano .env
```

填写配置（同上方 Docker 部署的第 4 步）。

### 5. 生成 Session

```bash
python generate_session.py
```

按提示输入手机号和验证码，Session String 会自动写入 `.env`。

### 6. 启动

```bash
python run.py
```

---

## 配置详解

### Telegram 配置

| 变量 | 必填 | 说明 | 获取方式 |
|------|------|------|----------|
| `TELEGRAM_API_ID` | ✅ | API ID | https://my.telegram.org → API development tools |
| `TELEGRAM_API_HASH` | ✅ | API Hash | 同上 |
| `TELEGRAM_SESSION_STRING` | ✅ | 会话字符串 | 运行 `python generate_session.py` |
| `TELEGRAM_CHANNEL_ID` | ✅ | 私有群聊 ID | 创建私有群聊，获取负数 ID |
| `TELEGRAM_MONITOR_CHANNELS` | ✅ | 监听频道列表 | 逗号分隔的频道 ID |
| `TELEGRAM_BOT_TOKEN` | ❌ | Bot Token | @BotFather（用于负载均衡） |
| `CHANNEL_CATEGORIES` | ❌ | 频道分类映射 | `频道ID=分类`，逗号分隔 |
| `ADULT_CHANNELS` | ❌ | 18+ 频道列表 | 逗号分隔的频道 ID |
| `CAPTION_ONLY_CHANNELS` | ❌ | 仅从 caption 提取标题 | 文件名无意义的频道 ID |

**分类说明：**
- `tv` — 电视剧
- `movie` — 电影
- `anime` — 动漫
- `dongman` — 东漫（国产动漫）
- `cosplay` — 18+ 内容
- `overseas` — 海外剧
- `variety` — 综艺
- `documentary` — 纪录片

**配置示例：**

```env
TELEGRAM_CHANNEL_ID=-1001234567890
TELEGRAM_MONITOR_CHANNELS=-1001111111111,-1002222222222,-1003333333333
CHANNEL_CATEGORIES=-1001111111111=movie,-1002222222222=tv,-1003333333333=anime
ADULT_CHANNELS=-1004444444444
CAPTION_ONLY_CHANNELS=-1005555555555
```

### 代理配置

#### 模式一：sing-box（推荐，国内服务器）

```env
SINGBOX_ENABLED=true
SINGBOX_ADDRESS=你的服务器地址
SINGBOX_PORT=443
SINGBOX_UUID=你的UUID
SINGBOX_PATH=/?ed=2048
SINGBOX_HOST=你的域名
SINGBOX_TLS=true
SINGBOX_FINGERPRINT=chrome
SINGBOX_SOCKS_PORT=10808
SINGBOX_PROTOCOL=vless
```

#### 模式二：外部代理（已有代理工具）

```env
PROXY_ENABLED=true
PROXY_SCHEME=socks5
PROXY_HOST=127.0.0.1
PROXY_PORT=7890
```

#### 模式三：订阅代理（有订阅链接）

```env
PROXY_SUB_URL=https://你的订阅地址
```

#### 模式四：直连（海外服务器）

```env
SINGBOX_ENABLED=false
PROXY_ENABLED=false
```

### 流媒体配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STREAM_HOST` | `0.0.0.0` | 监听地址 |
| `STREAM_PORT` | `8001` | 监听端口 |
| `BASE_URL` | `http://localhost:8001` | 外网访问地址（用于 STRM 文件） |
| `STREAM_LOCAL_URL` | `http://localhost:8001` | 内网访问地址 |
| `STREAM_CONCURRENCY` | `3` | 并发下载数 |
| `STREAM_MAX_RETRIES` | `3` | 最大重试次数 |

**重要：** `BASE_URL` 必须是 Emby 服务器能访问到的地址。如果 Emby 和本服务在同一台机器，可以用 `http://localhost:8001`；如果在不同机器，需要用内网 IP 或外网 IP。

### 缓存配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CACHE_DIR` | `./cache` | 缓存目录 |
| `MEMORY_CACHE_SIZE` | `256` | 内存缓存大小（MB） |
| `DISK_CACHE_MAX_GB` | `50` | 磁盘缓存上限（GB） |

---

## Emby/Jellyfin 配置

### 添加媒体库

1. 打开 Emby/Jellyfin 管理面板
2. 点击「媒体库」→「新媒体库」
3. 选择类型（如「电影」或「电视节目」）
4. 添加文件夹：选择项目的 `strm/` 目录
5. 保存

### 播放设置

1. 进入「管理」→「播放」
2. **启用**：直接播放（Direct Play）
3. **禁用**：转码（Transcoding）
4. 保存

### 扫描媒体库

1. 进入「媒体库」
2. 点击「扫描所有媒体库」
3. 等待扫描完成

---

## 常用命令

### 生成 Session String

```bash
python generate_session.py
```

### 扫描历史消息

```bash
# 从监听频道转发到私有群聊（每个频道最多 100 条）
python scan_history.py forward --limit 100

# 从私有群聊录入数据库并生成 STRM
python scan_history.py record --limit 100
```

### 重新生成 STRM 文件

```bash
python fix_strm.py
```

### 查看数据库内容

```bash
python check_db.py
```

### 诊断工具

```bash
python scripts/diagnose.py
```

### 清理未识别记录

```bash
python scripts/clear_unrecognized.py
```

---

## 常见问题

### 无法连接 Telegram

**原因：** 国内服务器无法直接访问 Telegram。

**解决：**
1. 配置代理（sing-box 或外部代理）
2. 确保代理服务正在运行
3. 在 Web 面板测试 Telegram 连接

### STRM 文件无法播放

**原因：** Emby 无法访问流媒体服务器。

**解决：**
1. 检查 `BASE_URL` 是否正确
2. 检查防火墙是否开放端口
3. 确保 Emby 启用了直接播放

### 视频未被检测到

**原因：** 频道 ID 配置错误或权限不足。

**解决：**
1. 检查 `TELEGRAM_CHANNEL_ID` 和 `TELEGRAM_MONITOR_CHANNELS`
2. 确保 ID 是负数（如 `-1001234567890`）
3. 确保 Bot 或账号在频道/群聊中

### 文件名乱码或相同

**原因：** 频道没有设置有意义的文件名。

**解决：**
在 `.env` 中添加：
```env
CAPTION_ONLY_CHANNELS=-100xxxxxxxxxx
```
这样会从消息 caption 中提取标题。

### 如何获取频道 ID？

1. 转发频道消息到 @userinfobot
2. 或使用 @RawDataBot
3. 或在 Web 面板的 Telegram 页面查看

### 如何获取群聊 ID？

1. 将 @userinfobot 添加到群聊，发送 /start
2. 或使用 @RawDataBot
3. 或将 Bot 添加到群聊，在 Web 面板查看

### Docker 部署后无法访问

**解决：**
1. 检查容器是否运行：`docker-compose ps`
2. 查看日志：`docker-compose logs -f`
3. 确保端口映射正确：`-p 8001:8001`

---

## 更新日志

### v2.0

- 新增 Web 管理面板
- 新增 18+ 内容独立管理
- 新增订阅自动代理
- 新增 Bot 负载均衡
- 优化文件名解析
- 优化 STRM 目录结构
