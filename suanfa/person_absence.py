"""
人员离岗检测模块
检测逻辑：画面中检测不到人员并持续超过设定时长（默认10秒）即告警
"""
import cv2
import numpy as np
import time
from pathlib import Path
from .base_algorithm import BaseAlgorithm


class PersonAbsenceDetector(BaseAlgorithm):
    def __init__(self, confidence_threshold: float = 0.5,
                 absence_duration_threshold: float = 10.0):
        self.people_class_id = 0
        self.class_config = {
            0: {"name": "人员", "color": (255, 0, 255),
                "status": "人员离岗", "level": "high"},
        }
        self.absence_duration_threshold = absence_duration_threshold
        self.absence_start_time         = None
        self.absence_duration           = 0.0
        self.last_detection_time        = None

        super().__init__(confidence_threshold)
        self.detection_count = {"absence_events": 0, "total_checks": 0}

    def get_algorithm_name(self) -> str:
        return "person_absence_detection"

    def get_engine_path(self) -> str:
        return str(Path(__file__).parent.parent / "models" / "人员识别" / "person.engine")

    def enable(self):
        super().enable()
        self.detection_count     = {"absence_events": 0, "total_checks": 0}
        self.absence_start_time  = None
        self.absence_duration    = 0.0
        self.last_detection_time = None

    def disable(self):
        super().disable()
        self.absence_start_time  = None
        self.absence_duration    = 0.0
        self.last_detection_time = None

    def reset_count(self):
        self.detection_count     = {"absence_events": 0, "total_checks": 0}
        self.absence_start_time  = None
        self.absence_duration    = 0.0
        self.last_detection_time = None

    def update_config(self, confidence_threshold=None,
                      absence_duration_threshold=None, **kwargs):
        super().update_config(confidence_threshold, **kwargs)
        if absence_duration_threshold is not None:
            self.absence_duration_threshold = max(1.0, float(absence_duration_threshold))

    def detect(self, frame: np.ndarray):
        """
        Returns:
            tuple: (processed_frame, detection_info, frame_detections, absence_alerts)
        """
        if not self.is_enabled:
            return frame, [], {"people": 0, "absence": False, "duration": 0.0}, []

        if not self._validate_frame(frame):
            return frame, [], {"people": 0, "absence": False, "duration": 0.0}, []

        try:
            raw_dets         = self._infer(frame)
            detection_info   = []
            frame_detections = {"people": 0, "absence": False, "duration": 0.0}
            processed_frame  = frame.copy()
            absence_alerts   = []
            current_time     = time.time()

            self.detection_count["total_checks"] += 1
            people_detected = False

            for det in raw_dets:
                if det['class_id'] == self.people_class_id:
                    if self._validate_bbox(det['bbox'], frame.shape):
                        people_detected = True
                        frame_detections["people"] += 1

            h, w = processed_frame.shape[:2]

            if not people_detected:
                if self.absence_start_time is None:
                    self.absence_start_time = current_time
                    self.absence_duration   = 0.0
                else:
                    self.absence_duration = current_time - self.absence_start_time

                if self.absence_duration >= self.absence_duration_threshold:
                    frame_detections["absence"]  = True
                    frame_detections["duration"] = self.absence_duration
                    absence_alerts.append({
                        "duration": self.absence_duration,
                        "message":  f"人员离岗{self.absence_duration:.1f}秒",
                        "status":   "人员离岗",
                    })
                    self.detection_count["absence_events"] += 1
            else:
                self.last_detection_time = current_time
                self.absence_start_time  = None
                self.absence_duration    = 0.0

            if frame_detections["absence"]:
                status_color = (0, 0, 255)
                status_text  = f"ABSENCE ALERT! {self.absence_duration:.1f}s"
                cv2.rectangle(processed_frame, (50, 50), (w - 50, h - 50), status_color, 5)
                cv2.putText(processed_frame, "NO PERSON DETECTED!",
                            (max(0, w // 2 - 200), h // 2 - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, status_color, 3)
                cv2.putText(processed_frame, status_text,
                            (max(0, w // 2 - 150), h // 2 + 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
            elif self.absence_duration > 0:
                status_color = (0, 255, 255)
                status_text  = (f"Warning: No person "
                                f"{self.absence_duration:.1f}s/{self.absence_duration_threshold:.0f}s")
                cv2.rectangle(processed_frame, (0, 0), (w, 50), (0, 0, 0), -1)
                cv2.putText(processed_frame, status_text, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
            elif people_detected:
                status_color = (0, 255, 0)
                status_text  = f"Normal: {frame_detections['people']} person(s) detected"
                cv2.rectangle(processed_frame, (0, 0), (w, 40), (0, 0, 0), -1)
                cv2.putText(processed_frame, status_text, (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)

            return processed_frame, detection_info, frame_detections, absence_alerts

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {e}")
            import traceback; traceback.print_exc()
            return frame, [], {"people": 0, "absence": False, "duration": 0.0}, []

    def check_person_absence(self, frame):
        if not self.is_enabled:
            return False, 0.0
        try:
            _, _, fd, alerts = self.detect(frame)
            if alerts:
                return True, alerts[0]["duration"]
            return False, fd["duration"]
        except Exception as e:
            print(f"[{self.algorithm_name}] check_person_absence 失败: {e}")
            return False, 0.0

    def get_status_info(self) -> dict:
        return {
            'name':           "人员离岗检测",
            'enabled':        self.is_enabled,
            'absence_events': self.detection_count["absence_events"],
            'total_checks':   self.detection_count["total_checks"],
            'details':        self.detection_count,
            'config': {'absence_duration_threshold': self.absence_duration_threshold},
            'emoji':  '🚶',
        }
