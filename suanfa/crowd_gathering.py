"""
人员聚集检测模块
检测逻辑：画面中检测到指定人数（默认3人）及以上并持续超过设定时长（默认10秒）即告警
"""
import cv2
import numpy as np
import time
from pathlib import Path
from .base_algorithm import BaseAlgorithm


class CrowdGatheringDetector(BaseAlgorithm):
    def __init__(self, confidence_threshold: float = 0.5,
                 min_people: int = 3,
                 duration_threshold: float = 10.0):
        self.people_class_id = 0
        self.class_config = {
            0: {"name": "人员", "color": (255, 200, 0), "status": "人员聚集", "level": "high"},
        }
        self.min_people         = min_people
        self.duration_threshold = duration_threshold
        self.gathering_start_time = None
        self.last_people_count    = 0
        self.gathering_duration   = 0.0

        super().__init__(confidence_threshold)
        self.detection_count = {"people": 0, "gathering_events": 0}

    def get_algorithm_name(self) -> str:
        return "crowd_gathering_detection"

    def get_engine_path(self) -> str:
        return str(Path(__file__).parent.parent / "models" / "人员识别" / "person.engine")

    def enable(self):
        super().enable()
        self.detection_count      = {"people": 0, "gathering_events": 0}
        self.gathering_start_time = None
        self.last_people_count    = 0
        self.gathering_duration   = 0.0

    def disable(self):
        super().disable()
        self.gathering_start_time = None
        self.last_people_count    = 0
        self.gathering_duration   = 0.0

    def reset_count(self):
        self.detection_count      = {"people": 0, "gathering_events": 0}
        self.gathering_start_time = None
        self.last_people_count    = 0
        self.gathering_duration   = 0.0

    def update_config(self, confidence_threshold=None,
                      min_people=None, duration_threshold=None, **kwargs):
        super().update_config(confidence_threshold, **kwargs)
        if min_people is not None:
            self.min_people = max(2, int(min_people))
        if duration_threshold is not None:
            self.duration_threshold = max(1.0, float(duration_threshold))

    def detect(self, frame: np.ndarray):
        """
        Returns:
            tuple: (processed_frame, detection_info, frame_detections, gathering_alerts)
        """
        if not self.is_enabled:
            return frame, [], {"people": 0, "gathering": False, "duration": 0.0}, []

        if not self._validate_frame(frame):
            return frame, [], {"people": 0, "gathering": False, "duration": 0.0}, []

        try:
            raw_dets         = self._infer(frame)
            detection_info   = []
            frame_detections = {"people": 0, "gathering": False, "duration": 0.0}
            processed_frame  = frame.copy()
            gathering_alerts = []
            people_boxes     = []
            current_time     = time.time()

            for det in raw_dets:
                if det['class_id'] != self.people_class_id:
                    continue
                if not self._validate_bbox(det['bbox'], frame.shape):
                    continue

                x1, y1, x2, y2 = self._clamp_bbox(det['bbox'], frame.shape)
                people_boxes.append({"bbox": (x1, y1, x2, y2),
                                     "confidence": det['confidence']})
                frame_detections["people"] += 1

            people_count = frame_detections["people"]

            if people_count >= self.min_people:
                if self.gathering_start_time is None:
                    self.gathering_start_time = current_time
                    self.gathering_duration   = 0.0
                else:
                    self.gathering_duration = current_time - self.gathering_start_time

                if self.gathering_duration >= self.duration_threshold:
                    frame_detections["gathering"] = True
                    frame_detections["duration"]  = self.gathering_duration
                    gathering_alerts.append({
                        "people_count": people_count,
                        "duration":     self.gathering_duration,
                        "bboxes":       people_boxes,
                        "message":      f"检测到{people_count}人聚集",
                        "status":       "人员聚集",
                    })
            else:
                self.gathering_start_time = None
                self.gathering_duration   = 0.0

            for pb in people_boxes:
                x1, y1, x2, y2 = pb["bbox"]
                conf = pb["confidence"]

                if frame_detections["gathering"]:
                    color  = (0, 0, 255)
                    status = f"GATHERING {self.gathering_duration:.1f}s"
                elif self.gathering_duration > 0:
                    color  = (0, 255, 255)
                    status = f"Warning {self.gathering_duration:.1f}s"
                else:
                    color  = (0, 255, 0)
                    status = "Normal"

                processed_frame = self._draw_detection_box(
                    processed_frame, x1, y1, x2, y2, conf, color, status
                )

            if people_count > 0:
                info_text = f"People: {people_count}"
                if self.gathering_duration > 0:
                    info_text += f" | Duration: {self.gathering_duration:.1f}s/{self.duration_threshold:.0f}s"
                if frame_detections["gathering"]:
                    info_text += " | ALERT!"
                cv2.rectangle(processed_frame, (0, 0),
                              (processed_frame.shape[1], 40), (0, 0, 0), -1)
                cv2.putText(processed_frame, info_text, (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            self.detection_count["people"] += frame_detections["people"]
            if frame_detections["gathering"]:
                self.detection_count["gathering_events"] += 1
            self.last_people_count = people_count

            return processed_frame, detection_info, frame_detections, gathering_alerts

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {e}")
            import traceback; traceback.print_exc()
            return frame, [], {"people": 0, "gathering": False, "duration": 0.0}, []

    def _draw_detection_box(self, frame, x1, y1, x2, y2, conf, color, status):
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        label = f"Person {conf:.1%} - {status}"
        lsize = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
        bg_y1 = max(0, int(y1) - lsize[1] - 10)
        cv2.rectangle(frame, (int(x1), bg_y1),
                      (int(x1) + lsize[0] + 10, int(y1)), color, -1)
        cv2.putText(frame, label, (int(x1) + 5, int(y1) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        return frame

    def check_crowd_gathering(self, frame):
        if not self.is_enabled:
            return False, 0, 0.0, []
        try:
            _, _, fd, alerts = self.detect(frame)
            if alerts:
                a = alerts[0]
                return True, a["people_count"], a["duration"], a["bboxes"]
            return False, fd["people"], fd["duration"], []
        except Exception as e:
            print(f"[{self.algorithm_name}] check_crowd_gathering 失败: {e}")
            return False, 0, 0.0, []

    def get_status_info(self) -> dict:
        return {
            'name':             "人员聚集检测",
            'enabled':          self.is_enabled,
            'count':            self.detection_count["people"],
            'gathering_events': self.detection_count["gathering_events"],
            'details':          self.detection_count,
            'config': {
                'min_people':         self.min_people,
                'duration_threshold': self.duration_threshold,
            },
            'emoji': '👥',
        }
