from __future__ import annotations

import datetime as _datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse, urlunparse


if not hasattr(_datetime, "UTC"):
    _datetime.UTC = _datetime.timezone.utc


try:
    from onvif import ONVIFCamera  # type: ignore
except ImportError:  # pragma: no cover - optional runtime dependency
    ONVIFCamera = None


@dataclass
class OnvifResult:
    success: bool
    message: str = ""
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "success": self.success,
            "message": self.message,
        }
        if self.data is not None:
            payload["data"] = self.data
        return payload


class OnvifService:
    """Resolve ONVIF camera profiles into concrete RTSP playback URLs."""

    def is_available(self) -> bool:
        return ONVIFCamera is not None

    def profiles(self, config: Dict[str, Any]) -> OnvifResult:
        missing = self._missing_required(config)
        if missing:
            return OnvifResult(False, f"缺少 ONVIF 参数: {', '.join(missing)}")
        if ONVIFCamera is None:
            return OnvifResult(False, "当前环境未安装 onvif-zeep，无法自动获取 ONVIF 码流")

        try:
            camera = self._create_camera(config)
            media = camera.create_media_service()
            raw_profiles = media.GetProfiles() or []
            device_info = self._get_device_info(camera)

            profiles: List[Dict[str, Any]] = []
            for index, profile in enumerate(raw_profiles):
                profiles.append(self._profile_to_dict(profile, index))

            return OnvifResult(True, "ONVIF 码流获取成功", {
                "device": device_info,
                "profiles": profiles,
            })
        except Exception as exc:  # pylint: disable=broad-except
            return OnvifResult(False, f"ONVIF 连接失败: {exc}")

    def resolve_stream_uri(self, config: Dict[str, Any]) -> OnvifResult:
        missing = self._missing_required(config)
        if missing:
            return OnvifResult(False, f"缺少 ONVIF 参数: {', '.join(missing)}")
        if ONVIFCamera is None:
            return OnvifResult(False, "当前环境未安装 onvif-zeep，无法自动解析 ONVIF 地址")

        try:
            camera = self._create_camera(config)
            media = camera.create_media_service()
            profile_token = str(
                config.get("onvif_profile_token")
                or config.get("profile_token")
                or ""
            ).strip()
            if not profile_token:
                profiles = media.GetProfiles() or []
                if not profiles:
                    return OnvifResult(False, "设备未返回可用 ONVIF Profile")
                profile_token = str(getattr(profiles[0], "token", "") or "").strip()

            request = {
                "StreamSetup": {
                    "Stream": "RTP-Unicast",
                    "Transport": {"Protocol": "RTSP"},
                },
                "ProfileToken": profile_token,
            }
            response = media.GetStreamUri(request)
            uri = str(getattr(response, "Uri", "") or "").strip()
            if not uri:
                return OnvifResult(False, "设备未返回 RTSP 播放地址")

            uri = self._ensure_rtsp_auth(
                uri,
                str(config.get("username") or ""),
                str(config.get("password") or ""),
            )
            return OnvifResult(True, "ONVIF 地址解析成功", {
                "rtsp_url": uri,
                "profile_token": profile_token,
            })
        except Exception as exc:  # pylint: disable=broad-except
            return OnvifResult(False, f"ONVIF 地址解析失败: {exc}")

    def resolve_url_or_empty(self, config: Dict[str, Any]) -> str:
        result = self.resolve_stream_uri(config)
        if not result.success:
            return ""
        return str((result.data or {}).get("rtsp_url") or "").strip()

    def _create_camera(self, config: Dict[str, Any]):
        ip = str(config.get("ip") or "").strip()
        port = int(config.get("onvif_port") or config.get("port") or 80)
        username = str(config.get("username") or "").strip()
        password = str(config.get("password") or "")
        return ONVIFCamera(ip, port, username, password)

    def _missing_required(self, config: Dict[str, Any]) -> List[str]:
        required = {
            "ip": config.get("ip"),
            "username": config.get("username"),
            "password": config.get("password"),
        }
        return [key for key, value in required.items() if not str(value or "").strip()]

    def _get_device_info(self, camera) -> Dict[str, Any]:
        try:
            devicemgmt = camera.create_devicemgmt_service()
            info = devicemgmt.GetDeviceInformation()
            return {
                "manufacturer": getattr(info, "Manufacturer", "") or "",
                "model": getattr(info, "Model", "") or "",
                "firmware_version": getattr(info, "FirmwareVersion", "") or "",
                "serial_number": getattr(info, "SerialNumber", "") or "",
                "hardware_id": getattr(info, "HardwareId", "") or "",
            }
        except Exception:
            return {}

    def _profile_to_dict(self, profile, index: int) -> Dict[str, Any]:
        video_encoder = getattr(profile, "VideoEncoderConfiguration", None)
        resolution = getattr(video_encoder, "Resolution", None)
        width = getattr(resolution, "Width", None)
        height = getattr(resolution, "Height", None)
        encoding = getattr(video_encoder, "Encoding", "") or ""
        name = getattr(profile, "Name", "") or f"Profile {index + 1}"
        token = getattr(profile, "token", "") or ""

        label_parts = [str(name)]
        if width and height:
            label_parts.append(f"{width}x{height}")
        if encoding:
            label_parts.append(str(encoding))

        return {
            "token": str(token),
            "name": str(name),
            "label": " / ".join(label_parts),
            "encoding": str(encoding),
            "width": width,
            "height": height,
        }

    def _ensure_rtsp_auth(self, uri: str, username: str, password: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme.lower() != "rtsp" or parsed.username or not username:
            return uri

        auth = quote(username, safe="")
        if password:
            auth += f":{quote(password, safe='')}"
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunparse(parsed._replace(netloc=f"{auth}@{host}"))


onvif_service = OnvifService()


__all__ = ["onvif_service", "OnvifService", "OnvifResult"]
