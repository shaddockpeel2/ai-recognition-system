"""
安全帽检测模块
负责检测图像中的安全帽佩戴情况
"""
import cv2
import numpy as np
from pathlib import Path
from .base_algorithm import BaseAlgorithm


class HelmetDetector(BaseAlgorithm):
    def __init__(self, confidence_threshold: float = 0.5):
        # 兼容两套模型类别：
        # 1. 老模型：0=安全帽, 2=未戴安全帽, 5=人员
        # 2. 新模型：0=hat, 1=person
        self.class_config = {
            0: {"name": "安全帽",    "color": (0, 255, 0),   "status": "已佩戴"},
            1: {"name": "人员",      "color": (255, 255, 0), "status": "检测到人员"},
            2: {"name": "未戴安全帽","color": (0, 0, 255),   "status": "未佩戴"},
            5: {"name": "人员",      "color": (255, 255, 0), "status": "检测到人员"},
        }
        # super().__init__() 内部会执行 self.detection_count = {}，
        # 必须在 super() 之后再赋值，否则会被覆盖导致 detect() 中 KeyError
        super().__init__(confidence_threshold)
        self.detection_count = {"helmet": 0, "no_helmet": 0, "person": 0}

    def get_algorithm_name(self) -> str:
        return "helmet_detection"

    def get_engine_path(self) -> str:
        return str(Path(__file__).parent.parent / "models" / "safehat" / "helmet.engine")

    def reset_count(self):
        self.detection_count = {"helmet": 0, "no_helmet": 0, "person": 0}

    def detect(self, frame: np.ndarray):
        """
        Returns:
            tuple: (processed_frame, detection_info, frame_detections)
        """
        if not self.is_enabled:
            return frame, [], {"helmet": 0, "no_helmet": 0, "person": 0}

        if not self._validate_frame(frame):
            return frame, [], {"helmet": 0, "no_helmet": 0, "person": 0}

        try:
            raw_dets = self._infer(frame)
            class_ids = {det.get("class_id") for det in raw_dets}

            # 新模型只有 hat/person 两类时，按“帽子是否落在人员头部区域”推断是否佩戴安全帽。
            if 1 in class_ids and 2 not in class_ids and 5 not in class_ids:
                processed_frame, detection_info, frame_detections = self._detect_by_hat_person_matching(
                    frame, raw_dets
                )
            else:
                processed_frame, detection_info, frame_detections = self._detect_by_explicit_labels(
                    frame, raw_dets
                )

            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]

            return processed_frame, detection_info, frame_detections

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {e}")
            import traceback; traceback.print_exc()
            return frame, [], {"helmet": 0, "no_helmet": 0, "person": 0}

    def _detect_by_explicit_labels(self, frame: np.ndarray, raw_dets):
        """兼容老模型：直接使用模型输出的未戴安全帽类别。"""
        detection_info = []
        frame_detections = {"helmet": 0, "no_helmet": 0, "person": 0}
        processed_frame = frame.copy()

        for det in raw_dets:
            class_id = det['class_id']
            if class_id not in self.class_config:
                continue
            if not self._validate_bbox(det['bbox'], frame.shape):
                continue

            x1, y1, x2, y2 = self._clamp_bbox(det['bbox'], frame.shape)
            confidence = float(det['confidence'])
            class_info = self.class_config[class_id]

            processed_frame = self._draw_detection_box(
                processed_frame, x1, y1, x2, y2, confidence, class_info
            )

            if class_id == 0:
                frame_detections["helmet"] += 1
                detection_info.append(f"[OK] HELMET ({confidence:.1%})")
            elif class_id == 2:
                frame_detections["no_helmet"] += 1
                detection_info.append(f"[!] NO HELMET ({confidence:.1%})")
            elif class_id in (1, 5):
                frame_detections["person"] += 1
                detection_info.append(f"[P] PERSON ({confidence:.1%})")

        return processed_frame, detection_info, frame_detections

    def _detect_by_hat_person_matching(self, frame: np.ndarray, raw_dets):
        """适配新模型：通过 hat 与 person 的空间关系判断是否佩戴安全帽。"""
        hats = []
        persons = []
        for det in raw_dets:
            class_id = det.get("class_id")
            bbox = det.get("bbox")
            if class_id not in (0, 1) or not self._validate_bbox(bbox, frame.shape):
                continue

            parsed = {
                "bbox": self._clamp_bbox(bbox, frame.shape),
                "confidence": float(det.get("confidence", 0.0)),
            }
            if class_id == 0:
                hats.append(parsed)
            else:
                persons.append(parsed)

        hats.sort(key=lambda item: item["confidence"], reverse=True)
        persons.sort(key=lambda item: item["bbox"][1])

        detection_info = []
        frame_detections = {"helmet": 0, "no_helmet": 0, "person": 0}
        processed_frame = frame.copy()
        used_hat_indexes = set()

        for person in persons:
            frame_detections["person"] += 1
            matched_hat_index = self._find_matching_hat(person["bbox"], hats, used_hat_indexes)

            if matched_hat_index is not None:
                used_hat_indexes.add(matched_hat_index)
                hat_confidence = hats[matched_hat_index]["confidence"]
                frame_detections["helmet"] += 1
                detection_info.append(f"[OK] HELMET ({hat_confidence:.1%})")
                processed_frame = self._draw_person_status_box(
                    processed_frame,
                    person["bbox"],
                    hat_confidence,
                    has_helmet=True,
                )
            else:
                person_confidence = person["confidence"]
                frame_detections["no_helmet"] += 1
                detection_info.append(f"[!] NO HELMET ({person_confidence:.1%})")
                processed_frame = self._draw_person_status_box(
                    processed_frame,
                    person["bbox"],
                    person_confidence,
                    has_helmet=False,
                )

        return processed_frame, detection_info, frame_detections

    def _find_matching_hat(self, person_bbox, hats, used_hat_indexes):
        """在人员框顶部区域寻找最合适的安全帽框。"""
        best_index = None
        best_score = -1.0

        for index, hat in enumerate(hats):
            if index in used_hat_indexes:
                continue
            if not self._hat_matches_person(hat["bbox"], person_bbox):
                continue

            score = hat["confidence"]
            if score > best_score:
                best_score = score
                best_index = index

        return best_index

    def _hat_matches_person(self, hat_bbox, person_bbox):
        """判断安全帽框是否位于对应人员的头部区域。"""
        hx1, hy1, hx2, hy2 = hat_bbox
        px1, py1, px2, py2 = person_bbox

        person_width = max(1, px2 - px1)
        person_height = max(1, py2 - py1)
        hat_width = max(1, hx2 - hx1)
        hat_height = max(1, hy2 - hy1)
        hat_center_x = (hx1 + hx2) / 2.0
        hat_center_y = (hy1 + hy2) / 2.0

        horizontal_padding = person_width * 0.15
        top_band_bottom = py1 + person_height * 0.45
        top_band_top = py1 - person_height * 0.12

        horizontal_overlap = max(0, min(hx2, px2) - max(hx1, px1))
        overlap_ratio = horizontal_overlap / hat_width

        return (
            px1 - horizontal_padding <= hat_center_x <= px2 + horizontal_padding
            and top_band_top <= hat_center_y <= top_band_bottom
            and overlap_ratio >= 0.4
            and hat_width <= person_width * 0.9
            and hat_height <= person_height * 0.45
        )

    def _draw_person_status_box(self, frame, bbox, confidence, has_helmet):
        """在人员框上直接标注佩戴状态，便于大屏查看。"""
        x1, y1, x2, y2 = bbox
        class_info = {
            "name": "安全帽" if has_helmet else "未戴安全帽",
            "color": (0, 255, 0) if has_helmet else (0, 0, 255),
        }
        return self._draw_detection_box(frame, x1, y1, x2, y2, confidence, class_info)

    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, class_info):
        color = class_info["color"]
        name  = class_info["name"]

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)

        cl = 25
        ct = 6
        cv2.line(frame, (x1, y1), (x1 + cl, y1), color, ct)
        cv2.line(frame, (x1, y1), (x1, y1 + cl), color, ct)
        cv2.line(frame, (x2, y1), (x2 - cl, y1), color, ct)
        cv2.line(frame, (x2, y1), (x2, y1 + cl), color, ct)
        cv2.line(frame, (x1, y2), (x1 + cl, y2), color, ct)
        cv2.line(frame, (x1, y2), (x1, y2 - cl), color, ct)
        cv2.line(frame, (x2, y2), (x2 - cl, y2), color, ct)
        cv2.line(frame, (x2, y2), (x2, y2 - cl), color, ct)

        if "未戴" in name:
            label = f"[!] NO HELMET: {confidence:.1%}"
        elif "安全帽" in name:
            label = f"[H] HELMET: {confidence:.1%}"
        else:
            label = f"[P] PERSON: {confidence:.1%}"

        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
        overlay    = frame.copy()
        bg_y1 = max(0, y1 - label_size[1] - 20)
        cv2.rectangle(overlay, (x1, bg_y1), (x1 + label_size[0] + 20, y1), color, -1)
        cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)
        cv2.rectangle(frame, (x1, bg_y1), (x1 + label_size[0] + 20, y1), color, 2)
        text_color = (255, 255, 255) if sum(color) < 400 else (0, 0, 0)
        cv2.putText(frame, label, (x1 + 10, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, text_color, 2)
        return frame

    def get_status_info(self) -> dict:
        return {
            'name':    "安全帽检测",
            'enabled': self.is_enabled,
            'count':   sum(self.detection_count.values()),
            'details': self.detection_count,
            'emoji':   '🪖'
        }
