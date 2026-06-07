import os
import sys
import json
import logging
import platform
import subprocess
import signal
import atexit
import threading
import time
import asyncio
import socket
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SINGBOX_DIR = Path(__file__).resolve().parent.parent.parent / "sing-box"
SINGBOX_CONFIG = SINGBOX_DIR / "config.json"
SINGBOX_BIN = SINGBOX_DIR / ("sing-box.exe" if sys.platform == "win32" else "sing-box")

_process: Optional[subprocess.Popen] = None
_monitor_thread: Optional[threading.Thread] = None
_stop_monitor = threading.Event()
_current_port: int = 10808
_restart_count: int = 0
_max_restarts: int = 10
_health_failures: int = 0
_max_health_failures: int = 3
_last_health_check: float = 0
_is_healthy: bool = False
_atexit_registered: bool = False
_process_lock = threading.Lock()


def _get_download_info() -> tuple[str, str]:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = "amd64"

    if sys.platform == "win32":
        os_name = "windows"
    elif sys.platform == "darwin":
        os_name = "darwin"
    else:
        os_name = "linux"

    version = "1.11.11"
    suffix = f"zip" if sys.platform == "win32" else "tar.gz"
    filename = f"sing-box-{version}-{os_name}-{arch}.{suffix}"
    url = f"https://github.com/SagerNet/sing-box/releases/download/v{version}/{filename}"
    return url, filename


def _download_file(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=120)
    return resp.read()


def ensure_binary() -> Path:
    if SINGBOX_BIN.exists():
        return SINGBOX_BIN

    SINGBOX_DIR.mkdir(parents=True, exist_ok=True)

    import zipfile
    import io

    url, filename = _get_download_info()
    logger.info(f"Downloading sing-box from {url}")

    data = None
    try:
        data = _download_file(url)
    except Exception as e:
        logger.warning(f"Download failed: {e}")
        mirrors = [
            f"https://ghfast.top/{url}",
            f"https://ghproxy.net/{url}",
            f"https://mirror.ghproxy.com/{url}",
        ]
        for mirror in mirrors:
            try:
                logger.info(f"Trying mirror: {mirror}")
                data = _download_file(mirror)
                break
            except Exception as e2:
                logger.warning(f"Mirror failed: {e2}")

    if data is None:
        raise RuntimeError("Failed to download sing-box from all sources")

    if sys.platform == "win32":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.endswith("sing-box.exe"):
                    with zf.open(name) as src, open(SINGBOX_BIN, "wb") as dst:
                        dst.write(src.read())
                    break
    else:
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for member in tf.getmembers():
                if member.name.endswith("/sing-box") and "/" in member.name:
                    with tf.extractfile(member) as src, open(SINGBOX_BIN, "wb") as dst:
                        dst.write(src.read())
                    os.chmod(SINGBOX_BIN, 0o755)
                    break

    if not SINGBOX_BIN.exists():
        raise RuntimeError("Failed to extract sing-box binary")

    logger.info(f"sing-box binary saved to {SINGBOX_BIN}")
    return SINGBOX_BIN


def generate_config(
    vless_address: str,
    vless_port: int,
    vless_uuid: str,
    vless_path: str = "/?ed=2048",
    vless_host: str = "",
    vless_tls: bool = True,
    vless_fp: str = "chrome",
    socks_port: int = 10808,
    protocol: str = "vless",
    cipher: str = "auto",
) -> str:
    server_name = vless_host if vless_host else vless_address

    tls_settings = {
        "enabled": vless_tls,
        "server_name": server_name if server_name else "",
        "insecure": True,
        "utls": {
            "enabled": True,
            "fingerprint": vless_fp,
        },
    }

    transport_settings = {
        "type": "ws",
        "path": vless_path,
        "headers": {
            "Host": vless_host if vless_host else vless_address,
        },
    }

    if protocol == "vless":
        outbound = {
            "type": "vless",
            "tag": "proxy",
            "server": vless_address,
            "server_port": vless_port,
            "uuid": vless_uuid,
            "flow": "",
            "network": "tcp",
            "tls": tls_settings,
            "transport": transport_settings,
        }
    elif protocol == "trojan":
        outbound = {
            "type": "trojan",
            "tag": "proxy",
            "server": vless_address,
            "server_port": vless_port,
            "password": vless_uuid,
            "network": "tcp",
            "tls": tls_settings,
            "transport": transport_settings,
        }
    elif protocol == "vmess":
        outbound = {
            "type": "vmess",
            "tag": "proxy",
            "server": vless_address,
            "server_port": vless_port,
            "uuid": vless_uuid,
            "alter_id": 0,
            "security": "auto",
            "network": "tcp",
            "tls": tls_settings,
            "transport": transport_settings,
        }
    elif protocol == "shadowsocks":
        outbound = {
            "type": "shadowsocks",
            "tag": "proxy",
            "server": vless_address,
            "server_port": vless_port,
            "method": cipher,
            "password": vless_uuid,
            "network": "tcp",
        }
    else:
        raise ValueError(f"Unsupported protocol: {protocol}")

    config = {
        "log": {
            "level": "info",
            "output": str(SINGBOX_DIR / "sing-box.log"),
            "timestamp": True,
        },
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": socks_port,
            }
        ],
        "outbounds": [
            outbound,
            {
                "type": "direct",
                "tag": "direct",
            },
        ],
        "route": {
            "rules": [
                {
                    "ip_is_private": True,
                    "outbound": "direct",
                },
                {
                    "domain": [
                        "api.telegram.org",
                        "web.telegram.org",
                        "desktop.telegram.org",
                        "updates.telegram.org",
                        "cdn-telegram.org",
                    ],
                    "outbound": "proxy",
                },
                {
                    "domain_suffix": [
                        ".telegram.org",
                        ".telegram.me",
                        ".t.me",
                        ".telegram-cdn.org",
                        ".telegra.ph",
                        ".telesco.pe",
                        ".tdesktop.com",
                    ],
                    "outbound": "proxy",
                },
                {
                    "ip_cidr": [
                        "91.108.0.0/16",
                        "149.154.0.0/16",
                        "185.76.151.0/24",
                    ],
                    "outbound": "proxy",
                },
            ],
            "final": "direct",
        },
        "dns": {
            "servers": [
                {
                    "tag": "dns-telegram",
                    "address": "https://1.1.1.1/dns-query",
                    "detour": "proxy",
                },
                {
                    "tag": "dns-direct",
                    "address": "https://223.5.5.5/dns-query",
                    "detour": "direct",
                },
            ],
            "rules": [
                {
                    "domain": [
                        "api.telegram.org",
                        "web.telegram.org",
                        "desktop.telegram.org",
                        "updates.telegram.org",
                        "cdn-telegram.org",
                    ],
                    "server": "dns-telegram",
                },
                {
                    "domain_suffix": [
                        ".telegram.org",
                        ".telegram.me",
                        ".t.me",
                        ".telegram-cdn.org",
                        ".telegra.ph",
                        ".telesco.pe",
                    ],
                    "server": "dns-telegram",
                },
            ],
            "final": "dns-direct",
        },
    }

    SINGBOX_DIR.mkdir(parents=True, exist_ok=True)
    config_str = json.dumps(config, indent=2, ensure_ascii=False)
    SINGBOX_CONFIG.write_text(config_str, encoding="utf-8")
    logger.info(f"sing-box config written to {SINGBOX_CONFIG}")
    return config_str


def _start_process(socks_port: int) -> subprocess.Popen:
    ensure_binary()

    cmd = [str(SINGBOX_BIN), "run", "-c", str(SINGBOX_CONFIG)]
    kwargs = {}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = si
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **kwargs,
    )

    time.sleep(2)

    if proc.poll() is not None:
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise RuntimeError(f"sing-box failed to start: {stderr}")

    return proc


def _check_socks5_health(port: int, timeout: float = 5.0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _check_telegram_health(port: int) -> bool:
    try:
        import socks as socks_mod
        import ssl

        s = socks_mod.socksocket()
        s.set_proxy(socks_mod.SOCKS5, "127.0.0.1", port)
        s.settimeout(10)
        s.connect(("api.telegram.org", 443))
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(s, server_hostname="api.telegram.org")
        ss.close()
        return True
    except ImportError:
        return _check_socks5_health(port)
    except Exception:
        return False


def _monitor_loop():
    global _process, _restart_count, _health_failures, _last_health_check, _is_healthy

    while not _stop_monitor.is_set():
        try:
            if _stop_monitor.wait(timeout=30):
                break

            if _process is None:
                continue

            if _process.poll() is not None:
                exit_code = _process.returncode
                stderr = ""
                try:
                    stderr = _process.stderr.read().decode(errors="replace") if _process.stderr else ""
                except Exception:
                    pass
                logger.warning(f"sing-box process exited (code={exit_code}): {stderr}")

                if _restart_count >= _max_restarts:
                    logger.error(f"sing-box crashed too many times ({_restart_count}), giving up")
                    _is_healthy = False
                    continue

                _restart_count += 1
                logger.info(f"Restarting sing-box (attempt {_restart_count}/{_max_restarts})...")

                try:
                    _process = _start_process(_current_port)
                    _health_failures = 0
                    _is_healthy = True
                    logger.info(f"sing-box restarted (PID={_process.pid})")
                except Exception as e:
                    logger.error(f"Failed to restart sing-box: {e}")
                    _is_healthy = False
                    time.sleep(10)
            else:
                _restart_count = 0

                if _check_socks5_health(_current_port):
                    _health_failures = 0
                    _is_healthy = True
                    _last_health_check = time.time()
                else:
                    _health_failures += 1
                    logger.warning(f"SOCKS5 health check failed ({_health_failures}/{_max_health_failures})")

                    if _health_failures >= _max_health_failures:
                        logger.error("Too many health check failures, restarting sing-box...")
                        _is_healthy = False
                        try:
                            _process.terminate()
                            _process.wait(timeout=5)
                        except Exception:
                            try:
                                _process.kill()
                            except Exception:
                                pass

                        try:
                            _process = _start_process(_current_port)
                            _health_failures = 0
                            _is_healthy = True
                            logger.info("sing-box restarted after health check failure")
                        except Exception as e:
                            logger.error(f"Failed to restart sing-box: {e}")

        except Exception as e:
            logger.error(f"Error in sing-box monitor: {e}")
            time.sleep(5)


def start(socks_port: int = 10808) -> int:
    global _process, _monitor_thread, _current_port, _restart_count, _health_failures, _is_healthy

    with _process_lock:
        if _process is not None and _process.poll() is None:
            logger.info("sing-box already running")
            return socks_port

        _current_port = socks_port
        _restart_count = 0
        _health_failures = 0

        _process = _start_process(socks_port)
        _is_healthy = True

    _stop_monitor.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True, name="singbox-monitor")
    _monitor_thread.start()

    global _atexit_registered
    if not _atexit_registered:
        atexit.register(stop)
        _atexit_registered = True

    def _signal_handler(sig, frame):
        stop()
        os._exit(0)

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    logger.info(f"sing-box started (PID={_process.pid}), SOCKS5 on 127.0.0.1:{socks_port}")
    return socks_port


def stop():
    global _process, _monitor_thread

    _stop_monitor.set()
    if _monitor_thread is not None:
        _monitor_thread.join(timeout=10)
        _monitor_thread = None

    with _process_lock:
        if _process is not None:
            try:
                _process.terminate()
                _process.wait(timeout=5)
            except Exception:
                try:
                    _process.kill()
                except Exception:
                    pass
            _process = None

    # Kill any remaining sing-box processes
    try:
        import subprocess
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/f", "/im", "sing-box.exe"], 
                         capture_output=True, timeout=5)
        else:
            subprocess.run(["pkill", "-f", "sing-box"], 
                         capture_output=True, timeout=5)
    except Exception:
        pass
    
    logger.info("sing-box stopped")


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def is_healthy() -> bool:
    return _is_healthy and is_running()


def get_status() -> dict:
    return {
        "running": is_running(),
        "healthy": is_healthy(),
        "pid": _process.pid if _process else None,
        "port": _current_port,
        "restart_count": _restart_count,
        "max_restarts": _max_restarts,
        "health_failures": _health_failures,
        "last_health_check": _last_health_check,
        "monitor_active": _monitor_thread is not None and _monitor_thread.is_alive(),
        "binary": str(SINGBOX_BIN),
        "config": str(SINGBOX_CONFIG),
    }
