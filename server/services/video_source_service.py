"""
视频源管理服务
统一管理摄像头、视频流、视频文件三种视频源
"""
from __future__ import annotations

import json
import ipaddress
import os
import requests
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from server.services.media_gateway_service import media_gateway
from server.services.onvif_service import onvif_service

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None


@dataclass
class VideoSource:
    """视频源数据模型"""
    
    id: int
    type: str  # camera, stream, file
    name: str
    description: str = ""
    protocol: str = ""  # RTSP, ONVIF, GB28181 等
    config: Dict[str, Any] = field(default_factory=dict)
    video_url: str = ""
    origin_url: str = ""
    play_url: str = ""
    preview_url: str = ""
    gateway: str = ""
    gateway_app: str = ""
    gateway_stream: str = ""
    gateway_key: str = ""
    gateway_error: str = ""
    algorithms: List[str] = field(default_factory=list)
    enabled: bool = True
    status: str = "initializing"  # initializing, online, offline, error
    resolution: Optional[str] = None
    codec: Optional[str] = None
    error_message: Optional[str] = None
    order: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_check: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VideoSource":
        """从字典构建实例"""
        return cls(
            id=int(data.get("id", 0)),
            type=data.get("type", "camera"),
            name=data.get("name", ""),
            description=data.get("description", ""),
            protocol=data.get("protocol", ""),
            config=data.get("config", {}),
            video_url=data.get("video_url", ""),
            origin_url=data.get("origin_url", ""),
            play_url=data.get("play_url", ""),
            preview_url=data.get("preview_url", ""),
            gateway=data.get("gateway", ""),
            gateway_app=data.get("gateway_app", ""),
            gateway_stream=data.get("gateway_stream", ""),
            gateway_key=data.get("gateway_key", ""),
            gateway_error=data.get("gateway_error", ""),
            algorithms=data.get("algorithms", []),
            enabled=data.get("enabled", True),
            status=data.get("status", "initializing"),
            resolution=data.get("resolution"),
            codec=data.get("codec"),
            error_message=data.get("error_message"),
            order=data.get("order", 0),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            last_check=data.get("last_check"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


@dataclass
class ConnectionResult:
    """连接检测结果"""
    success: bool
    status: str
    message: str = ""
    resolution: Optional[str] = None
    codec: Optional[str] = None


class VideoSourceService:
    """
    视频源服务层
    
    - 统一管理摄像头、视频流、视频文件
    - 支持多协议配置验证
    - 提供连接检测能力
    """

    def __init__(self, storage_path: str = "data/video_sources.json"):
        self.storage_path = Path(storage_path)
        self._lock = threading.Lock()
        self._sources: Dict[int, VideoSource] = {}
        self._load_sources()

    # 公共 API ------------------------------------------------------------
    
    def get_all_sources(self, source_type: str = "all") -> List[Dict[str, Any]]:
        """获取所有视频源"""
        with self._lock:
            sources = list(self._sources.values())
            
            # 按类型过滤
            if source_type != "all":
                sources = [s for s in sources if s.type == source_type]
            
            # 按order排序
            sources.sort(key=lambda x: x.order)
            
            return [source.to_dict() for source in sources]

    def get_source(self, source_id: int) -> Optional[Dict[str, Any]]:
        """获取单个视频源"""
        with self._lock:
            source = self._sources.get(source_id)
            return source.to_dict() if source else None

    def add_source(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """添加视频源"""
        source = self._build_source(payload)
        
        with self._lock:
            source.id = self._next_source_id()
            source.order = len(self._sources)
            self._sync_runtime_urls(source)
            self._sources[source.id] = source
            self._save_sources()
        
        # 添加完成后立即触发一次检测（非阻塞）
        self._check_source_async(source.id)
        
        return self.get_source(source.id)

    def update_source(self, source_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """更新视频源"""
        with self._lock:
            current = self._sources.get(source_id)
            if not current:
                return None
            old_gateway = (
                current.gateway,
                current.gateway_app,
                current.gateway_stream,
                current.gateway_key,
                current.origin_url,
            )

        updated = self._build_source(payload, existing=current)
        updated.id = source_id
        self._sync_runtime_urls(updated)

        with self._lock:
            if old_gateway[0] == "zlm" and (
                updated.gateway != "zlm"
                or old_gateway[1] != updated.gateway_app
                or old_gateway[2] != updated.gateway_stream
                or old_gateway[4] != updated.origin_url
            ):
                media_gateway.close_proxy(old_gateway[1], old_gateway[2], old_gateway[3])
            self._sources[source_id] = updated
            self._save_sources()
        
        self._check_source_async(source_id)
        return self.get_source(source_id)

    def delete_source(self, source_id: int) -> bool:
        """????????"""
        source_to_delete: Optional[VideoSource] = None
        with self._lock:
            source_to_delete = self._sources.get(source_id)
            if not source_to_delete:
                return False

            self._sources.pop(source_id)
            self._save_sources()

        if source_to_delete.gateway == "zlm":
            media_gateway.close_proxy(
                source_to_delete.gateway_app,
                source_to_delete.gateway_stream,
                source_to_delete.gateway_key,
            )

        self._cleanup_source_files(source_to_delete)
        return True

    def check_connection(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """检测连接（不保存，用于预检测）"""
        try:
            # 构建临时源对象
            temp_source = self._build_source({
                "type": config.get("type", "camera"),
                "name": "temp",
                "description": "temp",
                "protocol": config.get("protocol", ""),
                "config": config.get("config", {}),
                "video_url": config.get("video_url", ""),
                "origin_url": config.get("origin_url", ""),
                "play_url": config.get("play_url", ""),
                "preview_url": config.get("preview_url", ""),
            })
            self._sync_runtime_urls(temp_source)

            # ??????????????????????? OpenCV ??????
            source_brand = str(temp_source.config.get("brand", "")).strip().lower()
            source_protocol = str(temp_source.protocol or "").strip().lower()
            quick_check = bool(config.get("quick_check", False))
            if quick_check and (source_brand == "ezviz" or source_protocol == "ezviz"):
                has_url = bool(self._get_runtime_url(temp_source))
                return {
                    "success": has_url,
                    "status": "online" if has_url else "offline",
                    "message": "\u840d\u77f3\u5730\u5740\u83b7\u53d6\u6210\u529f" if has_url else "\u840d\u77f3\u5730\u5740\u83b7\u53d6\u5931\u8d25",
                    "video_url": temp_source.video_url,
                    "origin_url": temp_source.origin_url,
                    "play_url": temp_source.play_url,
                    "preview_url": temp_source.preview_url,
                    "gateway": temp_source.gateway,
                    "gateway_error": temp_source.gateway_error,
                    "resolution": None,
                    "codec": None
                }

            result = self._probe_connection(temp_source)
            
            return {
                "success": result.success,
                "status": result.status,
                "message": result.message,
                "video_url": temp_source.video_url,
                "origin_url": temp_source.origin_url,
                "play_url": temp_source.play_url,
                "preview_url": temp_source.preview_url,
                "gateway": temp_source.gateway,
                "gateway_error": temp_source.gateway_error,
                "resolution": result.resolution,
                "codec": result.codec
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"检测失败: {str(e)}"
            }

    def check_source(self, source_id: int) -> bool:
        """检测视频源"""
        self.refresh_runtime_urls(source_id)
        with self._lock:
            source = self._sources.get(source_id)
            if not source:
                return False

        result = self._probe_connection(source)

        with self._lock:
            refreshed = self._sources.get(source_id)
            if not refreshed:
                return False
            
            refreshed.status = result.status
            refreshed.resolution = result.resolution
            refreshed.codec = result.codec
            refreshed.error_message = None if result.success else result.message
            refreshed.last_check = datetime.now().isoformat()
            refreshed.updated_at = refreshed.last_check
            
            self._sources[source_id] = refreshed
            self._save_sources()

        return result.success

    def refresh_runtime_urls(self, source_id: int) -> Optional[Dict[str, Any]]:
        """Refresh origin/play/preview URLs, applying the current ZLM policy."""
        with self._lock:
            current = self._sources.get(source_id)
            if not current:
                return None
            source = VideoSource.from_dict(current.to_dict())
            old_gateway = (
                current.gateway,
                current.gateway_app,
                current.gateway_stream,
                current.gateway_key,
                current.origin_url,
            )

        self._sync_runtime_urls(source)

        with self._lock:
            if old_gateway[0] == "zlm" and (
                source.gateway != "zlm"
                or old_gateway[1] != source.gateway_app
                or old_gateway[2] != source.gateway_stream
                or old_gateway[4] != source.origin_url
            ):
                media_gateway.close_proxy(old_gateway[1], old_gateway[2], old_gateway[3])

            source.updated_at = datetime.now().isoformat()
            self._sources[source_id] = source
            self._save_sources()
            return source.to_dict()

    def check_all_sources(self, source_type: str = "all") -> None:
        """检测所有视频源"""
        sources = self.get_all_sources(source_type)
        for source in sources:
            self.check_source(source['id'])

    def _check_source_async(self, source_id: int) -> None:
        """后台异步检测，避免保存接口阻塞。"""
        thread = threading.Thread(
            target=self._safe_check_source,
            args=(source_id,),
            daemon=True,
            name=f"video-source-check-{source_id}",
        )
        thread.start()

    def _safe_check_source(self, source_id: int) -> None:
        """包装异步检测，确保后台异常不影响主流程。"""
        try:
            self.check_source(source_id)
        except Exception:
            pass

    def toggle_source(self, source_id: int, enabled: bool) -> bool:
        """启用/停用视频源"""
        with self._lock:
            source = self._sources.get(source_id)
            if not source:
                return False

            source.enabled = enabled
            source.status = "online" if enabled else "offline"
            source.updated_at = datetime.now().isoformat()

            if enabled:
                try:
                    refreshed_url = self._derive_origin_url(source)
                    if refreshed_url:
                        source.origin_url = refreshed_url
                        self._sync_runtime_urls(source)
                        source.error_message = None
                except Exception as exc:
                    source.status = "error"
                    source.error_message = f"刷新播放地址失败: {exc}"

            self._save_sources()
            return True

    def reorder_sources(self, order: List[int]) -> None:
        """重新排序视频源"""
        with self._lock:
            for idx, source_id in enumerate(order):
                if source_id in self._sources:
                    self._sources[source_id].order = idx
            
            self._save_sources()

    def discover_rtsp_streams(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """扫描指定网段中开放 RTSP 端口的主机，并生成常见 RTSP 地址。"""
        subnet_text = str(config.get("subnet", "")).strip()
        if not subnet_text:
            return {"success": False, "message": "缺少网段，例如 192.168.1.0/24"}

        try:
            network = ipaddress.ip_network(subnet_text, strict=False)
        except ValueError:
            return {"success": False, "message": "网段格式无效，例如 192.168.1.0/24"}

        if network.version != 4:
            return {"success": False, "message": "目前只支持 IPv4 网段扫描"}

        host_limit = min(max(int(config.get("host_limit", 64) or 64), 1), 512)
        port = int(config.get("port", 554) or 554)
        timeout = float(config.get("timeout", 0.35) or 0.35)
        brand = str(config.get("brand", "")).strip().lower()
        username = str(config.get("username", "")).strip()
        password = str(config.get("password", "")).strip()

        hosts = [str(ip) for ip in network.hosts()]
        if len(hosts) > host_limit:
            hosts = hosts[:host_limit]

        online_hosts: List[str] = []
        max_workers = min(64, max(8, len(hosts)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._is_tcp_port_open, host, port, timeout): host
                for host in hosts
            }
            for future in as_completed(futures):
                host = futures[future]
                try:
                    if future.result():
                        online_hosts.append(host)
                except Exception:
                    continue

        online_hosts.sort(key=lambda item: tuple(int(part) for part in item.split(".")))
        results = []
        for host in online_hosts:
            candidates = self._build_rtsp_candidates(
                host=host,
                port=port,
                username=username,
                password=password,
                brand=brand,
            )
            if not candidates:
                continue

            results.append({
                "ip": host,
                "port": port,
                "brand": brand or "generic",
                "reachable": True,
                "recommended_url": candidates[0]["url"],
                "candidates": candidates,
            })

        return {
            "success": True,
            "data": {
                "subnet": str(network),
                "port": port,
                "scanned_hosts": len(hosts),
                "discovered_hosts": len(results),
                "results": results,
            }
        }

    # 内部工具 ------------------------------------------------------------
    
    def _load_sources(self) -> None:
        """加载视频源"""
        if not self.storage_path.exists():
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.storage_path.write_text("[]", encoding="utf-8")
            self._sources = {}
            return

        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            sources = [VideoSource.from_dict(item) for item in data]
            self._sources = {source.id: source for source in sources}
        except Exception:
            self._sources = {}

    def _save_sources(self) -> None:
        """保存视频源"""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = [source.to_dict() for source in self._sources.values()]
        self.storage_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _next_source_id(self) -> int:
        """生成下一个ID"""
        if not self._sources:
            return 1
        return max(self._sources.keys()) + 1

    def _build_source(
        self, 
        payload: Dict[str, Any], 
        existing: Optional[VideoSource] = None
    ) -> VideoSource:
        """构建视频源对象"""
        source_type = payload.get("type") or (existing.type if existing else "camera")
        
        if existing:
            source = existing
            source.updated_at = datetime.now().isoformat()
        else:
            source = VideoSource(
                id=0,
                type=source_type,
                name="",
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat()
            )
        
        # 更新基本信息
        source.name = payload.get("name", source.name)
        source.description = payload.get("description", source.description)
        source.protocol = payload.get("protocol", source.protocol).upper()
        source.algorithms = payload.get("algorithms", source.algorithms)
        source.enabled = payload.get("enabled", source.enabled)
        
        # 更新配置
        config = payload.get("config", {})
        if existing and existing.config:
            merged_config = dict(existing.config)
            merged_config.update(config)
            source.config = merged_config
        else:
            source.config = config
        
        # 生成或更新video_url
        payload_video_url = str(payload.get("video_url", "") or "").strip()
        source.origin_url = str(payload.get("origin_url", "") or payload_video_url or "").strip()
        source.play_url = str(payload.get("play_url", "") or "").strip()
        source.preview_url = str(payload.get("preview_url", "") or "").strip()
        source.gateway = str(payload.get("gateway", "") or "").strip()
        source.gateway_app = str(payload.get("gateway_app", "") or "").strip()
        source.gateway_stream = str(payload.get("gateway_stream", "") or "").strip()
        source.gateway_key = str(payload.get("gateway_key", "") or "").strip()
        source.gateway_error = str(payload.get("gateway_error", "") or "").strip()
        
        return source

    def _derive_video_url(self, source: VideoSource) -> str:
        """生成视频URL"""
        return self._derive_origin_url(source)

    def _clean_runtime_url(self, value: Any) -> str:
        """Return only URLs that OpenCV/FFmpeg can actually open."""
        url = str(value or "").strip()
        if url.lower().startswith("gb28181://"):
            return ""
        return url

    def _derive_origin_url(self, source: VideoSource) -> str:
        """根据源配置生成原始接入地址，后续可再交给媒体网关转换。"""
        config = source.config
        
        if source.type == "camera":
            brand = str(config.get("brand", "")).strip().lower()
            if brand == "ezviz":
                return self._resolve_ezviz_play_url(config)
            if source.protocol == "RTSP":
                return self._clean_runtime_url(config.get("url", ""))
            elif source.protocol == "ONVIF":
                manual_url = self._clean_runtime_url(config.get("url"))
                stream_mode = str(config.get("stream_mode") or "").strip().lower()
                if stream_mode == "manual" and manual_url:
                    return manual_url
                onvif_url = onvif_service.resolve_url_or_empty(config)
                if onvif_url:
                    return onvif_url
                if manual_url:
                    return manual_url
                ip = config.get("ip")
                port = config.get("rtsp_port") or config.get("port") or 554
                username = config.get("username")
                password = config.get("password")
                channel = config.get("onvif_channel") or config.get("channel_id") or "101"
                if all([ip, username, password]):
                    auth = quote(str(username), safe="")
                    if password:
                        auth += f":{quote(str(password), safe='')}"
                    return f"rtsp://{auth}@{ip}:{port}/Streaming/Channels/{channel}"
            elif source.protocol == "GB28181":
                for key in ("url", "play_url", "rtsp_url", "flv_url", "rtmp_url", "hls_url"):
                    runtime_url = self._clean_runtime_url(config.get(key))
                    if runtime_url:
                        return runtime_url
                return ""
        elif source.type == "stream":
            return self._clean_runtime_url(config.get("url", ""))
        elif source.type == "file":
            return self._clean_runtime_url(config.get("file_path", ""))
        
        return ""

    def _sync_runtime_urls(self, source: VideoSource) -> None:
        """兼容旧 video_url 字段，同时明确原始地址和运行时播放地址。"""
        for attr in ("origin_url", "play_url", "preview_url", "video_url"):
            value = str(getattr(source, attr, "") or "").strip()
            if value.lower().startswith("gb28181://"):
                setattr(source, attr, "")

        if source.protocol == "GB28181" and source.config:
            for key in ("url", "play_url", "rtsp_url", "flv_url", "rtmp_url", "hls_url"):
                value = str(source.config.get(key, "") or "").strip()
                if value.lower().startswith("gb28181://"):
                    source.config[key] = ""

        derived_origin_url = self._derive_origin_url(source)
        if derived_origin_url:
            source.origin_url = derived_origin_url

        if not source.origin_url and source.video_url:
            source.origin_url = source.video_url

        gateway_result = media_gateway.ensure_proxy(
            source_id=source.id,
            source_type=source.type,
            config=source.config or {},
            origin_url=source.origin_url,
        )
        if gateway_result.enabled:
            source.play_url = gateway_result.play_url
            source.preview_url = gateway_result.preview_url
            source.gateway = gateway_result.gateway
            source.gateway_app = gateway_result.gateway_app
            source.gateway_stream = gateway_result.gateway_stream
            source.gateway_key = gateway_result.gateway_key
            source.gateway_error = ""
        else:
            source.play_url = source.origin_url
            source.gateway = ""
            source.gateway_app = ""
            source.gateway_stream = ""
            source.gateway_key = ""
            source.gateway_error = gateway_result.error

        if not source.preview_url:
            source.preview_url = source.play_url

        # 旧代码仍读取 video_url；在接入网关落地前，它指向实际检测用地址。
        self._sync_processed_stream_urls(source)
        source.video_url = source.play_url or source.origin_url

    def _sync_processed_stream_urls(self, source: VideoSource) -> None:
        """Populate ZLM URLs for the processed algorithm output stream."""
        if source.type == "file":
            return

        config = source.config or {}
        app = str(config.get("processed_gateway_app") or os.getenv("SAFEHAT_ZLM_RESULT_APP", "safehat_result")).strip()
        stream = str(config.get("processed_gateway_stream") or f"source_{source.id or 'new'}").strip()
        push_host = str(os.getenv("SAFEHAT_ZLM_PUSH_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        public_host = str(os.getenv("SAFEHAT_ZLM_PUBLIC_HOST", "127.0.0.1")).strip() or "127.0.0.1"
        http_port = str(os.getenv("SAFEHAT_ZLM_HTTP_PORT", "8080")).strip() or "8080"
        rtmp_port = str(os.getenv("SAFEHAT_ZLM_RTMP_PORT", "1935")).strip() or "1935"

        config.setdefault("processed_gateway_app", app)
        config.setdefault("processed_gateway_stream", stream)
        config["processed_output_url"] = f"rtmp://{push_host}:{rtmp_port}/{app}/{stream}"
        config["processed_preview_url"] = f"http://{public_host}:{http_port}/{app}/{stream}.live.flv"
        config["processed_webrtc_url"] = (
            f"http://{public_host}:{http_port}/index/api/webrtc?app={app}&stream={stream}&type=play"
        )
        source.config = config

    def _get_runtime_url(self, source: VideoSource) -> str:
        return (source.play_url or source.video_url or source.origin_url or "").strip()

    def _open_video_capture(self, url: str, transport: str = "tcp"):
        """使用与检测线程一致的低延迟参数打开视频源。"""
        is_network_stream = isinstance(url, str) and url.lower().startswith(
            ("rtsp://", "rtmp://", "http://", "https://")
        )

        previous_ffmpeg_options = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        try:
            if is_network_stream:
                if url.lower().startswith("rtmp://"):
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                        "rtmp_live;live|fflags;nobuffer|flags;low_delay|max_delay;500000"
                    )
                else:
                    rtsp_transport = "udp" if str(transport).lower() == "udp" else "tcp"
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                        f"rtsp_transport;{rtsp_transport}|fflags;nobuffer|flags;low_delay|max_delay;500000"
                    )
                capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            else:
                capture = cv2.VideoCapture(url)

            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return capture
        finally:
            if previous_ffmpeg_options is None:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            else:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = previous_ffmpeg_options

    def _resolve_ezviz_play_url(self, config: Dict[str, Any]) -> str:
        """根据萤石开放平台配置动态获取播放地址。"""
        app_key = str(config.get("app_key", "")).strip()
        app_secret = str(config.get("app_secret", "")).strip()
        device_serial = str(config.get("device_serial", "")).strip()
        verify_code = str(config.get("verify_code", "")).strip()
        channel_no = int(config.get("channel_no", 1) or 1)
        raw_protocol = config.get("ezviz_protocol", 3)
        protocol_explicit = bool(config.get("ezviz_protocol_explicit", False))
        protocol = int(raw_protocol or 3)
        if not protocol_explicit or protocol in (2, 4):
            protocol = 3
        expire_time = int(config.get("expire_time", 3600) or 3600)

        if not app_key or not app_secret or not device_serial:
            return ""

        access_token = self._get_ezviz_access_token(app_key, app_secret)
        preferred_protocols = [protocol]
        if protocol != 3:
            preferred_protocols.append(3)
        if 4 not in preferred_protocols:
            preferred_protocols.append(4)

        for candidate_protocol in preferred_protocols:
            play_data = self._get_ezviz_play_address(
                access_token=access_token,
                device_serial=device_serial,
                channel_no=channel_no,
                protocol=candidate_protocol,
                expire_time=expire_time,
                verify_code=verify_code or None,
            )
            resolved_url = self._pick_ezviz_best_url(play_data, candidate_protocol)
            if resolved_url:
                return resolved_url

        return ""

    def _get_ezviz_access_token(self, app_key: str, app_secret: str) -> str:
        response = requests.post(
            "https://open.ys7.com/api/lapp/token/get",
            data={"appKey": app_key, "appSecret": app_secret},
            timeout=6,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "200":
            raise RuntimeError(f"萤石 accessToken 获取失败: {payload}")

        token = payload.get("data", {}).get("accessToken")
        if not token:
            raise RuntimeError(f"萤石未返回 accessToken: {payload}")
        return token

    def _get_ezviz_play_address(
        self,
        access_token: str,
        device_serial: str,
        channel_no: int = 1,
        protocol: int = 3,
        expire_time: int = 3600,
        verify_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "accessToken": access_token,
            "deviceSerial": device_serial,
            "channelNo": channel_no,
            "protocol": protocol,
            "expireTime": expire_time,
        }
        if verify_code:
            payload["code"] = verify_code

        response = requests.post(
            "https://open.ys7.com/api/lapp/v2/live/address/get",
            data=payload,
            timeout=6,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "200":
            raise RuntimeError(f"萤石播放地址获取失败: {data}")

        return data.get("data", {}) or {}

    def _pick_ezviz_best_url(self, play_data: Dict[str, Any], protocol: int) -> str:
        candidates: List[Optional[str]] = []
        if protocol == 1:
            candidates.extend([play_data.get("url"), play_data.get("ezopen")])
        elif protocol == 2:
            candidates.extend([
                play_data.get("url"),
                play_data.get("hls"),
                play_data.get("hlsHd"),
                play_data.get("hlsUrl"),
            ])
        elif protocol == 3:
            candidates.extend([
                play_data.get("url"),
                play_data.get("rtmp"),
                play_data.get("rtmpHd"),
                play_data.get("rtmpUrl"),
            ])
        elif protocol == 4:
            candidates.extend([
                play_data.get("url"),
                play_data.get("flv"),
                play_data.get("flvHd"),
                play_data.get("flvUrl"),
            ])

        for value in play_data.values():
            if isinstance(value, str) and value.startswith(("http://", "https://", "rtmp://", "ezopen://", "ezviz://")):
                candidates.append(value)

        for candidate in candidates:
            if candidate:
                return candidate
        return ""

    def _is_tcp_port_open(self, host: str, port: int, timeout: float = 0.35) -> bool:
        """快速检测目标主机端口是否开放。"""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _cleanup_source_files(self, source: VideoSource) -> None:
        """????????????????????? data/videos ?????"""
        if source.type != "file":
            return

        file_path_text = str((source.config or {}).get("file_path", "")).strip()
        if not file_path_text:
            return

        try:
            target_path = Path(file_path_text)
            if not target_path.is_absolute():
                target_path = (Path.cwd() / target_path).resolve()
            else:
                target_path = target_path.resolve()

            videos_dir = (Path.cwd() / "data" / "videos").resolve()
            if videos_dir in target_path.parents and target_path.exists() and target_path.is_file():
                target_path.unlink()
        except Exception as exc:
            print(f"???????? [{file_path_text}]: {exc}")

    def _build_rtsp_candidates(
        self,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        brand: str = "",
    ) -> List[Dict[str, str]]:
        """根据品牌生成常见 RTSP URL 候选。"""
        auth = ""
        if username:
            auth = quote(username, safe="")
            if password:
                auth += f":{quote(password, safe='')}"
            auth += "@"

        base = f"rtsp://{auth}{host}:{port}"
        templates = {
            "hikvision": [
                ("主码流", f"{base}/h264/ch1/main/av_stream"),
                ("子码流", f"{base}/h264/ch1/sub/av_stream"),
                ("兼容主码流", f"{base}/Streaming/Channels/101"),
                ("兼容子码流", f"{base}/Streaming/Channels/102"),
            ],
            "dahua": [
                ("主码流", f"{base}/cam/realmonitor?channel=1&subtype=0"),
                ("子码流", f"{base}/cam/realmonitor?channel=1&subtype=1"),
            ],
            "uniview": [
                ("主码流", f"{base}/video1"),
                ("子码流", f"{base}/video2"),
                ("三码流", f"{base}/video3"),
            ],
            "generic": [
                ("通用主码流", f"{base}/Streaming/Channels/101"),
                ("通用子码流", f"{base}/Streaming/Channels/102"),
                ("通用主码流2", f"{base}/h264/ch1/main/av_stream"),
                ("通用子码流2", f"{base}/h264/ch1/sub/av_stream"),
                ("通用视频1", f"{base}/video1"),
                ("通用视频2", f"{base}/video2"),
            ],
        }

        pairs = templates.get(brand) or templates["generic"]
        seen = set()
        results: List[Dict[str, str]] = []
        for label, url in pairs:
            if url in seen:
                continue
            seen.add(url)
            results.append({"label": label, "url": url})
        return results

    def _probe_connection(self, source: VideoSource) -> ConnectionResult:
        """探测连接"""
        if cv2 is None:
            return ConnectionResult(
                False, 
                "error", 
                "系统未安装OpenCV，无法检测视频源"
            )
        
        # 对于文件类型
        if source.type == "file":
            file_path = source.config.get("file_path")
            if not file_path:
                return ConnectionResult(False, "error", "文件路径不能为空")
            
            path = Path(file_path)
            if not path.exists():
                return ConnectionResult(False, "offline", "文件不存在")
            
            try:
                capture = cv2.VideoCapture(str(path))
                ret, frame = capture.read()
                if ret and frame is not None:
                    height, width = frame.shape[:2]
                    fps = capture.get(cv2.CAP_PROP_FPS)
                    fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
                    codec = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])
                    capture.release()
                    return ConnectionResult(
                        True, 
                        "online", 
                        "视频文件可读取", 
                        f"{width}x{height}",
                        codec
                    )
                capture.release()
                return ConnectionResult(False, "offline", "视频文件无法读取")
            except Exception as e:
                return ConnectionResult(False, "error", f"读取失败: {str(e)}")
        
        # 对于摄像头和流类型
        url = self._get_runtime_url(source)
        if not url:
            return ConnectionResult(False, "error", "视频地址不能为空")
        
        # GB28181等特殊协议，暂时返回initializing
        if url.startswith("gb28181://"):
            return ConnectionResult(
                True, 
                "initializing", 
                "已保存配置，需在本地环境完成协议对接"
            )
        
        # RTSP/RTMP等标准流协议
        try:
            transport = source.config.get("transport") or ("tcp" if source.config.get("tcp_push", True) else "udp")
            capture = self._open_video_capture(url, transport=transport)
            ret, frame = capture.read()
            
            if ret and frame is not None:
                height, width = frame.shape[:2]
                fps = capture.get(cv2.CAP_PROP_FPS)
                fourcc = int(capture.get(cv2.CAP_PROP_FOURCC))
                codec = "".join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])
                capture.release()
                return ConnectionResult(
                    True, 
                    "online", 
                    "连接成功", 
                    f"{width}x{height}",
                    codec or "h264"
                )
            
            capture.release()
            return ConnectionResult(False, "offline", "连接失败或无数据")
        except Exception as e:
            return ConnectionResult(False, "error", f"连接失败: {str(e)}")


# 全局单例
video_source_service = VideoSourceService()


__all__ = ["video_source_service", "VideoSourceService"]
