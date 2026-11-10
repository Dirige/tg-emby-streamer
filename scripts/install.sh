#!/usr/bin/env bash
# TGEmby 安装与维护；仅管理本目录的 Compose 项目，不操作其他服务。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

say() { printf '%s\n' "$*"; }
fail() { say "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
首次安装： bash scripts/install.sh [--build | --offline] [--url http://服务器IP:8001] [--skip-login]
无人值守： bash scripts/install.sh --build --url http://服务器IP:8001 --skip-login
导入空库： bash scripts/install.sh --import /路径/tgemby-library.tar.gz [SHA256]
改地址：   bash scripts/install.sh --set-url http://服务器IP:8001
播放换锁： bash scripts/install.sh --rotate-token
刮削代理： bash scripts/install.sh --proxy http://代理主机:7890
TG 代理：  bash scripts/install.sh --telegram-proxy socks5://代理主机:7890
TG 登录：  bash scripts/install.sh --login
安装插件： bash scripts/install.sh --metatube-plugin
查看状态： bash scripts/install.sh --status
含密码的代理 URL 不要写入命令行；省略 URL 后按隐藏输入提示填写。
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then usage; exit 0; fi
command -v docker >/dev/null || fail "请先安装 Docker，详见 DEPLOY.md。"
command -v python3 >/dev/null || fail "请先安装 Python 3（安装脚本只使用其标准库）。"
docker compose version >/dev/null || fail "请安装 Docker Compose V2 插件。"
docker info >/dev/null 2>&1 || fail "无法访问 Docker；请启动 Docker 或用有权限的用户运行。"
if [[ ! -f .env ]]; then cp .env.example .env; chmod 600 .env; fi
env_get() { python3 scripts/envfile.py get "$1" --default "${2:-}"; }
env_set() { printf '%s\n' "$2" | python3 scripts/envfile.py set "$1"; }
PUID="${PUID:-$(env_get PUID 1000)}"
PGID="${PGID:-$(env_get PGID 1000)}"
[[ "$PUID" =~ ^[0-9]+$ && "$PGID" =~ ^[0-9]+$ ]] || fail "PUID/PGID 必须是非负整数。"
PORT="${TGEMBY_PORT:-$(env_get TGEMBY_PORT 8001)}"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail "TGEMBY_PORT 必须是端口数字。"

prepare_dirs() {
  local directory
  for directory in config strm cache jav_posters metatube share emby proxy; do
    mkdir -p "runtime/$directory"
    # 不递归改既有库的所有权；只保证挂载目录本身可写。
    if [[ "$(id -u)" == "0" ]]; then
      chown "$PUID:$PGID" "runtime/$directory"
    elif [[ "$(stat -c '%u:%g' "runtime/$directory")" != "$PUID:$PGID" ]]; then
      sudo chown "$PUID:$PGID" "runtime/$directory" || fail "目录权限调整失败，请检查 PUID/PGID。"
    fi
  done
}

run_python() {
  docker compose run --rm --no-deps --pull never --entrypoint python tgemby "$@"
}

wait_ready() {
  local attempt
  for attempt in {1..60}; do
    if docker compose exec -T tgemby python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=3)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  fail "TGEmby 未就绪。请执行 docker compose ps 和 docker compose logs --tail 50 tgemby。"
}

install_plugin() {
  local running
  running="$(docker compose ps --status running --quiet emby)"
  if [[ -n "$running" ]]; then docker compose stop emby; fi
  local result=0
  docker compose run --rm --no-deps --user 0:0 --entrypoint python \
    --volume "$ROOT/runtime/emby:/target" tgemby scripts/install_metatube.py \
    --directory /target/plugins --uid "$PUID" --gid "$PGID" || result=$?
  if [[ -n "$running" ]]; then docker compose start emby; fi
  return "$result"
}

case "${1:-}" in
  --status)
    docker compose ps
    docker compose exec -T tgemby python -c \
      "import urllib.request; print('TGEmby：',urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=5).status); print('MetaTube：',urllib.request.urlopen('http://metatube:8080/v1/providers',timeout=5).status)"
    exit 0 ;;
  --login)
    docker compose exec tgemby python scripts/generate_session.py
    docker compose restart tgemby
    wait_ready
    exit 0 ;;
  --set-url)
    [[ $# == 2 ]] || fail "用法：--set-url http://服务器IP:端口 或 https://域名"
    docker compose exec -T tgemby python scripts/deploy.py seturl --url "$2" --local-url "$2"
    env_set TGEMBY_PUBLIC_URL "$2"
    docker compose restart tgemby
    wait_ready
    exit 0 ;;
  --rotate-token)
    docker compose exec -T tgemby python scripts/deploy.py rotate-token
    docker compose restart tgemby
    wait_ready
    exit 0 ;;
  --proxy)
    proxy="${2:-}"
    if [[ -z "$proxy" ]]; then read -r -s -p "HTTP 刮削代理 URL（不回显）：" proxy; say ""; fi
    env_set SCRAPER_PROXY "$proxy"
    docker compose up -d tgemby metatube
    wait_ready
    say "MetaTube 和 TGEmby 的 HTTP/HTTPS 刮削代理已生效；Telegram 代理未改动。"
    exit 0 ;;
  --telegram-proxy)
    proxy="${2:-}"
    if [[ -z "$proxy" ]]; then read -r -s -p "Telegram 代理 URL（不回显）：" proxy; say ""; fi
    printf '%s\n' "$proxy" | docker compose exec -T tgemby python scripts/deploy.py proxy --proxy-url-stdin
    docker compose restart tgemby
    wait_ready
    exit 0 ;;
  --metatube-plugin)
    prepare_dirs
    install_plugin
    exit 0 ;;
  --import)
    [[ $# -ge 2 && $# -le 3 && -f "$2" ]] || fail "用法：--import /路径/tgemby-library.tar.gz [SHA256]"
    archive="$(realpath "$2")"
    prepare_dirs
    restart_services=()
    for service in tgemby emby; do
      if [[ -n "$(docker compose ps --status running --quiet "$service")" ]]; then
        restart_services+=("$service")
      fi
    done
    restore_services() {
      if [[ ${#restart_services[@]} -gt 0 ]]; then docker compose start "${restart_services[@]}"; fi
    }
    trap restore_services EXIT
    if [[ ${#restart_services[@]} -gt 0 ]]; then docker compose stop "${restart_services[@]}"; fi
    extra=()
    if [[ $# == 3 ]]; then extra+=(--sha256 "$3"); fi
    say "只导入空媒体库；既有空数据库会先备份。请等待文件校验和路径替换。"
    # 单文件只读挂载，既不复制 4GB 大包，也不要求作者的私有父目录对容器开放。
    docker compose run --rm --no-deps --entrypoint python \
      --volume "$archive:/app/import-bundle.tar.gz:ro" tgemby scripts/library_bundle.py import \
      --input /app/import-bundle.tar.gz --replace-empty "${extra[@]}"
    restore_services
    trap - EXIT
    if [[ " ${restart_services[*]} " == *" tgemby "* ]]; then wait_ready; fi
    say "导入完成。请用自己的账号加入 CHANNELS.md 中的来源频道，并在 Emby 扫描媒体库。"
    exit 0 ;;
esac

build=0
offline=0
skip_login=0
url="${TGEMBY_PUBLIC_URL:-$(env_get TGEMBY_PUBLIC_URL)}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) build=1; shift ;;
    --offline) offline=1; shift ;;
    --skip-login) skip_login=1; shift ;;
    --url) [[ $# -ge 2 ]] || fail "--url 后缺少地址。"; url="$2"; shift 2 ;;
    *) usage; fail "未知参数：$1" ;;
  esac
done
[[ "$build" == 0 || "$offline" == 0 ]] || fail "--build 和 --offline 不能同时使用。"
case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) fail "支持 amd64/arm64 的 Linux 主机。" ;; esac
if [[ -z "$url" ]]; then
  [[ -t 0 ]] || fail "无人值守安装必须加 --url http://服务器IP:$PORT 和 --skip-login。"
  read -r -p "填写 Emby 和播放设备均可访问的地址（如 http://192.168.1.20:$PORT）：" url
  [[ -n "$url" ]] || fail "不能把 localhost 当作其他设备的播放地址，请填写服务器地址。"
fi
env_set TGEMBY_PUBLIC_URL "$url"
prepare_dirs
docker compose config --quiet
if [[ "$build" == 1 ]]; then
  docker compose build tgemby
elif [[ "$offline" == 1 ]]; then
  image="${TGEMBY_IMAGE:-$(env_get TGEMBY_IMAGE dirige/tgemby:latest)}"
  docker image inspect "$image" >/dev/null 2>&1 || fail "本地没有 TGEmby 镜像，请先 docker load -i 离线镜像文件。"
elif ! docker compose pull tgemby; then
  say "发布镜像暂不可用，自动从随包源码本地构建（首次需要数分钟）。"
  docker compose build tgemby
fi
run_python scripts/deploy.py init --url "$url"
run_python scripts/deploy.py seturl --url "$url" --local-url "$url"
if [[ "$offline" == 1 ]]; then
  docker compose up -d --pull never
else
  docker compose up -d
fi
wait_ready
profiles="${COMPOSE_PROFILES:-$(env_get COMPOSE_PROFILES)}"
if [[ ",$profiles," == *",emby,"* ]]; then
  if [[ "$offline" == 1 ]]; then
    say "离线模式未下载插件；可预先放置官方 MetaTube.dll，或联网后运行 --metatube-plugin。"
  elif ! install_plugin; then
    say "自动下载 MetaTube 插件失败；已有 NFO 可照常使用。联网后运行 --metatube-plugin 重试。"
  fi
fi
if [[ "$skip_login" == 0 && -t 0 ]]; then
  read -r -p "现在登录你自己的 Telegram 账号吗？[Y/n] " answer
  if [[ ! "${answer:-Y}" =~ ^[Nn] ]]; then
    docker compose exec tgemby python scripts/generate_session.py
    docker compose restart tgemby
    wait_ready
  fi
else
  say "已跳过 Telegram 登录；稍后运行 bash scripts/install.sh --login。"
fi
say "部署完成。面板：$url；账号密码只保存在 runtime/config/access.txt。"
say "数据路径：runtime/config、runtime/strm、runtime/jav_posters；Emby 读 /media/tg。"
say "完整教程：DEPLOY.md；频道链接：CHANNELS.md；数据分享：DATA_SHARE.md。"
