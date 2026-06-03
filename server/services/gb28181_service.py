from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

from server.services.wvp_process_service import bootstrap_wvp_for_gb28181


@dataclass
class Gb28181Result:
    success: bool
    message: str = ""
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"success": self.success, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


class Gb28181Service:
    """WVP integration for GB28181 device/channel discovery and live play."""

    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        self.default_base_url = os.environ.get("SAFEHAT_WVP_BASE_URL", "http://127.0.0.1:18080")
        self.default_timeout = float(os.environ.get("SAFEHAT_WVP_TIMEOUT", "6") or 6)

    def config(self) -> Dict[str, Any]:
        self.default_base_url = bootstrap_wvp_for_gb28181(self.project_root)
        return {
            "wvp_base_url": self.default_base_url,
            "sip_server_ip": os.environ.get("SAFEHAT_GB_SIP_SERVER_IP", "192.168.1.19"),
            "sip_server_port": os.environ.get("SAFEHAT_GB_SIP_SERVER_PORT", "15060"),
            "sip_server_id": os.environ.get("SAFEHAT_GB_SIP_SERVER_ID", "34020000002000000001"),
            "sip_domain": os.environ.get("SAFEHAT_GB_SIP_DOMAIN", "3402000000"),
            "sip_password": os.environ.get("SAFEHAT_GB_SIP_PASSWORD", ""),
            "transport": os.environ.get("SAFEHAT_GB_TRANSPORT", "UDP"),
        }

    def list_devices(self, config: Dict[str, Any]) -> Gb28181Result:
        result = self._request(config, "/api/v1/device/list")
        if not result.success:
            return result

        raw = result.data or {}
        devices = self._normalize_list(raw, ("DeviceList", "deviceList", "devices", "list", "items", "records"))
        return Gb28181Result(True, data={
            "devices": [self._normalize_device(item) for item in devices],
            "raw": raw,
        })

    def list_channels(self, config: Dict[str, Any]) -> Gb28181Result:
        device_id = str(config.get("device_id") or config.get("serial") or "").strip()
        if not device_id:
            return Gb28181Result(False, "缺少 GB28181 设备国标 ID")

        params = {"serial": device_id}
        if config.get("online") not in (None, ""):
            params["online"] = str(config.get("online")).lower()

        result = self._request(config, "/api/v1/device/channellist", params=params)
        if not result.success:
            return result

        raw = result.data or {}
        channels = self._normalize_list(raw, ("ChannelList", "channelList", "channels", "list", "items", "records"))
        return Gb28181Result(True, data={
            "device_id": device_id,
            "channels": [self._normalize_channel(item) for item in channels],
            "raw": raw,
        })

    def start_play(self, config: Dict[str, Any]) -> Gb28181Result:
        device_id = str(config.get("device_id") or config.get("serial") or "").strip()
        channel_code = str(config.get("channel_code") or config.get("code") or config.get("channel") or "").strip()
        if not device_id:
            return Gb28181Result(False, "缺少 GB28181 设备国标 ID")
        if not channel_code:
            return Gb28181Result(False, "缺少 GB28181 通道国标 ID")

        params = {
            "serial": device_id,
            "code": channel_code,
            "transport": str(config.get("transport") or "UDP").upper(),
            "timeout": str(config.get("timeout") or 15),
        }
        if config.get("audio") not in (None, ""):
            params["audio"] = str(config.get("audio")).lower()

        result = self._request(config, "/api/v1/stream/start", params=params)
        if not result.success:
            return result

        raw = result.data or {}
        if raw.get("error"):
            return Gb28181Result(False, str(raw.get("error")), {"raw": raw})

        play = self._normalize_play(raw, config)
        if not play.get("play_url"):
            return Gb28181Result(False, "WVP 已响应，但没有返回可播放地址", {"raw": raw})

        return Gb28181Result(True, data=play)

    def stop_play(self, config: Dict[str, Any]) -> Gb28181Result:
        device_id = str(config.get("device_id") or config.get("serial") or "").strip()
        channel_code = str(config.get("channel_code") or config.get("code") or config.get("channel") or "").strip()
        if not device_id or not channel_code:
            return Gb28181Result(False, "缺少 GB28181 设备或通道 ID")

        result = self._request(
            config,
            "/api/v1/stream/stop",
            params={"serial": device_id, "code": channel_code, "check_outputs": "false"},
        )
        if not result.success:
            return result
        return Gb28181Result(True, data={"raw": result.data or {}})

    def _request(
        self,
        config: Dict[str, Any],
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Gb28181Result:
        self.default_base_url = bootstrap_wvp_for_gb28181(self.project_root)
        base_url = str(config.get("wvp_base_url") or self.default_base_url).strip().rstrip("/") + "/"
        if not base_url:
            return Gb28181Result(False, "缺少 WVP 地址")

        try:
            response = requests.get(
                urljoin(base_url, path.lstrip("/")),
                params=params or {},
                timeout=float(config.get("timeout") or self.default_timeout),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            return Gb28181Result(False, f"WVP 请求失败: {exc}")
        except ValueError as exc:
            return Gb28181Result(False, f"WVP 返回不是 JSON: {exc}")

        if isinstance(payload, dict):
            code = payload.get("code")
            if code not in (None, 0, "0", 200, "200"):
                return Gb28181Result(False, str(payload.get("msg") or payload.get("message") or payload))
            data = payload.get("data") if "data" in payload else payload
            if isinstance(data, dict):
                return Gb28181Result(True, data=data)
            if isinstance(data, list):
                return Gb28181Result(True, data={"list": data})
            return Gb28181Result(True, data={"value": data})

        if isinstance(payload, list):
            return Gb28181Result(True, data={"list": payload})
        return Gb28181Result(False, f"WVP 返回格式无法识别: {payload!r}")

    def _normalize_list(self, data: Dict[str, Any], keys: tuple[str, ...]) -> List[Dict[str, Any]]:
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _normalize_device(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": item.get("ID") or item.get("id") or item.get("deviceId") or item.get("device_id") or "",
            "name": item.get("Name") or item.get("name") or item.get("deviceName") or "",
            "online": item.get("Online") if item.get("Online") is not None else item.get("online"),
            "channel_count": item.get("ChannelCount") or item.get("channelCount") or 0,
            "remote_ip": item.get("RemoteIP") or item.get("remoteIp") or "",
            "raw": item,
        }

    def _normalize_channel(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": item.get("ID") or item.get("id") or item.get("channelId") or item.get("channel_id") or "",
            "name": item.get("Name") or item.get("name") or item.get("channelName") or "",
            "device_id": item.get("DeviceID") or item.get("deviceId") or item.get("device_id") or "",
            "online": item.get("Status") if item.get("Status") is not None else item.get("online"),
            "stream_id": item.get("StreamID") or item.get("streamId") or "",
            "raw": item,
        }

    def _normalize_play(self, raw: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        protocol = str(config.get("stream_protocol") or "rtsp").lower()
        urls = {
            "rtsp": data.get("RTSP") or data.get("rtsp") or data.get("rtspUrl"),
            "flv": data.get("FLV") or data.get("flv") or data.get("httpFlv") or data.get("HTTP_FLV"),
            "https_flv": data.get("HTTPS_FLV") or data.get("httpsFlv"),
            "ws_flv": data.get("WS_FLV") or data.get("wsFlv"),
            "rtmp": data.get("RTMP") or data.get("rtmp"),
            "hls": data.get("HLS") or data.get("hls"),
            "webrtc": data.get("WEBRTC") or data.get("webrtc"),
        }
        preferred = urls.get(protocol) or urls.get("rtsp") or urls.get("flv") or urls.get("rtmp") or urls.get("hls")
        return {
            "play_url": preferred or "",
            "preview_url": urls.get("flv") or preferred or "",
            "rtsp_url": urls.get("rtsp") or "",
            "flv_url": urls.get("flv") or "",
            "rtmp_url": urls.get("rtmp") or "",
            "hls_url": urls.get("hls") or "",
            "stream_id": data.get("StreamID") or data.get("streamId") or "",
            "device_id": data.get("DeviceID") or data.get("deviceId") or config.get("device_id") or "",
            "channel_code": data.get("ChannelID") or data.get("channelId") or config.get("channel_code") or "",
            "channel_name": data.get("ChannelName") or data.get("channelName") or config.get("channel_name") or "",
            "transport": data.get("Transport") or data.get("transport") or config.get("transport") or "UDP",
            "raw": raw,
        }


gb28181_service = Gb28181Service()


__all__ = ["gb28181_service", "Gb28181Service", "Gb28181Result"]
