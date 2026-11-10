FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# 清除历史 UI 示例值后再复制到最终镜像。不能在最终镜像 COPY 后删除，
# 否则原值仍会留在底层镜像层中。生产代码/配置不因此被改动。
COPY app/ ./app/
COPY scripts/ ./scripts/
RUN python scripts/check_release.py scrub --root /build \
    && python scripts/check_release.py scan --root /build

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="TG Emby Streamer" \
      org.opencontainers.image.source="https://github.com/Dirige/tg-emby-streamer"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH=/app/config/config.yaml \
    TGEMBY_ACCESS_LOG=false \
    METATUBE_URL=http://metatube:8080

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --no-create-home app \
    && mkdir -p /app/config /app/strm /app/cache /app/jav_posters \
    && chown app:app /app/config /app/strm /app/cache /app/jav_posters

COPY --from=builder /build/app/ ./app/
COPY --from=builder /build/scripts/ ./scripts/
COPY docker/entrypoint.sh ./docker/entrypoint.sh
COPY run.py config.example.yaml channels.json ./
RUN chmod 755 /app/docker/entrypoint.sh

USER app
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=20s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4)" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "run.py"]
