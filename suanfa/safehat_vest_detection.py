"""
安全帽反光衣检测模块
检测安全帽和反光衣穿戴情况，未戴安全帽或未穿反光衣时触发告警。
"""
import cv2
import numpy as np
from pathlib import Path

from .base_algorithm import BaseAlgorithm


class SafehatVestDetector(BaseAlgorithm):
    """安全帽反光衣检测器 - 继承 BaseAlgorithm"""

    def __init__(self, confidence_threshold: float = 0.5):
        super().__init__(confidence_threshold)
        self.debounce_frames = 5
        self.class_config = {
            0: {"name": "安全帽", "color": (0, 200, 0), "status": "已佩戴"},
            1: {"name": "未戴安全帽", "color": (0, 0, 255), "status": "未佩戴"},
            2: {"name": "反光衣", "color": (0, 200, 200), "status": "已穿戴"},
            3: {"name": "未穿反光衣", "color": (0, 128, 255), "status": "未穿戴"},
        }
        self.detection_count = {
            "hardhat": 0,
            "no_hardhat": 0,
            "safety_vest": 0,
            "no_safety_vest": 0,
            "entry": 0,
        }
        self.helmet_alarm_tripped = False
        self.vest_alarm_tripped = False
        self.entry_tripped = False
        self.no_helmet_frames = 0
        self.no_vest_frames = 0
        self.helmet_clear_frames = 0
        self.vest_clear_frames = 0
        self.compliant_frames = 0
        self.empty_frames = 0

    def get_algorithm_name(self) -> str:
        return "safehat_vest_detection"

    def get_engine_path(self) -> str:
        engine_path = Path(__file__).parent.parent / "models" / "安全帽反光衣" / "best_rk3576.rknn"
        return str(engine_path)

    def detect(self, frame: np.ndarray):
        if not self.is_enabled:
            return frame, [], self._empty_frame_detections(), []

        if not self._validate_frame(frame):
            return frame, [], self._empty_frame_detections(), []

        processed_frame = frame.copy()
        detection_info = []
        frame_detections = self._empty_frame_detections()
        alerts = []
        max_confidence = 0.0
        no_helmet_alert = None
        no_vest_alert = None

        try:
            detections = self._infer(frame)
            for det in detections:
                class_id = det.get("class_id")
                confidence = float(det.get("confidence", 0))
                bbox = det.get("bbox")

                if class_id not in self.class_config or not bbox:
                    continue
                if not self._validate_bbox(bbox, frame.shape):
                    continue

                x1, y1, x2, y2 = self._clamp_bbox(bbox, frame.shape)
                class_info = self.class_config[class_id]
                max_confidence = max(max_confidence, confidence)
                processed_frame = self._draw_detection_box(
                    processed_frame, x1, y1, x2, y2, confidence, class_info
                )

                if class_id == 0:
                    frame_detections["hardhat"] += 1
                    detection_info.append(f"安全帽 ({confidence:.1%})")
                elif class_id == 1:
                    frame_detections["no_hardhat"] += 1
                    detection_info.append(f"未戴安全帽 ({confidence:.1%})")
                    if no_helmet_alert is None or confidence > no_helmet_alert["confidence"]:
                        no_helmet_alert = {
                        "type": "safehat_vest_no_helmet_violation",
                        "message": "未戴安全帽",
                        "audio_type": "safehat_vest_no_helmet",
                        "confidence": confidence,
                        "location": f"({x1},{y1})-({x2},{y2})",
                        "bbox": [x1, y1, x2, y2],
                        }
                elif class_id == 2:
                    frame_detections["safety_vest"] += 1
                    detection_info.append(f"反光衣 ({confidence:.1%})")
                elif class_id == 3:
                    frame_detections["no_safety_vest"] += 1
                    detection_info.append(f"未穿反光衣 ({confidence:.1%})")
                    if no_vest_alert is None or confidence > no_vest_alert["confidence"]:
                        no_vest_alert = {
                        "type": "safehat_vest_no_vest_violation",
                        "message": "未穿反光衣",
                        "audio_type": "safehat_vest_no_vest",
                        "confidence": confidence,
                        "location": f"({x1},{y1})-({x2},{y2})",
                        "bbox": [x1, y1, x2, y2],
                        }

            alerts = self._update_state_and_build_alerts(
                frame_detections,
                no_helmet_alert,
                no_vest_alert,
                max_confidence,
            )
            if frame_detections["entry"]:
                detection_info.append("允许进入")

            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {str(e)}")

        return processed_frame, detection_info, frame_detections, alerts

    def _update_state_and_build_alerts(self, frame_detections, no_helmet_alert, no_vest_alert, max_confidence):
        has_helmet = frame_detections["hardhat"] > 0
        has_no_helmet = frame_detections["no_hardhat"] > 0
        has_vest = frame_detections["safety_vest"] > 0
        has_no_vest = frame_detections["no_safety_vest"] > 0
        has_any_target = has_helmet or has_no_helmet or has_vest or has_no_vest
        is_compliant = has_helmet and has_vest and not has_no_helmet and not has_no_vest

        self.empty_frames = self.empty_frames + 1 if not has_any_target else 0

        self.no_helmet_frames = self.no_helmet_frames + 1 if has_no_helmet else 0
        self.no_vest_frames = self.no_vest_frames + 1 if has_no_vest else 0

        helmet_clear = (has_helmet and not has_no_helmet) or not has_any_target
        vest_clear = (has_vest and not has_no_vest) or not has_any_target
        self.helmet_clear_frames = self.helmet_clear_frames + 1 if helmet_clear else 0
        self.vest_clear_frames = self.vest_clear_frames + 1 if vest_clear else 0
        self.compliant_frames = self.compliant_frames + 1 if is_compliant else 0

        if self.helmet_clear_frames >= self.debounce_frames:
            self.helmet_alarm_tripped = False
        if self.vest_clear_frames >= self.debounce_frames:
            self.vest_alarm_tripped = False
        if has_no_helmet or has_no_vest:
            self.entry_tripped = False

        alerts = []
        if self.no_helmet_frames >= self.debounce_frames and not self.helmet_alarm_tripped:
            self.helmet_alarm_tripped = True
            if no_helmet_alert:
                alerts.append(no_helmet_alert)

        if self.no_vest_frames >= self.debounce_frames and not self.vest_alarm_tripped:
            self.vest_alarm_tripped = True
            if no_vest_alert:
                alerts.append(no_vest_alert)

        if self.compliant_frames >= self.debounce_frames and not self.entry_tripped:
            self.entry_tripped = True
            frame_detections["entry"] = 1
            alerts.append({
                "type": "safehat_vest_entry",
                "message": "进入",
                "audio_type": "safehat_vest_entry",
                "confidence": max_confidence or 0.95,
                "location": "",
            })

        return alerts

    def get_status_info(self) -> dict:
        return {
            "name": "安全帽反光衣检测",
            "enabled": self.is_enabled,
            "count": sum(self.detection_count.values()),
            "details": self.detection_count.copy(),
            "emoji": "🦺",
        }

    def reset_count(self):
        self.detection_count = {
            "hardhat": 0,
            "no_hardhat": 0,
            "safety_vest": 0,
            "no_safety_vest": 0,
            "entry": 0,
        }
        self.helmet_alarm_tripped = False
        self.vest_alarm_tripped = False
        self.entry_tripped = False
        self.no_helmet_frames = 0
        self.no_vest_frames = 0
        self.helmet_clear_frames = 0
        self.vest_clear_frames = 0
        self.compliant_frames = 0
        self.empty_frames = 0

    @staticmethod
    def _empty_frame_detections():
        return {
            "hardhat": 0,
            "no_hardhat": 0,
            "safety_vest": 0,
            "no_safety_vest": 0,
            "entry": 0,
        }

    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, class_info):
        color = class_info["color"]
        label_map = {
            "已佩戴": "Hardhat",
            "未佩戴": "NO-Hardhat",
            "已穿戴": "Safety Vest",
            "未穿戴": "NO-Safety Vest",
        }
        label = f"{label_map.get(class_info['status'], class_info['name'])} {confidence:.2f}"

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        label_y1 = max(0, y1 - label_size[1] - 8)
        cv2.rectangle(frame, (x1, label_y1), (x1 + label_size[0] + 8, y1), color, -1)
        cv2.putText(
            frame,
            label,
            (x1 + 4, y1 - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
        )
        return frame


def create_safehat_vest_detector(confidence_threshold=0.5):
    return SafehatVestDetector(confidence_threshold)
