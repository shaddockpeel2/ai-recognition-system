"""
视频源管理路由
统一管理摄像头、视频流、视频文件三种视频源
"""
from flask import Blueprint, jsonify, request, Response, abort, current_app
from werkzeug.utils import secure_filename
from datetime import datetime
import secrets
import time
import threading
from pathlib import Path

from server.services.gb28181_service import gb28181_service
from server.services.onvif_service import onvif_service
from server.services.video_source_service import video_source_service

try:
    import cv2  # type: ignore
except ImportError:
    cv2 = None
import numpy as np


video_sources_bp = Blueprint("video_sources", __name__, url_prefix="/api/video_sources")
_sync_lock = threading.Lock()


def _apply_algorithm_config(video_detector, detector_key: str, detector_instance):
    """给预览流里临时创建的检测器实例应用当前后端算法配置"""
    try:
        if video_detector is None:
            return

        config = video_detector.get_algorithm_config(detector_key)
        if not config:
            return

        video_detector._apply_config_to_detector(detector_key, detector_instance, config)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[video_sources] 应用算法配置失败: {detector_key}, {exc}")


def _refresh_preview_detector_configs(video_detector, detectors: dict):
    """定期刷新预览流中的检测器配置，避免页面打开后继续使用旧阈值"""
    key_map = {
        'helmet': 'helmet_detection',
        'vest': 'vest_detection',
        'fall': 'fall_detection',
        'mask': 'mask_detection',
        'intrusion': 'intrusion_detection',
        'smoke': 'smoke_detection',
        'fire': 'fire_detection',
        'crowd': 'crowd_gathering_detection',
        'absence': 'person_absence_detection',
        'vehicle': 'vehicle_parking_detection',
        'extinguisher': 'fire_extinguisher_absence_detection',
        'reagent': 'reagent_bottle_detection',
        'occlusion': 'camera_occlusion_detection',
        'fight': 'fight_detection',
        'glove': 'glove_detection',
        'crack': 'crack_detection',
        'dog': 'dog_detection',
        'cat': 'cat_detection',
        'fatigue': 'fatigue_driving_detection',
        'trash': 'trash_detection',
        'ebike': 'electric_bicycle_detection',
    }
    for short_key, detector in detectors.items():
        detector_key = key_map.get(short_key)
        if detector_key:
            _apply_algorithm_config(video_detector, detector_key, detector)


def _sync_detector_algorithms():
    """根据视频源配置同步检测器算法启用状态"""
    with _sync_lock:
        try:
            detector = current_app.config.get("REAL_DETECTOR")
            if detector:
                detector.sync_algorithms_from_sources()
                # 重启视频源检测器以应用新配置
                if hasattr(detector, 'video_source_detector') and detector.video_source_detector:
                    detector.video_source_detector.restart_all_from_config()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[video_sources] 同步算法状态失败: {exc!r}")


def _apply_single_source_runtime(source_id: int):
    """仅同步单个视频源的运行状态，避免全量重启。"""
    with _sync_lock:
        try:
            detector = current_app.config.get("REAL_DETECTOR")
            if not detector:
                return

            detector.sync_algorithms_from_sources()
            video_detector = getattr(detector, 'video_source_detector', None)
            if not video_detector:
                return

            source = video_source_service.get_source(source_id)
            if not source or not source.get("enabled", True):
                video_detector.stop_detection_for_source(source_id, join_timeout=1.5)
                if hasattr(video_detector, '_sync_detector_states'):
                    video_detector._sync_detector_states()
                return

            video_url = source.get("play_url") or source.get("video_url") or source.get("origin_url")
            algorithms = source.get("algorithms", [])
            source_name = source.get("name", f"source-{source_id}")
            source_config = dict(source.get("config", {}) or {})
            for key in (
                "gateway",
                "gateway_app",
                "gateway_stream",
                "preview_url",
            ):
                if source.get(key):
                    source_config[key] = source.get(key)

            if not video_url:
                video_detector.stop_detection_for_source(source_id, join_timeout=1.5)
            else:
                video_detector.start_detection_for_source(source_id, video_url, algorithms, source_name, source_config)

            if hasattr(video_detector, '_sync_detector_states'):
                video_detector._sync_detector_states()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[video_sources] 鍗曡矾瑙嗛婧愬悓姝ュけ璐? {source_id}, {exc!r}")


@video_sources_bp.route("", methods=["GET", "POST"])
def handle_video_sources():
    """获取视频源列表或添加新视频源"""
    if request.method == "GET":
        source_type = request.args.get('type', 'all')  # camera, stream, file, all
        sources = video_source_service.get_all_sources(source_type)
        return jsonify({"success": True, "data": sources})

    # POST - 添加新视频源
    data = request.get_json() or {}
    try:
        source = video_source_service.add_source(data)
        _sync_detector_algorithms()
        return jsonify({"success": True, "data": source})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "message": f"创建视频源失败: {exc}"}), 500


@video_sources_bp.route("/<int:source_id>", methods=["GET", "PUT", "DELETE"])
def handle_video_source_detail(source_id: int):
    """获取、更新或删除单个视频源"""
    if request.method == "GET":
        source = video_source_service.get_source(source_id)
        if source:
            return jsonify({"success": True, "data": source})
        return jsonify({"success": False, "message": "视频源不存在"}), 404

    if request.method == "DELETE":
        # 先停止该视频源的检测线程
        try:
            detector = current_app.config.get("REAL_DETECTOR")
            if detector and hasattr(detector, 'video_source_detector'):
                video_detector = detector.video_source_detector
                if video_detector:
                    video_detector.stop_detection_for_source(source_id)
                    print(f"已停止视频源 {source_id} 的检测线程")
        except Exception as exc:
            print(f"停止检测线程时出错: {exc}")
        
        # 然后删除配置文件中的记录
        success = video_source_service.delete_source(source_id)
        if success:
            _sync_detector_algorithms()
            return jsonify({"success": True, "message": "删除成功"})
        return jsonify({"success": False, "message": "删除失败"}), 404

    # PUT - 更新视频源
    data = request.get_json() or {}
    try:
        source = video_source_service.update_source(source_id, data)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "message": f"更新视频源失败: {exc}"}), 500

    if source:
        _sync_detector_algorithms()
        return jsonify({"success": True, "data": source})
    return jsonify({"success": False, "message": "视频源不存在"}), 404


@video_sources_bp.route("/check", methods=["POST"])
def check_connection():
    """检测视频源连接"""
    data = request.get_json() or {}
    try:
        result = video_source_service.check_connection(data)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"success": False, "message": f"检测失败: {exc}"}), 500


@video_sources_bp.route("/discover_rtsp", methods=["POST"])
def discover_rtsp():
    """扫描网段内常见 RTSP 地址。"""
    data = request.get_json() or {}
    try:
        result = video_source_service.discover_rtsp_streams(data)
        status = 200 if result.get("success") else 400
        return jsonify(result), status
    except Exception as exc:
        return jsonify({"success": False, "message": f"扫描失败: {exc}"}), 500


@video_sources_bp.route("/onvif/profiles", methods=["POST"])
def onvif_profiles():
    """获取 ONVIF 设备的 Profile/码流列表。"""
    data = request.get_json() or {}
    config = data.get("config", data)
    result = onvif_service.profiles(config or {})
    status = 200 if result.success else 400
    return jsonify(result.to_dict()), status


@video_sources_bp.route("/onvif/resolve", methods=["POST"])
def onvif_resolve():
    """把 ONVIF Profile 解析成实际 RTSP 播放地址。"""
    data = request.get_json() or {}
    config = data.get("config", data)
    result = onvif_service.resolve_stream_uri(config or {})
    status = 200 if result.success else 400
    return jsonify(result.to_dict()), status


@video_sources_bp.route("/gb28181/config", methods=["GET"])
def gb28181_config():
    """鑾峰彇 GB28181/WVP 榛樿閰嶇疆銆?"""
    return jsonify({"success": True, "data": gb28181_service.config()})


@video_sources_bp.route("/gb28181/devices", methods=["GET"])
def gb28181_devices():
    """浠?WVP 鑾峰彇宸叉敞鍐?GB28181 璁惧銆?"""
    config = dict(request.args)
    result = gb28181_service.list_devices(config)
    status = 200 if result.success else 400
    return jsonify(result.to_dict()), status


@video_sources_bp.route("/gb28181/channels", methods=["GET"])
def gb28181_channels():
    """浠?WVP 鑾峰彇鎸囧畾 GB28181 璁惧鐨勯€氶亾銆?"""
    config = dict(request.args)
    result = gb28181_service.list_channels(config)
    status = 200 if result.success else 400
    return jsonify(result.to_dict()), status


@video_sources_bp.route("/gb28181/play", methods=["POST"])
def gb28181_play():
    """璋冪敤 WVP 鐐规挱 GB28181 閫氶亾锛岃繑鍥?ZLM 鎾斁鍦板潃銆?"""
    data = request.get_json() or {}
    config = data.get("config", data)
    result = gb28181_service.start_play(config or {})
    status = 200 if result.success else 400
    return jsonify(result.to_dict()), status


@video_sources_bp.route("/gb28181/stop", methods=["POST"])
def gb28181_stop():
    """鍋滄 WVP GB28181 鐐规挱銆?"""
    data = request.get_json() or {}
    config = data.get("config", data)
    result = gb28181_service.stop_play(config or {})
    status = 200 if result.success else 400
    return jsonify(result.to_dict()), status


@video_sources_bp.route("/<int:source_id>/check", methods=["POST"])
def check_source(source_id: int):
    """检测指定视频源"""
    success = video_source_service.check_source(source_id)
    source = video_source_service.get_source(source_id)
    return jsonify({"success": success, "data": source})


@video_sources_bp.route("/check-all", methods=["POST"])
def check_all_sources():
    """检测所有视频源"""
    source_type = request.args.get('type', 'all')
    video_source_service.check_all_sources(source_type)
    return jsonify({"success": True, "message": "已触发所有视频源检测"})


@video_sources_bp.route("/<int:source_id>/toggle", methods=["POST"])
def toggle_source(source_id: int):
    """启用/停用视频源"""
    data = request.get_json() or {}
    enabled = data.get('enabled', True)
    
    success = video_source_service.toggle_source(source_id, enabled)

    if success:
        _apply_single_source_runtime(source_id)
        return jsonify({"success": True, "message": "操作成功"})
    return jsonify({"success": False, "message": "操作失败"}), 404


@video_sources_bp.route("/reorder", methods=["POST"])
def reorder_sources():
    """重新排序视频源"""
    data = request.get_json() or {}
    order = data.get('order', [])
    
    try:
        video_source_service.reorder_sources(order)
        return jsonify({"success": True, "message": "排序成功"})
    except Exception as exc:
        return jsonify({"success": False, "message": f"排序失败: {exc}"}), 500


@video_sources_bp.route("/<int:source_id>/stream")
def stream_video_source(source_id: int):
    """???????? MJPEG ??????????????????"""
    source = video_source_service.get_source(source_id)
    if not source:
        abort(404, description="??????")

    if cv2 is None:
        abort(500, description="?????? OpenCV????????")

    real_detector = current_app.config.get("REAL_DETECTOR")
    video_detector = getattr(real_detector, "video_source_detector", None)
    if video_detector is None:
        abort(500, description="??????????")

    def build_waiting_frame():
        frame = np.zeros((360, 640, 3), dtype=np.uint8)
        frame[:] = (18, 24, 38)
        cv2.putText(frame, "Connecting...", (190, 165),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 220, 255), 2)
        cv2.putText(frame, "Waiting for first frame", (165, 205),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (220, 220, 220), 2)
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buffer.tobytes() if ok else None

    def generate():
        last_frame_version = None
        last_waiting_sent_at = 0.0
        last_sent_at = 0.0
        _web_fps_count = 0
        _web_fps_start = time.time()
        # 最高推送帧率上限：避免 Flask 被疯狂轮询压垮
        _min_interval = 1.0 / 20  # 20 FPS 上限
        try:
            while True:
                processed_frame, frame_version = video_detector.get_processed_frame_packet(source_id)
                if processed_frame is None:
                    now = time.time()
                    if now - last_waiting_sent_at >= 0.5:
                        waiting_bytes = build_waiting_frame()
                        if waiting_bytes:
                            yield (
                                b"--frame\r\n"
                                b"Content-Type: image/jpeg\r\n\r\n" + waiting_bytes + b"\r\n"
                            )
                            last_waiting_sent_at = now
                    time.sleep(0.05)
                    continue

                if frame_version == last_frame_version:
                    time.sleep(0.005)
                    continue

                # 帧率上限：距离上一帧不足 _min_interval 则等待
                now = time.time()
                wait = last_sent_at + _min_interval - now
                if wait > 0:
                    time.sleep(wait)

                success, buffer = cv2.imencode(".jpg", processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not success:
                    continue

                last_frame_version = frame_version
                last_sent_at = time.time()

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
                )

                # 每5秒打印一次 Web 推流帧率
                _web_fps_count += 1
                _web_fps_now = time.time()
                _web_fps_elapsed = _web_fps_now - _web_fps_start
                if _web_fps_elapsed >= 5.0:
                    print(f"[FPS][Web推流] source_{source_id} - 向浏览器推送: {_web_fps_count / _web_fps_elapsed:.1f} FPS")
                    _web_fps_count = 0
                    _web_fps_start = _web_fps_now

        except Exception as exc:  # pylint: disable=broad-except
            print(f"[video_sources] 推流异常: {exc}")
            import traceback
            traceback.print_exc()

    response = Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@video_sources_bp.route("/upload", methods=["POST"])
def upload_video_file():
    """上传视频文件"""
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "没有文件"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"success": False, "message": "文件名为空"}), 400
    
    # 验证文件类型
    allowed_extensions = {'mp4', 'avi', 'mov', 'mkv', 'flv'}
    extension = Path(file.filename).suffix.lower()
    if not extension or extension[1:] not in allowed_extensions:
        return jsonify({"success": False, "message": "不支持的文件类型"}), 400
    
    # 保存文件
    try:
        base_name = Path(file.filename).stem
        safe_stem = secure_filename(base_name) or "video"
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        random_suffix = secrets.token_hex(3)
        filename = f"{safe_stem}_{timestamp}_{random_suffix}{extension or '.mp4'}"
        upload_dir = Path('data/videos')
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = upload_dir / filename
        file.save(str(file_path))
        
        # 生成RTMP推流地址（如果需要）
        stream_url = f"rtmp://127.0.0.1:1935/live/{filename}"
        
        return jsonify({
            "success": True,
            "file_path": str(file_path),
            "stream_url": stream_url,
            "message": "上传成功"
        })
    except Exception as exc:
        return jsonify({"success": False, "message": f"上传失败: {exc}"}), 500


__all__ = ["video_sources_bp"]
