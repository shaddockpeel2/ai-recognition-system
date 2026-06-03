from flask import Flask, render_template, jsonify, request, Response, redirect, send_from_directory, session, url_for, g
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from urllib.parse import urlparse
from datetime import datetime, timedelta
import random
import json
import threading
import atexit
import logging
from pathlib import Path
import socket
import os
import configparser


def load_dotenv_file(path=".env"):
    """Load simple KEY=VALUE pairs before service modules read environment."""
    env_path = Path(path)
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as exc:
        print(f"Load .env failed: {exc}")


def detect_lan_ip(default="127.0.0.1"):
    """Best-effort local LAN IP detection for Windows access hints and ZLM URLs."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return default


def configure_windows_zlm():
    """Point optional ZLM gateway integration at the Windows Release01 bundle."""
    base_dir = Path(__file__).resolve().parent
    release_dir = base_dir / "Release01"
    zlm_bin = release_dir / "MediaServer.exe"
    source_config = release_dir / "config.ini"
    runtime_config = base_dir / "data" / "zlm_windows_config.ini"

    if not zlm_bin.exists() or not source_config.exists():
        print("[ZLM-Windows] Release01/MediaServer.exe or config.ini not found; ZLM gateway remains optional.")
        return

    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(source_config, encoding="utf-8")

    if not parser.has_section("api"):
        parser.add_section("api")
    if not parser.has_section("http"):
        parser.add_section("http")
    if not parser.has_section("rtsp"):
        parser.add_section("rtsp")
    if not parser.has_section("rtmp"):
        parser.add_section("rtmp")

    http_port = os.getenv("SAFEHAT_ZLM_HTTP_PORT", "8080")
    rtsp_port = os.getenv("SAFEHAT_ZLM_RTSP_PORT", "8554")
    rtmp_port = os.getenv("SAFEHAT_ZLM_RTMP_PORT", "1935")
    http_ssl_port = os.getenv("SAFEHAT_ZLM_HTTPS_PORT", "8443")
    secret = os.getenv("SAFEHAT_ZLM_SECRET") or parser.get(
        "api",
        "secret",
        fallback="035c73f7-bb6b-4889-a715-d9eb2d1925cc",
    )

    parser.set("api", "secret", secret)
    parser.set("http", "port", str(http_port))
    parser.set("http", "sslport", str(http_ssl_port))
    parser.set("rtsp", "port", str(rtsp_port))
    parser.set("rtmp", "port", str(rtmp_port))

    runtime_config.parent.mkdir(parents=True, exist_ok=True)
    with runtime_config.open("w", encoding="utf-8") as fp:
        parser.write(fp)

    os.environ.setdefault("SAFEHAT_ZLM_ENABLED", "1")
    os.environ["SAFEHAT_ZLM_BIN"] = str(zlm_bin)
    os.environ["SAFEHAT_ZLM_CONFIG"] = str(runtime_config)
    os.environ["SAFEHAT_ZLM_API_BASE"] = f"http://127.0.0.1:{http_port}"
    os.environ["SAFEHAT_ZLM_SECRET"] = secret
    os.environ["SAFEHAT_ZLM_HTTP_PORT"] = str(http_port)
    os.environ["SAFEHAT_ZLM_RTSP_PORT"] = str(rtsp_port)
    os.environ["SAFEHAT_ZLM_RTMP_PORT"] = str(rtmp_port)
    os.environ.setdefault("SAFEHAT_ZLM_PUBLIC_HOST", detect_lan_ip())
    os.environ.setdefault("SAFEHAT_ZLM_INTERNAL_HOST_MODE", "loopback")


load_dotenv_file()
configure_windows_zlm()

# 导入真实检测管理器
from safehat.core.real_detection import RealDetectionManager

# 导入新的管理器
from safehat.managers.channel_manager import channel_manager
from safehat.managers.alert_manager import alert_manager
from safehat.managers.merge_manager import merge_manager
from server.routers.channels import channels_bp
from server.routers.alerts import alerts_bp
from server.routers.merge import merge_bp
from server.routers.video_sources import video_sources_bp
from server.routers.push import push_bp

app = Flask(__name__)

# 配置日志级别 - 显示启动信息和访问日志
log = logging.getLogger('werkzeug')
log.setLevel(logging.INFO)  # 显示启动信息和访问日志

# 加载系统配置
import psutil
import time
start_time = time.time()
# 测试
# 系统配置存储（从文件加载或使用默认值）
SYSTEM_CONFIG_FILE = 'data/system_config.json'
def load_system_config():
    """加载系统配置"""
    default_config = {
        'dashboard_title': 'AI安全隐患识别系统',
        'max_channels': 6,
        'language': 'zh-CN'  # 默认中文
    }
    try:
        if os.path.exists(SYSTEM_CONFIG_FILE):
            with open(SYSTEM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {**default_config, **config}
    except Exception as e:
        print(f"加载系统配置失败: {e}")
    return default_config

def save_system_config(config):
    """保存系统配置"""
    try:
        # 确保data目录存在
        data_dir = os.path.dirname(SYSTEM_CONFIG_FILE)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
        
        with open(SYSTEM_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"系统配置已保存到: {SYSTEM_CONFIG_FILE}")
        return True
    except Exception as e:
        print(f"保存系统配置失败: {e}")
        import traceback
        traceback.print_exc()
        return False

system_config = load_system_config()

# ============ 本地账号登录（仅存储在本地 data/users.json） ============
app.secret_key = os.getenv('SAFEHAT_SECRET_KEY', 'safehat-local-secret-key')
USERS_FILE = 'data/users.json'


def load_users():
    """从本地 JSON 加载用户列表。"""
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            users = data.get('users', [])
            return users if isinstance(users, list) else []
    except Exception as e:
        print(f"加载用户数据失败: {e}")
    return []


def save_users(users):
    """持久化本地用户列表。"""
    try:
        data_dir = os.path.dirname(USERS_FILE)
        if data_dir and not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'users': users}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存用户数据失败: {e}")
        return False


def has_any_user():
    return len(load_users()) > 0


def get_user(username):
    username = (username or '').strip()
    if not username:
        return None
    for user in load_users():
        if str(user.get('username', '')).strip().lower() == username.lower():
            return user
    return None


def create_user(username, password):
    username = (username or '').strip()
    password = password or ''

    if len(username) < 3:
        return False, '用户名至少 3 位'
    if len(password) < 6:
        return False, '密码至少 6 位'
    if get_user(username):
        return False, '用户名已存在'

    users = load_users()
    users.append({
        'username': username,
        'password_hash': generate_password_hash(password),
        'created_at': datetime.now().isoformat(),
    })
    if not save_users(users):
        return False, '用户保存失败'
    return True, '注册成功'


def authenticate_user(username, password):
    user = get_user(username)
    if not user:
        return False
    return check_password_hash(user.get('password_hash', ''), password or '')


def get_safe_next_url(default='dashboard'):
    next_url = (request.values.get('next') or '').strip()
    if not next_url:
        return url_for(default)

    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return url_for(default)
    if not next_url.startswith('/'):
        return url_for(default)
    if next_url.startswith('//'):
        return url_for(default)
    return next_url


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if session.get('auth_user'):
            return view_func(*args, **kwargs)
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'message': '请先登录'}), 401
        return redirect(url_for('login', next=request.full_path if request.query_string else request.path))
    return wrapped


@app.context_processor
def inject_auth_user():
    return {'current_user': session.get('auth_user')}


@app.before_request
def enforce_authentication():
    endpoint = request.endpoint or ''
    g.current_user = session.get('auth_user')

    if endpoint in {'login', 'register', 'static'} or request.path.startswith('/static/'):
        return None

    if not has_any_user():
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'message': '请先注册本地账号'}), 401
        return redirect(url_for('register', next=request.full_path if request.query_string else request.path))

    if session.get('auth_user'):
        return None

    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': '请先登录'}), 401
    return redirect(url_for('login', next=request.full_path if request.query_string else request.path))

# MQTT 配置存储
MQTT_CONFIG_FILE = 'data/mqtt_config.json'
def load_mqtt_config():
    """加载 MQTT 兼容镜像配置"""
    default_config = {
        'MQBroker': 'localhost',
        'MQPort': 1883,
        'MQRootTopic': 'safehat',
        'EnableLogUploader': False,
        'SecPingInterval': 60,
        'MQIdPattern': '',
        'MQClientId': '',
        'MQUsername': '',
        'MQPassword': '',
        'MQUniqueSubscribe': False,
        'LogLevel': 'ERROR'
    }
    try:
        if os.path.exists(MQTT_CONFIG_FILE):
            with open(MQTT_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return {**default_config, **config}
    except Exception as e:
        print(f"加载 MQTT 配置失败: {e}")
    return default_config

def save_mqtt_config(config):
    """保存 MQTT 兼容镜像配置"""
    try:
        data_dir = os.path.dirname(MQTT_CONFIG_FILE)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
        with open(MQTT_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存 MQTT 配置失败: {e}")
        return False

from safehat.managers.push_manager import get_push_manager
mqtt_config = get_push_manager().build_system_mqtt_config()

# 初始化 MQTT 日志上报（运行时配置来自 MQTT 推送目标）
from safehat.managers import mqtt_log_handler as _mqtt_log
_mqtt_log.setup(mqtt_config)

# 算法配置存储
ALGORITHM_CONFIG_FILE = 'data/algorithm_config.json'
ALGORITHM_MODEL_DIRS = {
    'helmet_detection': 'models/safehat',
    'vest_detection': 'models/safehat',
    'smoke_detection': 'models/烟火检测',
    'fire_detection': 'models/烟火检测',
    'fall_detection': 'models/人员跌倒1.0',
    'mask_detection': 'models/口罩佩戴',
    'intrusion_detection': 'models/人员识别',
    'theft_detection': 'models/人员识别',
    'crowd_gathering_detection': 'models/人员识别',
    'person_absence_detection': 'models/人员识别',
    'vehicle_parking_detection': 'models/小汽车识别',
    'fire_extinguisher_absence_detection': 'models/灭火器识别',
    'reagent_bottle_detection': 'models/试剂瓶',
    'camera_occlusion_detection': 'models/摄像头遮挡',
    'fight_detection': 'models/打架识别',
    'glove_detection': 'models/手套检测',
    'crack_detection': 'models/缝隙检测',
    'dog_detection': 'models/dog',
    'cat_detection': 'models/cat',
    'fatigue_driving_detection': 'models/fatigue_driving',
    'trash_detection': 'models/trash',
    'electric_bicycle_detection': 'models/electric bicycle',
    'work_clothes_detection': 'models/工作服检测',
    'safehat_vest_detection': 'models/安全帽反光衣',
    'gas_cylinder_tilt_detection': 'models/钢瓶倾倒检测',
    'box_object_detection': 'models/box',
}
ALLOWED_MODEL_EXTENSIONS = {'.pt', '.onnx', '.engine', '.rknn', '.bin', '.xml', '.so'}

# 每个算法的默认配置（与前端 JS 保持一致）
_DEFAULT_ALGORITHM_CONFIG = {
    'helmet_detection':                   {'confidence_threshold': 0.5},
    'vest_detection':                     {'confidence_threshold': 0.5},
    'smoke_detection':                    {'confidence_threshold': 0.5},
    'fire_detection':                     {'confidence_threshold': 0.5},
    'fall_detection':                     {'confidence_threshold': 0.5, 'fall_duration_threshold': 2.0},
    'mask_detection':                     {'confidence_threshold': 0.5},
    'intrusion_detection':                {'confidence_threshold': 0.5},
    'crowd_gathering_detection':          {'confidence_threshold': 0.5, 'min_people': 3, 'duration_threshold': 10.0},
    'person_absence_detection':           {'confidence_threshold': 0.5, 'absence_duration_threshold': 10.0},
    'vehicle_parking_detection':          {'confidence_threshold': 0.5, 'parking_duration_threshold': 30.0},
    'fire_extinguisher_absence_detection':{'confidence_threshold': 0.5, 'absence_duration_threshold': 10.0},
    'reagent_bottle_detection':           {'confidence_threshold': 0.5},
    'camera_occlusion_detection':         {'confidence_threshold': 0.2},
    'fight_detection':                    {'confidence_threshold': 0.5, 'fight_duration_threshold': 5.0},
    'glove_detection':                    {'confidence_threshold': 0.5},
    'crack_detection':                    {'confidence_threshold': 0.5},
    'dog_detection':                      {'confidence_threshold': 0.5},
    'cat_detection':                      {'confidence_threshold': 0.5},
    'fatigue_driving_detection':          {'confidence_threshold': 0.5},
    'trash_detection':                    {'confidence_threshold': 0.5},
    'electric_bicycle_detection':         {'confidence_threshold': 0.5},
    'work_clothes_detection':             {'confidence_threshold': 0.5},
    'safehat_vest_detection':             {'confidence_threshold': 0.5},
    'gas_cylinder_tilt_detection':        {'confidence_threshold': 0.5},
    'box_object_detection':               {'confidence_threshold': 0.5},
}

def load_algorithm_config():
    """加载算法配置（文件 > 默认值 deep merge）"""
    config = {k: dict(v) for k, v in _DEFAULT_ALGORITHM_CONFIG.items()}
    try:
        if os.path.exists(ALGORITHM_CONFIG_FILE):
            with open(ALGORITHM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            for algo, params in saved.items():
                if algo in config:
                    config[algo].update(params)
                else:
                    config[algo] = params
    except Exception as e:
        print(f"加载算法配置失败: {e}")
    return config

def save_algorithm_config(config: dict) -> bool:
    """持久化算法配置到 data/algorithm_config.json"""
    try:
        os.makedirs(os.path.dirname(ALGORITHM_CONFIG_FILE), exist_ok=True)
        with open(ALGORITHM_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存算法配置失败: {e}")
        return False

app.register_blueprint(channels_bp)
app.register_blueprint(alerts_bp)
app.register_blueprint(merge_bp)
app.register_blueprint(video_sources_bp)
app.register_blueprint(push_bp)

# 真实检测管理器 - 替换原有的MockData
real_detector = RealDetectionManager()
app.config['REAL_DETECTOR'] = real_detector

# RealDetectionManager 内部已创建一个 VSD 并启动了检测线程，
# 先将其停止，再用 app.py 统一管理的 VSD 替换，避免重复线程冲突。
if real_detector.video_source_detector is not None:
    try:
        real_detector.video_source_detector.stop_all_detections()
    except Exception as _e:
        print(f"停止内部 VSD 失败（非致命）: {_e}")

# 初始化视频源检测器（统一由 app.py 管理）
from safehat.core.video_source_detector import VideoSourceDetector
real_detector.video_source_detector = VideoSourceDetector(real_detector)

# 启动所有已配置的视频源检测
real_detector.video_source_detector.restart_all_from_config()

# 将持久化的算法配置（data/algorithm_config.json）应用到已启动的检测器，
# 确保每次重启后阈值等参数都能立即生效。
try:
    real_detector.video_source_detector.update_all_detector_configs(load_algorithm_config())
    print("已将保存的算法配置应用到所有检测器")
except Exception as _e:
    print(f"应用算法配置失败（非致命）: {_e}")

# 注册程序退出时的清理函数
def cleanup():
    print("正在清理资源...")
    real_detector.stop_camera_detection()
    if hasattr(real_detector, 'video_source_detector') and real_detector.video_source_detector:
        real_detector.video_source_detector.stop_all_detections()
    _mqtt_log.teardown()   # 优雅关闭 MQTT 连接

atexit.register(cleanup)

# 保留MockData作为备用（如果真实检测失败时使用）
class MockData:
    def __init__(self):
        # 支持多种检测类别的计数
        self.detection_stats = {
            "helmet": 0,
            "vest": 0,
            "smoke": 0
        }
        
        # 检测类别配置
        self.detection_types = {
            "helmet": {"name": "安全帽", "icon": "🪖", "color": "#00ff88"},
            "vest": {"name": "反光衣", "icon": "🦺", "color": "#00d4ff"},
            "smoke": {"name": "烟雾检测", "icon": "🚭", "color": "#ff6b35"}
        }
        
        self.cameras = [
            {"id": 1, "name": "摄像头-1", "status": "在线", "location": "入口处"},
            {"id": 2, "name": "摄像头-2", "status": "在线", "location": "工作区A"},
            {"id": 3, "name": "摄像头-3", "status": "离线", "location": "工作区B"},
        ]
        self.model_status = {
            "helmet_detection": True,
            "vest_detection": True,
            "smoke_detection": False
        }
        self.system_config = {
            "detection_threshold": 0.5,
            "alert_interval": 30,
            "max_alerts": 100
        }
        self.alerts = []
        self.logs = []
        self.generate_mock_data()
    
    def generate_mock_data(self):
        """生成模拟数据"""
        locations = ["入口处", "工作区A", "工作区B", "出口处"]
        status_map = {
            "helmet": ["未佩戴", "佩戴不规范", "正常佩戴"],
            "vest": ["未穿戴", "穿戴不规范", "正常穿戴"], 
            "smoke": ["检测到烟雾", "疑似烟雾", "烟雾消散"]
        }
        
        # 生成历史告警数据
        for i in range(30):
            alert_time = datetime.now() - timedelta(minutes=random.randint(1, 720))
            detection_type = random.choice(list(self.detection_types.keys()))
            location = random.choice(locations)
            
            # 更新统计计数
            self.detection_stats[detection_type] += 1
            
            self.alerts.append({
                "id": i + 1,
                "type": self.detection_types[detection_type]["name"],
                "location": location,
                "time": alert_time.strftime("%Y-%m-%d %H:%M:%S"),
                "level": random.choice(["高", "中", "低"])
            })
        
        # 生成日志数据
        for i in range(50):
            log_time = datetime.now() - timedelta(minutes=random.randint(1, 1440))
            detection_type = random.choice(list(self.detection_types.keys()))
            status = random.choice(status_map[detection_type])
            
            self.logs.append({
                "time": log_time.strftime("%H:%M:%S"),
                "type": detection_type,
                "status": status,
                "location": random.choice(locations),
                "timestamp": log_time
            })
        
        # 按时间排序日志
        self.logs.sort(key=lambda x: x['timestamp'], reverse=True)
    
    def get_stats_data(self):
        """获取统计数据和趋势数据"""
        # 生成时间戳（最近12个时间点，每5分钟一个点）
        timestamps = []
        trend_data = {}
        
        # 初始化每个检测类型的趋势数据
        for detection_type in self.detection_types.keys():
            trend_data[detection_type] = []
        
        for i in range(12):
            time_point = datetime.now() - timedelta(minutes=55 - i * 5)
            timestamps.append(time_point.strftime("%H:%M"))
            
            # 为每个检测类型生成随机趋势数据
            for detection_type in self.detection_types.keys():
                if self.model_status.get(f"{detection_type}_detection", False):
                    # 如果模型启用，生成更多数据
                    trend_data[detection_type].append(random.randint(0, 8))
                else:
                    # 如果模型未启用，数据为0
                    trend_data[detection_type].append(0)
        
        return {
            "summary": self.detection_stats.copy(),
            "trend": {
                "timestamps": timestamps,
                **trend_data
            }
        }
    
    def get_recent_logs(self, limit=20):
        """获取最近的日志"""
        return self.logs[:limit]
    
    def add_new_detection(self):
        """模拟新的检测结果"""
        if random.random() < 0.4:  # 40%概率产生新检测
            detection_type = random.choice(list(self.detection_types.keys()))
            
            # 只有当对应模型启用时才产生检测结果
            if not self.model_status.get(f"{detection_type}_detection", False):
                return None
            
            locations = ["入口处", "工作区A", "工作区B", "出口处"]
            status_map = {
                "helmet": ["未佩戴", "佩戴不规范"],
                "vest": ["未穿戴", "穿戴不规范"], 
                "smoke": ["检测到烟雾", "疑似烟雾"]
            }
            
            new_log = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "type": detection_type,
                "status": random.choice(status_map[detection_type]),
                "location": random.choice(locations),
                "timestamp": datetime.now()
            }
            
            # 添加到日志开头
            self.logs.insert(0, new_log)
            # 限制日志数量
            if len(self.logs) > 100:
                self.logs = self.logs[:100]
            
            # 更新统计
            self.detection_stats[detection_type] += 1
            
            return new_log
        
        return None

# 全局数据对象 - 使用真实检测管理器
# mock_data = MockData()  # 保留作为备用

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('auth_user'):
        return redirect(get_safe_next_url())

    if not has_any_user():
        return redirect(url_for('register', next=request.values.get('next', '')))

    error = ''
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if authenticate_user(username, password):
            session['auth_user'] = username
            return redirect(get_safe_next_url())
        error = '用户名或密码错误'

    return render_template(
        'auth.html',
        auth_mode='login',
        auth_error=error,
        system_language=system_config.get('language', 'zh-CN'),
        has_users=has_any_user(),
        next_url=get_safe_next_url(),
    )


@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('auth_user'):
        return redirect(get_safe_next_url())

    error = ''
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''

        if password != confirm_password:
            error = '两次输入的密码不一致'
        else:
            success, message = create_user(username, password)
            if success:
                session['auth_user'] = username
                return redirect(get_safe_next_url())
            error = message

    return render_template(
        'auth.html',
        auth_mode='register',
        auth_error=error,
        system_language=system_config.get('language', 'zh-CN'),
        has_users=has_any_user(),
        next_url=get_safe_next_url(),
    )


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/account/profile')
def api_account_profile():
    username = session.get('auth_user') or ''
    return jsonify({'success': True, 'data': {'username': username}})


@app.route('/api/account/update', methods=['POST'])
def api_account_update():
    current_username = session.get('auth_user')
    if not current_username:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    payload = request.get_json() or {}
    target_username = (payload.get('username') or '').strip() or current_username
    old_password = payload.get('old_password') or ''
    new_password = payload.get('new_password') or ''
    confirm_password = payload.get('confirm_password') or ''

    if not authenticate_user(current_username, old_password):
        return jsonify({'success': False, 'message': '原密码错误'}), 400

    if len(target_username) < 3:
        return jsonify({'success': False, 'message': '用户名至少 3 位'}), 400

    if new_password:
        if len(new_password) < 6:
            return jsonify({'success': False, 'message': '新密码至少 6 位'}), 400
        if new_password != confirm_password:
            return jsonify({'success': False, 'message': '两次输入的新密码不一致'}), 400

    users = load_users()
    current_idx = None
    for idx, user in enumerate(users):
        if str(user.get('username', '')).strip().lower() == current_username.lower():
            current_idx = idx
            break

    if current_idx is None:
        return jsonify({'success': False, 'message': '当前账号不存在'}), 404

    for idx, user in enumerate(users):
        if idx == current_idx:
            continue
        if str(user.get('username', '')).strip().lower() == target_username.lower():
            return jsonify({'success': False, 'message': '用户名已存在'}), 400

    users[current_idx]['username'] = target_username
    users[current_idx]['updated_at'] = datetime.now().isoformat()
    if new_password:
        users[current_idx]['password_hash'] = generate_password_hash(new_password)

    if not save_users(users):
        return jsonify({'success': False, 'message': '保存失败'}), 500

    session['auth_user'] = target_username
    return jsonify({'success': True, 'message': '账号信息已更新'})


@app.route('/')
def index():
    """重定向到数据大屏"""
    return render_template('redirect.html')

@app.route('/dashboard')
def dashboard():
    """数据大屏页面"""
    stats_data = real_detector.get_stats_data()
    recent_logs = real_detector.get_recent_logs(10)
    
    # 转换日志格式为告警格式（保持兼容性）
    recent_alerts = []
    for log in recent_logs:
        type_config = real_detector.detection_types.get(log.get("type"), {})
        alert = {
            "type": type_config.get("name", log.get("type", "未知告警")),
            "location": log.get("location", ""),
            "time": datetime.now().strftime("%Y-%m-%d ") + log.get("time", ""),
            "level": "高" if "未" in log.get("status", "") else "低"
        }
        recent_alerts.append(alert)
    
    return render_template('dashboard.html', 
                         detection_types=real_detector.get_detection_types(),
                         stats_data=stats_data,
                         alerts=recent_alerts,
                         total_alerts=len(recent_logs),
                         dashboard_title=system_config.get('dashboard_title', 'AI安全隐患识别系统'),
                         max_channels=system_config.get('max_channels', 6),
                         system_language=system_config.get('language', 'zh-CN'))

@app.route('/admin')
def admin():
    """后台管理页面"""
    # 获取摄像头列表
    cameras = real_detector.get_camera_list()
    
    # 使用全局系统配置
    admin_system_config = {
        "detection_threshold": 0.5,
        "alert_interval": 30,
        "max_alerts": 100
    }
    
    return render_template('admin.html',
                         cameras=cameras,
                         model_status=real_detector.model_status,
                         system_config=admin_system_config,
                         detection_types=real_detector.get_detection_types(),
                         system_language=system_config.get('language', 'zh-CN'))

@app.route('/alert_screenshots/<path:filename>')
def alert_screenshots(filename):
    """提供告警截图静态访问"""
    return send_from_directory('alert_screenshots', filename, as_attachment=False)

def _get_alert_snapshot_files():
    """获取本地告警截图文件列表，按修改时间从新到旧排序"""
    screenshots_dir = Path('alert_screenshots')
    if not screenshots_dir.exists():
        return []

    allowed_suffixes = {'.jpg', '.jpeg', '.png', '.webp'}
    files = []
    for file_path in screenshots_dir.iterdir():
        if not file_path.is_file() or file_path.suffix.lower() not in allowed_suffixes:
            continue
        stat = file_path.stat()
        files.append({
            'name': file_path.name,
            'url': f'/alert_screenshots/{file_path.name}',
            'size': stat.st_size,
            'size_kb': round(stat.st_size / 1024, 1),
            'modified_at': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            'mtime': stat.st_mtime
        })

    return sorted(files, key=lambda item: item['mtime'], reverse=True)

@app.route('/api/system/alert_snapshot_cache', methods=['GET', 'POST'])
def api_alert_snapshot_cache():
    """告警截图缓存统计和清理"""
    try:
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            cleanup_count = int(data.get('cleanup_count', 0))
            if cleanup_count <= 0:
                return jsonify({'success': False, 'message': '清理数量必须大于0'}), 400

            files = _get_alert_snapshot_files()
            # 按最旧优先删除，避免误删最近告警截图。
            files_to_delete = sorted(files, key=lambda item: item['mtime'])[:cleanup_count]
            deleted_count = 0
            for item in files_to_delete:
                try:
                    (Path('alert_screenshots') / item['name']).unlink()
                    deleted_count += 1
                except OSError as exc:
                    app.logger.warning('删除告警截图失败 %s: %s', item['name'], exc)

            return jsonify({
                'success': True,
                'message': f'已清理 {deleted_count} 张告警截图',
                'data': {'deleted_count': deleted_count}
            })

        files = _get_alert_snapshot_files()
        total_size = sum(item['size'] for item in files)
        return jsonify({
            'success': True,
            'data': {
                'count': len(files),
                'total_size': total_size,
                'total_size_mb': round(total_size / 1024 / 1024, 2),
                'files': [
                    {key: item[key] for key in ('name', 'url', 'size_kb', 'modified_at')}
                    for item in files[:30]
                ]
            }
        })
    except Exception as e:
        app.logger.exception('读取告警截图缓存失败')
        return jsonify({'success': False, 'message': f'读取告警截图缓存失败: {e}'}), 500

@app.route('/api/network/interfaces')
def api_network_interfaces():
    """读取本机网口信息"""
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        interfaces = []

        for name, addr_list in addrs.items():
            mac = ''
            ipv4 = []
            ipv6 = []

            for addr in addr_list:
                if addr.family == socket.AF_INET:
                    ipv4.append({
                        'address': addr.address,
                        'netmask': addr.netmask or '',
                        'gateway': ''
                    })
                elif addr.family == socket.AF_INET6:
                    ipv6.append({'address': addr.address})
                elif getattr(psutil, 'AF_LINK', None) is not None and addr.family == psutil.AF_LINK:
                    mac = addr.address

            stat = stats.get(name)
            interfaces.append({
                'name': name,
                'is_up': bool(stat.isup) if stat else False,
                'mac': mac,
                'ipv4': ipv4,
                'ipv6': ipv6
            })

        return jsonify({'success': True, 'data': interfaces})
    except Exception as e:
        app.logger.exception('读取网口信息失败')
        return jsonify({'success': False, 'message': f'读取网口信息失败: {e}'}), 500

@app.route('/api/network/interface/config', methods=['POST'])
def api_network_interface_config():
    """网口配置占位接口，避免前端操作返回 404"""
    return jsonify({
        'success': False,
        'message': '当前后端暂未启用网口配置写入，仅支持读取网口信息'
    }), 501

def _parse_datetime_param(value):
    if not value:
        return None
    # datetime-local 可能返回 "2024-01-01T10:30"（无秒）或 "2024-01-01T10:30:00"（有秒）
    formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

@app.route('/api/alert_records')
def api_alert_records():
    """查询告警记录"""
    limit = request.args.get('limit', type=int)
    filters = {}
    type_filter = request.args.get('type')
    if type_filter and type_filter != 'all':
        filters['type'] = type_filter
    status_filter = request.args.get('status')
    if status_filter and status_filter != 'all':
        filters['status'] = status_filter
    risk_filter = request.args.get('risk_level')
    if risk_filter and risk_filter != 'all':
        filters['risk_level'] = risk_filter
    source_filter = request.args.get('source')
    if source_filter and source_filter != 'all':
        filters['source'] = source_filter
    keyword = request.args.get('keyword')
    if keyword:
        filters['keyword'] = keyword

    start_time = _parse_datetime_param(request.args.get('start_time'))
    end_time = _parse_datetime_param(request.args.get('end_time'))
    if start_time:
        filters['start_time'] = start_time
    if end_time:
        filters['end_time'] = end_time

    data = real_detector.get_alert_records(limit=limit, filters=filters)
    return jsonify({"success": True, "data": data["records"], "meta": data["meta"]})

@app.route('/api/alert_records/<record_id>/status', methods=['POST'])
def api_alert_record_status(record_id):
    """单条告警状态更新"""
    payload = request.get_json() or {}
    status = payload.get('status')
    if status not in {'未处理', '已处理', '误报'}:
        return jsonify({"success": False, "message": "状态值无效"}), 400
    updated = real_detector.update_alert_status(record_id, status, payload.get('note'))
    if updated:
        return jsonify({"success": True, "message": "状态已更新"})
    return jsonify({"success": False, "message": "未找到告警记录"}), 404

@app.route('/api/alert_records/<record_id>', methods=['DELETE'])
def api_alert_record_delete(record_id):
    """删除单条告警记录"""
    removed = real_detector.delete_alert_records(record_id)
    if removed:
        return jsonify({"success": True, "message": "告警记录已删除"})
    return jsonify({"success": False, "message": "未找到告警记录"}), 404

@app.route('/api/alert_records/bulk_action', methods=['POST'])
def api_alert_bulk_action():
    """批量处理告警"""
    payload = request.get_json() or {}
    action = payload.get('action')
    record_ids = payload.get('record_ids') or []
    if not isinstance(record_ids, list) or not record_ids:
        return jsonify({"success": False, "message": "缺少告警ID"}), 400
    if action == 'status':
        status = payload.get('status')
        if status not in {'未处理', '已处理', '误报'}:
            return jsonify({"success": False, "message": "状态值无效"}), 400
        updated = real_detector.update_alert_status(record_ids, status, payload.get('note'))
        return jsonify({"success": True, "affected": updated})
    if action == 'delete':
        removed = real_detector.delete_alert_records(record_ids)
        return jsonify({"success": True, "affected": removed})
    return jsonify({"success": False, "message": "不支持的操作"}), 400

@app.route('/channels')
def channels_page():
    """通道管理页面 - 重定向到admin页面"""
    return redirect('/admin')

@app.route('/merge')
def merge_page():
    """合流通道页面 - 保留兼容性"""
    return render_template('merge.html')

@app.route('/video_sources/<source_type>')
def video_sources_page(source_type='camera'):
    """视频接入管理页面 - 重定向到admin页面"""
    return redirect('/admin')

@app.route('/api/toggle_model', methods=['POST'])
def toggle_model():
    """切换模型状态"""
    data = request.get_json()
    model_type = data.get('model_type')
    
    if model_type in real_detector.model_status:
        new_status = real_detector.toggle_model(model_type)
        return jsonify({
            'success': True,
            'status': new_status,
            'message': f'{"启用" if new_status else "禁用"}成功'
        })
    
    return jsonify({'success': False, 'message': '无效的模型类型'})

@app.route('/api/algorithm_model/upload', methods=['POST'])
def upload_algorithm_model():
    """上传并登记算法模型文件。"""
    algorithm = (request.form.get('algorithm') or '').strip()
    file = request.files.get('file')

    if not algorithm:
        return jsonify({'success': False, 'message': '缺少算法类型'}), 400
    if algorithm not in ALGORITHM_MODEL_DIRS:
        return jsonify({'success': False, 'message': f'不支持的算法类型: {algorithm}'}), 400
    if not file or not file.filename:
        return jsonify({'success': False, 'message': '缺少模型文件'}), 400

    filename = secure_filename(file.filename)
    if not filename:
        return jsonify({'success': False, 'message': '模型文件名无效'}), 400

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_MODEL_EXTENSIONS:
        return jsonify({'success': False, 'message': f'不支持的模型格式: {suffix}'}), 400

    model_dir = Path(ALGORITHM_MODEL_DIRS[algorithm])
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / filename
    file.save(model_path)

    updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    config = load_algorithm_config()
    config.setdefault(algorithm, {}).update({
        'model_available': True,
        'model_filename': filename,
        'model_path': str(model_path),
        'model_updated_at': updated_at,
    })
    if not save_algorithm_config(config):
        return jsonify({'success': False, 'message': '模型已保存，但配置写入失败'}), 500

    vsd = getattr(real_detector, 'video_source_detector', None)
    if vsd and hasattr(vsd, 'update_algorithm_config'):
        try:
            vsd.update_algorithm_config(algorithm, config[algorithm])
        except Exception as e:
            print(f"应用模型配置失败 [{algorithm}]: {e}")

    return jsonify({
        'success': True,
        'data': {
            'algorithm': algorithm,
            'filename': filename,
            'model_path': str(model_path),
            'updated_at': updated_at,
        }
    })

@app.route('/api/update_config', methods=['POST'])
def update_config():
    """更新系统配置"""
    data = request.get_json()
    
    # 目前配置暂时只做响应，后续可以扩展
    print(f"收到配置更新请求: {data}")
    
    return jsonify({'success': True, 'message': '配置更新成功'})

@app.route('/api/stats')
def api_stats():
    """返回检测统计数据API"""
    return jsonify(real_detector.get_stats_data())

@app.route('/api/logs')
def api_logs():
    """返回最近的检测日志API"""
    logs = real_detector.get_recent_logs(20)
    # 直接返回，格式已经正确
    return jsonify(logs)

@app.route('/api/system/info')
def get_system_info():
    """获取系统信息"""
    try:
        # 计算运行时间
        uptime_seconds = int(time.time() - start_time)
        days = uptime_seconds // 86400
        hours = (uptime_seconds % 86400) // 3600
        uptime_str = f"{days}天{hours}小时" if days > 0 else f"{hours}小时"
        
        # 获取系统资源使用情况
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        memory_used_gb = memory.used / (1024**3)
        
        return jsonify({
            'success': True,
            'data': {
                'version': 'v2.0.1',
                'uptime': uptime_str,
                'cpu_usage': f"{cpu_percent:.0f}%",
                'memory_usage': f"{memory_used_gb:.1f}GB"
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/system/config', methods=['GET', 'POST'])
def system_config_api():
    """系统配置API"""
    global system_config
    
    if request.method == 'GET':
        return jsonify({
            'success': True,
            'data': system_config
        })
    else:  # POST
        try:
            data = request.json
            print(f"收到配置更新请求: {data}")
            
            # 更新配置
            if 'dashboard_title' in data:
                system_config['dashboard_title'] = data['dashboard_title']
            if 'max_channels' in data:
                system_config['max_channels'] = int(data['max_channels'])
            if 'language' in data:
                system_config['language'] = data['language']
                # 同步更新告警管理器的语言配置
                alert_manager.update_config({'language': data['language']})
            
            print(f"更新后的配置: {system_config}")
            
            # 保存到文件
            if save_system_config(system_config):
                return jsonify({
                    'success': True,
                    'message': '配置保存成功'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': '配置保存失败，请检查服务器日志'
                }), 500
        except Exception as e:
            print(f"配置更新异常: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({
                'success': False,
                'message': f'配置保存失败: {str(e)}'
            }), 500

@app.route('/api/mqtt/config', methods=['GET', 'POST'])
def mqtt_config_api():
    """MQTT 配置兼容 API，实际读写 MQTT 推送目标。"""
    global mqtt_config

    manager = get_push_manager()

    if request.method == 'GET':
        mqtt_config = manager.build_system_mqtt_config()
        return jsonify({'success': True, 'data': mqtt_config})
    else:
        try:
            data = request.json or {}
            mqtt_config = manager.apply_legacy_mqtt_config(data)
            return jsonify({'success': True, 'message': 'MQTT 配置保存成功', 'data': mqtt_config})
        except Exception as e:
            print(f"MQTT 配置更新异常: {e}")
            return jsonify({'success': False, 'message': f'保存失败: {str(e)}'}), 500

# 添加摄像头控制API
@app.route('/api/start_camera', methods=['POST'])
def start_camera():
    """启动摄像头检测"""
    data = request.get_json()
    camera_index = data.get('camera_index', 0)
    
    success = real_detector.start_camera_detection(camera_index)
    
    if success:
        return jsonify({
            'success': True,
            'message': f'摄像头 {camera_index} 检测已启动'
        })
    else:
        return jsonify({
            'success': False,
            'message': '摄像头启动失败，请检查设备连接'
        })

@app.route('/api/stop_camera', methods=['POST'])
def stop_camera():
    """停止摄像头检测"""
    real_detector.stop_camera_detection()
    
    return jsonify({
        'success': True,
        'message': '摄像头检测已停止'
    })

@app.route('/api/camera_status')
def camera_status():
    """获取摄像头状态"""
    status = real_detector.get_camera_status()
    return jsonify(status)

@app.route('/api/reset_stats', methods=['POST'])
def reset_stats():
    """重置统计数据"""
    real_detector.reset_stats()
    return jsonify({
        'success': True,
        'message': '统计数据已重置'
    })

@app.route('/api/video_detection/restart', methods=['POST'])
def restart_video_detection():
    """重启所有视频源检测"""
    try:
        if hasattr(real_detector, 'video_source_detector') and real_detector.video_source_detector:
            real_detector.video_source_detector.restart_all_from_config()
            return jsonify({
                'success': True,
                'message': '视频源检测已重启'
            })
        else:
            return jsonify({
                'success': False,
                'message': '视频源检测器未初始化'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'重启失败: {str(e)}'
        }), 500

@app.route('/api/video_detection/status', methods=['GET'])
def get_video_detection_status():
    """获取视频源检测状态"""
    try:
        if hasattr(real_detector, 'video_source_detector') and real_detector.video_source_detector:
            status = real_detector.video_source_detector.get_detection_status()
            return jsonify({
                'success': True,
                'data': status
            })
        else:
            return jsonify({
                'success': False,
                'message': '视频源检测器未初始化'
            })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取状态失败: {str(e)}'
        }), 500

# 摄像头管理API
@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    """获取摄像头列表"""
    cameras = real_detector.get_camera_list()
    return jsonify(cameras)

@app.route('/api/cameras', methods=['POST'])
def add_camera():
    """添加摄像头"""
    data = request.get_json()
    
    # 验证必需字段
    required_fields = ['name', 'location', 'camera_index']
    for field in required_fields:
        if field not in data:
            return jsonify({
                'success': False,
                'message': f'缺少必需字段: {field}'
            }), 400
    
    success, message = real_detector.add_camera(
        name=data['name'],
        location=data['location'],
        camera_index=data['camera_index']
    )
    
    return jsonify({
        'success': success,
        'message': message
    })

@app.route('/api/cameras/<int:camera_id>', methods=['PUT'])
def update_camera(camera_id):
    """更新摄像头信息"""
    data = request.get_json()
    
    success, message = real_detector.update_camera(camera_id, data)
    
    return jsonify({
        'success': success,
        'message': message
    })

@app.route('/api/cameras/<int:camera_id>', methods=['DELETE'])
def delete_camera(camera_id):
    """删除摄像头"""
    success, message = real_detector.delete_camera(camera_id)
    
    return jsonify({
        'success': success,
        'message': message
    })

@app.route('/api/cameras/<int:camera_id>/test', methods=['POST'])
def test_camera(camera_id):
    """测试摄像头连接"""
    success, message = real_detector.test_camera_connection(camera_id)
    
    return jsonify({
        'success': success,
        'message': message
    })

# 视频流路由
@app.route('/video_feed')
def video_feed():
    """返回实时视频流"""
    return Response(real_detector.generate_frames(),
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/current_frame')
def current_frame():
    """获取当前帧的Base64编码"""
    frame_data = real_detector.get_current_frame_base64()
    if frame_data:
        return jsonify({
            'success': True,
            'frame': frame_data
        })
    else:
        return jsonify({
            'success': False,
            'message': '无法获取当前帧'
        })

# ==================== 算法配置 API ====================

@app.route('/api/algorithm_config', methods=['GET'])
def get_algorithm_config():
    """获取所有算法的当前配置"""
    return jsonify({'success': True, 'data': load_algorithm_config()})


@app.route('/api/algorithm_config', methods=['POST'])
def set_algorithm_config():
    """
    保存算法配置并立即应用到运行中的检测器

    请求体: { "helmet_detection": {"confidence_threshold": 0.4}, ... }
    """
    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({'success': False, 'error': '请求体必须是 JSON 对象'}), 400

    # 合并到默认配置（防止前端漏传某些算法）
    merged = load_algorithm_config()
    for algo, params in data.items():
        if isinstance(params, dict):
            merged.setdefault(algo, {}).update(params)

    if not save_algorithm_config(merged):
        return jsonify({'success': False, 'error': '配置文件写入失败'}), 500

    # 立即应用到运行中的检测器
    vsd = getattr(real_detector, 'video_source_detector', None)
    if vsd:
        if hasattr(vsd, 'update_all_detector_configs'):
            vsd.update_all_detector_configs(merged)
        elif hasattr(vsd, 'apply_all_algorithm_configs'):
            for algo, params in merged.items():
                if isinstance(params, dict):
                    try:
                        vsd.update_algorithm_config(algo, params)
                    except Exception as e:
                        print(f"应用算法配置失败 [{algo}]: {e}")

        try:
            occlusion_detector = getattr(vsd, 'camera_occlusion_detector', None)
            if occlusion_detector is not None:
                print(f"[算法配置] camera_occlusion_detection 当前阈值: {occlusion_detector.confidence_threshold}")
        except Exception as e:
            print(f"[算法配置] 打印摄像头遮挡阈值失败: {e}")

    return jsonify({'success': True})


if __name__ == '__main__':
    lan_ip = detect_lan_ip()
    print("\n" + "="*60)
    print("🚀 SafeHat AI 安全隐患识别系统正在启动...")
    print("="*60)
    print("\n📌 访问地址:")
    print(f"   本机访问: http://localhost:5000")
    print(f"   局域网访问: http://{lan_ip}:5000")
    print(f"   或使用本机IP: http://<your-ip>:5000")
    print("\n📊 主要页面:")
    print(f"   数据大屏: http://localhost:5000/dashboard")
    print(f"   管理后台: http://localhost:5000/admin")
    print("\n" + "="*60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
