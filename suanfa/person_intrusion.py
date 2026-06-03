"""
人员入侵检测模块
检测逻辑：检测到人（class_id=0）立即告警
"""
import cv2
import numpy as np
from pathlib import Path
from .base_algorithm import BaseAlgorithm


class PersonIntrusionDetector(BaseAlgorithm):
    def __init__(self, confidence_threshold: float = 0.5):
        self.person_class_id = 0
        self.class_config = {
            0: {"name": "人员入侵", "color": (0, 0, 255),
                "status": "入侵", "level": "critical"},
        }
        super().__init__(confidence_threshold)
        self.detection_count = {"person": 0}

    def get_algorithm_name(self) -> str:
        return "person_intrusion_detection"

    def get_engine_path(self) -> str:
        return str(Path(__file__).parent.parent / "models" / "人员识别" / "person.engine")

    def enable(self):
        super().enable()
        self.detection_count = {"person": 0}

    def reset_count(self):
        self.detection_count = {"person": 0}

    def detect(self, frame: np.ndarray):
        """
        Returns:
            tuple: (processed_frame, detection_info, frame_detections, intrusion_alerts)
        """
        if not self.is_enabled:
            return frame, [], {"person": 0}, []

        if not self._validate_frame(frame):
            return frame, [], {"person": 0}, []

        try:
            raw_dets         = self._infer(frame)
            detection_info   = []
            frame_detections = {"person": 0}
            processed_frame  = frame.copy()
            intrusion_alerts = []

            for det in raw_dets:
                if det['class_id'] != self.person_class_id:
                    continue
                if not self._validate_bbox(det['bbox'], frame.shape):
                    continue

                x1, y1, x2, y2 = self._clamp_bbox(det['bbox'], frame.shape)
                conf = det['confidence']
                class_info = self.class_config[0]

                processed_frame = self._draw_detection_box(
                    processed_frame, x1, y1, x2, y2, conf, class_info
                )
                frame_detections["person"] += 1
                detection_info.append(f"[!] INTRUSION: {conf:.1%}")
                intrusion_alerts.append({
                    "bbox":       (x1, y1, x2, y2),
                    "confidence": conf,
                    "message":    "检测到人员入侵",
                    "status":     "入侵",
                })

            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]

            return processed_frame, detection_info, frame_detections, intrusion_alerts

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {e}")
            import traceback; traceback.print_exc()
            return frame, [], {"person": 0}, []

    def _draw_detection_box(self, frame, x1, y1, x2, y2, conf, class_info):
        color = class_info["color"]

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 4)

        cl, ct = 30, 8
        cv2.line(frame, (x1, y1), (x1 + cl, y1), color, ct)
        cv2.line(frame, (x1, y1), (x1, y1 + cl), color, ct)
        cv2.line(frame, (x2, y1), (x2 - cl, y1), color, ct)
        cv2.line(frame, (x2, y1), (x2, y1 + cl), color, ct)
        cv2.line(frame, (x1, y2), (x1 + cl, y2), color, ct)
        cv2.line(frame, (x1, y2), (x1, y2 - cl), color, ct)
        cv2.line(frame, (x2, y2), (x2 - cl, y2), color, ct)
        cv2.line(frame, (x2, y2), (x2, y2 - cl), color, ct)

        label = f"[!] INTRUSION: {conf:.1%}"
        lsize = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]

        overlay = frame.copy()
        bg_y1   = max(0, y1 - lsize[1] - 25)
        cv2.rectangle(overlay, (x1, bg_y1), (x1 + lsize[0] + 25, y1), color, -1)
        cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
        cv2.rectangle(frame, (x1, bg_y1), (x1 + lsize[0] + 25, y1), color, 3)
        cv2.putText(frame, label, (x1 + 12, y1 - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        ts     = 40
        pts    = np.array([
            [cx, cy - ts], [cx - ts, cy + ts], [cx + ts, cy + ts]
        ], np.int32)
        cv2.fillPoly(frame, [pts], (0, 0, 255))
        cv2.polylines(frame, [pts], True, (255, 255, 255), 3)
        cv2.putText(frame, "!", (cx - 8, cy + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 4)

        return frame

    def check_person_intrusion(self, frame):
        if not self.is_enabled:
            return False, 0, []
        try:
            _, _, fd, alerts = self.detect(frame)
            return bool(alerts), fd["person"], [a["bbox"] for a in alerts]
        except Exception as e:
            print(f"[{self.algorithm_name}] check_person_intrusion 失败: {e}")
            return False, 0, []

    def get_status_info(self) -> dict:
        return {
            'name':    "人员入侵检测",
            'enabled': self.is_enabled,
            'count':   self.detection_count["person"],
            'details': self.detection_count,
            'emoji':   '🚫',
        }
