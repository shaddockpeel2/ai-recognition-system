"""
通道管理服务

负责通道配置的增删改查、协议校验、连接检测等逻辑。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover
    cv2 = None


PROTOCOL_ALIASES = {
    "LOCAL_FILE": "FILE",
    "MP4": "FILE",
    "GB": "GB28181",
}

SUPPORTED_PROTOCOLS = {
    "RTSP": {"required": ["url"], "display_name": "RTSP 网络流"},
    "ONVIF": {"required": ["ip", "username", "password"], "display_name": "ONVIF 标准"},
    "FILE": {"required": ["file_path"], "display_name": "本地文件"},
    "GB28181": {"required": ["channel_code", "server_ip", "server_port"], "display_name": "GB28181 国标"},
    "DSS": {"required": ["server_ip", "server_port", "device_code"], "display_name": "大华 DSS"},
    "EZV": {"required": ["device_serial", "verify_code"], "display_name": "萤石 EZV"},
    "ISIP": {"required": ["ip", "server_port", "channel_code"], "display_name": "海康 ISIP"},
}


@dataclass
class Channel:
    """通道数据模型"""

    id: int
    name: str
    protocol: str
    location: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    status: str = "initializing"
    resolution: Optional[str] = None
    video_url: str = ""
    error_message: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_check: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Channel":
        """从原始字典构建 Channel 实例"""
        return cls(
            id=int(data.get("id", 0)),
            name=data.get("name", f"通道{data.get('id', '')}"),
            protocol=str(data.get("protocol", "RTSP")).upper(),
            location=data.get("location", ""),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            config=data.get("config", {}),
            status=data.get("status", "initializing"),
            resolution=data.get("resolution"),
            video_url=data.get("video_url", data.get("config", {}).get("url", "")),
            error_message=data.get("error_message"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            last_check=data.get("last_check"),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data


@dataclass
class ConnectorResult:
    success: bool
    status: str
    message: str = ""
    resolution: Optional[str] = None


class BaseStreamConnector:
    """协议连接器基类"""

    def __init__(self, channel: Channel):
        self.channel = channel

    def probe(self) -> ConnectorResult:
        raise NotImplementedError


class RTSPConnector(BaseStreamConnector):
    def probe(self) -> ConnectorResult:
        if cv2 is None:
            return ConnectorResult(False, "error", "系统未安装OpenCV，无法检测RTSP")

        url = self.channel.video_url or self.channel.config.get("url")
        if not url:
            return ConnectorResult(False, "error", "RTSP地址不能为空")

        capture = cv2.VideoCapture(url)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ret, frame = capture.read()
        if ret and frame is not None:
            height, width = frame.shape[:2]
            capture.release()
            return ConnectorResult(True, "online", "RTSP连接成功", f"{width}x{height}")

        capture.release()
        return ConnectorResult(False, "offline", "RTSP连接失败或无数据")


class FileConnector(BaseStreamConnector):
    def probe(self) -> ConnectorResult:
        if cv2 is None:
            return ConnectorResult(False, "error", "系统未安装OpenCV，无法读取文件")

        file_path = self.channel.config.get("file_path")
        if not file_path:
            return ConnectorResult(False, "error", "请提供本地文件路径")

        path = Path(file_path)
        if not path.exists():
            return ConnectorResult(False, "offline", "本地文件不存在")

        capture = cv2.VideoCapture(str(path))
        ret, frame = capture.read()
        if ret and frame is not None:
            height, width = frame.shape[:2]
            capture.release()
            return ConnectorResult(True, "online", "视频文件可读取", f"{width}x{height}")

        capture.release()
        return ConnectorResult(False, "offline", "视频文件无法读取")


class OnvifConnector(RTSPConnector):
    """通过转换为RTSP方式进行检测"""

    def probe(self) -> ConnectorResult:
        url = self.channel.video_url
        if not url:
            return ConnectorResult(False, "error", "ONVIF 配置缺少视频地址")
        return super().probe()


class PlaceholderConnector(BaseStreamConnector):
    """暂未实现协议的占位实现"""

    def probe(self) -> ConnectorResult:
        return ConnectorResult(
            True,
            "initializing",
            "已保存配置，需在本地环境完成协议网关对接"
        )


CONNECTOR_MAP = {
    "RTSP": RTSPConnector,
    "FILE": FileConnector,
    "ONVIF": OnvifConnector,
    "GB28181": PlaceholderConnector,
    "DSS": PlaceholderConnector,
    "EZV": PlaceholderConnector,
    "ISIP": PlaceholderConnector,
}


class ChannelService:
    """
    通道服务层

    - 负责通道增删改查
    - 支持多协议配置验证
    - 提供通道检测能力
    """

    def __init__(self, storage_path: str = "data/channels.json"):
        self.storage_path = Path(storage_path)
        self._lock = threading.Lock()
        self._channels: Dict[int, Channel] = {}
        self._load_channels()

    # 公共 API ------------------------------------------------------------
    def get_all_channels(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [channel.to_dict() for channel in self._channels.values()]

    def get_channel(self, channel_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            channel = self._channels.get(channel_id)
            return channel.to_dict() if channel else None

    def add_channel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        channel = self._build_channel(payload)
        with self._lock:
            channel.id = self._next_channel_id()
            self._channels[channel.id] = channel
            self._save_channels()
        # 添加完成后立即触发一次检测（非阻塞）
        self.check_channel(channel.id)
        return self.get_channel(channel.id)  # 返回最新状态

    def update_channel(self, channel_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            current = self._channels.get(channel_id)
            if not current:
                return None

        updated = self._build_channel(payload, existing=current)
        updated.id = channel_id

        with self._lock:
            self._channels[channel_id] = updated
            self._save_channels()
        self.check_channel(channel_id)
        return self.get_channel(channel_id)

    def delete_channel(self, channel_id: int) -> bool:
        with self._lock:
            if channel_id in self._channels:
                self._channels.pop(channel_id)
                self._save_channels()
                return True
            return False

    def check_channel(self, channel_id: int) -> bool:
        with self._lock:
            channel = self._channels.get(channel_id)
            if not channel:
                return False

        connector = self._build_connector(channel)
        result = connector.probe()

        with self._lock:
            refreshed = self._channels.get(channel_id)
            if not refreshed:
                return False
            refreshed.status = result.status
            refreshed.resolution = result.resolution
            refreshed.error_message = None if result.success else result.message
            refreshed.last_check = datetime.now().isoformat()
            refreshed.updated_at = refreshed.last_check
            self._channels[channel_id] = refreshed
            self._save_channels()

        return result.success

    def check_all_channels(self) -> None:
        channel_ids = list(self._channels.keys())
        for channel_id in channel_ids:
            self.check_channel(channel_id)

    # 内部工具 ------------------------------------------------------------
    def _load_channels(self) -> None:
        if not self.storage_path.exists():
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text("[]", encoding="utf-8")
            self._channels = {}
            return

        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            channels = [Channel.from_dict(item) for item in data]
            self._channels = {channel.id: channel for channel in channels}
        except Exception:
            self._channels = {}

    def _save_channels(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = [channel.to_dict() for channel in self._channels.values()]
        self.storage_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _next_channel_id(self) -> int:
        if not self._channels:
            return 1
        return max(self._channels.keys()) + 1

    def _build_channel(self, payload: Dict[str, Any], existing: Optional[Channel] = None) -> Channel:
        protocol = str(payload.get("protocol") or (existing.protocol if existing else "RTSP")).upper()
        protocol = PROTOCOL_ALIASES.get(protocol, protocol)
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError(f"不支持的协议类型: {protocol}")

        config = payload.get("config") or {}
        if existing:
            merged_config = dict(existing.config)
            merged_config.update(config)
            config = merged_config

        self._validate_config(protocol, config)

        channel = existing if existing else Channel(id=0, name="", protocol=protocol)
        channel.name = payload.get("name", channel.name or f"{protocol}通道")
        channel.protocol = protocol
        channel.location = payload.get("location", channel.location or "")
        channel.description = payload.get("description", channel.description or "")
        channel.tags = payload.get("tags", channel.tags or [])
        channel.config = config
        channel.video_url = self._derive_video_url(protocol, config)
        channel.status = payload.get("status", "initializing")
        channel.resolution = payload.get("resolution") if payload.get("resolution") else channel.resolution
        channel.error_message = None
        channel.last_check = channel.last_check if existing else None
        channel.updated_at = datetime.now().isoformat()
        if not channel.created_at:
            channel.created_at = datetime.now().isoformat()
        return channel

    def _validate_config(self, protocol: str, config: Dict[str, Any]) -> None:
        required = SUPPORTED_PROTOCOLS.get(protocol, {}).get("required", [])
        missing = [field for field in required if not config.get(field)]
        if missing:
            raise ValueError(f"{protocol} 协议缺少必要字段: {', '.join(missing)}")

    def _derive_video_url(self, protocol: str, config: Dict[str, Any]) -> str:
        if protocol == "RTSP":
            return config.get("url", "")
        if protocol == "FILE":
            return config.get("file_path", "")
        if protocol == "ONVIF":
            ip = config.get("ip")
            port = config.get("port", 554)
            username = config.get("username")
            password = config.get("password")
            channel_id = config.get("channel_id", "101")
            if all([ip, username, password]):
                return f"rtsp://{username}:{password}@{ip}:{port}/Streaming/Channels/{channel_id}"
            return ""
        if protocol == "GB28181":
            for key in ("url", "play_url", "rtsp_url", "flv_url", "rtmp_url", "hls_url"):
                url = str(config.get(key, "") or "").strip()
                if url and not url.lower().startswith("gb28181://"):
                    return url
            return ""
        if protocol == "DSS":
            return f"dss://{config.get('server_ip', '')}:{config.get('server_port', '')}/{config.get('device_code', '')}"
        if protocol == "EZV":
            return f"ezv://{config.get('device_serial', '')}"
        if protocol == "ISIP":
            return f"isip://{config.get('ip', '')}:{config.get('server_port', '')}/{config.get('channel_code', '')}"
        return config.get("url", "")

    def _build_connector(self, channel: Channel) -> BaseStreamConnector:
        connector_cls = CONNECTOR_MAP.get(channel.protocol, PlaceholderConnector)
        return connector_cls(channel)


__all__ = ["ChannelService"]
