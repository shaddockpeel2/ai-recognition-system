"""
人员跌倒检测模块
负责检测图像中的人员跌倒情况
"""
import cv2
import numpy as np
import time
from pathlib import Path
from collections import defaultdict
from .base_algorithm import BaseAlgorithm


class FallDetector(BaseAlgorithm):
    def __init__(self, confidence_threshold: float = 0.5,
                 fall_duration_threshold: float = 2.0):
        # 类别配置: 0=跌倒(FALL), 1=蹲下(HUNKER), 2=人员(PERSON)
        self.class_config = {
            0: {"name": "跌倒", "color": (0, 0, 255),   "status": "FALL",   "level": "high"},
            1: {"name": "蹲下", "color": (0, 165, 255),  "status": "HUNKER", "level": "medium"},
            2: {"name": "人员", "color": (0, 255, 0),    "status": "NORMAL", "level": "low"},
        }
        self.fall_duration_threshold = fall_duration_threshold
        self.fall_tracks          = defaultdict(dict)
        self.next_track_id        = 0

        super().__init__(confidence_threshold)
        self.detection_count = {"fall": 0, "hunker": 0, "person": 0}

    def get_algorithm_name(self) -> str:
        return "fall_detection"

    def get_engine_path(self) -> str:
        return str(Path(__file__).parent.parent / "models" / "人员跌倒1.0" / "fall.engine")

    def enable(self):
        super().enable()
        self.detection_count = {"fall": 0, "hunker": 0, "person": 0}
        self.fall_tracks = defaultdict(dict)

    def reset_count(self):
        self.detection_count = {"fall": 0, "hunker": 0, "person": 0}
        self.fall_tracks = defaultdict(dict)

    def update_config(self, confidence_threshold=None,
                      fall_duration_threshold=None, **kwargs):
        super().update_config(confidence_threshold, **kwargs)
        if fall_duration_threshold is not None:
            self.fall_duration_threshold = float(fall_duration_threshold)
            self.fall_tracks = defaultdict(dict)

    def detect(self, frame: np.ndarray):
        """
        Returns:
            tuple: (processed_frame, detection_info, frame_detections)
        """
        if not self.is_enabled:
            return frame, [], {"fall": 0, "hunker": 0, "person": 0}

        if not self._validate_frame(frame):
            return frame, [], {"fall": 0, "hunker": 0, "person": 0}

        try:
            raw_dets        = self._infer(frame)
            detection_info  = []
            frame_detections = {"fall": 0, "hunker": 0, "person": 0}
            processed_frame = frame.copy()
            current_time    = time.time()

            for det in raw_dets:
                class_id = det['class_id']
                if class_id not in self.class_config:
                    continue
                if not self._validate_bbox(det['bbox'], frame.shape):
                    continue

                x1, y1, x2, y2 = self._clamp_bbox(det['bbox'], frame.shape)
                confidence = det['confidence']
                class_info = self.class_config[class_id]

                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2

                if class_id == 0:  # 跌倒
                    track_id = self._find_or_create_track((cx, cy), current_time)
                    duration = current_time - self.fall_tracks[track_id]['first_seen']
                    is_confirmed = duration >= self.fall_duration_threshold

                    if is_confirmed:
                        frame_detections["fall"] += 1
                        color = (0, 0, 255)
                        label = f"[!] FALL DOWN: {confidence:.1%} ({duration:.1f}s)"
                        detection_info.append(f"[FALL] {confidence:.1%} {duration:.1f}s")
                    else:
                        color = (0, 255, 255)
                        label = f"[?] Checking: {confidence:.1%} ({duration:.1f}s)"

                    processed_frame = self._draw_box(
                        processed_frame, x1, y1, x2, y2, color, label
                    )

                elif class_id == 1:  # 蹲下
                    frame_detections["hunker"] += 1
                    detection_info.append(f"[H] HUNKER {confidence:.1%}")
                    processed_frame = self._draw_box(
                        processed_frame, x1, y1, x2, y2,
                        class_info["color"], f"[H] HUNKER: {confidence:.1%}"
                    )

                elif class_id == 2:  # 人员
                    frame_detections["person"] += 1
                    detection_info.append(f"[P] PERSON {confidence:.1%}")
                    processed_frame = self._draw_box(
                        processed_frame, x1, y1, x2, y2,
                        class_info["color"], f"[P] PERSON: {confidence:.1%}"
                    )

            self._cleanup_old_tracks(current_time, max_age=5.0)

            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]

            return processed_frame, detection_info, frame_detections

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {e}")
            import traceback; traceback.print_exc()
            return frame, [], {"fall": 0, "hunker": 0, "person": 0}

    def _find_or_create_track(self, position, current_time: float) -> int:
        x, y = position
        threshold = 50
        min_dist  = float('inf')
        closest   = None

        for tid, info in self.fall_tracks.items():
            if 'position' not in info:
                continue
            tx, ty = info['position']
            d = ((x - tx) ** 2 + (y - ty) ** 2) ** 0.5
            if d < min_dist and d < threshold:
                min_dist = d
                closest  = tid

        if closest is not None:
            self.fall_tracks[closest]['last_seen'] = current_time
            self.fall_tracks[closest]['position']  = (x, y)
            return closest

        tid = self.next_track_id
        self.next_track_id += 1
        self.fall_tracks[tid] = {
            'first_seen': current_time,
            'last_seen':  current_time,
            'position':   (x, y),
        }
        return tid

    def _cleanup_old_tracks(self, current_time: float, max_age: float = 5.0):
        expired = [tid for tid, info in self.fall_tracks.items()
                   if current_time - info.get('last_seen', 0) > max_age]
        for tid in expired:
            del self.fall_tracks[tid]

    def _draw_box(self, frame, x1, y1, x2, y2, color, label):
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)

        cl, ct = 25, 6
        cv2.line(frame, (x1, y1), (x1 + cl, y1), color, ct)
        cv2.line(frame, (x1, y1), (x1, y1 + cl), color, ct)
        cv2.line(frame, (x2, y1), (x2 - cl, y1), color, ct)
        cv2.line(frame, (x2, y1), (x2, y1 + cl), color, ct)
        cv2.line(frame, (x1, y2), (x1 + cl, y2), color, ct)
        cv2.line(frame, (x1, y2), (x1, y2 - cl), color, ct)
        cv2.line(frame, (x2, y2), (x2 - cl, y2), color, ct)
        cv2.line(frame, (x2, y2), (x2, y2 - cl), color, ct)

        lsize = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
        ov = frame.copy()
        bg_y1 = max(0, y1 - lsize[1] - 20)
        cv2.rectangle(ov, (x1, bg_y1), (x1 + lsize[0] + 20, y1), color, -1)
        cv2.addWeighted(ov, 0.8, frame, 0.2, 0, frame)
        cv2.rectangle(frame, (x1, bg_y1), (x1 + lsize[0] + 20, y1), color, 2)
        text_color = (255, 255, 255) if sum(color) < 400 else (0, 0, 0)
        cv2.putText(frame, label, (x1 + 10, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, text_color, 2)
        return frame

    def check_confirmed_falls(self) -> list:
        current_time = time.time()
        return [
            {'track_id': tid, 'duration': current_time - info['first_seen'],
             'position': info['position']}
            for tid, info in self.fall_tracks.items()
            if current_time - info['first_seen'] >= self.fall_duration_threshold
        ]

    def get_status_info(self) -> dict:
        return {
            'name':          "人员跌倒检测",
            'enabled':       self.is_enabled,
            'count':         sum(self.detection_count.values()),
            'details':       self.detection_count,
            'emoji':         '🤕',
            'active_tracks': len(self.fall_tracks),
        }
