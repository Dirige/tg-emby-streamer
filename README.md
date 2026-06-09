# TG Emby Streamer

Telegram 频道视频自动入库到 Emby/Jellyfin

## 功能

- 自动监听 Telegram 频道
- 中文分类：电影、电视剧、动漫、海外剧、cosplay
- Emby 规范 STRM 文件
- Token 认证
- HTTP Range 流媒体
- 多 Bot 负载均衡
- 诊断接口 /api/diagnose
- Web 管理面板

## 快速开始

Docker:
```bash
git clone https://github.com/Dirige/tg-emby-streamer.git
cd tg-emby-streamer
cp .env.example .env
nano .env
docker-compose up -d
```

## 配置

见 .env.example

## STRM 命名

```
strm/电视剧/剧名/Season 01/剧名 - S01E01.strm
strm/电影/片名/片名.strm
strm/动漫/名/Season 01/名 - S01E01.strm
```

## 诊断

GET http://IP:8001/api/diagnose

## 许可证

MIT License
