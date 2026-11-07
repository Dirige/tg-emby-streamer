# TG Emby Streamer 生产部署

目标是让宿主机目录尽量简单，镜像内置程序代码，宿主机只保留运行配置和数据。

## 目录结构

```text
tgemby/
  docker-compose.yml
  config/
    config.yaml
    media.db
  strm/
  cache/
```

说明：

- `config/config.yaml`：全部配置都在这里，包括 Telegram、代理、TMDB、Emby、Web 登录、通知。
- `config/media.db`：媒体库数据库，必须持久化；迁移机器时带上它。
- `strm/`：STRM、NFO、海报、剧照输出目录，给 Emby/Jellyfin 扫描。
- `cache/`：视频分片缓存，可以不迁移，删除后会自动重建。

## 启动

```bash
docker compose up -d
```

访问：

```text
http://服务器IP:8001
```

## 更新镜像

如果使用远程镜像：

```bash
docker compose pull
docker compose up -d
```

如果本机源码构建：

```bash
docker build -t tg-emby-streamer:local .
docker compose up -d
```

## 换机器迁移

只需要复制：

```text
docker-compose.yml
config/
strm/
```

`cache/` 可选。换机器后记得修改 `config/config.yaml`：

```yaml
server:
  local_url: http://新机器IP:8001
```

如果旧 STRM 文件里写的是旧 IP，需要重新入库或重新生成 STRM，或者尽量使用固定域名。
