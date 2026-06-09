# 部署教程

## Docker 部署

1. git clone https://github.com/Dirige/tg-emby-streamer.git
2. cd tg-emby-streamer
3. cp .env.example .env && nano .env
4. docker-compose up -d

## 本地部署

1. pip install -r requirements.txt
2. cp .env.example .env && nano .env
3. python generate_session.py
4. python run.py

## 配置

见 .env.example

## Emby 配置

1. 添加媒体库指向 strm/ 目录
2. 启用直接播放，禁用转码
3. 扫描媒体库

## 诊断

GET http://IP:8001/api/diagnose
