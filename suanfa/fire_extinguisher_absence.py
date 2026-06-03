"""
灭火器缺失检测模块 - 重构版本

检测逻辑：
  画面中检测不到灭火器并持续超过设定时长（默认 10 秒）即告警。

变更说明（相对旧版本）：
  - 移除了 ultralytics.YOLO 直接依赖
  - 改为继承 BaseAlgorithm，通过 infer.py 执行推理
  - 推理层自动根据平台选择后端：
      librknn_yolo.so（C++ RKNN，RK3568 NPU）
      rknnlite（Python RKNN，回退）
      ultralytics（.pt，开发/测试）
  - 业务逻辑、告警判断、UI 绘制完全不变
"""

import cv2
import numpy as np
import time
from pathlib import Path
from .base_algorithm import BaseAlgorithm


class FireExtinguisherAbsenceDetector(BaseAlgorithm):
    """灭火器缺失检测器 - 继承 BaseAlgorithm"""

    def __init__(self, confidence_threshold: float = 0.5,
                 absence_duration_threshold: float = 10.0):
        """
        Args:
            confidence_threshold:       置信度阈值（0.1-0.95，默认 0.5）
            absence_duration_threshold: 触发告警的缺失持续时长（秒，默认 10）
        """
        # 调用基类初始化：自动完成格式发现 + 模型加载
        super().__init__(confidence_threshold)

        # 类别配置（模型单类：class_id=0 为灭火器）
        self.extinguisher_class_id = 0
        self.class_config = {
            0: {"name": "EXTINGUISHER", "color": (0, 255, 0),
                "status": "Present", "level": "safe"}
        }

        # 缺失告警参数
        self.absence_duration_threshold = absence_duration_threshold

        # 缺失状态追踪
        self.absence_start_time  = None
        self.absence_duration    = 0.0
        self.last_detection_time = None

        self.detection_count = {"absence_events": 0, "total_checks": 0}

    # ------------------------------------------------------------------ #
    # BaseAlgorithm 抽象方法实现                                           #
    # ------------------------------------------------------------------ #

    def get_algorithm_name(self) -> str:
        return "fire_extinguisher_absence"

    def get_engine_path(self) -> str:
        """
        返回模型文件路径（以 .engine 为"锚点"）。
        base_algorithm._load_engine() 会在同目录中按优先级搜索：
          best.rknn → 使用 C++ RKNN（NPU）
          best.pt   → 使用 ultralytics（开发机回退）
        """
        engine_path = (Path(__file__).parent.parent
                       / "models" / "灭火器识别" / "fire_extinguisher.engine")
        return str(engine_path)

    def detect(self, frame: np.ndarray):
        """
        执行一帧检测

        Returns:
            tuple: (processed_frame, detection_info, frame_detections, absence_alerts)
        """
        if not self.is_enabled:
            return frame, [], self._empty_result(), []

        if not self._validate_frame(frame):
            return frame, [], self._empty_result(), []

        try:
            # ---- 调用统一推理接口（infer.py 自动选择后端） ----
            raw_dets = self._infer(frame)

            detection_info  = []
            frame_detections = self._empty_result()
            processed_frame  = frame.copy()
            absence_alerts   = []
            current_time     = time.time()

            self.detection_count["total_checks"] += 1
            extinguisher_boxes = []

            for det in raw_dets:
                if det['class_id'] != self.extinguisher_class_id:
                    continue
                if not self._validate_bbox(det['bbox'], frame.shape):
                    continue

                x1, y1, x2, y2 = self._clamp_bbox(det['bbox'], frame.shape)
                conf = det['confidence']

                extinguisher_boxes.append({
                    "bbox": (x1, y1, x2, y2),
                    "confidence": conf
                })
                frame_detections["extinguisher_count"] += 1

            # ---- 更新缺失状态 ----
            if frame_detections["extinguisher_count"] == 0:
                if self.absence_start_time is None:
                    self.absence_start_time = current_time
                    self.absence_duration   = 0.0
                else:
                    self.absence_duration = current_time - self.absence_start_time

                if self.absence_duration >= self.absence_duration_threshold:
                    frame_detections["is_absent"]       = True
                    frame_detections["absence_duration"] = self.absence_duration
                    absence_alerts.append({
                        "absence_duration": self.absence_duration,
                        "message": f"灭火器缺失 {self.absence_duration:.1f}秒",
                        "status":  "灭火器缺失"
                    })
                self.last_detection_time = None
            else:
                self.absence_start_time  = None
                self.absence_duration    = 0.0
                self.last_detection_time = current_time

            # ---- 绘制检测结果 ----
            if extinguisher_boxes:
                for eb in extinguisher_boxes:
                    x1, y1, x2, y2 = eb["bbox"]
                    processed_frame = self._draw_detection_box(
                        processed_frame, x1, y1, x2, y2, eb["confidence"],
                        (0, 255, 0), "Present"
                    )
                    detection_info.append(
                        f"[OK] EXTINGUISHER {eb['confidence']:.1%}"
                    )
            else:
                h, w = processed_frame.shape[:2]
                if frame_detections["is_absent"]:
                    color     = (0, 0, 255)
                    info_text = f"ALERT! No Extinguisher: {self.absence_duration:.1f}s"
                elif self.absence_duration > 0:
                    color     = (0, 255, 255)
                    info_text = (f"Warning: Missing "
                                 f"{self.absence_duration:.1f}s"
                                 f"/{self.absence_duration_threshold:.0f}s")
                else:
                    color     = (0, 255, 255)
                    info_text = "Checking for extinguisher..."

                cv2.rectangle(processed_frame, (0, 0), (w, 80), (0, 0, 0), -1)
                cv2.putText(processed_frame, info_text, (max(0, w // 2 - 260), 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 3)

            # ---- 顶部状态栏 ----
            cnt = frame_detections["extinguisher_count"]
            status_color = (0, 255, 0) if cnt > 0 else (
                           (0, 0, 255) if frame_detections["is_absent"] else (0, 255, 255))
            status_text = f"Extinguisher: {'Present' if cnt > 0 else 'MISSING'}"
            if cnt == 0 and self.absence_duration > 0:
                status_text += f" ({self.absence_duration:.1f}s)"

            cv2.rectangle(processed_frame, (0, 0),
                          (processed_frame.shape[1], 40), (0, 0, 0), -1)
            cv2.putText(processed_frame, status_text, (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)

            if frame_detections["is_absent"]:
                self.detection_count["absence_events"] += 1

            return processed_frame, detection_info, frame_detections, absence_alerts

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {e}")
            import traceback
            traceback.print_exc()
            return frame, [], self._empty_result(), []

    # ------------------------------------------------------------------ #
    # 辅助方法                                                             #
    # ------------------------------------------------------------------ #

    def _empty_result(self) -> dict:
        return {"extinguisher_count": 0, "is_absent": False, "absence_duration": 0.0}

    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, color, status):
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        label = f"EXTINGUISHER {confidence:.1%} - {status}"
        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
        bg_y1 = max(0, int(y1) - label_size[1] - 10)
        cv2.rectangle(frame,
                      (int(x1), bg_y1),
                      (int(x1) + label_size[0] + 10, int(y1)),
                      color, -1)
        cv2.putText(frame, label, (int(x1) + 5, int(y1) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        return frame

    def update_config(self, confidence_threshold=None,
                      absence_duration_threshold=None, **kwargs):
        """更新配置参数"""
        super().update_config(confidence_threshold, **kwargs)
        if absence_duration_threshold is not None:
            self.absence_duration_threshold = max(1.0, float(absence_duration_threshold))
            print(f"[{self.algorithm_name}] 缺失时长阈值更新为: "
                  f"{self.absence_duration_threshold}秒")

    def reset_count(self):
        self.detection_count    = {"absence_events": 0, "total_checks": 0}
        self.absence_start_time = None
        self.absence_duration   = 0.0
        self.last_detection_time = None

    def get_status_info(self) -> dict:
        return {
            'name':           "灭火器缺失检测",
            'enabled':        self.is_enabled,
            'absence_events': self.detection_count["absence_events"],
            'total_checks':   self.detection_count["total_checks"],
            'details':        self.detection_count,
            'config': {
                'confidence_threshold':       self.confidence_threshold,
                'absence_duration_threshold': self.absence_duration_threshold,
            },
            'emoji': '🧯'
        }

    def check_extinguisher_absence(self, frame):
        """
        简便接口：检查是否发生灭火器缺失

        Returns:
            (is_absent: bool, absence_duration: float)
        """
        if not self.is_enabled:
            return False, 0.0
        try:
            _, _, fd, _ = self.detect(frame)
            return fd["is_absent"], fd["absence_duration"]
        except Exception as e:
            print(f"[{self.algorithm_name}] check_extinguisher_absence 失败: {e}")
            return False, 0.0
