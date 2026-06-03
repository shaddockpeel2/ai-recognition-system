"""
打架识别检测模块
负责检测图像中的打架行为
"""
import cv2
import numpy as np
import time
from pathlib import Path
from collections import defaultdict
from .base_algorithm import BaseAlgorithm


class FightDetector(BaseAlgorithm):
    def __init__(self, confidence_threshold: float = 0.5,
                 fight_duration_threshold: float = 5.0):
        self.class_config = {
            0: {"name": "打架", "color": (0, 0, 255), "status": "FIGHT", "level": "high"},
        }
        self.fight_duration_threshold = fight_duration_threshold
        self.no_detection_timeout     = 1.0
        self.fight_tracks             = defaultdict(dict)

        super().__init__(confidence_threshold)
        self.detection_count = {"fight": 0}

    def get_algorithm_name(self) -> str:
        return "fight_detection"

    def get_engine_path(self) -> str:
        return str(Path(__file__).parent.parent / "models" / "打架识别" / "fight.engine")

    def enable(self):
        super().enable()
        self.fight_tracks.clear()

    def reset_count(self):
        self.detection_count = {"fight": 0}

    def update_config(self, confidence_threshold=None,
                      fight_duration_threshold=None, **kwargs):
        super().update_config(confidence_threshold, **kwargs)
        if fight_duration_threshold is not None:
            self.fight_duration_threshold = float(fight_duration_threshold)
            print(f"[{self.algorithm_name}] 打架持续时长阈值: {self.fight_duration_threshold}秒")

    def detect(self, frame: np.ndarray, video_source_id=None):
        """
        Returns:
            tuple: (processed_frame, alerts)
        """
        if not self.is_enabled:
            return frame, []

        if not self._validate_frame(frame):
            return frame, []

        alerts      = []
        current_time = time.time()

        try:
            raw_dets = self._infer(frame)
            self._cleanup_expired_tracks(current_time)
            fight_detected_this_frame = False

            for det in raw_dets:
                if det['class_id'] != 0:
                    continue
                if not self._validate_bbox(det['bbox'], frame.shape):
                    continue

                x1, y1, x2, y2 = self._clamp_bbox(det['bbox'], frame.shape)
                conf = det['confidence']
                fight_detected_this_frame = True

                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                loc_key  = f"{video_source_id}_{center_x // 100}_{center_y // 100}"

                color = self.class_config[0]["color"]
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                if loc_key not in self.fight_tracks:
                    self.fight_tracks[loc_key] = {
                        'first_seen':        current_time,
                        'last_seen':         current_time,
                        'consecutive_frames': 1,
                        'alerted':           False,
                    }
                else:
                    self.fight_tracks[loc_key]['last_seen'] = current_time
                    self.fight_tracks[loc_key]['consecutive_frames'] += 1

                track    = self.fight_tracks[loc_key]
                duration = current_time - track['first_seen']

                if duration >= self.fight_duration_threshold and not track['alerted']:
                    self.detection_count["fight"] += 1
                    track['alerted'] = True
                    alerts.append({
                        "type":      "fight_violation",
                        "message":   f"检测到打架行为（持续{duration:.1f}秒）",
                        "location":  f"位置({center_x}, {center_y})",
                        "confidence": conf,
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "details": {
                            "duration":            duration,
                            "consecutive_frames":  track['consecutive_frames'],
                        },
                    })
                    label = f"FIGHT ALERT {conf:.2f} ({duration:.1f}s)"
                    self._draw_label(frame, x1, y1, label, color)
                else:
                    label = f"Suspicious {conf:.2f} ({duration:.1f}s/{self.fight_duration_threshold}s)"
                    self._draw_label(frame, x1, y1, label, (255, 165, 0), scale=0.5)

            if not fight_detected_this_frame:
                self.fight_tracks.clear()

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {e}")
            import traceback; traceback.print_exc()

        return frame, alerts

    def _cleanup_expired_tracks(self, current_time: float):
        expired = [k for k, v in self.fight_tracks.items()
                   if current_time - v['last_seen'] > self.no_detection_timeout]
        for k in expired:
            del self.fight_tracks[k]

    def _draw_label(self, frame, x1, y1, label, color, scale=0.6):
        size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.rectangle(frame, (x1, y1 - size[1] - 10), (x1 + size[0], y1), color, -1)
        cv2.putText(frame, label, (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2)

    def get_status_info(self) -> dict:
        return {
            "name":    "打架识别检测",
            "enabled": self.is_enabled,
            "count":   self.detection_count["fight"],
            "details": self.detection_count,
            "config": {
                "confidence_threshold":      self.confidence_threshold,
                "fight_duration_threshold":  self.fight_duration_threshold,
            },
            "active_tracks": len(self.fight_tracks),
            "emoji": "🥊",
        }


def create_fight_detector(confidence_threshold=0.5) -> FightDetector:
    return FightDetector(confidence_threshold)
