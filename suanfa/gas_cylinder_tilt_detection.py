"""
钢瓶倾倒检测模块
负责检测钢瓶状态，检测到倾倒/异常状态时触发告警。
"""
import cv2
import numpy as np
from pathlib import Path

from .base_algorithm import BaseAlgorithm


class GasCylinderTiltDetector(BaseAlgorithm):
    """钢瓶倾倒检测器 - 继承 BaseAlgorithm"""

    def __init__(self, confidence_threshold: float = 0.5):
        super().__init__(confidence_threshold)
        self.class_config = {
            0: {"name": "正常", "label": "NORMAL", "color": (0, 200, 0), "status": "正常"},
            1: {"name": "钢瓶倾倒", "label": "TILTED", "color": (0, 0, 255), "status": "报警"},
        }
        self.detection_count = {"normal": 0, "unmormal": 0}

    def get_algorithm_name(self) -> str:
        return "gas_cylinder_tilt_detection"

    def get_engine_path(self) -> str:
        engine_path = Path(__file__).parent.parent / "models" / "钢瓶倾倒检测" / "best_rk3576.rknn"
        return str(engine_path)

    def detect(self, frame: np.ndarray):
        if not self.is_enabled:
            return frame, [], {"normal": 0, "unmormal": 0}, []

        if not self._validate_frame(frame):
            return frame, [], {"normal": 0, "unmormal": 0}, []

        processed_frame = frame.copy()
        detection_info = []
        frame_detections = {"normal": 0, "unmormal": 0}
        alerts = []

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
                processed_frame = self._draw_detection_box(
                    processed_frame, x1, y1, x2, y2, confidence, class_info
                )

                if class_id == 0:
                    frame_detections["normal"] += 1
                    detection_info.append(f"正常 ({confidence:.1%})")
                elif class_id == 1:
                    frame_detections["unmormal"] += 1
                    detection_info.append(f"钢瓶倾倒 ({confidence:.1%})")
                    alerts.append({
                        "type": "gas_cylinder_tilt_violation",
                        "message": "检测到钢瓶倾倒",
                        "confidence": confidence,
                        "location": f"({x1},{y1})-({x2},{y2})",
                        "bbox": [x1, y1, x2, y2],
                    })

            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {str(e)}")

        return processed_frame, detection_info, frame_detections, alerts

    def get_status_info(self) -> dict:
        return {
            "name": "钢瓶倾倒检测",
            "enabled": self.is_enabled,
            "count": sum(self.detection_count.values()),
            "details": self.detection_count.copy(),
            "emoji": "🧯",
        }

    def reset_count(self):
        self.detection_count = {"normal": 0, "unmormal": 0}

    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, class_info):
        color = class_info["color"]
        label = f"{class_info['label']} {confidence:.2f}"

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


def create_gas_cylinder_tilt_detector(confidence_threshold=0.5):
    return GasCylinderTiltDetector(confidence_threshold)
