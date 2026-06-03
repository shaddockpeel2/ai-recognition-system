"""
工作服检测模块
负责检测工作服/便装穿戴情况，检测到便装时触发告警。
"""
import cv2
import numpy as np
from pathlib import Path

from .base_algorithm import BaseAlgorithm


class WorkClothesDetector(BaseAlgorithm):
    """工作服检测器 - 继承 BaseAlgorithm"""

    def __init__(self, confidence_threshold: float = 0.5):
        super().__init__(confidence_threshold)
        self.class_config = {
            0: {"name": "工作服", "color": (0, 200, 0), "status": "合规"},
            1: {"name": "便装", "color": (0, 0, 255), "status": "违规"},
        }
        self.detection_count = {"work_clothes": 0, "casual_clothes": 0}

    def get_algorithm_name(self) -> str:
        return "work_clothes_detection"

    def get_engine_path(self) -> str:
        engine_path = Path(__file__).parent.parent / "models" / "工作服检测" / "best_rk3576.rknn"
        return str(engine_path)

    def detect(self, frame: np.ndarray):
        if not self.is_enabled:
            return frame, [], {"work_clothes": 0, "casual_clothes": 0}, []

        if not self._validate_frame(frame):
            return frame, [], {"work_clothes": 0, "casual_clothes": 0}, []

        processed_frame = frame.copy()
        detection_info = []
        frame_detections = {"work_clothes": 0, "casual_clothes": 0}
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
                    frame_detections["work_clothes"] += 1
                    detection_info.append(f"工作服 ({confidence:.1%})")
                elif class_id == 1:
                    frame_detections["casual_clothes"] += 1
                    detection_info.append(f"便装 ({confidence:.1%})")
                    alerts.append({
                        "type": "work_clothes_violation",
                        "message": "检测到未穿工作服",
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
            "name": "工作服检测",
            "enabled": self.is_enabled,
            "count": sum(self.detection_count.values()),
            "details": self.detection_count.copy(),
            "emoji": "👔",
        }

    def reset_count(self):
        self.detection_count = {"work_clothes": 0, "casual_clothes": 0}

    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, class_info):
        color = class_info["color"]
        label_prefix = "CASUAL" if class_info["status"] == "违规" else "WORK"
        label = f"{label_prefix} {confidence:.2f}"

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


def create_work_clothes_detector(confidence_threshold=0.5):
    return WorkClothesDetector(confidence_threshold)
