"""
视频源检测模块
负责对添加的视频源（摄像头/视频流/视频文件）进行后台检测
"""
import cv2
import threading
import time
import os
import queue
import numpy as np
from datetime import datetime
from collections import deque
from pathlib import Path
import json

from safehat.core.frame_reader import ReaderOptions, create_frame_reader
from safehat.core.frame_pusher import create_processed_frame_pusher
from suanfa import HelmetDetector, VestDetector, FallDetector, MaskDetector, PersonIntrusionDetector, SmokeDetector, FireDetector, CrowdGatheringDetector, PersonAbsenceDetector, VehicleParkingDetector, FireExtinguisherAbsenceDetector, ReagentBottleDetector, CameraOcclusionDetector, FightDetector, GloveDetector, CrackDetector, DogDetector, CatDetector, FatigueDrivingDetector, TrashDetector, ElectricBicycleDetector, TheftDetector, WorkClothesDetector, SafehatVestDetector, GasCylinderTiltDetector, BoxObjectDetector


class VideoSourceDetector:
    """视频源检测器 - 支持多路视频源同时检测"""
    
    def __init__(self, real_detection_manager):
        """
        初始化视频源检测器
        
        Args:
            real_detection_manager: RealDetectionManager实例，用于记录告警
        """
        self.manager = real_detection_manager
        
        # 检测器（共享使用）
        self.helmet_detector = HelmetDetector()
        self.vest_detector = VestDetector()
        self.fall_detector = FallDetector()
        self.mask_detector = MaskDetector()
        self.person_intrusion_detector = PersonIntrusionDetector()
        self.smoke_detector = SmokeDetector()
        self.fire_detector = FireDetector()
        self.crowd_gathering_detector = CrowdGatheringDetector()
        self.person_absence_detector = PersonAbsenceDetector()
        self.vehicle_parking_detector = VehicleParkingDetector()
        self.fire_extinguisher_absence_detector = FireExtinguisherAbsenceDetector()
        self.reagent_bottle_detector = ReagentBottleDetector()
        self.camera_occlusion_detector = CameraOcclusionDetector()
        self.fight_detector = FightDetector()
        self.glove_detector = GloveDetector()
        self.crack_detector = CrackDetector()
        self.dog_detector = DogDetector()
        self.cat_detector = CatDetector()
        self.fatigue_driving_detector = FatigueDrivingDetector()
        self.trash_detector = TrashDetector()
        self.electric_bicycle_detector = ElectricBicycleDetector()
        self.theft_detector = TheftDetector()
        self.work_clothes_detector = WorkClothesDetector()
        self.safehat_vest_detector = SafehatVestDetector()
        self.gas_cylinder_tilt_detector = GasCylinderTiltDetector()
        self.box_object_detector = BoxObjectDetector()
        self.detector_map = {
            'helmet_detection': self.helmet_detector,
            'vest_detection': self.vest_detector,
            'fall_detection': self.fall_detector,
            'mask_detection': self.mask_detector,
            'intrusion_detection': self.person_intrusion_detector,
            'smoke_detection': self.smoke_detector,
            'fire_detection': self.fire_detector,
            'crowd_gathering_detection': self.crowd_gathering_detector,
            'person_absence_detection': self.person_absence_detector,
            'vehicle_parking_detection': self.vehicle_parking_detector,
            'fire_extinguisher_absence_detection': self.fire_extinguisher_absence_detector,
            'reagent_bottle_detection': self.reagent_bottle_detector,
            'camera_occlusion_detection': self.camera_occlusion_detector,
            'fight_detection': self.fight_detector,
            'glove_detection': self.glove_detector,
            'crack_detection': self.crack_detector,
            'dog_detection': self.dog_detector,
            'cat_detection': self.cat_detector,
            'fatigue_driving_detection': self.fatigue_driving_detector,
            'trash_detection': self.trash_detector,
            'electric_bicycle_detection': self.electric_bicycle_detector,
            'theft_detection': self.theft_detector,
            'work_clothes_detection': self.work_clothes_detector,
            'safehat_vest_detection': self.safehat_vest_detector,
            'gas_cylinder_tilt_detection': self.gas_cylinder_tilt_detector,
            'box_object_detection': self.box_object_detector,
        }
        self.detector_locks = {
            algorithm_name: threading.RLock()
            for algorithm_name in self.detector_map
        }

        # 检测线程管理
        self.detection_threads = {}  # {source_id: thread}
        self.stop_flags = {}  # {source_id: bool}
        self.stream_resources = {}  # {source_id: {"cap": cv2.VideoCapture, "reader_thread": Thread, "reader_state": dict}}
        self.lifecycle_lock = threading.RLock()
        self.frame_lock = threading.Lock()
        self.latest_processed_frames = {}  # {source_id: frame}
        self.processed_frame_versions = {}  # {source_id: version}
        self.processed_source_versions = {}  # {source_id: raw_frame_version}
        self.latest_raw_frames = {}  # {source_id: frame}
        self.raw_frame_versions = {}  # {source_id: version}
        self.theft_llm_tasks = {}  # {source_id: {"status": str, "started_at": float}}
        self.theft_llm_lock = threading.Lock()
        self.theft_llm_queue = queue.Queue()
        self.theft_llm_worker = threading.Thread(
            target=self._theft_llm_worker_loop,
            daemon=True,
            name="TheftLLMWorker",
        )
        self.theft_llm_worker.start()

        # 视频源配置路径
        project_root = Path(__file__).resolve().parent.parent.parent
        self.video_source_path = project_root / "data" / "video_sources.json"
        self.algorithm_config_path = project_root / "data" / "algorithm_config.json"
        self.legacy_algorithm_config_path = project_root / "data" / "algorithm_configs.json"
        self.algorithm_configs = self._load_algorithm_configs()
        self.apply_all_algorithm_configs()

        print("视频源检测器初始化完成")

    def update_processed_frame(self, source_id: int, frame, source_version=None):
        """缓存每路视频源的最新展示帧。"""
        if frame is None:
            return False

        with self.frame_lock:
            current_source_version = self.processed_source_versions.get(source_id, -1)
            if source_version is not None and source_version < current_source_version:
                return False

            self.latest_processed_frames[source_id] = frame.copy()
            self.processed_frame_versions[source_id] = self.processed_frame_versions.get(source_id, 0) + 1
            if source_version is not None:
                self.processed_source_versions[source_id] = source_version
            return True

    def get_processed_frame(self, source_id: int):
        """获取指定视频源的最新展示帧。"""
        with self.frame_lock:
            frame = self.latest_processed_frames.get(source_id)
            return None if frame is None else frame.copy()

    def get_processed_frame_packet(self, source_id: int):
        """返回最新展示帧和版本号，供预览端跳过重复旧帧。"""
        with self.frame_lock:
            frame = self.latest_processed_frames.get(source_id)
            if frame is None:
                return None, None
            return frame.copy(), self.processed_frame_versions.get(source_id, 0)

    def update_raw_frame(self, source_id: int, frame):
        """只保留最新原始帧，检测线程始终消费最新数据。"""
        if frame is None:
            return

        with self.frame_lock:
            self.latest_raw_frames[source_id] = frame.copy()
            self.raw_frame_versions[source_id] = self.raw_frame_versions.get(source_id, 0) + 1

    def get_raw_frame_packet(self, source_id: int):
        """返回最新原始帧和版本号。"""
        with self.frame_lock:
            frame = self.latest_raw_frames.get(source_id)
            if frame is None:
                return None, None
            return frame.copy(), self.raw_frame_versions.get(source_id, 0)

    def clear_processed_frame(self, source_id: int):
        """清理指定视频源的展示帧缓存。"""
        with self.frame_lock:
            self.latest_processed_frames.pop(source_id, None)
            self.processed_frame_versions.pop(source_id, None)
            self.processed_source_versions.pop(source_id, None)
            self.latest_raw_frames.pop(source_id, None)
            self.raw_frame_versions.pop(source_id, None)

    def _open_video_capture(self, video_url: str, transport: str = 'tcp'):
        """以低延迟参数打开视频源，优先降低 RTSP 缓冲。"""
        is_network_stream = isinstance(video_url, str) and video_url.lower().startswith(
            ('rtsp://', 'rtmp://', 'http://', 'https://')
        )

        previous_ffmpeg_options = os.environ.get('OPENCV_FFMPEG_CAPTURE_OPTIONS')
        try:
            if is_network_stream:
                if isinstance(video_url, str) and video_url.lower().startswith('rtmp://'):
                    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
                        'rtmp_live;live|fflags;nobuffer|flags;low_delay|max_delay;500000'
                    )
                else:
                    rtsp_transport = 'udp' if str(transport).lower() == 'udp' else 'tcp'
                    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
                        f'rtsp_transport;{rtsp_transport}|fflags;nobuffer|flags;low_delay|max_delay;500000'
                    )
                cap = cv2.VideoCapture(video_url, cv2.CAP_FFMPEG)
            else:
                cap = cv2.VideoCapture(video_url)

            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        finally:
            if previous_ffmpeg_options is None:
                os.environ.pop('OPENCV_FFMPEG_CAPTURE_OPTIONS', None)
            else:
                os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = previous_ffmpeg_options

    def _read_latest_frame(self, cap):
        """
        尽量丢弃积压缓冲，只取最新帧，避免 RTSP 画面越看越慢。
        """
        latest_frame = None
        success = False

        # grab 不解码，成本比 read 低，用来尽量丢弃解码缓冲里的旧帧。
        for _ in range(8):
            grabbed = cap.grab()
            if not grabbed:
                break

        success, latest_frame = cap.read()
        return success, latest_frame

    def _create_frame_reader(self, video_url: str, source_config: dict):
        """创建统一读帧器，RK 上优先走 GStreamer MPP 硬解码。"""
        source_config = source_config or {}
        transport = source_config.get('transport') or ('tcp' if source_config.get('tcp_push', True) else 'udp')
        codec = (
            source_config.get('algorithm_input_codec')
            or source_config.get('codec')
            or os.getenv('SAFEHAT_ALGORITHM_INPUT_CODEC', 'h264')
        )
        prefer_hardware_decode = str(
            source_config.get(
                'hardware_decode',
                os.getenv('SAFEHAT_MPP_DECODE_ENABLED', '1')
            )
        ).strip().lower() not in {'0', 'false', 'no', 'off'}
        allow_fallback = str(
            source_config.get(
                'hardware_decode_fallback',
                os.getenv('SAFEHAT_MPP_DECODE_FALLBACK', '1')
            )
        ).strip().lower() not in {'0', 'false', 'no', 'off'}

        return create_frame_reader(
            video_url,
            ReaderOptions(
                transport=str(transport or 'tcp'),
                codec=str(codec or 'h264'),
                prefer_hardware_decode=prefer_hardware_decode,
                allow_fallback=allow_fallback,
            )
        )

    def _default_algorithm_configs(self):
        """默认算法配置"""
        return {
            'helmet_detection': {'confidence_threshold': 0.5},
            'vest_detection': {'confidence_threshold': 0.5},
            'smoke_detection': {'confidence_threshold': 0.5},
            'fire_detection': {'confidence_threshold': 0.5},
            'fall_detection': {'confidence_threshold': 0.5, 'fall_duration_threshold': 2.0},
            'mask_detection': {'confidence_threshold': 0.5},
            'intrusion_detection': {'confidence_threshold': 0.5},
            'crowd_gathering_detection': {'confidence_threshold': 0.5, 'min_people': 3, 'duration_threshold': 10.0},
            'person_absence_detection': {'confidence_threshold': 0.5, 'absence_duration_threshold': 10.0},
            'vehicle_parking_detection': {'confidence_threshold': 0.5, 'parking_duration_threshold': 30.0},
            'fire_extinguisher_absence_detection': {'confidence_threshold': 0.5, 'absence_duration_threshold': 10.0},
            'reagent_bottle_detection': {'confidence_threshold': 0.5},
            'camera_occlusion_detection': {'confidence_threshold': 0.2},
            'fight_detection': {'confidence_threshold': 0.5, 'fight_duration_threshold': 5.0},
            'glove_detection': {'confidence_threshold': 0.5},
            'crack_detection': {'confidence_threshold': 0.5},
            'dog_detection': {'confidence_threshold': 0.5},
            'cat_detection': {'confidence_threshold': 0.5},
            'fatigue_driving_detection': {'confidence_threshold': 0.5},
            'trash_detection': {'confidence_threshold': 0.5},
            'electric_bicycle_detection': {'confidence_threshold': 0.5},
            'theft_detection': {'confidence_threshold': 0.5},
            'work_clothes_detection': {'confidence_threshold': 0.5},
            'safehat_vest_detection': {'confidence_threshold': 0.5},
            'gas_cylinder_tilt_detection': {'confidence_threshold': 0.5},
            'box_object_detection': {'confidence_threshold': 0.5},
        }

    def _load_algorithm_configs(self):
        """加载算法配置"""
        defaults = self._default_algorithm_configs()
        try:
            config_path = self.algorithm_config_path
            if not config_path.exists() and self.legacy_algorithm_config_path.exists():
                config_path = self.legacy_algorithm_config_path

            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                for key, value in loaded.items():
                    if isinstance(value, dict):
                        defaults.setdefault(key, {}).update(value)
        except Exception as e:
            print(f"加载算法配置失败: {e}")
        return defaults

    def _save_algorithm_configs(self):
        """保存算法配置"""
        try:
            self.algorithm_config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.algorithm_config_path, 'w', encoding='utf-8') as f:
                json.dump(self.algorithm_configs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存算法配置失败: {e}")

    def get_algorithm_configs(self):
        """获取全部算法配置"""
        return json.loads(json.dumps(self.algorithm_configs))

    def get_algorithm_config(self, algorithm_name: str):
        """获取单个算法配置"""
        return self.algorithm_configs.get(algorithm_name, {}).copy()

    def update_algorithm_config(self, algorithm_name: str, config: dict):
        """更新单个算法配置并实时下发到检测器"""
        if algorithm_name not in self.detector_map:
            raise ValueError(f"不支持的算法类型: {algorithm_name}")

        current = self.algorithm_configs.get(algorithm_name, {}).copy()
        current.update(config or {})
        self.algorithm_configs[algorithm_name] = current
        self._save_algorithm_configs()
        self._apply_config_to_detector(algorithm_name, self.detector_map[algorithm_name], current)
        return current.copy()

    def update_all_detector_configs(self, configs: dict):
        """兼容 app.py 现有调用：批量更新并立即应用全部配置"""
        if not isinstance(configs, dict):
            return

        updated_algorithms = []
        for algorithm_name, config in configs.items():
            if algorithm_name not in self.detector_map or not isinstance(config, dict):
                continue
            current = self.algorithm_configs.get(algorithm_name, {}).copy()
            current.update(config)
            self.algorithm_configs[algorithm_name] = current
            updated_algorithms.append(algorithm_name)

        self._save_algorithm_configs()
        for algorithm_name in updated_algorithms:
            self._apply_config_to_detector(
                algorithm_name,
                self.detector_map[algorithm_name],
                self.algorithm_configs.get(algorithm_name, {})
            )

    def apply_all_algorithm_configs(self):
        """将配置应用到所有共享检测器"""
        for algorithm_name, detector in self.detector_map.items():
            self._apply_config_to_detector(
                algorithm_name,
                detector,
                self.algorithm_configs.get(algorithm_name, {})
            )

    def _get_detector_lock(self, algorithm_name: str):
        lock = self.detector_locks.get(algorithm_name)
        if lock is None:
            lock = threading.RLock()
            self.detector_locks[algorithm_name] = lock
        return lock

    def _run_detector_operation(self, algorithm_name: str, operation):
        with self._get_detector_lock(algorithm_name):
            return operation()

    def _register_stream_resource(self, source_id: int, cap, reader_thread, reader_state):
        with self.lifecycle_lock:
            self.stream_resources[source_id] = {
                "cap": cap,
                "reader_thread": reader_thread,
                "reader_state": reader_state,
            }

    def _pop_stream_resource(self, source_id: int):
        return self.stream_resources.pop(source_id, None)

    def _apply_config_to_detector(self, algorithm_name, detector, config):
        """按检测器实现差异下发配置"""
        if not config or detector is None:
            return

        try:
            with self._get_detector_lock(algorithm_name):
                if algorithm_name == 'fight_detection':
                    detector.update_config(config)
                    return

                if algorithm_name == 'crowd_gathering_detection':
                    confidence = config.get('confidence_threshold')
                    if confidence is not None:
                        detector.confidence_threshold = max(0.1, min(0.95, float(confidence)))
                    detector.update_config(
                        min_people=config.get('min_people'),
                        duration_threshold=config.get('duration_threshold')
                    )
                    return

                if algorithm_name == 'person_absence_detection':
                    confidence = config.get('confidence_threshold')
                    if confidence is not None:
                        detector.confidence_threshold = max(0.1, min(0.95, float(confidence)))
                    detector.update_config(
                        absence_duration_threshold=config.get('absence_duration_threshold')
                    )
                    return

                if hasattr(detector, 'update_config'):
                    detector.update_config(**config)
        except TypeError:
            confidence = config.get('confidence_threshold')
            if confidence is not None and hasattr(detector, 'confidence_threshold'):
                with self._get_detector_lock(algorithm_name):
                    detector.confidence_threshold = max(0.1, min(0.95, float(confidence)))
        except Exception as e:
            print(f"应用算法配置失败 [{algorithm_name}]: {e}")

    def _get_all_active_algorithms(self):
        """获取所有活跃视频源正在使用的算法集合"""
        active_algorithms = set()
        try:
            if self.video_source_path.exists():
                with open(self.video_source_path, 'r', encoding='utf-8') as f:
                    sources = json.load(f)

                for source in sources:
                    # 只统计启用的视频源
                    if source.get('enabled', True):
                        for algorithm in source.get('algorithms', []):
                            active_algorithms.add(algorithm)
        except Exception as e:
            print(f"获取活跃算法失败: {str(e)}")

        return active_algorithms

    def _resolve_alert_display_fields(self, source_id: int, fallback_name: str):
        """解析告警展示字段：通道号与地点。"""
        channel_name = str(source_id)
        location_name = (fallback_name or '').strip() or str(source_id)

        try:
            if not self.video_source_path.exists():
                return channel_name, location_name

            with open(self.video_source_path, 'r', encoding='utf-8') as f:
                sources = json.load(f)

            source = next((s for s in sources if int(s.get('id', -1)) == int(source_id)), None)
            if not source:
                return channel_name, location_name

            source_config = source.get('config', {}) or {}
            location_name = (
                str(source.get('description') or source.get('name') or fallback_name or source_id).strip()
                or str(source_id)
            )

            for candidate in (
                source_config.get('camera_id'),
                source_config.get('stream_id'),
                source_config.get('file_id'),
                source_config.get('channel_no'),
                source_config.get('channel_code'),
                source.get('id'),
            ):
                value = str(candidate or '').strip()
                if value:
                    channel_name = value
                    break
        except Exception:
            pass

        return channel_name, location_name

    def _sync_detector_states(self):
        """
        根据所有活跃视频源的算法配置，同步检测器启用状态

        注意：此方法仅在没有检测循环运行时调用（系统启动/停止/重启时）
        避免在检测循环运行时调用，否则可能导致正在运行的检测被中断
        """
        active_algorithms = self._get_all_active_algorithms()

        # 算法到检测器的映射
        # ⚠️ 只在所有检测线程都已停止时才禁用检测器
        # 否则可能会导致正在运行的检测循环无法生成告警
        with self.lifecycle_lock:
            has_running_threads = any(thread.is_alive() for thread in self.detection_threads.values())

        # 同步每个检测器的状态
        for algorithm, detector in self.detector_map.items():
            should_enable = algorithm in active_algorithms

            if should_enable and not detector.is_enabled:
                detector.enable()
                print(f"启用检测器: {algorithm}")
            elif not should_enable and detector.is_enabled and not has_running_threads:
                # ✅ 只有在没有运行中的检测线程时才禁用检测器
                detector.disable()
                print(f"禁用检测器: {algorithm}")

    def start_detection_for_source(self, source_id: int, video_url: str,
                                   algorithms: list, source_name: str = "",
                                   source_config=None):
        """
        为指定视频源启动检测线程

        Args:
            source_id: 视频源ID
            video_url: 视频地址
            algorithms: 启用的算法列表 ['helmet_detection', 'vest_detection']
            source_name: 视频源名称
        """
        with self.lifecycle_lock:
            # 如果已经在检测，先停止
            if source_id in self.detection_threads:
                stopped = self.stop_detection_for_source(source_id, join_timeout=5.0)
                if not stopped:
                    print(f"视频源检测停止超时，放弃重启 - ID: {source_id}, 名称: {source_name}")
                    return False

            # 设置停止标志为False
            self.stop_flags[source_id] = False

            # 启动检测线程
            thread = threading.Thread(
                target=self._detection_loop,
                args=(source_id, video_url, algorithms, source_name, source_config or {}),
                daemon=True,
                name=f"VideoDetector-{source_id}"
            )
            thread.start()
            self.detection_threads[source_id] = thread

            print(f"视频源检测已启动 - ID: {source_id}, 名称: {source_name}")
            return True

    def stop_detection_for_source(self, source_id: int, join_timeout: float = 3.0):
        """停止指定视频源的检测"""
        with self.lifecycle_lock:
            if source_id in self.stop_flags:
                self.stop_flags[source_id] = True

            resource = self.stream_resources.get(source_id)
            if resource:
                reader_state = resource.get("reader_state") or {}
                reader_state["stream_failed"] = True
                reader_state["stopped"] = True

            thread = self.detection_threads.get(source_id)
            if thread and thread.is_alive():
                # 避免线程自 join 造成死锁
                if thread is not threading.current_thread():
                    thread.join(timeout=join_timeout)
                if thread.is_alive():
                    print(f"视频源检测停止超时 - ID: {source_id}")
                    return False

            self.detection_threads.pop(source_id, None)
            self.stop_flags.pop(source_id, None)
            self.stream_resources.pop(source_id, None)
            self.clear_processed_frame(source_id)

            print(f"视频源检测已停止 - ID: {source_id}")
            return True

    def stop_all_detections(self):
        """停止所有视频源的检测"""
        with self.lifecycle_lock:
            source_ids = list(self.detection_threads.keys())
        for source_id in source_ids:
            self.stop_detection_for_source(source_id, join_timeout=5.0)

        # 同步检测器状态（应该全部禁用，因为没有活跃的视频源）
        self._sync_detector_states()
        print("所有视频源检测已停止")

    def _submit_theft_llm_task(self, source_id, source_name, source_channel, source_location, person_count, processed_frame, fallback_confidence):
        """串行提交偷盗大模型任务，前一个未完成时后续任务排队等待。"""
        with self.theft_llm_lock:
            task = self.theft_llm_tasks.get(source_id)
            if task:
                if task.get("status") in {"queued", "running"}:
                    return False

            self.theft_llm_tasks[source_id] = {
                "status": "queued",
                "started_at": time.time(),
            }
        task_payload = {
            "source_id": source_id,
            "source_name": source_name,
            "source_channel": source_channel,
            "source_location": source_location,
            "person_count": person_count,
            "snapshot_frame": processed_frame.copy(),
            "fallback_confidence": fallback_confidence,
        }
        self.theft_llm_queue.put(task_payload)
        print(f"[{source_name}] theft llm task queued")
        return True

    def _theft_llm_worker_loop(self):
        """单线程串行消费偷盗 LLM 任务，避免多个模型推理并发执行。"""
        while True:
            task = self.theft_llm_queue.get()
            if task is None:
                self.theft_llm_queue.task_done()
                break

            source_id = task.get("source_id")
            source_name = task.get("source_name", f"source_{source_id}")
            with self.theft_llm_lock:
                if source_id in self.theft_llm_tasks:
                    self.theft_llm_tasks[source_id]["status"] = "running"
            print(f"[{source_name}] theft llm worker start")
            try:
                self._run_theft_llm_task(
                    source_id=source_id,
                    source_name=source_name,
                    source_channel=task.get("source_channel"),
                    source_location=task.get("source_location"),
                    person_count=task.get("person_count", 0),
                    snapshot_frame=task.get("snapshot_frame"),
                    fallback_confidence=task.get("fallback_confidence", 0.92),
                )
            finally:
                self.theft_llm_queue.task_done()
                print(f"[{source_name}] theft llm worker done")

    def _run_theft_llm_task(self, source_id, source_name, source_channel, source_location, person_count, snapshot_frame, fallback_confidence):
        """读取缓存帧并执行偷盗 LLM 判定，完成后写入结果和告警。"""
        try:
            cache_data = self.manager.get_theft_frame_cache(source_id=source_id).get("cache")
            llm_result = self.theft_detector.judge_with_llm(
                cache_payload=cache_data or {},
                endpoint="",
                timeout=20,
                extra={"trigger": "auto_10_frames"}
            )
            self.manager.add_theft_llm_result({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_id": source_id,
                "channel_name": source_channel,
                "location": source_location,
                "person_count": person_count,
                "frame_count": len((cache_data or {}).get("frames") or []),
                "algorithm": "theft_detection",
                "is_theft": llm_result.get("is_theft", True),
                "score": llm_result.get("score"),
                "reason": llm_result.get("reason") or llm_result.get("detail") or llm_result.get("error", ""),
                "mode": llm_result.get("mode", "mock"),
                "raw": llm_result,
            })

            if llm_result.get("is_theft") is True:
                detection_type = "theft_violation"
                result_data = {
                    "info": f"LLM判定偷盗行为：检测到 {person_count} 人",
                    "status": "偷盗行为",
                    "source": source_location,
                    "location": source_location,
                    "channel_name": source_channel,
                    "algorithm": "theft_detection",
                    "confidence": llm_result.get("score") or fallback_confidence,
                    "person_count": person_count,
                    "llm_reason": llm_result.get("reason") or llm_result.get("detail") or llm_result.get("error", ""),
                }
                h, w = snapshot_frame.shape[:2]
                cv2.putText(snapshot_frame, "LLM THEFT CONFIRMED", (int(w/2) - 190, 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
                cv2.putText(snapshot_frame, f"Persons: {person_count}", (int(w/2) - 90, 90),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                self.manager._create_alert_record(
                    detection_type,
                    result_data,
                    snapshot_frame
                )

            print(
                f"[{source_name}] theft llm result: "
                f"is_theft={llm_result.get('is_theft')}, "
                f"score={llm_result.get('score')}, "
                f"reason={llm_result.get('reason') or llm_result.get('detail') or llm_result.get('error', '')}"
            )
        except Exception as e:
            print(f"[{source_name}] theft llm task error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            with self.theft_llm_lock:
                self.theft_llm_tasks.pop(source_id, None)

    def restart_all_from_config(self):
        """从配置文件重新加载并启动所有启用的视频源检测"""
        with self.lifecycle_lock:
            # 先停止所有现有检测
            self.stop_all_detections()

        # 加载视频源配置
        if not self.video_source_path.exists():
            print("视频源配置文件不存在，未启动任何检测")
            return

        try:
            with open(self.video_source_path, 'r', encoding='utf-8') as f:
                sources = json.load(f)

            if not sources:
                print("视频源列表为空，未启动任何检测")
                return

            # 启动所有启用且有地址的视频源。算法为空时也启动读帧链路，用于实时预览。
            started_count = 0
            for source in sources:
                if not source.get('enabled', True):
                    print(f"视频源 [{source.get('name', source.get('id'))}] 未启用，跳过")
                    continue

                algorithms = source.get('algorithms', [])

                source_id = source.get('id')
                video_url = source.get('play_url') or source.get('video_url') or source.get('origin_url')
                source_name = source.get('name', f'视频源{source_id}')
                source_config = source.get('config', {}) or {}

                # 启动时按当前策略刷新运行时 URL：网络源默认经 ZLM 统一成内部 RTSP。
                try:
                    from server.services.video_source_service import video_source_service
                    refreshed_source = video_source_service.refresh_runtime_urls(source_id)
                    if refreshed_source:
                        source = refreshed_source
                        video_url = source.get('play_url') or source.get('video_url') or source.get('origin_url')
                        source_config = source.get('config', {}) or {}
                except Exception as exc:
                    print(f"刷新视频源运行时地址失败 [{source_name}]: {exc}")

                if str(source_config.get('brand', '')).strip().lower() == 'ezviz':
                    try:
                        from server.services.video_source_service import VideoSource, video_source_service
                        fresh_source = VideoSource.from_dict(source)
                        video_url = video_source_service._derive_video_url(fresh_source)
                    except Exception as exc:
                        print(f"Ezviz play url resolve failed [{source_name}]: {exc}")
                        video_url = ''

                if not video_url:
                    print(f"视频源 [{source_name}] 未配置视频地址，跳过检测")
                    continue

                if source_id and video_url:
                    self.start_detection_for_source(
                        source_id, video_url, algorithms, source_name, source_config
                    )
                    started_count += 1

            # 同步检测器状态
            self._sync_detector_states()

            if started_count > 0:
                print(f"已启动 {started_count} 个视频源的检测")
            else:
                print("没有启动任何视频源读帧任务（所有视频源均未启用或未配置视频地址）")

        except Exception as e:
            print(f"加载视频源配置失败: {str(e)}")

    def _detection_loop(self, source_id: int, video_url: str,
                       algorithms: list, source_name: str, source_config=None):
        """
        检测循环 - 在后台线程中运行

        Args:
            source_id: 视频源ID
            video_url: 视频地址
            algorithms: 启用的算法列表
            source_name: 视频源名称
        """
        print(f"开始检测循环 - {source_name} ({video_url})")
        source_channel, source_location = self._resolve_alert_display_fields(source_id, source_name)

        frame_reader = None
        frame_pusher = None
        reader_thread = None
        frame_count = 0
        last_alert_time = {}  # 记录上次告警时间，避免频繁告警
        alert_interval = 5  # 同一类型告警间隔5秒
        theft_recent_frames = deque(maxlen=10)
        theft_last_llm_time = 0.0
        theft_detected_frame_count = 0
        theft_last_submit_count = 0

        try:
            # 打开视频源。网络流优先使用 ZLM 统一 RTSP + GStreamer MPP 硬解码；
            # 不满足条件或硬解不可用时回退到 OpenCV/FFmpeg。
            source_config = source_config or {}
            frame_reader = self._create_frame_reader(video_url, source_config)
            if not frame_reader.is_opened():
                print(f"无法打开视频源: {source_name}")
                return
            frame_pusher = create_processed_frame_pusher(source_id, source_config)

            is_local_file = video_url.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))
            file_fps = 0.0
            file_frame_interval = 0.0
            if is_local_file:
                file_fps = float(frame_reader.get(cv2.CAP_PROP_FPS) or 0.0)
                if file_fps <= 0 or file_fps > 240:
                    file_fps = 25.0
                file_frame_interval = max(1.0 / file_fps, 0.001)
            reader_state = {
                "stopped": False,
                "stream_failed": False,
            }
            next_file_frame_time = time.perf_counter()

            def reader_loop():
                next_frame_time = time.perf_counter()
                _r_fps_count = 0
                _r_fps_start = time.time()
                while not self.stop_flags.get(source_id, False):
                    if is_local_file:
                        now = time.perf_counter()
                        if now < next_frame_time:
                            time.sleep(min(next_frame_time - now, 0.01))

                        ret, raw_frame = frame_reader.read()
                        if ret and raw_frame is not None:
                            next_frame_time = max(next_frame_time + file_frame_interval, time.perf_counter())
                        else:
                            next_frame_time = time.perf_counter()
                    else:
                        # 直接 read() 而非 read_latest()。
                        # read_latest() 内部会 grab() × 8，每次 grab 都阻塞等下一帧，
                        # 对 30 FPS 摄像头实测结果是 8×33ms=264ms/帧 → 只有 ~3 FPS。
                        # 跳帧逻辑由检测线程通过版本号比较完成，reader 无需主动丢帧。
                        ret, raw_frame = frame_reader.read()

                    if ret and raw_frame is not None:
                        self.update_raw_frame(source_id, raw_frame)
                        # 未启用算法时直接预览原图；启用算法时只由检测线程写展示帧，避免检测框被原图覆盖。
                        if not algorithms:
                            raw_version = self.raw_frame_versions.get(source_id, 0)
                            self.update_processed_frame(source_id, raw_frame, raw_version)
                        _r_fps_count += 1
                        _r_fps_now = time.time()
                        _r_fps_elapsed = _r_fps_now - _r_fps_start
                        if _r_fps_elapsed >= 5.0:
                            print(f"[FPS][读帧] {source_name} - reader线程读取: {_r_fps_count / _r_fps_elapsed:.1f} FPS")
                            _r_fps_count = 0
                            _r_fps_start = _r_fps_now
                        continue

                    if is_local_file:
                        frame_reader.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        time.sleep(0.005)
                        continue

                    reader_state["stream_failed"] = True
                    break

                reader_state["stopped"] = True

            reader_thread = threading.Thread(
                target=reader_loop,
                daemon=True,
                name=f"VideoReader-{source_id}"
            )
            self._register_stream_resource(source_id, frame_reader, reader_thread, reader_state)
            if not is_local_file:
                reader_thread.start()

            # 启用对应的检测器
            helmet_enabled = 'helmet_detection' in algorithms
            vest_enabled = 'vest_detection' in algorithms
            fall_enabled = 'fall_detection' in algorithms
            mask_enabled = 'mask_detection' in algorithms
            intrusion_enabled = 'intrusion_detection' in algorithms
            smoke_enabled = 'smoke_detection' in algorithms
            fire_enabled = 'fire_detection' in algorithms
            crowd_gathering_enabled = 'crowd_gathering_detection' in algorithms
            person_absence_enabled = 'person_absence_detection' in algorithms
            vehicle_parking_enabled = 'vehicle_parking_detection' in algorithms
            fire_extinguisher_absence_enabled = 'fire_extinguisher_absence_detection' in algorithms
            reagent_bottle_enabled = 'reagent_bottle_detection' in algorithms
            camera_occlusion_enabled = 'camera_occlusion_detection' in algorithms
            fight_enabled = 'fight_detection' in algorithms
            glove_enabled = 'glove_detection' in algorithms
            crack_enabled = 'crack_detection' in algorithms
            dog_enabled = 'dog_detection' in algorithms
            cat_enabled = 'cat_detection' in algorithms
            fatigue_driving_enabled = 'fatigue_driving_detection' in algorithms
            trash_enabled = 'trash_detection' in algorithms
            electric_bicycle_enabled = 'electric_bicycle_detection' in algorithms
            theft_enabled = 'theft_detection' in algorithms
            work_clothes_enabled = 'work_clothes_detection' in algorithms
            safehat_vest_enabled = 'safehat_vest_detection' in algorithms
            gas_cylinder_tilt_enabled = 'gas_cylinder_tilt_detection' in algorithms
            box_object_enabled = 'box_object_detection' in algorithms

            # ✅ 根据算法配置启用/禁用检测器
            # 注意：这里只管理本视频源需要的检测器，不影响其他视频源
            # 检测器的 is_enabled 由自身逻辑管理，这里只是确保需要的检测器已启用
            if helmet_enabled:
                self.helmet_detector.enable()

            if vest_enabled:
                self.vest_detector.enable()

            if fall_enabled:
                self.fall_detector.enable()

            if mask_enabled:
                self.mask_detector.enable()

            if intrusion_enabled:
                self.person_intrusion_detector.enable()

            if smoke_enabled:
                self.smoke_detector.enable()

            if fire_enabled:
                self.fire_detector.enable()

            if crowd_gathering_enabled:
                self.crowd_gathering_detector.enable()

            if person_absence_enabled:
                self.person_absence_detector.enable()

            if vehicle_parking_enabled:
                self.vehicle_parking_detector.enable()

            if fire_extinguisher_absence_enabled:
                self.fire_extinguisher_absence_detector.enable()

            if reagent_bottle_enabled:
                self.reagent_bottle_detector.enable()

            if camera_occlusion_enabled:
                self.camera_occlusion_detector.enable()

            if fight_enabled:
                self.fight_detector.enable()

            if glove_enabled:
                self.glove_detector.enable()

            if crack_enabled:
                self.crack_detector.enable()

            if dog_enabled:
                self.dog_detector.enable()

            if cat_enabled:
                self.cat_detector.enable()

            if fatigue_driving_enabled:
                self.fatigue_driving_detector.enable()

            if trash_enabled:
                self.trash_detector.enable()

            if electric_bicycle_enabled:
                self.electric_bicycle_detector.enable()

            if theft_enabled:
                self.theft_detector.enable()

            if work_clothes_enabled:
                self.work_clothes_detector.enable()

            if safehat_vest_enabled:
                self.safehat_vest_detector.enable()

            if gas_cylinder_tilt_enabled:
                self.gas_cylinder_tilt_detector.enable()

            if box_object_enabled:
                self.box_object_detector.enable()

            print(f"检测器状态 - 安全帽: {helmet_enabled}, 反光衣: {vest_enabled}, 跌倒: {fall_enabled}, 口罩: {mask_enabled}, 入侵: {intrusion_enabled}, 烟雾: {smoke_enabled}, 火灾: {fire_enabled}, 聚集: {crowd_gathering_enabled}, 离岗: {person_absence_enabled}, 违停: {vehicle_parking_enabled}, 灭火器: {fire_extinguisher_absence_enabled}, 试剂瓶: {reagent_bottle_enabled}, 摄像头遮挡: {camera_occlusion_enabled}, 打架: {fight_enabled}, 手套: {glove_enabled}, 裂缝: {crack_enabled}, 狗: {dog_enabled}, 猫: {cat_enabled}, 疲劳驾驶: {fatigue_driving_enabled}, 垃圾: {trash_enabled}, 电瓶车: {electric_bicycle_enabled}, 工作服: {work_clothes_enabled}, 安全帽反光衣: {safehat_vest_enabled}, 钢瓶倾倒: {gas_cylinder_tilt_enabled}, 箱上放物: {box_object_enabled}")

            previous_raw_version = -1

            # FPS 统计（每5秒输出一次检测帧率）
            _fps_count = 0
            _fps_start = time.time()

            # 主检测循环
            while not self.stop_flags.get(source_id, False):
                # 定期检查算法配置（每100帧检查一次）
                if frame_count % 100 == 0:
                    # 重新加载视频源配置，检查算法是否变化
                    try:
                        if self.video_source_path.exists():
                            with open(self.video_source_path, 'r', encoding='utf-8') as f:
                                sources = json.load(f)
                            current_source = next((s for s in sources if s.get('id') == source_id), None)
                            if current_source:
                                current_algorithms = current_source.get('algorithms', [])
                                current_enabled = current_source.get('enabled', True)

                                # 视频源禁用时停止；算法清空时保留读帧链路以继续实时预览。
                                if not current_enabled:
                                    print(f"视频源 [{source_name}] 已被禁用，停止检测")
                                    break

                                # 如果算法配置改变，更新检测器状态
                                if set(current_algorithms) != set(algorithms):
                                    print(f"视频源 [{source_name}] 算法配置已改变，将由系统重启检测")
                                    break
                    except Exception as e:
                        print(f"检查算法配置失败: {str(e)}")

                if is_local_file:
                    now = time.perf_counter()
                    if now < next_file_frame_time:
                        time.sleep(min(next_file_frame_time - now, 0.01))

                    ret, frame = frame_reader.read()
                    if not ret or frame is None:
                        frame_reader.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        next_file_frame_time = time.perf_counter()
                        continue

                    next_file_frame_time = max(
                        next_file_frame_time + file_frame_interval,
                        time.perf_counter()
                    )
                else:
                    frame, raw_version = self.get_raw_frame_packet(source_id)
                    if frame is None:
                        if reader_state["stream_failed"] or reader_state["stopped"]:
                            print(f"读取视频帧失败: {source_name}")
                            break
                        time.sleep(0.005)
                        continue

                    if raw_version == previous_raw_version:
                        if reader_state["stream_failed"] or reader_state["stopped"]:
                            print(f"读取视频帧失败: {source_name}")
                            break
                        time.sleep(0.002)
                        continue

                    previous_raw_version = raw_version

                frame_count += 1

                # 每5秒打印一次检测端帧率
                _fps_count += 1
                _fps_now = time.time()
                _fps_elapsed = _fps_now - _fps_start
                if _fps_elapsed >= 5.0:
                    print(f"[FPS][检测] {source_name} - 检测处理: {_fps_count / _fps_elapsed:.1f} FPS")
                    _fps_count = 0
                    _fps_start = _fps_now

                try:
                    # 执行检测
                    current_time = time.time()
                    display_frame = frame.copy()
                    # 只有 theft 算法启用时才缓存帧，避免无用拷贝
                    if theft_enabled:
                        theft_recent_frames.append((current_time, frame.copy()))

                    # 1. 执行人员跌倒检测（使用独立的模型）
                    # 1. 人员跌倒检测
                    if fall_enabled:
                        (processed_frame_fall, _, _), confirmed_falls = self._run_detector_operation(
                            'fall_detection',
                            lambda: (
                                self.fall_detector.detect(display_frame),
                                self.fall_detector.check_confirmed_falls()
                            )
                        )
                        display_frame = processed_frame_fall.copy()

                        # 持续超过阈值的跌倒轨迹才告警

                        for fall_info in confirmed_falls:
                            detection_type = "fall_violation"
                            track_key = f"{detection_type}_{fall_info['track_id']}"
                            if track_key in last_alert_time:
                                continue

                            result_data = {
                                "info": f"确认人员跌倒，持续 {fall_info['duration']:.1f} 秒",
                                "status": "跌倒",
                                "source": source_location,
                                "location": source_location,
                                "channel_name": source_channel,
                                "algorithm": "fall_detection",
                                "confidence": 0.95
                            }

                            snapshot = processed_frame_fall.copy()
                            px, py = fall_info['position']
                            cv2.putText(snapshot, "FALL DETECTED!", (int(px) - 100, int(py) - 70),
                                      cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 4)
                            cv2.putText(snapshot, f"Duration: {fall_info['duration']:.1f}s", (int(px) - 80, int(py) - 30),
                                      cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[track_key] = current_time
                            print(f"[{source_name}] 跌倒告警: 持续 {fall_info['duration']:.1f} 秒")

                    # 2. 安全帽 / 反光衣
                    if helmet_enabled:
                        processed_frame_helmet, _, helmet_stats = self._run_detector_operation('helmet_detection', lambda: self.helmet_detector.detect(display_frame))
                        display_frame = processed_frame_helmet.copy()

                        no_helmet_count = helmet_stats.get("no_helmet", 0)
                        if no_helmet_count > 0:
                            detection_type = "helmet_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": f"检测到 {no_helmet_count} 人未佩戴安全帽",
                                    "status": "未戴安全帽",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "helmet_detection",
                                    "confidence": 0.95,
                                    "count": no_helmet_count
                                }

                                snapshot = processed_frame_helmet.copy()
                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 安全帽告警: {result_data['info']}")

                    if vest_enabled:
                        processed_frame_vest, _, vest_stats = self._run_detector_operation('vest_detection', lambda: self.vest_detector.detect(display_frame))
                        display_frame = processed_frame_vest.copy()

                        no_vest_count = vest_stats.get("no_vest", 0)
                        if no_vest_count > 0:
                            detection_type = "vest_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": f"检测到 {no_vest_count} 人未穿反光衣",
                                    "status": "未穿反光衣",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "vest_detection",
                                    "confidence": 0.95,
                                    "count": no_vest_count
                                }

                                snapshot = processed_frame_vest.copy()
                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 反光衣告警: {result_data['info']}")

                    # 3. 口罩
                    if mask_enabled:
                        processed_frame_mask, _, mask_frame_detections = self._run_detector_operation('mask_detection', lambda: self.mask_detector.detect(display_frame))
                        display_frame = processed_frame_mask.copy()
                        has_no_mask_violation = (
                            mask_frame_detections.get("mask", 0) == 0 and np.mean(frame) > 10
                        )

                        if has_no_mask_violation:
                            detection_type = "mask_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": "检测到未佩戴口罩",
                                    "status": "未戴口罩",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "mask_detection",
                                    "confidence": 0.90
                                }

                                snapshot = processed_frame_mask.copy()
                                h, w = snapshot.shape[:2]
                                cv2.putText(snapshot, "NO MASK DETECTED!", (int(w/2) - 150, 50),
                                          cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
                                cv2.putText(snapshot, "Please wear a mask!", (int(w/2) - 120, 90),
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 口罩告警: 未佩戴口罩")

                    # 4. 区域入侵
                    if intrusion_enabled:
                        processed_frame_intrusion, _, intrusion_frame_detections, intrusion_alerts = self._run_detector_operation('intrusion_detection', lambda: self.person_intrusion_detector.detect(display_frame))
                        display_frame = processed_frame_intrusion.copy()
                        has_intrusion = bool(intrusion_alerts)
                        person_count = intrusion_frame_detections.get("person", 0)

                        if has_intrusion and person_count > 0:
                            detection_type = "intrusion_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": f"区域入侵：检测到 {person_count} 人",
                                    "status": "区域入侵",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "intrusion_detection",
                                    "confidence": 0.92,
                                    "person_count": person_count
                                }

                                snapshot = processed_frame_intrusion.copy()
                                h, w = snapshot.shape[:2]
                                cv2.putText(snapshot, f"INTRUSION ALERT! {person_count} PERSON(S)", (int(w/2) - 200, 50),
                                          cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
                                cv2.putText(snapshot, "Unauthorized Access Detected", (int(w/2) - 180, 90),
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 入侵告警: {person_count} 人")


                    if theft_enabled:
                        processed_frame_theft, _, theft_frame_detections, theft_alerts = self._run_detector_operation(
                            'theft_detection',
                            lambda: self.theft_detector.detect(display_frame)
                        )
                        display_frame = processed_frame_theft.copy()
                        has_theft = bool(theft_alerts)
                        person_count = theft_frame_detections.get("person", 0)

                        if has_theft and person_count > 0:
                            theft_detected_frame_count += 1
                            # 先不触发告警/推送，只缓存最近10帧给后续大模型推理
                            self.manager.cache_theft_frames(
                                source_id=source_id,
                                source_name=source_channel,
                                source_location=source_location,
                                frame_items=list(theft_recent_frames),
                                person_count=person_count,
                                confidence=theft_alerts[0].get("confidence", 0.92),
                            )
                            # 凑满10帧后异步调用一次大模型判定，避免阻塞视频流刷新
                            if (
                                len(theft_recent_frames) >= 10
                                and theft_detected_frame_count - theft_last_submit_count >= 10
                                and (current_time - theft_last_llm_time) >= alert_interval
                            ):
                                submitted = self._submit_theft_llm_task(
                                    source_id=source_id,
                                    source_name=source_name,
                                    source_channel=source_channel,
                                    source_location=source_location,
                                    person_count=person_count,
                                    processed_frame=processed_frame_theft,
                                    fallback_confidence=theft_alerts[0].get("confidence", 0.92),
                                )
                                if submitted:
                                    theft_last_llm_time = current_time
                                    theft_last_submit_count = theft_detected_frame_count
                        else:
                            theft_detected_frame_count = 0
                            theft_last_submit_count = 0
                    # 5. 烟雾
                    if smoke_enabled:
                        processed_frame_smoke, _, smoke_frame_detections, smoke_alerts = self._run_detector_operation('smoke_detection', lambda: self.smoke_detector.detect(display_frame))
                        display_frame = processed_frame_smoke.copy()
                        has_smoke = bool(smoke_alerts)
                        smoke_count = smoke_frame_detections.get("smoke", 0)
                        smoke_bboxes = smoke_alerts

                        if has_smoke and smoke_count > 0:
                            detection_type = "smoke_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": f"检测到烟雾，共 {smoke_count} 处",
                                    "status": "烟雾",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "smoke_detection",
                                    "confidence": smoke_bboxes[0]["confidence"] if smoke_bboxes else 0.85
                                }

                                snapshot = processed_frame_smoke.copy()
                                h, w = snapshot.shape[:2]
                                cv2.putText(snapshot, f"SMOKE ALERT! {smoke_count} AREA(S)", (int(w/2) - 180, 50),
                                          cv2.FONT_HERSHEY_SIMPLEX, 1.0, (128, 128, 128), 3)
                                cv2.putText(snapshot, "Smoke Detected - Check Area", (int(w/2) - 170, 90),
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 烟雾告警: {smoke_count} 处")

                    # 6. 火灾
                    if fire_enabled:
                        processed_frame_fire, _, fire_frame_detections, fire_alerts = self._run_detector_operation('fire_detection', lambda: self.fire_detector.detect(display_frame))
                        display_frame = processed_frame_fire.copy()
                        has_fire = bool(fire_alerts)
                        fire_count = fire_frame_detections.get("fire", 0)
                        fire_bboxes = fire_alerts

                        if has_fire and fire_count > 0:
                            detection_type = "fire_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": f"检测到火灾，共 {fire_count} 处",
                                    "status": "火灾",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "fire_detection",
                                    "confidence": fire_bboxes[0]["confidence"] if fire_bboxes else 0.90
                                }

                                snapshot = processed_frame_fire.copy()
                                h, w = snapshot.shape[:2]
                                cv2.putText(snapshot, f"!!! FIRE ALERT !!! {fire_count} AREA(S)", (int(w/2) - 220, 50),
                                          cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 69, 255), 4)
                                cv2.putText(snapshot, "EMERGENCY - Fire Detected!", (int(w/2) - 180, 95),
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 140, 255), 3)

                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 火灾告警: {fire_count} 处")

                    # 7. 人员聚集
                    if crowd_gathering_enabled:
                        processed_frame_gathering, _, gathering_frame_detections, gathering_alerts = self._run_detector_operation('crowd_gathering_detection', lambda: self.crowd_gathering_detector.detect(display_frame))
                        display_frame = processed_frame_gathering.copy()
                        is_gathering = gathering_frame_detections.get("gathering", False)
                        people_count = gathering_frame_detections.get("people", 0)
                        gathering_duration = gathering_frame_detections.get("duration", 0.0)

                        if is_gathering:
                            detection_type = "crowd_gathering_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": f"人员异常聚集：共 {people_count} 人，持续 {gathering_duration:.1f} 秒",
                                    "status": "人员聚集",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "crowd_gathering_detection",
                                    "confidence": 0.90,
                                    "people_count": people_count,
                                    "duration": gathering_duration
                                }

                                snapshot = processed_frame_gathering.copy()
                                h, w = snapshot.shape[:2]
                                cv2.putText(snapshot, f"CROWD GATHERING ALERT! {people_count} PEOPLE", (int(w/2) - 250, 50),
                                          cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
                                cv2.putText(snapshot, f"Duration: {gathering_duration:.1f}s", (int(w/2) - 120, 90),
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 聚集告警: {people_count} 人，{gathering_duration:.1f} 秒")

                    # 8. 人员离岗
                    if person_absence_enabled:
                        processed_frame_absence, _, absence_frame_detections, absence_alerts = self._run_detector_operation('person_absence_detection', lambda: self.person_absence_detector.detect(display_frame))
                        display_frame = processed_frame_absence.copy()
                        is_absence = absence_frame_detections.get("absence", False)
                        absence_duration = absence_frame_detections.get("duration", 0.0)

                        if is_absence:
                            detection_type = "person_absence_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": f"岗位离岗：无人值守 {absence_duration:.1f} 秒",
                                    "status": "人员离岗",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "person_absence_detection",
                                    "confidence": 0.95,
                                    "duration": absence_duration
                                }

                                snapshot = processed_frame_absence.copy()
                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 离岗告警: {absence_duration:.1f} 秒")

                    # 9. 车辆违停
                    if vehicle_parking_enabled:
                        processed_frame_vehicle, _, vehicle_frame_detections, parking_violations = self._run_detector_operation('vehicle_parking_detection', lambda: self.vehicle_parking_detector.detect(display_frame))
                        display_frame = processed_frame_vehicle.copy()
                        has_violation = bool(parking_violations)

                        if has_violation:
                            for violation in parking_violations:
                                detection_type = "vehicle_parking_violation"
                                vehicle_id = violation["vehicle_id"]
                                vehicle_key = f"{detection_type}_{vehicle_id}"
                                if vehicle_key in last_alert_time:
                                    continue

                                result_data = {
                                    "info": f"车辆违停：车辆 #{vehicle_id} 已停留 {violation['duration']:.1f} 秒",
                                    "status": "车辆违停",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "vehicle_parking_detection",
                                    "confidence": violation["confidence"],
                                    "duration": violation["duration"],
                                    "vehicle_id": vehicle_id
                                }

                                snapshot = processed_frame_vehicle.copy()
                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[vehicle_key] = current_time
                                print(f"[{source_name}] 违停告警: 车辆 #{vehicle_id}，{violation['duration']:.1f} 秒")

                    # 10. 灭火器缺失
                    if fire_extinguisher_absence_enabled:
                        processed_frame_extinguisher, _, extinguisher_frame_detections, _ = self._run_detector_operation('fire_extinguisher_absence_detection', lambda: self.fire_extinguisher_absence_detector.detect(display_frame))
                        display_frame = processed_frame_extinguisher.copy()
                        is_absent = extinguisher_frame_detections.get("is_absent", False)
                        absence_duration = extinguisher_frame_detections.get("absence_duration", 0.0)

                        if is_absent:
                            detection_type = "fire_extinguisher_absence_violation"
                            if detection_type not in last_alert_time or current_time - last_alert_time[detection_type] >= alert_interval:
                                result_data = {
                                    "info": f"灭火器缺失{absence_duration:.1f}秒",
                                    "status": "灭火器缺失",
                                    "source": source_location,
                                    "location": source_location,
                                    "channel_name": source_channel,
                                    "algorithm": "fire_extinguisher_absence_detection",
                                    "confidence": 0.95,
                                    "duration": absence_duration
                                }

                                snapshot = processed_frame_extinguisher.copy()
                                self.manager._create_alert_record(
                                    detection_type,
                                    result_data,
                                    snapshot
                                )
                                last_alert_time[detection_type] = current_time
                                print(f"[{source_name}] 🧯🚨 灭火器缺失告警: 已缺失{absence_duration:.1f}秒")

                    # 11. 试剂瓶检测
                    if reagent_bottle_enabled:
                        # 执行试剂瓶检测
                        processed_frame_reagent, _, reagent_detections = self._run_detector_operation('reagent_bottle_detection', lambda: self.reagent_bottle_detector.detect(frame))
                        display_frame = processed_frame_reagent.copy()

                        # 检查是否有试剂瓶被检测到
                        if reagent_detections.get("reagent_bottle", 0) > 0:
                            detection_type = "reagent_bottle_violation"

                            # 避免频繁告警
                            if detection_type in last_alert_time and current_time - last_alert_time[detection_type] < alert_interval:
                                continue

                            bottle_count = reagent_detections["reagent_bottle"]

                            result_data = {
                                "info": f"检测到{bottle_count}个试剂瓶/危险化学品",
                                "status": "试剂瓶检测",
                                "source": source_location,
                                "location": source_location,
                                "channel_name": source_channel,
                                "algorithm": "reagent_bottle_detection",
                                "confidence": 0.90,
                                "bottle_count": bottle_count
                            }

                            # 使用带检测框的处理帧
                            snapshot = processed_frame_reagent.copy()

                            # 在快照上添加大号告警文字
                            h, w = snapshot.shape[:2]
                            cv2.putText(snapshot, "REAGENT BOTTLE DETECTED!", (int(w/2) - 200, 50),
                                      cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
                            cv2.putText(snapshot, f"Count: {bottle_count}", (int(w/2) - 60, 90),
                                      cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[detection_type] = current_time
                            print(f"[{source_name}] 🧪🚨 检测到试剂瓶告警: {bottle_count}个试剂瓶/危险化学品")

                    # 12. 打架识别检测
                    if fight_enabled:
                        # 执行打架识别检测（传入video_source_id用于追踪）
                        processed_frame_fight, fight_alerts = self._run_detector_operation('fight_detection', lambda: self.fight_detector.detect(frame, video_source_id=source_id))
                        display_frame = processed_frame_fight.copy()

                        # 处理打架告警
                        for alert in fight_alerts:
                            detection_type = alert.get("type", "fight_violation")

                            # 使用唯一标识避免重复告警（基于位置和时间）
                            alert_key = f"{detection_type}_{alert.get('location', '')}"

                            # 避免频繁告警（同一位置的打架只告警一次）
                            if alert_key in last_alert_time:
                                continue

                            result_data = {
                                "info": alert.get("message", "检测到打架行为"),
                                "status": "打架",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "fight_detection",
                                "confidence": alert.get("confidence", 0.95)
                            }

                            # 使用带检测框的处理帧
                            snapshot = processed_frame_fight.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            duration = alert.get("details", {}).get("duration", 0)
                            print(f"[{source_name}] 👊🚨 检测到打架告警: 持续{duration:.1f}秒")

                    # 13. 手套检测
                    if glove_enabled:
                        # 执行手套检测（传入video_source_id用于追踪）
                        processed_frame_glove, glove_alerts = self._run_detector_operation('glove_detection', lambda: self.glove_detector.detect(frame, video_source_id=source_id))
                        display_frame = processed_frame_glove.copy()

                        # 处理未戴手套告警
                        for alert in glove_alerts:
                            detection_type = alert.get("type", "glove_violation")

                            # 使用唯一标识避免重复告警（基于位置和时间）
                            alert_key = f"{detection_type}_{alert.get('location', '')}"

                            # 避免频繁告警（同一位置的未戴手套只告警一次）
                            if alert_key in last_alert_time:
                                continue

                            result_data = {
                                "info": alert.get("message", "检测到未佩戴手套"),
                                "status": "未戴手套",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "glove_detection",
                                "confidence": alert.get("confidence", 0.95)
                            }

                            # 使用带检测框的处理帧
                            snapshot = processed_frame_glove.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            duration = alert.get("details", {}).get("duration", 0)
                            print(f"[{source_name}] 🧤🚨 检测到未戴手套告警: 持续{duration:.1f}秒")

                    # 14. 裂缝检测
                    if crack_enabled:
                        # 执行裂缝检测
                        processed_frame_crack, crack_info, crack_stats, crack_alerts = self._run_detector_operation('crack_detection', lambda: self.crack_detector.detect(frame))
                        display_frame = processed_frame_crack.copy()

                        # 处理裂缝告警
                        for alert in crack_alerts:
                            detection_type = "crack_violation"

                            # 按同类型限频：同一路视频源内，1秒内同类型只告警一次
                            alert_key = detection_type
                            if alert_key in last_alert_time:
                                if current_time - last_alert_time[alert_key] < 1:
                                    continue

                            result_data = {
                                "info": alert.get("message", "检测到裂缝"),
                                "status": "裂缝",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "crack_detection",
                                "confidence": alert.get("confidence", 0.95)
                            }

                            # 使用带检测框的处理帧作为告警截图
                            snapshot = processed_frame_crack.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] ⚡🚨 裂缝告警: 置信度{alert.get('confidence', 0):.2%}, 位置{alert.get('location', '未知')}")

                    # 15. 狗检测
                    if dog_enabled:
                        # 执行狗检测
                        processed_frame_dog, dog_info, dog_stats, dog_alerts = self._run_detector_operation('dog_detection', lambda: self.dog_detector.detect(frame))
                        display_frame = processed_frame_dog.copy()

                        # 处理狗告警
                        for alert in dog_alerts:
                            detection_type = "dog_violation"

                            # 猫/狗这类目标位置会抖动，按类型限频更稳定，避免同一秒几十条告警
                            alert_key = detection_type
                            if alert_key in last_alert_time and current_time - last_alert_time[alert_key] < 1:
                                continue

                            result_data = {
                                "info": alert.get("message", "检测到狗"),
                                "status": "狗",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "dog_detection",
                                "confidence": alert.get("confidence", 0.95)
                            }

                            # 使用带检测框的处理帧作为告警截图
                            snapshot = processed_frame_dog.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] 🐕🚨 狗告警: 置信度{alert.get('confidence', 0):.2%}, 位置{alert.get('location', '未知')}")

                    # 16. 猫检测
                    if cat_enabled:
                        # 执行猫检测
                        processed_frame_cat, cat_info, cat_stats, cat_alerts = self._run_detector_operation('cat_detection', lambda: self.cat_detector.detect(frame))
                        display_frame = processed_frame_cat.copy()

                        # 处理猫告警
                        for alert in cat_alerts:
                            detection_type = "cat_violation"

                            # 猫/狗这类目标位置会抖动，按类型限频更稳定，避免同一秒几十条告警
                            alert_key = detection_type
                            if alert_key in last_alert_time and current_time - last_alert_time[alert_key] < 1:
                                continue

                            result_data = {
                                "info": alert.get("message", "检测到猫"),
                                "status": "猫",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "cat_detection",
                                "confidence": alert.get("confidence", 0.95)
                            }

                            # 使用带检测框的处理帧作为告警截图
                            snapshot = processed_frame_cat.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] 🐱🚨 猫告警: 置信度{alert.get('confidence', 0):.2%}, 位置{alert.get('location', '未知')}")

                    # 17. 摄像头遮挡检测
                    if camera_occlusion_enabled:
                        # 执行摄像头遮挡检测
                        processed_frame_occlusion, occlusion_info, occlusion_stats = self._run_detector_operation('camera_occlusion_detection', lambda: self.camera_occlusion_detector.detect(frame))
                        display_frame = processed_frame_occlusion.copy()

                        # 检查是否检测到遮挡
                        if occlusion_stats.get("occlusion", 0) > 0:
                            detection_type = "camera_occlusion_violation"

                            # 避免频繁告警
                            if detection_type in last_alert_time and current_time - last_alert_time[detection_type] < alert_interval:
                                continue

                            result_data = {
                                "info": f"检测到摄像头遮挡（{occlusion_stats['occlusion']}处）",
                                "status": "遮挡",
                                "source": source_location,
                                "location": source_location,
                                "channel_name": source_channel,
                                "algorithm": "camera_occlusion_detection",
                                "confidence": 0.95  # 遮挡检测置信度较高
                            }

                            # 使用带检测框的处理帧作为告警截图
                            snapshot = processed_frame_occlusion.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[detection_type] = current_time
                            print(f"[{source_name}] 📵🚨 摄像头遮挡告警: 检测到{occlusion_stats['occlusion']}处遮挡")

                    # 18. 疲劳驾驶检测
                    if fatigue_driving_enabled:
                        # 执行疲劳驾驶检测
                        processed_frame_fatigue, fatigue_info, fatigue_stats, fatigue_alerts = self._run_detector_operation('fatigue_driving_detection', lambda: self.fatigue_driving_detector.detect(frame))
                        display_frame = processed_frame_fatigue.copy()

                        # 处理疲劳驾驶告警
                        for alert in fatigue_alerts:
                            detection_type = "fatigue_driving_violation"

                            # 避免频繁告警（10秒内不重复）
                            if detection_type in last_alert_time and current_time - last_alert_time[detection_type] < 10:
                                continue

                            details = alert.get('details', {})
                            result_data = {
                                "info": alert.get('message', '检测到疲劳驾驶'),
                                "status": "疲劳",
                                "source": source_location,
                                "location": alert.get('location', source_location),
                                "channel_name": source_channel,
                                "algorithm": "fatigue_driving_detection",
                                "confidence": alert.get('confidence', 0.8),
                                "details": {
                                    "fatigue_score": details.get('fatigue_score', 0),
                                    "close_eyes_count": details.get('close_eyes_count', 0),
                                    "yawn_count": details.get('yawn_count', 0),
                                    "reason": details.get('reason', '')
                                }
                            }

                            # 使用带检测框和告警信息的处理帧作为告警截图
                            snapshot = processed_frame_fatigue.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[detection_type] = current_time
                            print(f"[{source_name}] 😴🚨 疲劳驾驶告警: {alert.get('message', '')}")

                    # 19. 垃圾检测
                    if trash_enabled:
                        # 执行垃圾检测
                        processed_frame_trash, trash_info, trash_stats, trash_alerts = self._run_detector_operation('trash_detection', lambda: self.trash_detector.detect(frame))
                        display_frame = processed_frame_trash.copy()

                        # 处理垃圾告警
                        for alert in trash_alerts:
                            detection_type = "trash_violation"

                            # 垃圾检测的框位置每帧变化较大，按类型限频避免高频刷告警
                            alert_key = detection_type
                            if alert_key in last_alert_time and current_time - last_alert_time[alert_key] < 1:
                                continue

                            result_data = {
                                "info": alert.get("message", "检测到垃圾"),
                                "status": "垃圾",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "trash_detection",
                                "confidence": alert.get("confidence", 0.95)
                            }

                            # 使用带检测框的处理帧作为告警截图
                            snapshot = processed_frame_trash.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] 🗑️🚨 垃圾告警: 置信度{alert.get('confidence', 0):.2%}, 位置{alert.get('location', '未知')}")

                    # 20. 电瓶车检测
                    if electric_bicycle_enabled:
                        # 执行电瓶车检测
                        processed_frame_ebike, ebike_info, ebike_stats, ebike_alerts = self._run_detector_operation('electric_bicycle_detection', lambda: self.electric_bicycle_detector.detect(frame))
                        display_frame = processed_frame_ebike.copy()

                        # 处理电瓶车告警
                        for alert in ebike_alerts:
                            detection_type = "electric_bicycle_violation"

                            # 按同类型限频：同一路视频源内，1秒内同类型只告警一次
                            alert_key = detection_type
                            if alert_key in last_alert_time:
                                if current_time - last_alert_time[alert_key] < 1:
                                    continue

                            result_data = {
                                "info": alert.get("message", "检测到电瓶车"),
                                "status": "电瓶车",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "electric_bicycle_detection",
                                "confidence": alert.get("confidence", 0.95)
                            }

                            # 使用带检测框的处理帧作为告警截图
                            snapshot = processed_frame_ebike.copy()

                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] 🛴🚨 电瓶车告警: 置信度{alert.get('confidence', 0):.2%}, 位置{alert.get('location', '未知')}")

                    # 21. 工作服检测
                    if work_clothes_enabled:
                        processed_frame_work_clothes, _, work_clothes_stats, work_clothes_alerts = self._run_detector_operation(
                            'work_clothes_detection',
                            lambda: self.work_clothes_detector.detect(display_frame)
                        )
                        display_frame = processed_frame_work_clothes.copy()

                        for alert in work_clothes_alerts:
                            detection_type = "work_clothes_violation"
                            alert_key = detection_type
                            if alert_key in last_alert_time and current_time - last_alert_time[alert_key] < alert_interval:
                                continue

                            casual_count = work_clothes_stats.get("casual_clothes", 0)
                            result_data = {
                                "info": alert.get("message", f"检测到 {casual_count} 人未穿工作服"),
                                "status": "未穿工作服",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "work_clothes_detection",
                                "confidence": alert.get("confidence", 0.95),
                                "count": casual_count
                            }

                            snapshot = processed_frame_work_clothes.copy()
                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] 工作服告警: {result_data['info']}")

                    # 22. 安全帽反光衣检测
                    if safehat_vest_enabled:
                        processed_frame_safehat_vest, _, safehat_vest_stats, safehat_vest_alerts = self._run_detector_operation(
                            'safehat_vest_detection',
                            lambda: self.safehat_vest_detector.detect(display_frame)
                        )
                        display_frame = processed_frame_safehat_vest.copy()

                        for alert in safehat_vest_alerts:
                            detection_type = alert.get("type", "safehat_vest_no_helmet_violation")
                            alert_key = detection_type
                            if alert_key in last_alert_time and current_time - last_alert_time[alert_key] < alert_interval:
                                continue

                            is_entry = detection_type == "safehat_vest_entry"
                            status = "进入" if is_entry else alert.get("message", "安全帽反光衣告警")
                            result_data = {
                                "info": alert.get("message", status),
                                "status": status,
                                "source": source_location,
                                "location": alert.get("location") or source_location,
                                "channel_name": source_channel,
                                "algorithm": "safehat_vest_detection",
                                "confidence": alert.get("confidence", 0.95),
                                "count": (
                                    safehat_vest_stats.get("entry", 0)
                                    if is_entry else
                                    safehat_vest_stats.get("no_hardhat", 0) + safehat_vest_stats.get("no_safety_vest", 0)
                                ),
                                "audio_type": alert.get("audio_type"),
                            }

                            snapshot = processed_frame_safehat_vest.copy()
                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] 安全帽反光衣提示: {result_data['info']}")

                    # 23. 钢瓶倾倒检测
                    if gas_cylinder_tilt_enabled:
                        processed_frame_gas_cylinder, _, gas_cylinder_stats, gas_cylinder_alerts = self._run_detector_operation(
                            'gas_cylinder_tilt_detection',
                            lambda: self.gas_cylinder_tilt_detector.detect(display_frame)
                        )
                        display_frame = processed_frame_gas_cylinder.copy()

                        for alert in gas_cylinder_alerts:
                            detection_type = "gas_cylinder_tilt_violation"
                            alert_key = detection_type
                            if alert_key in last_alert_time and current_time - last_alert_time[alert_key] < alert_interval:
                                continue

                            tilt_count = gas_cylinder_stats.get("unmormal", 0)
                            result_data = {
                                "info": alert.get("message", f"检测到 {tilt_count} 个钢瓶倾倒"),
                                "status": "钢瓶倾倒",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "gas_cylinder_tilt_detection",
                                "confidence": alert.get("confidence", 0.95),
                                "count": tilt_count
                            }

                            snapshot = processed_frame_gas_cylinder.copy()
                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] 钢瓶倾倒告警: {result_data['info']}")

                    # 24. 箱上放物检测
                    if box_object_enabled:
                        processed_frame_box, _, box_stats, box_alerts = self._run_detector_operation(
                            'box_object_detection',
                            lambda: self.box_object_detector.detect(display_frame)
                        )
                        display_frame = processed_frame_box.copy()

                        for alert in box_alerts:
                            detection_type = "box_object_violation"
                            alert_key = detection_type
                            if alert_key in last_alert_time and current_time - last_alert_time[alert_key] < alert_interval:
                                continue

                            unnormal_count = box_stats.get("unnormal", 0)
                            result_data = {
                                "info": alert.get("message", f"检测到 {unnormal_count} 处箱上放物"),
                                "status": "箱上放物警告",
                                "source": source_location,
                                "location": alert.get("location", source_location),
                                "channel_name": source_channel,
                                "algorithm": "box_object_detection",
                                "confidence": alert.get("confidence", 0.95),
                                "count": unnormal_count
                            }

                            snapshot = processed_frame_box.copy()
                            self.manager._create_alert_record(
                                detection_type,
                                result_data,
                                snapshot
                            )
                            last_alert_time[alert_key] = current_time
                            print(f"[{source_name}] 箱上放物告警: {result_data['info']}")

                except Exception as e:
                    print(f"检测过程出错 ({source_name}): {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue

                self.update_processed_frame(source_id, display_frame, raw_version if not is_local_file else frame_count)
                if frame_pusher:
                    frame_pusher.write(display_frame)

                # 文件源节奏由 reader_loop 按原始 FPS 控制，这里不再额外加速或减速。

        except Exception as e:
            print(f"检测循环异常 ({source_name}): {str(e)}")

        finally:
            self.stop_flags[source_id] = True

            if reader_thread and reader_thread.is_alive():
                reader_thread.join(timeout=1)

            # 清理资源
            if frame_pusher:
                frame_pusher.release()
            if frame_reader:
                frame_reader.release()

            self._pop_stream_resource(source_id)
            self.clear_processed_frame(source_id)

            print(f"检测循环结束 - {source_name}")

    def get_detection_status(self):
        """获取所有检测线程的状态"""
        status = {}
        with self.lifecycle_lock:
            for source_id, thread in self.detection_threads.items():
                status[source_id] = {
                    'running': thread.is_alive(),
                    'thread_name': thread.name
                }
        return status

    @staticmethod
    def _draw_violation_box(frame, bbox, label, color):
        try:
            x1, y1, x2, y2 = [int(coord) for coord in bbox]
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            text = f"{label}"
            cv2.putText(frame, text, (x1, max(10, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"绘制告警框失败: {exc}")
