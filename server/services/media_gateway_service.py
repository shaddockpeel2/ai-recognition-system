"""
Optional media gateway integration for ZLMediaKit.

This layer keeps protocol/proxy concerns out of the detector. When disabled,
callers keep using the original URL directly.
"""
from __future__ import annotations

import os
import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from server.services.zlm_process_service import bootstrap_zlm_for_app


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _falsey(value: Any) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "off"}


def _detect_lan_ip(default: str = "127.0.0.1") -> str:
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return default


@dataclass
class GatewayResult:
    enabled: bool
    origin_url: str
    play_url: str
    preview_url: str
    gateway: str = ""
    gateway_app: str = ""
    gateway_stream: str = ""
    gateway_key: str = ""
    error: str = ""


class ZLMediaKitGateway:
    """Small wrapper around ZLMediaKit's HTTP API."""

    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        self._refresh_settings()

    def _read_config_value(self, section: str, key: str, default: str) -> str:
        parser = configparser.ConfigParser(strict=False)
        config_path = Path(os.getenv("SAFEHAT_ZLM_CONFIG") or self.project_root / "Release" / "config.ini")
        try:
            parser.read(config_path, encoding="utf-8")
            return parser.get(section, key, fallback=default)
        except Exception:
            return default

    def _refresh_settings(self) -> None:
        config_http_port = self._read_config_value("http", "port", "8080")
        config_rtsp_port = self._read_config_value("rtsp", "port", "8554")
        config_secret = self._read_config_value("api", "secret", "035c73f7-bb6b-4889-a715-d9eb2d1925cc")

        self.api_base = os.getenv("SAFEHAT_ZLM_API_BASE", f"http://127.0.0.1:{config_http_port}").rstrip("/")
        self.secret = os.getenv("SAFEHAT_ZLM_SECRET", config_secret)
        self.public_host = os.getenv("SAFEHAT_ZLM_PUBLIC_HOST", "127.0.0.1")
        self.internal_host = self._resolve_internal_host()
        self.rtsp_port = int(os.getenv("SAFEHAT_ZLM_RTSP_PORT", config_rtsp_port))
        self.http_port = int(os.getenv("SAFEHAT_ZLM_HTTP_PORT", config_http_port))
        self.vhost = os.getenv("SAFEHAT_ZLM_VHOST", "__defaultVhost__")
        self.default_app = os.getenv("SAFEHAT_ZLM_APP", "live")
        self.timeout = float(os.getenv("SAFEHAT_ZLM_TIMEOUT", "5"))

    def _resolve_internal_host(self) -> str:
        explicit = str(os.getenv("SAFEHAT_ZLM_INTERNAL_HOST", "")).strip()
        if explicit:
            return explicit

        mode = str(os.getenv("SAFEHAT_ZLM_INTERNAL_HOST_MODE", "public")).strip().lower()
        if mode in {"loopback", "localhost", "local"}:
            return "127.0.0.1"
        if mode in {"lan", "auto_lan"}:
            return _detect_lan_ip()
        if mode in {"api", "api_host"}:
            try:
                return urlparse(self.api_base).hostname or self.public_host
            except Exception:
                return self.public_host
        return self.public_host

    def should_proxy(self, source_type: str, config: Dict[str, Any], origin_url: str) -> bool:
        if _truthy(os.getenv("SAFEHAT_ZLM_DISABLED")):
            return False
        if _falsey(os.getenv("SAFEHAT_ZLM_APP_PROXY_ENABLED", "1")):
            return False
        if source_type == "file":
            return False
        scheme = urlparse(origin_url).scheme.lower()
        if scheme not in {"rtsp", "rtmp", "http", "https"}:
            return False

        # The new low-latency path treats ZLM as the default media bus for
        # network sources. Set SAFEHAT_ZLM_FORCE_GATEWAY=0 to restore the old
        # opt-in behavior; set SAFEHAT_ZLM_DISABLED=1 to bypass ZLM completely.
        if _truthy(os.getenv("SAFEHAT_ZLM_FORCE_GATEWAY", "0")):
            return True

        requested = config.get("use_media_gateway")
        if requested is None:
            return _truthy(os.getenv("SAFEHAT_ZLM_DEFAULT_GATEWAY", "0"))
        return _truthy(requested) and not _falsey(requested)

    def ensure_proxy(
        self,
        source_id: int,
        source_type: str,
        config: Dict[str, Any],
        origin_url: str,
    ) -> GatewayResult:
        if not self.should_proxy(source_type, config, origin_url):
            return GatewayResult(
                enabled=False,
                origin_url=origin_url,
                play_url=origin_url,
                preview_url=origin_url,
            )

        app = str(config.get("gateway_app") or self.default_app).strip() or self.default_app
        stream = str(config.get("gateway_stream") or f"source_{source_id or 'new'}").strip()

        try:
            bootstrap_zlm_for_app(self.project_root)
            self._refresh_settings()
            gateway_key = self._add_stream_proxy(app=app, stream=stream, origin_url=origin_url)
        except Exception as exc:  # pylint: disable=broad-except
            return GatewayResult(
                enabled=False,
                origin_url=origin_url,
                play_url=origin_url,
                preview_url=origin_url,
                error=str(exc),
            )

        return GatewayResult(
            enabled=True,
            origin_url=origin_url,
            play_url=f"rtsp://{self.internal_host}:{self.rtsp_port}/{app}/{stream}",
            preview_url=f"http://{self.public_host}:{self.http_port}/{app}/{stream}.live.flv",
            gateway="zlm",
            gateway_app=app,
            gateway_stream=stream,
            gateway_key=gateway_key,
        )

    def close_proxy(self, app: str, stream: str, key: str = "") -> None:
        app = str(app or "").strip()
        stream = str(stream or "").strip()
        key = str(key or "").strip()
        if not key and (not app or not stream):
            return

        try:
            self._refresh_settings()
            proxy_key = key or f"{self.vhost}/{app}/{stream}"
            response = requests.get(
                f"{self.api_base}/index/api/delStreamProxy",
                params={
                    "secret": self.secret,
                    "key": proxy_key,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[ZLM] delete stream proxy failed: {app}/{stream}, {exc}")

    def _add_stream_proxy(self, app: str, stream: str, origin_url: str) -> str:
        self.close_proxy(app, stream)
        response = requests.get(
            f"{self.api_base}/index/api/addStreamProxy",
            params={
                "secret": self.secret,
                "vhost": self.vhost,
                "app": app,
                "stream": stream,
                "url": origin_url,
                "enable_rtsp": 1,
                "enable_rtmp": 1,
                "enable_hls": 1,
                "enable_mp4": 0,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("code", -1)) != 0:
            raise RuntimeError(f"ZLMediaKit addStreamProxy failed: {payload}")
        data = payload.get("data") or {}
        return str(data.get("key") or f"{self.vhost}/{app}/{stream}")


media_gateway = ZLMediaKitGateway()


__all__ = ["media_gateway", "ZLMediaKitGateway", "GatewayResult"]
