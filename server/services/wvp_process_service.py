"""Start and health-check a local WVP process for GB28181 integration."""
from __future__ import annotations

import atexit
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests

from server.services.zlm_process_service import bootstrap_zlm_for_app

_wvp_process: Optional[subprocess.Popen] = None


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_server_port(config_path: Path, default: str = "18080") -> str:
    try:
        in_server = False
        for raw_line in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line.startswith((" ", "\t")):
                in_server = stripped == "server:"
                continue
            if in_server and stripped.startswith("port:"):
                return stripped.split(":", 1)[1].strip() or default
    except OSError:
        pass
    return default


def _default_jar_path(project_root: Path) -> Path:
    target_dir = project_root / "wvp-GB28181-pro" / "target"
    candidates = sorted(target_dir.glob("wvp-pro-*.jar"))
    if candidates:
        return candidates[-1]

    release_dir = project_root / "release-wvp"
    candidates = sorted(release_dir.glob("wvp-pro-*.jar"))
    if candidates:
        return candidates[-1]

    return target_dir / "wvp-pro-2.7.4.jar"


def _default_config_path(project_root: Path) -> Path:
    return project_root / "deploy" / "wvp" / "application-safehat.yml"


def _default_java_bin(project_root: Path) -> str:
    bundled_java = project_root / "jdk-21.0.11" / "bin" / "java"
    if bundled_java.exists():
        return str(bundled_java)
    return "java"


def _wvp_is_healthy(base_url: str, timeout: float = 1.5) -> bool:
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/api/v1/device/list",
            timeout=timeout,
        )
        return response.status_code < 500
    except Exception:
        return False


def bootstrap_wvp_for_gb28181(project_root: Path) -> str:
    """Optionally start WVP and return the configured WVP base URL.

    Set SAFEHAT_WVP_AUTO_START=0 to disable process management.
    """
    bootstrap_zlm_for_app(project_root)

    wvp_jar = Path(os.getenv("SAFEHAT_WVP_JAR") or _default_jar_path(project_root))
    wvp_config = Path(os.getenv("SAFEHAT_WVP_CONFIG") or _default_config_path(project_root))
    java_bin = os.getenv("SAFEHAT_WVP_JAVA_BIN") or _default_java_bin(project_root)
    port = os.getenv("SAFEHAT_WVP_PORT") or _read_server_port(wvp_config)
    base_url = os.getenv("SAFEHAT_WVP_BASE_URL", f"http://127.0.0.1:{port}").rstrip("/")
    wait_seconds = float(os.getenv("SAFEHAT_WVP_STARTUP_TIMEOUT", "25") or 25)

    os.environ.setdefault("SAFEHAT_WVP_BASE_URL", base_url)
    os.environ.setdefault("SAFEHAT_WVP_PORT", str(port))

    if _wvp_is_healthy(base_url):
        return base_url

    if not _truthy(os.getenv("SAFEHAT_WVP_AUTO_START", "1")):
        print("[WVP] Auto start disabled; WVP is not running yet.")
        return base_url

    if not wvp_jar.exists():
        print(f"[WVP] jar not found: {wvp_jar}")
        return base_url
    if not wvp_config.exists():
        print(f"[WVP] config not found: {wvp_config}")
        return base_url

    global _wvp_process
    if _wvp_process is not None and _wvp_process.poll() is None:
        deadline = time.time() + min(wait_seconds, 8)
        while time.time() < deadline:
            if _wvp_is_healthy(base_url):
                return base_url
            time.sleep(0.5)
        return base_url

    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "wvp.log"
    log_file = log_path.open("a", encoding="utf-8", errors="ignore")

    command = [
        java_bin,
        "-Xms256m",
        "-Xmx768m",
        "-Dfile.encoding=UTF-8",
        "-jar",
        str(wvp_jar),
        f"--spring.config.additional-location=file:{wvp_config}",
    ]
    print(f"[WVP] Starting WVP: {wvp_jar}")
    creationflags = 0
    if platform.system().lower().startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        _wvp_process = subprocess.Popen(
            command,
            cwd=str(project_root),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    except OSError as exc:
        print(f"[WVP] start failed: {exc}; see {log_path}")
        return base_url

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _wvp_process.poll() is not None:
            print(f"[WVP] WVP exited early, code={_wvp_process.returncode}; see {log_path}")
            return base_url
        if _wvp_is_healthy(base_url):
            print("[WVP] WVP started and API is reachable.")
            return base_url
        time.sleep(1)

    print(f"[WVP] WVP started, but API health check timed out; see {log_path}")
    return base_url


def stop_managed_wvp() -> None:
    """Stop only the WVP process started by this Python process."""
    global _wvp_process
    if _wvp_process is None or _wvp_process.poll() is not None:
        return

    print("[WVP] Stopping managed WVP...")
    _wvp_process.terminate()
    try:
        _wvp_process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        _wvp_process.kill()
        _wvp_process.wait(timeout=5)
    finally:
        _wvp_process = None


atexit.register(stop_managed_wvp)


__all__ = ["bootstrap_wvp_for_gb28181", "stop_managed_wvp"]
