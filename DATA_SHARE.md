# 数据包私下分享说明

## 包含与排除

包含：TGEmby 媒体数据库、STRM、NFO、本地媒体图片、`jav_posters` 通知封面、频道规则/监控状态/加入链接，以及已核对来源的 Emby 刮削字段和本地缓存图片。

不包含：视频本体/分片缓存、Telegram Session/API 凭据、作者的面板密码/播放 Token、Emby 内部数据库、用户/观看记录、Bot Token 或代理订阅。

原来源 `(chat_id, message_id)` 和分类/规则保留；不会为迁移重新解释或删掉其他来源。不能确认来源身份的 Emby 刮削字段不会硬套给影片；原 STRM/NFO 仍保留。

## 1. 作者导出

在带项目依赖的 Python 环境执行，生产服务不用停，工具只读 SQLite 快照：

```bash
python scripts/library_bundle.py export \
  --config /你自己的TGEmby配置目录/config.yaml \
  --output /你的私有目录/tgemby-library \
  --emby-config /你自己的Emby配置目录 \
  --part-size-mb 1800
```

`--emby-config` 可省略。`--output` 必须是一个还不存在的新目录，不会覆盖既有导出物。

需要补充频道链接时，`--channel-links` 必须指向 **ID → URL 映射**，不是完整的 `channels.json`：

```json
{
  "-1001234567890": "https://t.me/你的公开频道用户名"
}
```

也可只读查询已知频道的公开用户名，不会加入频道或创建邀请：

```bash
python scripts/export_channel_links.py \
  --config /你自己的TGEmby配置目录/config.yaml \
  --channels channels.json \
  --output /你的私有目录/channel-links.json
```

然后给导出命令加 `--channel-links /你的私有目录/channel-links.json`。无公开用户名的私有频道仍需管理员提供有效邀请，不能根据数字 ID 猜一个链接。

### 输出目录

```text
tgemby-library.tar.gz             整包
tgemby-library.tar.gz.part000      第一卷（1800 MiB）
tgemby-library.tar.gz.part001      第二卷
tgemby-library.tar.gz.part002      ……最后一卷可能较小
checksums.sha256                  整包和各卷校验值
manifest.json                     包内文件指纹/数量
channels.json                     规则和加入链接
CHANNELS.md                       可直接点开的频道表
```

敏感信息检查和库完整性检查通过后才生成最终压缩包。STRM 的原地址/Token 被移除，路径改成占位；NFO 图片使用相对引用。个人私聊来源会阻止默认分享，失败扫描诊断记录不分享。

## 2. 上传 Telegram

使用你自己的已登录用户会话发送，免费账号保守按单文件 2GB 处理，每卷 1800 MiB；不用 Bot API 上传大文件。

先校验、不连接 Telegram：

```bash
python scripts/upload_tg.py --input /你的私有目录/tgemby-library --to me --dry-run
```

发到自己的**收藏 / Saved Messages**：

```bash
python scripts/upload_tg.py \
  --config /你自己的TGEmby配置目录/config.yaml \
  --input /你的私有目录/tgemby-library \
  --to me
```

明确要发给朋友时才把 `me` 换成对方用户名或聊天 ID。目录里存在分卷时不会重复发送同名整包；会检查缺卷和已知 SHA256。进度保存在同目录 `.upload-state.json`，重跑跳过已经确认成功的完整文件；中断的单个文件需重新上传。

上传工具只在内存中复用会话，不修改作者的 Session/config；进度文件不保存登录凭据，也不选入分享文件。

**Telegram 普通聊天/收藏不是端到端加密。** 此包主要含成人媒体清单/封面，只私下分享给成年、可信、合法获授权的对象。不要拿生产备份替代它。

## 3. 接收者还原与导入

全部分卷放到同一目录，按数字顺序合并后校验：

```bash
cat tgemby-library.tar.gz.part* > tgemby-library.tar.gz
sha256sum -c checksums.sha256
```

若收到的是整包而不是分卷，跳过合并，并核对整包 SHA256。`checksums.sha256` 含分卷条目而本机没有分卷时，只核对整包那一行，不要把“缺少分卷文件”误当作整包损坏。

先按 [DEPLOY.md](DEPLOY.md) 安装，**跳过登录**，再从项目目录导入：

```bash
bash scripts/install.sh --import /你保存数据包的目录/tgemby-library.tar.gz
```

脚本离线导入到空媒体库，备份初始化空 DB，替换为你的地址/Token/路径后恢复容器；已有非空媒体库不会被覆盖。之后加入频道、用自己的账号登录，在 Emby 扫描媒体库即可。

导入后路径：

- `runtime/config/media.db`：媒体数据库。
- `runtime/config/CHANNELS.md` / `channels.json`：频道资料。
- `runtime/strm/`：STRM、NFO 和本地图片，Emby 映射到 `/media/tg`。
- `runtime/jav_posters/`：通知封面。
- `runtime/config/emby_metadata/`：Emby 便携媒体字段清单（如有）。

## 4. 干净源码包与镜像

源码可以公开发布，成人媒体包保持私下分享；生产数据、虚拟环境和 Git 历史不进源码包：

```bash
python scripts/check_release.py bundle --output /你的发布目录 \
  --private-config /你自己的TGEmby配置目录/config.yaml
```

输出 `tgemby-source.tar.gz` 与 `source-checksums.sha256`。历史 UI 示例里的实际 API Key 只在发布副本中清除，不修改作者的生产配置。

构建 amd64/arm64 OCI 归档：

```bash
bash scripts/publish.sh --local
```

分享包另提供按架构的 `tgemby-share-20261109-amd64.docker.tar.gz` / `tgemby-share-20261109-arm64.docker.tar.gz`，接收者可直接 `docker load -i 文件名`，再按部署教程运行 `install.sh --offline`。它们只含 TGEmby，不包含视频数据或其他服务镜像。

本次组合发布包名为 `tgemby-deploy-share-20261109.tar.gz`，含上述源码/镜像、官方 MetaTube DLL/插件原包及对应源码/许可证、详细教程和 `一键部署.sh`。它与 `tgemby-library.tar.gz.part000～002` 是两类文件，**不要把部署组合包传给 `--import`**。MetaTube/Emby/mihomo 服务镜像仍需拉取或预先加载。

ARM 主机交叉构建 amd64 需要 QEMU/binfmt；这会注册主机解释器，已有 Docker 主机应由管理员确认后安装。普通同架构 `install.sh --build` 不需要它。

发布到 Docker Hub：**先撤销曾在聊天中泄露的旧 Token**，在本机自行交互登录，不把新 Token 发到聊天里：

```bash
docker login -u dirige
bash scripts/publish.sh --push
```

目标为 `dirige/tgemby:latest` 和版本标签。没有有效登录时只能完成本地构建/归档，不能把“本地已有镜像”当成“Docker Hub 上传成功”。
