"""
箱上放物检测模块
负责检测箱体状态，检测到箱上放物/异常状态时触发告警。
"""
import cv2
import numpy as np
from pathlib import Path

from .base_algorithm import BaseAlgorithm


class BoxObjectDetector(BaseAlgorithm):
    """箱上放物检测器 - 继承 BaseAlgorithm"""

    def __init__(self, confidence_threshold: float = 0.5):
        super().__init__(confidence_threshold)
        self.class_config = {
            0: {"name": "箱体", "label": "BOX", "color": (0, 200, 0), "status": "正常"},
            1: {"name": "箱上放物", "label": "UNNORMAL", "color": (0, 0, 255), "status": "报警"},
        }
        self.detection_count = {"box": 0, "unnormal": 0}

    def get_algorithm_name(self) -> str:
        return "box_object_detection"

    def get_engine_path(self) -> str:
        engine_path = Path(__file__).parent.parent / "models" / "box" / "best_rk3576.rknn"
        return str(engine_path)

    def detect(self, frame: np.ndarray):
        if not self.is_enabled:
            return frame, [], {"box": 0, "unnormal": 0}, []

        if not self._validate_frame(frame):
            return frame, [], {"box": 0, "unnormal": 0}, []

        processed_frame = frame.copy()
        detection_info = []
        frame_detections = {"box": 0, "unnormal": 0}
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
                    frame_detections["box"] += 1
                    detection_info.append(f"箱体 ({confidence:.1%})")
                elif class_id == 1:
                    frame_detections["unnormal"] += 1
                    detection_info.append(f"箱上放物 ({confidence:.1%})")
                    alerts.append({
                        "type": "box_object_violation",
                        "message": "箱上放物警告",
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
            "name": "箱上放物检测",
            "enabled": self.is_enabled,
            "count": sum(self.detection_count.values()),
            "details": self.detection_count.copy(),
            "emoji": "📦",
        }

    def reset_count(self):
        self.detection_count = {"box": 0, "unnormal": 0}

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


def create_box_object_detector(confidence_threshold=0.5):
    return BoxObjectDetector(confidence_threshold)
