"""Start and health-check a local ZLMediaKit MediaServer process.

This service is intentionally small: SafeHat still talks to ZLM through HTTP
APIs, while this module only helps local deployments bring MediaServer up
before video sources are restored.
"""
from __future__ import annotations

import atexit
import configparser
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests


_zlm_process: Optional[subprocess.Popen] = None
_last_start_failure_at: float = 0.0
_last_cooldown_notice_at: float = 0.0


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _default_media_server_path(base_dir: Path) -> Path:
    if platform.system().lower().startswith("win"):
        return base_dir / "Release" / "MediaServer.exe"
    return base_dir / "Release" / "MediaServer"


def _default_config_path(base_dir: Path) -> Path:
    return base_dir / "Release" / "config.ini"


def _read_zlm_secret(config_path: Path) -> str:
    try:
        for line in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            text = line.strip()
            if text.startswith("secret="):
                return text.split("=", 1)[1].strip()
    except OSError:
        pass
    return "035c73f7-bb6b-4889-a715-d9eb2d1925cc"


def _read_zlm_config_value(config_path: Path, section: str, key: str, default: str) -> str:
    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read(config_path, encoding="utf-8")
        return parser.get(section, key, fallback=default)
    except Exception:
        return default


def _zlm_is_healthy(api_base: str, secret: str, timeout: float = 1.5) -> bool:
    try:
        response = requests.get(
            f"{api_base.rstrip('/')}/index/api/getServerConfig",
            params={"secret": secret},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return int(payload.get("code", -1)) == 0
    except Exception:
        return False


def _prepare_zlm_runtime_dirs(zlm_bin: Path) -> Path:
    """Create writable runtime directories expected by MediaServer."""
    release_dir = zlm_bin.parent
    for relative_path in ("log", "ffmpeg", "www", "www/snap"):
        (release_dir / relative_path).mkdir(parents=True, exist_ok=True)
    return release_dir


def bootstrap_zlm_for_app(base_dir: Path) -> None:
    """Optionally start ZLM and populate SAFEHAT_ZLM_* env defaults.

    Set SAFEHAT_ZLM_AUTO_START=0 to disable process management.
    """
    auto_start = _truthy(os.getenv("SAFEHAT_ZLM_AUTO_START", "1"))
    zlm_bin = Path(os.getenv("SAFEHAT_ZLM_BIN") or _default_media_server_path(base_dir))
    zlm_config = Path(os.getenv("SAFEHAT_ZLM_CONFIG") or _default_config_path(base_dir))
    http_port = _read_zlm_config_value(zlm_config, "http", "port", "8080")
    rtsp_port = _read_zlm_config_value(zlm_config, "rtsp", "port", "8554")
    rtmp_port = _read_zlm_config_value(zlm_config, "rtmp", "port", "1935")
    api_base = os.getenv("SAFEHAT_ZLM_API_BASE", f"http://127.0.0.1:{http_port}")
    secret = os.getenv("SAFEHAT_ZLM_SECRET") or _read_zlm_secret(zlm_config)

    os.environ.setdefault("SAFEHAT_ZLM_ENABLED", "1")
    os.environ.setdefault("SAFEHAT_ZLM_API_BASE", api_base)
    os.environ.setdefault("SAFEHAT_ZLM_SECRET", secret)
    os.environ.setdefault("SAFEHAT_ZLM_PUBLIC_HOST", "127.0.0.1")
    os.environ.setdefault("SAFEHAT_ZLM_RTSP_PORT", str(rtsp_port))
    os.environ.setdefault("SAFEHAT_ZLM_HTTP_PORT", str(http_port))
    os.environ.setdefault("SAFEHAT_ZLM_RTMP_PORT", str(rtmp_port))

    if _zlm_is_healthy(api_base, secret):
        print("[ZLM] MediaServer already running.")
        return

    if not auto_start:
        print("[ZLM] Auto start disabled; MediaServer is not running yet.")
        return

    global _last_start_failure_at, _last_cooldown_notice_at
    cooldown_seconds = float(os.getenv("SAFEHAT_ZLM_RESTART_COOLDOWN", "30") or 30)
    if _last_start_failure_at and time.time() - _last_start_failure_at < cooldown_seconds:
        if time.time() - _last_cooldown_notice_at >= 5:
            remaining = max(1, int(cooldown_seconds - (time.time() - _last_start_failure_at)))
            print(f"[ZLM] Previous MediaServer start failed; retry in ~{remaining}s. Check logs/zlm.log.")
            _last_cooldown_notice_at = time.time()
        return

    if not zlm_bin.exists():
        print(f"[ZLM] MediaServer not found: {zlm_bin}")
        return
    if not zlm_config.exists():
        print(f"[ZLM] config.ini not found: {zlm_config}")
        return

    try:
        _prepare_zlm_runtime_dirs(zlm_bin)
    except OSError as exc:
        print(f"[ZLM] failed to prepare runtime dirs under {zlm_bin.parent}: {exc}")
        return

    global _zlm_process
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "zlm.log"
    log_file = log_path.open("a", encoding="utf-8", errors="ignore")

    command = [str(zlm_bin), "-c", str(zlm_config)]
    use_sudo = (
        not platform.system().lower().startswith("win")
        and _truthy(os.getenv("SAFEHAT_ZLM_SUDO_START", "0"))
    )
    if use_sudo:
        command = ["sudo", "-n"] + command

    print(f"[ZLM] Starting MediaServer: {' '.join(command)}")
    _zlm_process = subprocess.Popen(
        command,
        cwd=str(zlm_bin.parent),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    wait_seconds = float(os.getenv("SAFEHAT_ZLM_STARTUP_TIMEOUT", "8"))
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _zlm_process.poll() is not None:
            print(f"[ZLM] MediaServer exited early, code={_zlm_process.returncode}")
            if use_sudo and _zlm_process.returncode != 0:
                print("[ZLM] sudo start failed or MediaServer crashed. If sudo needs a password, add NOPASSWD or start ZLM manually.")
            _last_start_failure_at = time.time()
            _last_cooldown_notice_at = 0.0
            return
        if _zlm_is_healthy(api_base, secret):
            print("[ZLM] MediaServer started and API is healthy.")
            return
        time.sleep(0.5)

    print("[ZLM] MediaServer started, but API health check timed out.")
    _last_start_failure_at = time.time()
    _last_cooldown_notice_at = 0.0


def stop_managed_zlm() -> None:
    """Stop only the MediaServer process started by this Python process."""
    global _zlm_process
    if _zlm_process is None or _zlm_process.poll() is not None:
        return

    print("[ZLM] Stopping managed MediaServer...")
    _zlm_process.terminate()
    try:
        _zlm_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _zlm_process.kill()
        _zlm_process.wait(timeout=5)
    finally:
        _zlm_process = None


atexit.register(stop_managed_zlm)


__all__ = ["bootstrap_zlm_for_app", "stop_managed_zlm"]
