"""
真实检测数据管理模块
负责管理摄像头检测和数据存储
"""
import base64
import json
from pathlib import Path
import cv2
import threading
import time
import queue
from datetime import datetime, timedelta
from collections import deque
import numpy as np

# 导入检测模块
from suanfa import HelmetDetector, VestDetector


class RealDetectionManager:
    def __init__(self):
        """初始化真实检测管理器"""
        # 检测器
        self.helmet_detector = HelmetDetector()
        self.vest_detector = VestDetector()
        
        # 视频源检测器（延迟初始化，避免循环导入）
        self.video_source_detector = None
        
        # 摄像头相关
        self.cap = None
        self.camera_index = 0
        self.is_camera_running = False
        self.detection_thread = None
        self.stop_detection = False
        
        # 视频流相关
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self.video_enabled = True
        
        # 检测类别配置（用于前端显示，不包含detector对象）
        self.detection_types = {
            # 正常检测类型
            "helmet": {
                "name": "安全帽检测",
                "icon": "🪖", 
                "color": "#00ff88"
            },
            "vest": {
                "name": "反光衣检测",
                "icon": "🦺", 
                "color": "#00d4ff"
            },
            # 违规类型（告警）
            "helmet_violation": {
                "name": "未戴安全帽",
                "icon": "⚠️",
                "color": "#ff4444"
            },
            "vest_violation": {
                "name": "未穿反光衣",
                "icon": "⚠️",
                "color": "#ff6b35"
            },
            "fall_violation": {
                "name": "人员跌倒",
                "icon": "🚨",
                "color": "#ff0000"
            },
            "mask_violation": {
                "name": "未佩戴口罩",
                "icon": "😷",
                "color": "#ff4466"
            },
            "intrusion_violation": {
                "name": "人员入侵",
                "icon": "🚫",
                "color": "#ff0000"
            },
            "theft_violation": {
                "name": "偷盗检测",
                "icon": "🚨",
                "color": "#ff0033"
            },
            "smoke_violation": {
                "name": "烟雾检测",
                "icon": "💨",
                "color": "#808080"
            },
            "fire_violation": {
                "name": "火灾检测",
                "icon": "🔥",
                "color": "#ff4500"
            },
            "crowd_gathering_violation": {
                "name": "人员聚集",
                "icon": "👥",
                "color": "#ffa500"
            },
            "person_absence_violation": {
                "name": "人员离岗",
                "icon": "🚶",
                "color": "#9370db"
            },
            "vehicle_parking_violation": {
                "name": "车辆违停",
                "icon": "🚗",
                "color": "#ffa500"
            },
            "fire_extinguisher_absence_violation": {
                "name": "灭火器缺失",
                "icon": "🧯",
                "color": "#ff0000"
            },
            "reagent_bottle_violation": {
                "name": "试剂瓶检测",
                "icon": "🧪",
                "color": "#9933ff"
            },
            "camera_occlusion_violation": {
                "name": "摄像头遮挡",
                "icon": "📵",
                "color": "#ff0000"
            },
            "fight_violation": {
                "name": "打架识别",
                "icon": "👊",
                "color": "#ff0000"
            },
            "glove_violation": {
                "name": "未戴手套",
                "icon": "🧤",
                "color": "#ff4444"
            },
            "work_clothes_violation": {
                "name": "未穿工作服",
                "icon": "👔",
                "color": "#ff4444"
            },
            "gas_cylinder_tilt_violation": {
                "name": "钢瓶倾倒",
                "icon": "🧯",
                "color": "#ff4444"
            },
            "box_object_violation": {
                "name": "箱上放物警告",
                "icon": "📦",
                "color": "#ff4444"
            },
            "safehat_vest_no_helmet_violation": {
                "name": "未戴安全帽",
                "icon": "⚠️",
                "color": "#ff4444"
            },
            "safehat_vest_no_vest_violation": {
                "name": "未穿反光衣",
                "icon": "⚠️",
                "color": "#ff6b35"
            },
            "safehat_vest_entry": {
                "name": "进入",
                "icon": "✅",
                "color": "#00c853"
            },
            "crack_violation": {
                "name": "裂缝检测",
                "icon": "⚡",
                "color": "#ff6600"
            },
            "dog_violation": {
                "name": "狗检测",
                "icon": "🐕",
                "color": "#ff9900"
            },
            "cat_violation": {
                "name": "猫检测",
                "icon": "🐱",
                "color": "#ff69b4"
            },
            "fatigue_driving_violation": {
                "name": "疲劳驾驶",
                "icon": "😴",
                "color": "#ff6347"
            },
            "trash_violation": {
                "name": "垃圾检测",
                "icon": "🗑️",
                "color": "#ff9500"
            },
            "electric_bicycle_violation": {
                "name": "电瓶车检测",
                "icon": "🛴",
                "color": "#ffa500"
            },
            "smoke_detection": {
                "name": "烟雾检测",
                "icon": "🚬",
                "color": "#ff9500"
            },
            "fall_detection": {
                "name": "跌倒检测",
                "icon": "🤕",
                "color": "#ff3b30"
            },
            "intrusion_detection": {
                "name": "区域入侵",
                "icon": "🚫",
                "color": "#ff2d55"
            },
            "theft_detection": {
                "name": "偷盗检测",
                "icon": "🚨",
                "color": "#ff0033"
            },
            "crack_detection": {
                "name": "裂缝检测",
                "icon": "⚡",
                "color": "#af52de"
            },
            "work_clothes_detection": {
                "name": "工作服检测",
                "icon": "👔",
                "color": "#00c853"
            },
            "safehat_vest_detection": {
                "name": "安全帽反光衣检测",
                "icon": "🦺",
                "color": "#00c853"
            },
            "gas_cylinder_tilt_detection": {
                "name": "钢瓶倾倒检测",
                "icon": "🧯",
                "color": "#ff4444"
            },
            "box_object_detection": {
                "name": "箱上放物检测",
                "icon": "📦",
                "color": "#ff4444"
            }
        }
        
        # 检测器映射（内部使用）
        self.detectors = {
            "helmet": self.helmet_detector,
            "vest": self.vest_detector
        }
        self.video_source_path = Path("data/video_sources.json")
        
        # 数据存储 - 使用线程安全的数据结构
        self.lock = threading.Lock()
        self.stats_data = {
            "summary": {
                "helmet": 0, 
                "vest": 0, 
                "helmet_violation": 0, 
                "vest_violation": 0,
                "fall_violation": 0,  # 添加跌倒统计
                "mask_violation": 0,  # 添加口罩统计
                "intrusion_violation": 0,  # 添加人员入侵统计
                "theft_violation": 0,
                "smoke_violation": 0,  # 添加烟雾统计
                "fire_violation": 0,  # 添加火灾统计
                "crowd_gathering_violation": 0,  # 添加人员聚集统计
                "person_absence_violation": 0,  # 添加人员离岗统计
                "vehicle_parking_violation": 0,  # 添加车辆违停统计
                "fire_extinguisher_absence_violation": 0,  # 添加灭火器缺失统计
                "reagent_bottle_violation": 0,  # 添加试剂瓶统计
                "camera_occlusion_violation": 0,  # 添加摄像头遮挡统计
                "fight_violation": 0,  # 添加打架统计
                "glove_violation": 0,  # 添加手套统计
                "work_clothes_violation": 0,  # 添加工作服统计
                "safehat_vest_no_helmet_violation": 0,
                "safehat_vest_no_vest_violation": 0,
                "gas_cylinder_tilt_violation": 0,
                "box_object_violation": 0,
                "crack_violation": 0,  # 添加裂缝统计
                "dog_violation": 0,  # 添加狗统计
                "cat_violation": 0,  # 添加猫统计
                "fatigue_driving_violation": 0,  # 添加疲劳驾驶统计
                "trash_violation": 0,  # 添加垃圾统计
                "electric_bicycle_violation": 0  # 添加电瓶车统计
            },
            "trend": {
                "timestamps": deque(maxlen=12),  # 最近12个时间点
                "helmet": deque(maxlen=12),
                "vest": deque(maxlen=12),
                "helmet_violation": deque(maxlen=12),
                "vest_violation": deque(maxlen=12),
                "fall_violation": deque(maxlen=12),  # 添加跌倒趋势
                "mask_violation": deque(maxlen=12),  # 添加口罩趋势
                "intrusion_violation": deque(maxlen=12),  # 添加人员入侵趋势
                "theft_violation": deque(maxlen=12),
                "smoke_violation": deque(maxlen=12),  # 添加烟雾趋势
                "fire_violation": deque(maxlen=12),  # 添加火灾趋势
                "crowd_gathering_violation": deque(maxlen=12),  # 添加人员聚集趋势
                "person_absence_violation": deque(maxlen=12),  # 添加人员离岗趋势
                "vehicle_parking_violation": deque(maxlen=12),  # 添加车辆违停趋势
                "fire_extinguisher_absence_violation": deque(maxlen=12),  # 添加灭火器缺失趋势
                "reagent_bottle_violation": deque(maxlen=12),  # 添加试剂瓶趋势
                "camera_occlusion_violation": deque(maxlen=12),  # 添加摄像头遮挡趋势
                "fight_violation": deque(maxlen=12),  # 添加打架趋势
                "glove_violation": deque(maxlen=12),  # 添加手套趋势
                "work_clothes_violation": deque(maxlen=12),  # 添加工作服趋势
                "safehat_vest_no_helmet_violation": deque(maxlen=12),
                "safehat_vest_no_vest_violation": deque(maxlen=12),
                "gas_cylinder_tilt_violation": deque(maxlen=12),
                "box_object_violation": deque(maxlen=12),
                "crack_violation": deque(maxlen=12),  # 添加裂缝趋势
                "dog_violation": deque(maxlen=12),  # 添加狗趋势
                "cat_violation": deque(maxlen=12),  # 添加猫趋势
                "fatigue_driving_violation": deque(maxlen=12),  # 添加疲劳驾驶趋势
                "trash_violation": deque(maxlen=12),  # 添加垃圾趋势
                "electric_bicycle_violation": deque(maxlen=12)  # 添加电瓶车趋势
            },
            "logs": deque(maxlen=50)  # 最近50条日志
        }

        # 告警记录持久化
        self.alert_records_path = Path("data/alert_records.json")
        self.alert_records = deque(maxlen=500)
        self._load_alert_records()
        self.alert_dedup_window = 3.0
        self.recent_alert_keys = {}
        self.alert_task_queue = queue.Queue(maxsize=256)
        self.alert_worker_running = True
        self.alert_worker_thread = threading.Thread(
            target=self._alert_worker_loop,
            daemon=True,
            name="AlertRecordWorker"
        )
        self.alert_worker_thread.start()
        
        # 偷盗检测帧缓存（用于后续大模型推理），按来源保存最近10帧
        self.theft_frame_cache = {}
        self.theft_cache_lock = threading.Lock()
        self.theft_cache_sources = deque(maxlen=16)
        self.theft_cache_frame_limit = 10
        self.theft_llm_results = deque(maxlen=200)
        self.theft_llm_lock = threading.Lock()
        
        # 趋势数据管理 
        self.trend_interval = 300  # 5分钟一个点
        self.last_trend_time = time.time()
        self.current_trend_counts = {
            "helmet": 0, 
            "vest": 0, 
            "helmet_violation": 0, 
            "vest_violation": 0,
            "fall_violation": 0,  # 添加跌倒计数
            "mask_violation": 0,  # 添加口罩计数
            "intrusion_violation": 0,  # 添加人员入侵计数
            "theft_violation": 0,
            "smoke_violation": 0,  # 添加烟雾计数
            "fire_violation": 0,  # 添加火灾计数
            "crowd_gathering_violation": 0,  # 添加人员聚集计数
            "person_absence_violation": 0,  # 添加人员离岗计数
            "vehicle_parking_violation": 0,  # 添加车辆违停计数
            "fire_extinguisher_absence_violation": 0,  # 添加灭火器缺失计数
            "reagent_bottle_violation": 0,  # 添加试剂瓶计数
            "camera_occlusion_violation": 0,  # 添加摄像头遮挡计数
            "fight_violation": 0,  # 添加打架计数
            "glove_violation": 0,  # 添加手套计数
            "work_clothes_violation": 0,  # 添加工作服计数
            "safehat_vest_no_helmet_violation": 0,
            "safehat_vest_no_vest_violation": 0,
            "gas_cylinder_tilt_violation": 0,
            "box_object_violation": 0,
            "crack_violation": 0  # 添加裂缝计数
        }
        
        # 模型状态
        self.model_status = {
            "helmet_detection": True,
            "vest_detection": True
        }
        
        # 初始化趋势数据
        self._initialize_trend_data()
        
        # 初始化默认帧
        self._initialize_default_frame()
        # 初始化算法启用状态
        self.sync_algorithms_from_sources()
        
        print("真实检测管理器初始化完成")
    
    def _init_video_source_detector(self):
        """初始化视频源检测器"""
        try:
            from safehat.core.video_source_detector import VideoSourceDetector
            self.video_source_detector = VideoSourceDetector(self)
            # 自动启动所有启用的视频源检测
            self.video_source_detector.restart_all_from_config()
        except Exception as e:
            print(f"视频源检测器初始化失败: {str(e)}")
    
    def _initialize_trend_data(self):
        """初始化趋势数据"""
        current_time = datetime.now()
        for i in range(12):
            time_point = current_time - timedelta(minutes=55 - i * 5)
            self.stats_data["trend"]["timestamps"].append(time_point.strftime("%H:%M"))
            self.stats_data["trend"]["helmet"].append(0)
            self.stats_data["trend"]["vest"].append(0)
            self.stats_data["trend"]["helmet_violation"].append(0)
            self.stats_data["trend"]["vest_violation"].append(0)
            self.stats_data["trend"]["fall_violation"].append(0)  # 初始化跌倒趋势
            self.stats_data["trend"]["mask_violation"].append(0)  # 初始化口罩趋势
            self.stats_data["trend"]["intrusion_violation"].append(0)  # 初始化人员入侵趋势
            self.stats_data["trend"]["theft_violation"].append(0)
            self.stats_data["trend"]["smoke_violation"].append(0)  # 初始化烟雾趋势
            self.stats_data["trend"]["fire_violation"].append(0)  # 初始化火灾趋势
            self.stats_data["trend"]["crowd_gathering_violation"].append(0)  # 初始化人员聚集趋势
            self.stats_data["trend"]["person_absence_violation"].append(0)  # 初始化人员离岗趋势
            self.stats_data["trend"]["vehicle_parking_violation"].append(0)  # 初始化车辆违停趋势
            self.stats_data["trend"]["fire_extinguisher_absence_violation"].append(0)  # 初始化灭火器缺失趋势
            self.stats_data["trend"]["reagent_bottle_violation"].append(0)  # 初始化试剂瓶趋势
            self.stats_data["trend"]["camera_occlusion_violation"].append(0)  # 初始化摄像头遮挡趋势
            self.stats_data["trend"]["fight_violation"].append(0)  # 初始化打架趋势
            self.stats_data["trend"]["glove_violation"].append(0)  # 初始化手套趋势
            self.stats_data["trend"]["work_clothes_violation"].append(0)  # 初始化工作服趋势
            self.stats_data["trend"]["safehat_vest_no_helmet_violation"].append(0)
            self.stats_data["trend"]["safehat_vest_no_vest_violation"].append(0)
            self.stats_data["trend"]["gas_cylinder_tilt_violation"].append(0)
            self.stats_data["trend"]["box_object_violation"].append(0)
            self.stats_data["trend"]["crack_violation"].append(0)  # 初始化裂缝趋势
    
    def _initialize_default_frame(self):
        """初始化默认帧"""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        # 绘制背景
        cv2.rectangle(frame, (0, 0), (640, 480), (20, 20, 20), -1)
        
        # 绘制标题
        cv2.putText(frame, "AI Safety Detection System", (150, 200), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 2)
        
        # 绘制状态信息
        cv2.putText(frame, "Camera Status: Disconnected", (180, 250), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        cv2.putText(frame, "Click 'Start Detection' to begin", (160, 300), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (128, 128, 128), 2)
        
        # 绘制时间戳
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, timestamp, (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        
        with self.frame_lock:
            self.current_frame = frame

    def _load_alert_records(self):
        """加载历史告警记录"""
        try:
            if self.alert_records_path.exists():
                raw = self.alert_records_path.read_text(encoding="utf-8").strip()
                if raw:
                    records = json.loads(raw)
                    for record in records:
                        self.alert_records.append(record)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"加载告警记录失败: {exc}")
        finally:
            self._refresh_alert_cache()
            self._rebuild_violation_summary_from_records()

    def _save_alert_records(self):
        """持久化告警记录"""
        try:
            self.alert_records_path.parent.mkdir(parents=True, exist_ok=True)
            with self.alert_records_path.open("w", encoding="utf-8") as fp:
                json.dump(list(self.alert_records), fp, ensure_ascii=False, indent=2)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"保存告警记录失败: {exc}")

    def _refresh_alert_cache(self):
        """刷新内存告警缓存供前端使用"""
        self.stats_data["logs"].clear()
        for record in list(self.alert_records)[:50]:
            self.stats_data["logs"].append(record)

    def _rebuild_violation_summary_from_records(self):
        """按当前告警记录重建违规统计，避免删除后排行仍显示旧累计。"""
        summary = self.stats_data.get("summary", {})
        violation_keys = [key for key in summary.keys() if key.endswith("_violation")]
        for key in violation_keys:
            summary[key] = 0

        for record in self.alert_records:
            record_type = record.get("type")
            if record_type in summary and str(record_type).endswith("_violation"):
                summary[record_type] += 1

    def _capture_alert_snapshot(self, detection_type=None, frame=None):
        """保存当前帧为告警截图"""
        if frame is not None:
            frame_to_save = frame.copy()
        else:
            with self.frame_lock:
                frame_to_save = None if self.current_frame is None else self.current_frame.copy()

        if frame_to_save is None:
            return None

        screenshots_dir = Path("alert_screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{detection_type or 'alert'}.jpg"
        file_path = screenshots_dir / filename

        try:
            cv2.imwrite(str(file_path), frame_to_save, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return f"/alert_screenshots/{filename}"
        except Exception as exc:  # pylint: disable=broad-except
            print(f"保存告警截图失败: {exc}")
            return None

    def _get_active_camera(self):
        """获取当前摄像头的基础信息"""
        default = {
            "name": f"摄像头-{self.camera_index}",
            "location": "实时检测区域"
        }
        cameras = getattr(self, "cameras", None)
        if not cameras:
            return default
        for camera in cameras:
            if camera.get("camera_index") == self.camera_index:
                return camera
        return default

    def _map_algorithm_from_type(self, detection_type: str) -> str:
        if "box_object" in detection_type:
            return "box_object_detection"
        if "gas_cylinder_tilt" in detection_type:
            return "gas_cylinder_tilt_detection"
        if "safehat_vest" in detection_type:
            return "safehat_vest_detection"
        if "helmet" in detection_type:
            return "helmet_detection"
        if "vest" in detection_type:
            return "vest_detection"
        return "general_detection"

    def _map_audio_type(self, detection_type: str) -> str:
        if detection_type == "safehat_vest_no_helmet_violation":
            return "safehat_vest_no_helmet"
        if detection_type == "safehat_vest_no_vest_violation":
            return "safehat_vest_no_vest"
        if detection_type == "safehat_vest_entry":
            return "safehat_vest_entry"
        if "helmet" in detection_type:
            return "helmet_warning"
        if "vest" in detection_type:
            return "vest_warning"
        if "fall" in detection_type:
            return "fall_warning"
        if "mask" in detection_type:
            return "mask_warning"
        if "intrusion" in detection_type:
            return "intrusion_warning"
        if "theft" in detection_type:
            return "intrusion_warning"
        if "smoke" in detection_type:
            return "smoke_warning"
        if "fire" in detection_type:
            return "fire_warning"
        if "crowd_gathering" in detection_type:
            return "crowd_gathering_warning"
        if "person_absence" in detection_type or "absence" in detection_type:
            return "person_absence_warning"
        if "reagent_bottle" in detection_type:
            return "reagent_bottle_warning"
        if "camera_occlusion" in detection_type or "occlusion" in detection_type:
            return "camera_occlusion_warning"
        if "fight" in detection_type:
            return "fight_warning"
        if "glove" in detection_type:
            return "glove_warning"
        if "work_clothes" in detection_type:
            return "work_clothes_warning"
        if "gas_cylinder_tilt" in detection_type:
            return "gas_cylinder_tilt_warning"
        if "box_object" in detection_type:
            return "box_object_warning"
        if "crack" in detection_type:
            return "crack_warning"
        if "dog" in detection_type:
            return "dog_warning"
        if "cat" in detection_type:
            return "cat_warning"
        return "intrusion_warning"
    
    def _trigger_push(self, record: dict):
        """触发告警推送到APP/硬件"""
        try:
            # 延迟导入避免循环依赖
            from safehat.managers.push_manager import get_push_manager
            import os
            import socket
            import base64
            from pathlib import Path
            
            push_manager = get_push_manager()
            
            # 获取服务器地址（从环境变量或自动检测）
            server_host = os.getenv('SERVER_HOST')
            if not server_host or server_host == 'localhost':
                # 自动获取本机局域网IP
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    server_host = s.getsockname()[0]
                    s.close()
                except Exception:
                    server_host = 'localhost'
            
            server_port = os.getenv('SERVER_PORT', '5000')
            base_url = f"http://{server_host}:{server_port}"
            
            # 🔥 将图片转换为base64编码（阿里云公网可直接使用）
            snapshot_base64 = ''
            snapshot_filename = ''
            if record.get('snapshot'):
                try:
                    # 从snapshot路径中提取文件名
                    snapshot_path = record['snapshot']
                    if snapshot_path.startswith('/'):
                        snapshot_path = snapshot_path[1:]  # 移除开头的 /
                    
                    file_path = Path(snapshot_path)
                    if file_path.exists():
                        with open(file_path, 'rb') as img_file:
                            snapshot_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                            snapshot_filename = file_path.name
                            print(f"   📸 图片已编码为Base64 (大小: {len(snapshot_base64)} 字符)")
                except Exception as e:
                    print(f"   ⚠️  图片Base64编码失败: {e}")
            
            # 构建推送数据
            push_data = {
                'id': record['id'],
                'type': record['type'],
                'type_name': record['type_name'],
                'message': record['message'],
                'risk_level': record['risk_level'],
                'confidence': record.get('confidence', 0.9),
                'location': record.get('location', '未知区域'),
                'timestamp': record['timestamp'],
                'video_source_id': str(record.get('channel_id', 'camera')),
                'video_source_name': record.get('channel_name', '实时监控'),
                'video_source_type': 'camera',
                'video_url': '',
                'snapshot_url': f"{base_url}{record['snapshot']}" if record.get('snapshot') else '',
                'snapshot_base64': snapshot_base64,  # 🔥 Base64编码的图片
                'snapshot_filename': snapshot_filename,  # 原始文件名
                'detection_details': {
                    'algorithm': record.get('algorithm', ''),
                    'audio_type': record.get('audio_type', '')
                }
            }
            
            # 异步推送
            push_manager.push_alert(push_data)
            print(f"✅ 触发告警推送: {record['type_name']} @ {record['channel_name']}")
            if snapshot_base64:
                print(f"   📷 图片已包含 (Base64编码)")
            else:
                print(f"   📷 截图URL: {push_data['snapshot_url']} (可能无法从公网访问)")
            
        except Exception as e:
            print(f"❌ 触发告警推送失败: {e}")
            import traceback
            traceback.print_exc()

    def _create_alert_record_sync(self, detection_type: str, result: dict, snapshot_frame=None):
        """同步创建告警记录并保存。"""
        now = datetime.now()
        type_config = self.detection_types.get(detection_type, {})
        camera_meta = self._get_active_camera()
        confidence = result.get("confidence")

        # 🔥 导入设备身份配置
        from safehat.managers.push_manager import CUSTOMER_ID, DEVICE_ID, PROJECT_ID

        record = {
            "id": now.strftime("%Y%m%d%H%M%S%f"),
            "type": detection_type,
            "type_name": type_config.get("name", detection_type),
            "status": "未处理",
            "risk_level": "高" if "violation" in detection_type else "中",
            "source": result.get("source", "实时监控"),
            "location": result.get("location", camera_meta.get("location", "实时检测区域")),
            "algorithm": result.get("algorithm") or self._map_algorithm_from_type(detection_type),
            "confidence": round(float(confidence), 4) if confidence is not None else None,
            "timestamp": now.isoformat(),
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "message": result.get("info") or result.get("status", ""),
            "llm_reason": result.get("llm_reason") or result.get("reason") or "",
            "snapshot": self._capture_alert_snapshot(detection_type, snapshot_frame),
            "audio_type": result.get("audio_type") or self._map_audio_type(detection_type),
            "channel_name": result.get("channel_name", camera_meta.get("name")),
            "handled_by": None,
            "handled_at": None,
            "note": "",
            # ⭐ 设备身份信息
            "customer_id": CUSTOMER_ID,
            "device_id": DEVICE_ID,
            "project_id": PROJECT_ID
        }

        with self.lock:
            self.alert_records.appendleft(record)
            self.stats_data["logs"].appendleft(record)
            self._save_alert_records()
            
            # 更新统计数据（summary和current_trend_counts）
            if detection_type in self.stats_data["summary"]:
                self.stats_data["summary"][detection_type] += 1
            
            if detection_type in self.current_trend_counts:
                self.current_trend_counts[detection_type] += 1
        
        # ⭐ 触发告警推送到APP/硬件
        self._trigger_push(record)

        return record

    def _alert_worker_loop(self):
        """后台异步处理告警，避免阻塞检测线程。"""
        while self.alert_worker_running:
            try:
                task = self.alert_task_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if task is None:
                self.alert_task_queue.task_done()
                break

            try:
                self._create_alert_record_sync(
                    task["detection_type"],
                    task["result"],
                    task.get("snapshot_frame")
                )
            except Exception as e:
                print(f"异步创建告警记录失败: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self.alert_task_queue.task_done()

    def _create_alert_record(self, detection_type: str, result: dict, snapshot_frame=None):
        """异步创建告警记录，检测线程只负责入队。"""
        result = dict(result or {})
        dedup_key = self._build_alert_dedup_key(detection_type, result)
        now = time.time()

        with self.lock:
            expired_keys = [
                key for key, ts in self.recent_alert_keys.items()
                if now - ts >= self.alert_dedup_window
            ]
            for key in expired_keys:
                self.recent_alert_keys.pop(key, None)

            if dedup_key:
                last_seen = self.recent_alert_keys.get(dedup_key)
                if last_seen is not None and now - last_seen < self.alert_dedup_window:
                    return None
                self.recent_alert_keys[dedup_key] = now

        task = {
            "detection_type": detection_type,
            "result": result,
            "snapshot_frame": snapshot_frame.copy() if snapshot_frame is not None else None,
        }

        try:
            self.alert_task_queue.put_nowait(task)
        except queue.Full:
            print(f"告警队列已满，丢弃本次告警: {detection_type}")
        return None

    def _build_alert_dedup_key(self, detection_type: str, result: dict):
        """构建告警去重键，压制同一位置短时间内的重复告警。"""
        if not detection_type:
            return None

        source = str(result.get("source", "")).strip()
        location = str(result.get("location", "")).strip()
        algorithm = str(result.get("algorithm", "")).strip()
        status = str(result.get("status", "")).strip()
        count = result.get("count")
        person_count = result.get("person_count")

        return "|".join([
            detection_type,
            source,
            location,
            algorithm,
            status,
            str(count if count is not None else ""),
            str(person_count if person_count is not None else ""),
        ])

    def stop_alert_worker(self):
        """停止告警后台线程。"""
        self.alert_worker_running = False
        try:
            self.alert_task_queue.put_nowait(None)
        except queue.Full:
            pass

        if self.alert_worker_thread and self.alert_worker_thread.is_alive():
            self.alert_worker_thread.join(timeout=2)

    def _filter_alerts(self, records, filters):
        """根据过滤条件筛选告警"""
        if not filters:
            return records

        start_time = filters.get("start_time")
        end_time = filters.get("end_time")
        keyword = (filters.get("keyword") or "").strip()

        filtered = []
        for record in records:
            if filters.get("type") and record.get("type") != filters["type"]:
                continue
            if filters.get("status") and record.get("status") != filters["status"]:
                continue
            if filters.get("risk_level") and record.get("risk_level") != filters["risk_level"]:
                continue
            if filters.get("source") and record.get("source") != filters["source"]:
                continue

            if start_time or end_time:
                record_time = None
                try:
                    record_time = datetime.fromisoformat(record.get("timestamp"))
                except (TypeError, ValueError):
                    pass
                if record_time:
                    if start_time and record_time < start_time:
                        continue
                    if end_time and record_time > end_time:
                        continue

            if keyword:
                text = f"{record.get('type_name','')}{record.get('message','')}{record.get('location','')}"
                if keyword not in text:
                    continue

            filtered.append(record)
        return filtered

    def _build_alert_meta(self, all_records, filtered_records):
        """统计告警元数据"""
        status_counts = {}
        type_counts = {}
        risk_counts = {}

        for record in all_records:
            status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1
            type_counts[record["type_name"]] = type_counts.get(record["type_name"], 0) + 1
            risk_counts[record["risk_level"]] = risk_counts.get(record["risk_level"], 0) + 1

        return {
            "total": len(all_records),
            "filtered": len(filtered_records),
            "status_counts": status_counts,
            "type_counts": type_counts,
            "risk_counts": risk_counts,
            "pending": status_counts.get("未处理", 0)
        }
    
    def start_camera_detection(self, camera_index=0):
        """启动摄像头检测"""
        if self.is_camera_running:
            print("检测已在运行中")
            return False
        
        # 尝试打开摄像头
        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            print(f"无法打开摄像头 {camera_index}")
            return False
        
        # 设置摄像头参数
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        
        self.camera_index = camera_index
        self.is_camera_running = True
        self.stop_detection = False
        
        # 启用检测器
        if self.model_status.get("helmet_detection", True):
            self.detectors["helmet"].enable()
        if self.model_status.get("vest_detection", True):
            self.detectors["vest"].enable()
        
        # 启动检测线程
        self.detection_thread = threading.Thread(target=self._detection_loop, daemon=True)
        self.detection_thread.start()
        
        print(f"摄像头检测已启动 - 摄像头 {camera_index}")
        return True
    
    def stop_camera_detection(self):
        """停止摄像头检测"""
        if not self.is_camera_running:
            return
        
        self.stop_detection = True
        self.is_camera_running = False
        
        # 等待检测线程结束
        if self.detection_thread and self.detection_thread.is_alive():
            self.detection_thread.join(timeout=2)
        
        # 释放摄像头
        if self.cap:
            self.cap.release()
            self.cap = None
        
        # 禁用检测器
        self.detectors["helmet"].disable()
        self.detectors["vest"].disable()
        
        print("摄像头检测已停止")
    
    def _detection_loop(self):
        """检测循环 - 在后台线程中运行"""
        print("开始检测循环...")
        frame_count = 0
        
        while not self.stop_detection and self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                print("读取摄像头帧失败")
                break
            
            frame_count += 1
            
            # 每隔几帧进行一次检测，避免过度消耗CPU
            if frame_count % 5 != 0:
                continue
            
            try:
                # 执行检测
                detection_results = self._process_frame(frame)
                
                # 处理检测结果
                if detection_results:
                    self._update_detection_data(detection_results)
                
                # 更新趋势数据
                self._update_trend_data()
                
            except Exception as e:
                print(f"检测过程出错: {str(e)}")
                continue
        
        print("检测循环结束")
    
    def _process_frame(self, frame):
        """处理单帧图像"""
        detection_results = []
        display_frame = frame.copy()
        camera_meta = self._get_active_camera()
        channel_name = camera_meta.get("name", f"摄像头-{self.camera_index}")
        location_label = camera_meta.get("location", "实时检测区域")
        data_source = "实时监控" if self.is_camera_running else "视频文件"

        if self.detectors["helmet"].is_enabled:
            try:
                processed_frame_helmet, _, helmet_stats = self.detectors["helmet"].detect(display_frame)
                display_frame = processed_frame_helmet.copy()

                for _ in range(helmet_stats.get("helmet", 0)):
                    detection_results.append({
                        "type": "helmet",
                        "info": "检测到已佩戴安全帽",
                        "status": "已佩戴",
                        "confidence": 1.0,
                        "location": location_label,
                        "source": data_source,
                        "channel_name": channel_name,
                        "algorithm": "helmet_detection"
                    })

                no_helmet_count = helmet_stats.get("no_helmet", 0)
                if no_helmet_count > 0:
                    violation_snapshot = display_frame.copy()
                    for _ in range(no_helmet_count):
                        detection_results.append({
                            "type": "helmet_violation",
                            "info": "检测到未戴安全帽",
                            "status": "未佩戴",
                            "confidence": 1.0,
                            "location": location_label,
                            "source": data_source,
                            "channel_name": channel_name,
                            "algorithm": "helmet_detection",
                            "snapshot": violation_snapshot
                        })
            except Exception as e:
                print(f"安全帽检测错误: {str(e)}")

        if self.detectors["vest"].is_enabled:
            try:
                processed_frame_vest, _, vest_stats = self.detectors["vest"].detect(display_frame)
                display_frame = processed_frame_vest.copy()

                for _ in range(vest_stats.get("vest", 0)):
                    detection_results.append({
                        "type": "vest",
                        "info": "检测到已穿反光衣",
                        "status": "已穿戴",
                        "confidence": 1.0,
                        "location": location_label,
                        "source": data_source,
                        "channel_name": channel_name,
                        "algorithm": "vest_detection"
                    })

                no_vest_count = vest_stats.get("no_vest", 0)
                if no_vest_count > 0:
                    violation_snapshot = display_frame.copy()
                    for _ in range(no_vest_count):
                        detection_results.append({
                            "type": "vest_violation",
                            "info": "检测到未穿反光衣",
                            "status": "未穿戴",
                            "confidence": 1.0,
                            "location": location_label,
                            "source": data_source,
                            "channel_name": channel_name,
                            "algorithm": "vest_detection",
                            "snapshot": violation_snapshot
                        })
            except Exception as e:
                print(f"反光衣检测错误: {str(e)}")
        
        # 添加检测统计信息到帧上
        display_frame = self._add_detection_stats(display_frame)
        
        # 更新当前帧用于视频流
        with self.frame_lock:
            self.current_frame = display_frame.copy()
        
        return detection_results
    
    def _draw_helmet_detection(self, frame, class_id, bbox, confidence):
        """绘制安全帽检测结果"""
        x1, y1, x2, y2 = bbox
        
        # 根据类别设置颜色和标签
        if class_id == 0:  # 安全帽
            color = (0, 255, 0)  # 绿色
            label = f"HELMET: {confidence:.1%}"
        elif class_id == 2:  # 未戴安全帽
            color = (0, 0, 255)  # 红色
            label = f"NO HELMET: {confidence:.1%}"
        
        # 绘制检测框
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        
        # 绘制标签背景
        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
        cv2.rectangle(frame, (x1, y1 - label_size[1] - 10), 
                     (x1 + label_size[0] + 10, y1), color, -1)
        
        # 绘制标签文字
        cv2.putText(frame, label, (x1 + 5, y1 - 5), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        return frame
    
    def _draw_vest_detection(self, frame, class_id, bbox, confidence):
        """绘制反光衣检测结果"""
        x1, y1, x2, y2 = bbox
        
        # 根据类别设置颜色和标签
        if class_id == 7:  # 安全背心
            color = (255, 165, 0)  # 橙色
            label = f"VEST: {confidence:.1%}"
        else:  # 未穿安全背心
            color = (0, 0, 255)  # 红色
            label = f"NO VEST: {confidence:.1%}"
        
        # 绘制检测框
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        
        # 绘制标签背景
        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
        cv2.rectangle(frame, (x1, y1 - label_size[1] - 10), 
                     (x1 + label_size[0] + 10, y1), color, -1)
        
        # 绘制标签文字
        cv2.putText(frame, label, (x1 + 5, y1 - 5), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        return frame
    
    def _add_detection_stats(self, frame):
        """在帧上添加检测统计信息"""
        display_frame = frame.copy()
        height, width = display_frame.shape[:2]
        
        # 添加时间戳
        timestamp = datetime.now().strftime("%H:%M:%S")
        cv2.putText(display_frame, timestamp, (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        
        # 添加检测状态信息（简化显示）
        y_pos = height - 60
        
        # 安全帽检测状态
        helmet_status = "ON" if self.detectors['helmet'].is_enabled else "OFF"
        helmet_color = (0, 255, 0) if self.detectors['helmet'].is_enabled else (128, 128, 128)
        cv2.putText(display_frame, f"Helmet Detection: {helmet_status}", 
                   (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.5, helmet_color, 2)
        
        # 反光衣检测状态
        vest_status = "ON" if self.detectors['vest'].is_enabled else "OFF"
        vest_color = (0, 255, 0) if self.detectors['vest'].is_enabled else (128, 128, 128)
        cv2.putText(display_frame, f"Vest Detection: {vest_status}", 
                   (10, y_pos + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, vest_color, 2)
        
        return display_frame
    
    def _update_detection_data(self, detection_results):
        """更新检测数据"""
        with self.lock:
            current_time = datetime.now()
            time_str = current_time.strftime("%H:%M:%S")
            
            for result in detection_results:
                detection_type = result["type"]
                status = result["status"]
                
                # 跳过人员检测，只记录安全相关的检测
                if detection_type == "person":
                    continue
                
                # 更新总计数
                if detection_type in self.stats_data["summary"]:
                    self.stats_data["summary"][detection_type] += 1
                
                # 更新当前趋势计数
                if detection_type in self.current_trend_counts:
                    self.current_trend_counts[detection_type] += 1
                # 仅对违规检测生成告警记录
                if "violation" in detection_type:
                    self._create_alert_record(
                        detection_type,
                        {
                            **result,
                            "time": time_str
                        },
                        result.get("snapshot")
                    )
                
                # 获取检测类型名称
                type_name = self.detection_types.get(detection_type, {}).get('name', detection_type)
                print(f"检测到: {type_name} - {status}")
    
    def _update_trend_data(self):
        """更新趋势数据 - 统计最近7天的告警数据（实时更新版）"""
        with self.lock:
            # 计算最近7天的日期
            today = datetime.now().date()
            dates = [(today - timedelta(days=i)).strftime("%m-%d") for i in range(6, -1, -1)]
            
            # 初始化趋势数据结构
            trend_counts = {date: {} for date in dates}
            
            # 统计告警记录
            for record in self.alert_records:
                timestamp = record.get('timestamp', '')
                record_type = record.get('type', '')
                
                if not timestamp or not record_type:
                    continue
                
                # 只统计violation类型
                if 'violation' not in record_type:
                    continue
                
                try:
                    # 解析时间戳
                    normalized = timestamp if 'T' in timestamp else timestamp.replace(' ', 'T')
                    record_date = datetime.fromisoformat(normalized).date()
                    date_str = record_date.strftime("%m-%d")
                    
                    # 只统计最近7天的
                    if date_str in dates:
                        if date_str not in trend_counts:
                            trend_counts[date_str] = {}
                        if record_type not in trend_counts[date_str]:
                            trend_counts[date_str][record_type] = 0
                        trend_counts[date_str][record_type] += 1
                except (ValueError, AttributeError) as e:
                    # print(f"解析时间戳失败: {timestamp}, 错误: {e}")
                    continue
            
            # 更新stats_data
            self.stats_data["trend"]["timestamps"] = dates
            
            # 获取所有出现的violation类型
            all_types = set()
            for date_counts in trend_counts.values():
                all_types.update(date_counts.keys())
            
            # 为每种类型构建7天的数据数组
            for vtype in all_types:
                self.stats_data["trend"][vtype] = [
                    trend_counts[date].get(vtype, 0) for date in dates
                ]
            
            # print(f"趋势数据已更新: 最近7天 ({dates[0]} ~ {dates[-1]})")
            # print(f"  统计类型: {list(all_types)}")


    
    def get_stats_data(self):
        """获取统计数据"""
        # 更新趋势数据（每次调用时检查是否需要更新）
        self._update_trend_data()
        
        with self.lock:
            # 构建趋势数据字典，包含所有violation类型
            trend_data = {
                "timestamps": list(self.stats_data["trend"]["timestamps"])
            }
            
            # 动态添加所有violation类型的趋势数据
            for key in self.stats_data["trend"].keys():
                if key != "timestamps":
                    trend_data[key] = list(self.stats_data["trend"][key])
            
            return {
                "summary": dict(self.stats_data["summary"]),
                "trend": trend_data
            }

    def get_alert_records(self, limit=None, filters=None):
        """获取告警记录列表"""
        with self.lock:
            all_records = list(self.alert_records)
        filtered = self._filter_alerts(all_records, filters or {})
        meta = self._build_alert_meta(all_records, filtered)
        if limit is not None:
            filtered = filtered[:limit]
        return {"records": filtered, "meta": meta}
    
    def get_recent_logs(self, limit=20):
        """获取最近的日志"""
        data = self.get_alert_records(limit=limit)
        return data["records"]

    def update_alert_status(self, record_ids, new_status, note=None):
        """更新告警状态"""
        if isinstance(record_ids, str):
            record_ids = [record_ids]
        record_ids = set(record_ids or [])
        if not record_ids:
            return 0

        updated = 0
        with self.lock:
            now_iso = datetime.now().isoformat()
            for record in self.alert_records:
                if record["id"] in record_ids:
                    record["status"] = new_status
                    record["handled_at"] = now_iso
                    if note is not None:
                        record["note"] = note
                    updated += 1
            if updated:
                self._save_alert_records()
                self._refresh_alert_cache()
        return updated

    def delete_alert_records(self, record_ids):
        """删除告警记录"""
        if isinstance(record_ids, str):
            record_ids = [record_ids]
        record_ids = set(record_ids or [])
        if not record_ids:
            return 0

        with self.lock:
            before = len(self.alert_records)
            remaining = [rec for rec in self.alert_records if rec["id"] not in record_ids]
            maxlen = self.alert_records.maxlen or 500
            self.alert_records = deque(remaining, maxlen=maxlen)
            removed = before - len(self.alert_records)
            if removed:
                self._save_alert_records()
                self._refresh_alert_cache()
                self._rebuild_violation_summary_from_records()
        return removed
    
    def toggle_model(self, model_type):
        """切换模型状态"""
        if model_type in self.model_status:
            self.model_status[model_type] = not self.model_status[model_type]
            
            # 如果摄像头在运行，实时更新检测器状态
            if self.is_camera_running:
                if model_type == "helmet_detection":
                    if self.model_status[model_type]:
                        self.detectors["helmet"].enable()
                    else:
                        self.detectors["helmet"].disable()
                elif model_type == "vest_detection":
                    if self.model_status[model_type]:
                        self.detectors["vest"].enable()
                    else:
                        self.detectors["vest"].disable()
            
            return self.model_status[model_type]
        
        return False
    
    def get_detection_types(self):
        """获取检测类型配置"""
        return self.detection_types
    
    def get_camera_status(self):
        """获取摄像头状态"""
        return {
            "is_running": self.is_camera_running,
            "camera_index": self.camera_index if self.is_camera_running else None
        }
    
    def reset_stats(self):
        """重置统计数据"""
        with self.lock:
            self.stats_data["summary"] = {
                "helmet": 0, 
                "vest": 0, 
                "helmet_violation": 0, 
                "vest_violation": 0
            }
            self.stats_data["logs"].clear()
            self.current_trend_counts = {
                "helmet": 0, 
                "vest": 0, 
                "helmet_violation": 0, 
                "vest_violation": 0
            }
            
            # 重置检测器计数
            self.detectors["helmet"].reset_count()
            self.detectors["vest"].reset_count()
            
        print("统计数据已重置")
    
    def generate_frames(self):
        """生成视频帧流"""
        frame_count = 0
        while True:
            try:
                with self.frame_lock:
                    if self.current_frame is not None:
                        frame = self.current_frame.copy()
                    else:
                        # 如果没有帧，创建一个默认背景
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        cv2.rectangle(frame, (0, 0), (640, 480), (20, 20, 20), -1)
                        cv2.putText(frame, "Camera Feed Loading...", (150, 240), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                        cv2.putText(frame, f"Frame: {frame_count}", (10, 30), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                
                # 编码为JPEG
                try:
                    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    if buffer is not None and len(buffer) > 0:
                        frame_bytes = buffer.tobytes()
                        
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                        frame_count += 1
                    else:
                        print("视频帧编码失败：buffer为空")
                        time.sleep(0.1)
                        continue
                        
                except Exception as e:
                    print(f"视频帧编码错误: {str(e)}")
                    time.sleep(0.1)
                    continue
                
                # 控制帧率
                time.sleep(0.033)  # 约30fps
                
            except Exception as e:
                print(f"视频流生成错误: {str(e)}")
                time.sleep(0.1)
                continue
    
    def get_current_frame_base64(self):
        """获取当前帧的Base64编码（用于静态图片展示）"""
        with self.frame_lock:
            if self.current_frame is not None:
                frame = self.current_frame.copy()
            else:
                # 创建默认帧
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(frame, "No Camera Feed", (200, 240), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        try:
            _, buffer = cv2.imencode('.jpg', frame)
            jpg_as_text = base64.b64encode(buffer).decode()
            return f"data:image/jpeg;base64,{jpg_as_text}"
        except Exception as e:
            print(f"帧编码为Base64失败: {str(e)}")
            return None
    
    # 摄像头管理功能
    def get_camera_list(self):
        """获取摄像头列表"""
        with self.lock:
            # 如果还没有摄像头列表，创建默认的
            if not hasattr(self, 'cameras'):
                self.cameras = [
                    {
                        "id": 1, 
                        "name": "摄像头-0", 
                        "status": "在线" if self.is_camera_running else "离线", 
                        "location": "实时检测区域",
                        "camera_index": 0
                    }
                ]
            
            # 更新当前摄像头状态
            for camera in self.cameras:
                if camera["camera_index"] == self.camera_index and self.is_camera_running:
                    camera["status"] = "在线"
                else:
                    camera["status"] = "离线"
            
            return self.cameras.copy()
    
    def add_camera(self, name, location, camera_index):
        """添加新摄像头"""
        with self.lock:
            if not hasattr(self, 'cameras'):
                self.cameras = []
            
            # 检查摄像头索引是否已存在
            for camera in self.cameras:
                if camera["camera_index"] == camera_index:
                    return False, f"摄像头索引 {camera_index} 已存在"
            
            # 生成新ID
            max_id = max([cam["id"] for cam in self.cameras]) if self.cameras else 0
            new_id = max_id + 1
            
            # 添加新摄像头
            new_camera = {
                "id": new_id,
                "name": name,
                "location": location,
                "camera_index": camera_index,
                "status": "离线"
            }
            
            self.cameras.append(new_camera)
            
            print(f"添加摄像头: {name} (索引: {camera_index})")
            return True, f"摄像头 {name} 添加成功"
    
    def update_camera(self, camera_id, data):
        """更新摄像头信息"""
        with self.lock:
            if not hasattr(self, 'cameras'):
                return False, "摄像头列表为空"
            
            # 查找要更新的摄像头
            camera = None
            for cam in self.cameras:
                if cam["id"] == camera_id:
                    camera = cam
                    break
            
            if not camera:
                return False, f"未找到ID为 {camera_id} 的摄像头"
            
            # 如果要更新camera_index，检查是否冲突
            if "camera_index" in data and data["camera_index"] != camera["camera_index"]:
                for cam in self.cameras:
                    if cam["id"] != camera_id and cam["camera_index"] == data["camera_index"]:
                        return False, f"摄像头索引 {data['camera_index']} 已被使用"
            
            # 更新字段
            for key, value in data.items():
                if key in ["name", "location", "camera_index"]:
                    camera[key] = value
            
            print(f"更新摄像头: {camera['name']}")
            return True, f"摄像头 {camera['name']} 更新成功"
    
    def delete_camera(self, camera_id):
        """删除摄像头"""
        with self.lock:
            if not hasattr(self, 'cameras'):
                return False, "摄像头列表为空"
            
            # 查找要删除的摄像头
            camera_to_delete = None
            for i, cam in enumerate(self.cameras):
                if cam["id"] == camera_id:
                    camera_to_delete = (i, cam)
                    break
            
            if not camera_to_delete:
                return False, f"未找到ID为 {camera_id} 的摄像头"
            
            index, camera = camera_to_delete
            
            # 如果要删除的摄像头正在使用，先停止检测
            if camera["camera_index"] == self.camera_index and self.is_camera_running:
                self.stop_camera_detection()
            
            # 删除摄像头
            self.cameras.pop(index)
            
            print(f"删除摄像头: {camera['name']}")
            return True, f"摄像头 {camera['name']} 删除成功"
    
    def test_camera_connection(self, camera_id):
        """测试摄像头连接"""
        with self.lock:
            if not hasattr(self, 'cameras'):
                return False, "摄像头列表为空"
            
            # 查找摄像头
            camera = None
            for cam in self.cameras:
                if cam["id"] == camera_id:
                    camera = cam
                    break
            
            if not camera:
                return False, f"未找到ID为 {camera_id} 的摄像头"
        
        # 测试连接（不在锁内执行，避免阻塞）
        import cv2
        try:
            test_cap = cv2.VideoCapture(camera["camera_index"])
            if test_cap.isOpened():
                ret, frame = test_cap.read()
                test_cap.release()
                
                if ret and frame is not None:
                    return True, f"摄像头 {camera['name']} 连接正常"
                else:
                    return False, f"摄像头 {camera['name']} 无法读取图像"
            else:
                return False, f"摄像头 {camera['name']} 无法打开"
        except Exception as e:
            return False, f"摄像头 {camera['name']} 测试失败: {str(e)}"

    def _encode_frame_to_data_url(self, frame):
        """将 OpenCV 帧编码为 data URL（jpeg/base64）"""
        if frame is None:
            return None
        try:
            ok, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok or buffer is None:
                return None
            return f"data:image/jpeg;base64,{base64.b64encode(buffer).decode()}"
        except Exception:
            return None

    def cache_theft_frames(self, source_id, source_name, source_location, frame_items, person_count=0, confidence=None):
        """缓存偷盗检测触发时的最近10帧，不写告警记录，不触发推送"""
        encoded_frames = []
        for item in list(frame_items or [])[-self.theft_cache_frame_limit:]:
            captured_at = None
            frame = None

            if isinstance(item, dict):
                frame = item.get("frame")
                captured_at = item.get("captured_at")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                captured_at, frame = item[0], item[1]
            else:
                frame = item

            encoded = self._encode_frame_to_data_url(frame)
            if not encoded:
                continue

            if isinstance(captured_at, (int, float)):
                captured_at = datetime.fromtimestamp(captured_at).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            elif captured_at is None:
                captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            else:
                captured_at = str(captured_at)

            encoded_frames.append({
                "captured_at": captured_at,
                "image": encoded,
            })

        if not encoded_frames:
            return

        source_key = str(source_id)
        payload = {
            "source_id": source_id,
            "channel_name": source_name,
            "location": source_location,
            "algorithm": "theft_detection",
            "person_count": int(person_count or 0),
            "confidence": round(float(confidence), 4) if confidence is not None else None,
            "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "frame_count": len(encoded_frames),
            "frames": encoded_frames,
        }

        with self.theft_cache_lock:
            if source_key not in self.theft_frame_cache:
                if len(self.theft_cache_sources) == self.theft_cache_sources.maxlen:
                    oldest_key = self.theft_cache_sources.popleft()
                    self.theft_frame_cache.pop(oldest_key, None)
                self.theft_cache_sources.append(source_key)
            self.theft_frame_cache[source_key] = payload

    def get_theft_frame_cache(self, source_id=None):
        """获取偷盗检测帧缓存"""
        with self.theft_cache_lock:
            if source_id is not None:
                key = str(source_id)
                return {
                    "source_id": source_id,
                    "cache": self.theft_frame_cache.get(key),
                }

            source_ids = list(self.theft_cache_sources)
            caches = [self.theft_frame_cache[k] for k in source_ids if k in self.theft_frame_cache]
            return {
                "total_sources": len(caches),
                "sources": caches,
            }

    def add_theft_llm_result(self, result: dict):
        """保存偷盗大模型判定结果，供前端查询"""
        if not isinstance(result, dict):
            return
        with self.theft_llm_lock:
            self.theft_llm_results.appendleft(result)

    def get_theft_llm_results(self, limit=20, source_id=None):
        """获取偷盗大模型判定结果"""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))

        with self.theft_llm_lock:
            records = list(self.theft_llm_results)

        if source_id is not None:
            source_id = str(source_id)
            records = [r for r in records if str(r.get("source_id")) == source_id]

        return records[:limit]

    def sync_algorithms_from_sources(self):
        """根据已启用的视频源算法配置，同步检测器启用状态"""
        algorithms_in_use = set()
        try:
            if self.video_source_path.exists():
                raw_text = self.video_source_path.read_text(encoding="utf-8").strip() or "[]"
                sources = json.loads(raw_text)
                for source in sources:
                    if source.get("enabled", True):
                        for algorithm in source.get("algorithms", []):
                            algorithms_in_use.add(str(algorithm))
        except Exception as exc:  # pylint: disable=broad-except
            print(f"同步算法配置失败: {exc}")
            return

        desired_state = {
            "helmet_detection": "helmet_detection" in algorithms_in_use,
            "vest_detection": "vest_detection" in algorithms_in_use
        }

        for model_key, detector_key in (("helmet_detection", "helmet"), ("vest_detection", "vest")):
            if model_key not in self.model_status:
                continue
            should_enable = desired_state.get(model_key, False)
            self.model_status[model_key] = should_enable

            detector = self.detectors.get(detector_key)
            if not detector:
                continue

            if should_enable and not detector.is_enabled:
                detector.enable()
            elif not should_enable and detector.is_enabled:
                detector.disable()
