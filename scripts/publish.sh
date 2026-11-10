#!/usr/bin/env bash
# 构建双架构 OCI 归档，或在用户自行 docker login 后发布到 Docker Hub。
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:---local}"
version="${2:-share-20261109}"
case "$mode" in
  --local|--push) ;;
  *) printf '%s\n' '用法：bash scripts/publish.sh --local [版本] 或 --push [版本]'; exit 1 ;;
esac
if ! docker buildx inspect tgemby-release >/dev/null 2>&1; then
  docker buildx create --name tgemby-release --driver docker-container >/dev/null
fi
docker buildx inspect tgemby-release --bootstrap
if [[ "$mode" == "--push" ]]; then
  if [[ ! -f "${DOCKER_CONFIG:-$HOME/.docker}/config.json" ]]; then
    printf '%s\n' '未找到 Docker 登录凭据。请先撤销泄露的 Token，然后在本机自行 docker login -u dirige。' >&2
    exit 1
  fi
  docker buildx build --builder tgemby-release --platform linux/amd64,linux/arm64 \
    --tag "dirige/tgemby:$version" --tag dirige/tgemby:latest --push .
else
  mkdir -p dist
  docker buildx build --builder tgemby-release --platform linux/amd64,linux/arm64 \
    --tag "dirige/tgemby:$version" --tag dirige/tgemby:latest \
    --output "type=oci,dest=dist/tgemby-$version.oci.tar" .
  printf '%s\n' "双架构 OCI 镜像已保存：dist/tgemby-$version.oci.tar"
fi
