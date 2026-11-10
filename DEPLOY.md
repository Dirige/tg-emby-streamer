# TGEmby 完整部署教程

**推荐顺序：安装 → 设置代理（国内）→ 导入媒体包 → 加入频道 → 登录 Telegram → 设置 Emby。**

“开箱即用”指规则、STRM、NFO 和封面可直接导入；你仍必须使用**自己的 Telegram 账号、API 凭据和频道权限**。数据包不是视频文件，也不能绕过私有频道权限。

## 1. 安装前准备

- Linux 主机/NAS，支持 `amd64`（普通电脑）和 `arm64`。
- Docker 和 Docker Compose V2；安装脚本还需要 Python 3，仅使用其标准库。
- 建议至少留 **25GB 可用磁盘**。媒体包约 4.3GiB，导入验证和写入会临时需要约两倍解压空间；视频缓存另外计算。
- 可登录的 Telegram 账号。国内通常需要自己准备可用代理，项目不提供代理节点或订阅。
- 默认端口：TGEmby `8001`、Emby `8096`；MetaTube 的宿主机端口 `8082` 默认仅本机可访问。

### 没有 Docker？Ubuntu/Debian 的安装命令

已有 Docker 的主机跳过这一段。其他系统请按 [Docker 官方安装文档](https://docs.docker.com/engine/install/) 操作。

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git python3
sudo install -m 0755 -d /etc/apt/keyrings
. /etc/os-release
curl -fsSL "https://download.docker.com/linux/$ID/gpg" | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

最后重新登录 SSH，让 Docker 用户组权限生效。Docker 用户组拥有近似 root 的权限，只给可信用户。

检查：

```bash
docker version
docker compose version
```

## 2. 获取部署文件

### 拿到组合发布包（最省事）

组合包包含干净源码、amd64/arm64 的 TGEmby 离线镜像、已核对的官方 Emby 版 MetaTube 插件和中文教程，媒体数据仍单独放在三个分卷中。

```bash
tar -xzf tgemby-deploy-share-20261109.tar.gz
cd tgemby-release
bash 一键部署.sh --url http://192.168.1.20:8001
cd tgemby
```

把 IP 改成你的服务器地址。入口自动选架构、核对校验值、加载 TGEmby 镜像，启动 MetaTube/Emby，并为自带 Emby 预放官方插件与无凭据的后端地址；默认跳过 Telegram 登录，方便先导入空库。已有 Emby 则给入口加 `--existing-emby`，不重复启动 Emby。

**组合包没有打包 MetaTube/Emby/mihomo 服务镜像。** 入口会拉取本机缺少的服务镜像；国内需先配置 Docker 自身代理。全部依赖镜像已事先加载时可加 `--no-pull`。这个入口只用于新的安装目录；后续维护使用 `tgemby/scripts/install.sh`。

端口/UID/代理 profile 要预先自定义时，先解压内层 `tgemby-source.tar.gz`，复制 `tgemby/.env.example` 为 `tgemby/.env` 并修改，再运行入口。请先看组合包中的 `先看我.md`。

### 拿到作者分享的源码包（无需 GitHub）

```bash
tar -xzf tgemby-source.tar.gz
cd tgemby
```

### 使用 GitHub（维护者发布这些文件后）

```bash
git clone https://github.com/Dirige/tg-emby-streamer.git
cd tg-emby-streamer
```

不要直接复制作者的 `runtime/`、`config.yaml`、Session 或 Emby `/config`。发布包会为你生成自己的密码。

## 3. 一键部署

把下面的 `192.168.1.20` 改成**你的服务器 IP**；有 HTTPS 反代则填写 `https://你的域名`。

```bash
bash scripts/install.sh --url http://192.168.1.20:8001 --skip-login
```

这个地址必须同时被 **Emby 服务器和播放设备**访问到。不要填写 `localhost`、`127.0.0.1` 或只能被容器解析的 `tgemby`，否则其他设备不能回源播放。

脚本会创建数据目录、生成随机密码和播放 Token、导入 19 条默认规则、启动 TGEmby/MetaTube。默认 `.env` 的 `COMPOSE_PROFILES=emby` 会同时启动一个独立 Emby，并尝试下载官方 **Emby 版** MetaTube 插件。

安装默认拉取 `dirige/tgemby:share-20261109`（见 `.env.example` 的 `TGEMBY_IMAGE`）。该镜像同时通过 GitHub Actions 发布到 **ghcr.io/dirige/tgemby:latest**（仓库首次发布后把 Package 设为 Public 即可匿名拉取）；如果你愿意收到它，把 `tgemby/.env` 里的 `TGEMBY_IMAGE` 改成：

```env
TGEMBY_IMAGE=ghcr.io/dirige/tgemby:latest
```

国内服务器拉取 GHCR 需按第 4 节给 Docker 配置自身代理，或使用随包离线镜像。若镜像尚未发布或不可拉取，脚本会自动用随包源码构建；也可明确要求本地构建：

```bash
bash scripts/install.sh --build --url http://192.168.1.20:8001 --skip-login
```

### 使用分享的离线 TGEmby 镜像

根据主机架构选择 `amd64` 或 `arm64` 的 Docker 归档（`uname -m`：`x86_64` 对应 amd64，`aarch64` 对应 arm64）：

```bash
docker load -i tgemby-share-20261109-arm64.docker.tar.gz
bash scripts/install.sh --offline --url http://192.168.1.20:8001 --skip-login
```

`--offline` 不拉取或构建镜像，也不联网下载插件。**MetaTube 和可选 Emby 的镜像仍须事先拉取/加载到本机**；没有它们不能凭 TGEmby 单个归档完成全套离线安装。`--offline` 与 `--build` 不同时使用。

`--skip-login` 是为了先导入空库，避免登录后的自动扫描提前产生记录。没有媒体包的人可省略它，在脚本提示时登录。

### 已有 Emby，或者端口被占用

在第一次运行安装脚本**之前**准备 `.env`：

```bash
cp .env.example .env
nano .env
```

按需修改：

```dotenv
# 已有 Emby：留空，不重复部署另一个 Emby
COMPOSE_PROFILES=

# 如果原端口被占用，可改成下面的测试端口
TGEMBY_PORT=18001
METATUBE_PORT=18082
EMBY_PORT=18096
```

对应安装地址也要改成 `http://服务器IP:18001`。不要改容器内部 `8001`/`8080`/`8096` 端口。

### 手动启动（不用安装脚本）

下面以默认 UID/GID `1000:1000` 为例，先编辑 `.env` 的端口和 profile：

```bash
cp .env.example .env
mkdir -p runtime/{config,strm,cache,jav_posters,metatube,emby,proxy,share}
sudo chown 1000:1000 runtime/{config,strm,cache,jav_posters,metatube,emby,proxy,share}
docker compose build tgemby
docker compose run --rm --no-deps --entrypoint python tgemby scripts/deploy.py init --url http://192.168.1.20:8001
docker compose up -d
```

初始化在容器里执行，配置中的 `/app/...` 是容器路径，不是宿主机路径。不要在宿主机生成带宿主机绝对路径的配置，再直接交给 Docker。

## 4. 国内代理：区分三个用途

| 用途 | 在哪里设置 | 注意 |
|---|---|---|
| Telegram 登录/拉流 | `runtime/config/config.yaml` 的 `proxy` | 支持 HTTP/SOCKS5，和刮削代理独立 |
| MetaTube/TGEmby 的 HTTP 刮削 | `.env` 的 `SCRAPER_PROXY` | 同时传给 `HTTP_PROXY` 与 `HTTPS_PROXY` |
| Docker 拉镜像/构建依赖 | Docker daemon/客户端代理 | `SCRAPER_PROXY` 不能解决镜像拉取失败 |

### 方案 A：已有局域网代理，最简单

例如你的代理在 `192.168.1.2:7890`：

```bash
# MetaTube + TGEmby 的 HTTP 刮削
bash scripts/install.sh --proxy http://192.168.1.2:7890

# Telegram，按你的代理实际支持的协议选择
bash scripts/install.sh --telegram-proxy socks5://192.168.1.2:7890
```

如果 URL 带用户名/密码，不要写在命令里。运行不带 URL 的命令，脚本会隐藏输入：

```bash
bash scripts/install.sh --proxy
bash scripts/install.sh --telegram-proxy
```

特殊字符应做 URL 编码，例如密码中的 `@` 写作 `%40`。

### 方案 B：代理在这台服务器上

容器里的 `127.0.0.1` 是**容器自己**。使用：

```bash
bash scripts/install.sh --proxy http://host.docker.internal:7890
bash scripts/install.sh --telegram-proxy socks5://host.docker.internal:7890
```

代理必须监听 Docker 网关/可达网卡，而不只是宿主机 `127.0.0.1`；在防火墙中只允许 Docker 网段和可信内网。Clash/mihomo 通常需要 `allow-lan: true`，但**不能把未鉴权代理暴露到公网**。

### 方案 C：本套 Compose 自带 mihomo

1. 准备自己可用的完整 Clash/mihomo 配置，包括代理节点或订阅。
2. 放进 `runtime/proxy/config.yaml`。
3. 确认配置包含下列监听设置，不要重复添加同名字段：

```yaml
mixed-port: 7890
allow-lan: true
bind-address: '*'
log-level: warning
```

4. 修改 `.env`：需要自带 Emby 就填 `COMPOSE_PROFILES=emby,proxy`；已有 Emby 则填 `COMPOSE_PROFILES=proxy`。
5. 启动并设置代理：

```bash
docker compose up -d proxy
bash scripts/install.sh --proxy http://proxy:7890
bash scripts/install.sh --telegram-proxy socks5://proxy:7890
```

`proxy:7890` 是 Docker 内网地址；宿主机的代理端口只发布在 `127.0.0.1:7890`。订阅文件属于私密凭据，不要发给别人。

## 5. 导入媒体数据包

必须导入到**空媒体库**；脚本会先停本项目的 TGEmby/Emby，结束后恢复原先正在运行的容器。不会停止你的其他 Emby 或 systemd 服务。

如果收到分卷，把全部分卷放到同一目录：

```bash
cat tgemby-library.tar.gz.part* > tgemby-library.tar.gz
sha256sum -c checksums.sha256
```

整包和每个分卷都应显示 `OK`。缺卷、校验失败不要继续。压缩包内的文件校验用于检查完整性，来自可信维护者的整包 SHA256 还用于核对下载来源。

从项目目录导入，替换下面的路径：

```bash
bash scripts/install.sh --import /你保存数据包的目录/tgemby-library.tar.gz
```

也可以带上维护者提供的 64 位整包 SHA256：

```bash
bash scripts/install.sh --import /你保存数据包的目录/tgemby-library.tar.gz 这里替换为整包SHA256
```

导入工具会：

- 检查每个文件的 SHA256、SQLite 完整性和 STRM 来源身份。
- 把 STRM、NFO、图片、通知封面及媒体数据库写入你自己的 `runtime`。
- 将 STRM 地址与 Token 替换成**你自己的值**，NFO 图片改为相对路径。
- 保留原频道规则、分类、监控列表和启用/停用状态。
- 保留你自己的 Telegram/Emby 配置、密码和通知设置。
- 替换初始化的空数据库前，保存 `media-before-import-*.db` 备份。

数据包不包含视频缓存、Telegram Session、作者密码/Token、Emby 用户和观看记录。Emby 内部数据库没有直接复制，已核对的媒体字段转成了便携 NFO/图片。

## 6. 加入频道并登录 Telegram

### 关注频道

打开 [CHANNELS.md](CHANNELS.md)，点击需要的频道链接，使用准备给 TGEmby 登录的**同一个账号**加入。

原 19 条规则全部保留，但不是 19 条全部启用。未提供公开链接的私有频道需要管理员有效邀请；`t.me/c/...` 是成员消息链接，不是邀请。某些频道旧消息对新成员不可见、视频可能已删除，这些条目无法凭数据包恢复权限。

### 申请 API ID/API Hash

1. 打开 <https://my.telegram.org>，登录自己的手机号。
2. 进入 **API development tools**，创建应用。
3. 保存自己的数字 `api_id` 和字符串 `api_hash`；它们不是 Bot Token，不要分享。

### 登录

```bash
bash scripts/install.sh --login
```

依次输入 API ID、API Hash、带国家区号的手机号、Telegram 客户端收到的验证码；开了两步验证就再输云密码。API Hash 不回显，Session 直接写入 `runtime/config/config.yaml`，工具不会打印 Session。

登录后脚本重启 TGEmby，让主进程使用新会话。已导入的库无需全量重新扫描或重新刮削；只想同步新增内容时，让自动监控继续运行即可。

## 7. 登录管理面板

打开 `http://服务器IP:8001`（或你设置的端口/域名）。账号和随机密码保存在：

```text
runtime/config/access.txt
```

这是私密文件，可以在本机查看，但不要截图或发到群里。浏览器 `/login` 是面板登录；`/api/auth/*` 是 Telegram 授权接口，需要先登录面板。

常用操作：频道规则管理、已入库媒体、手动扫描、系统设置、缓存清理。

### 可选：转发 Bot、入库通知与 Emby 自动刷新

在面板设置或私密 `runtime/config/config.yaml` 填你自己的值：

- 向 [@BotFather](https://t.me/BotFather) 创建 Bot，Token 填到 `notify.bot_token`。
- `bot.admin_users` 填允许转发入库的 Telegram 用户 ID，逗号分隔；不要把所有人设成管理员。
- 给 Bot 发一次 `/start`，然后配置 `notify.enabled: true` 和 `notify.target` 接收通知。
- Emby 控制台创建自己的 API 密钥，TGEmby 中填 `emby.url` 与 `emby.api_key`；同套 Compose 地址为 `http://emby:8096`。

上述功能默认不继承作者的 Bot、接收人或 Emby Key，避免向陌生群组发通知。修改后重启本项目 TGEmby。

## 8. Emby 媒体库设置

### 使用自带 Emby

`.env` 的 `COMPOSE_PROFILES=emby` 已启用它。浏览器访问 `http://服务器IP:8096`，按首次向导建立**自己的管理员账户**。

在“控制台 → 媒体库 → 添加媒体库”分别添加分类，而不是把所有内容都当电影：

| 分类 | 内容类型 | Emby 中选择的路径 |
|---|---|---|
| 电影 | 电影 | `/media/tg/电影` |
| 电视剧 | 电视剧 | `/media/tg/电视剧` |
| 动漫 | 电视剧 | `/media/tg/动漫` |
| 海外剧 | 电视剧 | `/media/tg/海外剧` |
| 18+、国产、无码、水果派 | 各自独立电影库 | 对应 `/media/tg/分类名` |

成人媒体库仅对成年、获授权用户开放，不给儿童账户访问。没有数据的分类可以暂不添加。

启用 NFO 元数据读取和本地图片识别，先做一次**扫描媒体库**。已有刮削资料时不要点“替换全部元数据/图片”。STRM 目录默认只读，Emby 自己的缓存写在它的 `/config`，不向 STRM 目录保存新文件。

同一作品有多份 STRM 时保留全部文件。在 Emby 媒体库的高级设置中启用**按文件合并多版本**（`EnableMultiVersionByFiles`）；不想按刮削资料误并其他来源时，关闭**按元数据合并多版本**（`EnableMultiVersionByMetadata`）。Emby 4.10 的隔离样本已验证同目录 2～3 个版本可显示为单张卡片，播放时选择具体版本，原 Telegram 来源不会被删掉。不同版本的界面名称可能略有差别。

### 使用已有 Emby/Jellyfin

把本项目的**宿主机**目录挂到它的容器，例如在其 Compose 配置添加：

```yaml
volumes:
  - /你的项目绝对路径/runtime/strm:/media/tg:ro
```

随后在那个 Emby 里添加上表路径。不要拷贝作者 Emby `/config`，否则可能把用户数据一起拷走。

## 9. MetaTube 后端与 Emby 插件

**后端和插件是两件东西。** Compose 已部署 MetaTube 后端，TGEmby 用 `.env` 的 `METATUBE_URL=http://metatube:8080` 连接；无需在 TGEmby 面板另找一个 MetaTube 地址选项。

### 检测后端

```bash
curl -f http://127.0.0.1:8082/
curl -f http://127.0.0.1:8082/v1/providers
```

改了端口就把 `8082` 换掉。**`/health` 和 `/v1/health` 不是该后端的健康接口，返回 404 不代表坏了。**

### 安装 Emby 插件

自带 Emby 的安装脚本会尝试自动安装；失败或后续更新时执行：

```bash
bash scripts/install.sh --metatube-plugin
```

工具从 [官方 Release](https://github.com/metatube-community/jellyfin-plugin-metatube/releases/latest) 下载 `Emby.MetaTube@版本.zip`，核对官方 SHA256，只取 `MetaTube.dll`；已有不同版本会先备份。**不要把 Jellyfin 版 DLL 安装到 Emby。**

组合包的 `一键部署.sh` 已预装随包核对过的 DLL，不需要再从 GitHub 下载。已有 Emby 的用户应停止**自己的 Emby**、备份旧 DLL，再把随包 `MetaTube.dll` 放进自己的 Emby 配置目录 `plugins/` 后重启；不要复制他人的 Emby 配置或账户。

然后在 Emby 的 MetaTube 插件设置：

- **Server**：同套 Compose 填 `http://metatube:8080`，不要加 `/v1`。
- **Token**：默认后端没设置鉴权，留空。不要误填 TGEmby 的播放 Token。
- 在 `18+` 电影库的元数据下载器/图片获取器里按需启用 MetaTube；普通电影/电视剧用 TMDB，国产/无码/解说的原始来源记录不要盲目按番号重刮。

已有 Emby 若不在本项目 Docker 网络里，不能解析 `metatube`。可把后端只绑定到服务器内网 IP，例如 `.env` 改 `METATUBE_BIND=192.168.1.20` 后 `docker compose up -d metatube`，该 Emby 的插件 Server 填 `http://192.168.1.20:8082`。防火墙只允许 Emby 主机访问，不开放到公网。

### 不用本套 Compose，单独部署后端的完整例子

下面代理 IP/目录只是示例，请换成你自己的；已经启动本套 MetaTube 则不要重复执行占用相同端口。

```bash
mkdir -p "$HOME/metatube-data"
docker run -d --name metatube --restart unless-stopped \
  -p 127.0.0.1:8082:8080 \
  -v "$HOME/metatube-data:/config" \
  -e HTTP_PROXY=http://192.168.1.2:7890 \
  -e HTTPS_PROXY=http://192.168.1.2:7890 \
  -e NO_PROXY=localhost,127.0.0.1 \
  ghcr.io/metatube-community/metatube-server:latest \
  -dsn /config/metatube.db -db-auto-migrate
```

不需代理就去掉两个 `PROXY` 参数。[官方代理说明](https://metatube-community.github.io/wiki/proxy-configuration/)要求同时设置 HTTP/HTTPS 代理；日本/美国出口对部分源更兼容，但服务端封禁、地区限制仍可能造成失败。

## 10. 路径、搬家和备份

| 内容 | 宿主机 | TGEmby/Emby 容器 |
|---|---|---|
| 配置、密码、媒体数据库 | `runtime/config/` | TGEmby `/app/config/` |
| STRM、NFO、媒体图片 | `runtime/strm/` | TGEmby `/app/strm/`；Emby `/media/tg/` |
| 视频分片缓存 | `runtime/cache/` | TGEmby `/app/cache/` |
| 通知封面 | `runtime/jav_posters/` | TGEmby `/app/jav_posters/` |
| MetaTube SQLite | `runtime/metatube/` | MetaTube `/config/` |
| 自带 Emby 账户/配置 | `runtime/emby/` | Emby `/config/` |
| 代理订阅/节点 | `runtime/proxy/` | mihomo 配置目录 |

搬家/换 IP/域名只需：

```bash
bash scripts/install.sh --set-url https://你的新域名
```

这会更新外部地址、STRM 地址、密码说明文件并重启 TGEmby。要作废泄露的**播放 Token**，另执行：

```bash
bash scripts/install.sh --rotate-token
```

改 URL 不等于改 Token，二者不要混淆。

### 私密备份

先停**本项目**容器，避免直接压缩正在写入的 SQLite/WAL：

```bash
docker compose stop
tar -czf "tgemby-private-backup-$(date +%F).tar.gz" runtime/config runtime/strm runtime/jav_posters runtime/metatube runtime/emby runtime/proxy
docker compose up -d
```

这是含账户凭据的私密备份，**不能当分享包发送**。分享给别人请用 [DATA_SHARE.md](DATA_SHARE.md) 的脱敏导出工具；视频缓存通常不用备份。

## 11. 常见问题

| 现象 | 先检查 |
|---|---|
| `PermissionError` | `.env` 的 PUID/PGID 是否有权读写 `runtime`；不要把宿主机绝对路径配置交给容器 |
| 导入提示库非空 | 是否在导入前登录并自动扫描过；工具不会覆盖已有媒体，使用新的项目/空库 |
| `PEER_ID_INVALID`/无权播放 | 是否同一个 Telegram 账号已经加入来源频道、能在 TG 客户端打开那条消息 |
| 登录超时 | Telegram 代理协议、监听地址和端口是否正确；不是只设置 `SCRAPER_PROXY` |
| MetaTube 404 | 检测 `/v1/providers`，不要检测不存在的 `/v1/health` |
| MetaTube 搜不到/补图失败 | 容器的代理能否连通、源站是否封禁出口 IP；已导入 NFO/图片无需全库重刮 |
| Emby 看得到影片但不能播放 | STRM URL 是否可从 Emby/播放设备访问，消息是否已删除、Token 是否已更换 |
| Docker 拉镜像失败 | 配置 Docker 自身代理/可信镜像来源，或使用随包的离线镜像；容器代理不解决拉镜像问题 |
| 内网能播、外网不能播 | 使用外网可达的 HTTPS 域名作为 STRM URL，配置反向代理、端口和防火墙；仅内网 IP 不行 |

状态检查：

```bash
bash scripts/install.sh --status
docker compose logs --tail 50 tgemby
```

Docker 镜像默认关闭 Uvicorn URL 访问日志，避免播放 Token 被写入日志；若另用 Nginx 等反代，也不要公开带查询参数的访问日志。提 Issue 前自行遮住手机号、API Hash、Session、密码、代理订阅、Emby Key 和播放 Token。

官方参考：[MetaTube 部署](https://metatube-community.github.io/wiki/server-deployment/) · [插件安装](https://metatube-community.github.io/wiki/plugin-installation/) · [代理配置](https://metatube-community.github.io/wiki/proxy-configuration/)
