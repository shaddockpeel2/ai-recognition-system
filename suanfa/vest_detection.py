"""
反光衣检测模块 - 重构版本
负责检测图像中的反光衣穿戴情况
"""
import cv2
import numpy as np
from .base_algorithm import BaseAlgorithm
from pathlib import Path


class VestDetector(BaseAlgorithm):
    """反光衣检测器 - 继承 BaseAlgorithm"""
    
    def __init__(self, confidence_threshold: float = 0.5):
        """
        初始化反光衣检测器
        
        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.5）
        """
        # 调用基类初始化（会自动加载引擎）
        super().__init__(confidence_threshold)
        
        # 类别配置 - 根据 safehat.yaml
        self.class_config = {
            7: {"name": "安全背心", "color": (255, 165, 0), "status": "已穿戴"},      # 橙色 - 安全
            4: {"name": "未穿安全背心", "color": (0, 0, 255), "status": "未穿戴"},   # 红色 - 危险
            5: {"name": "人员", "color": (255, 255, 0), "status": "检测到人员"}      # 黄色 - 提示
        }
        
        # 检测计数
        self.detection_count = {"vest": 0, "no_vest": 0, "person": 0}
    
    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "vest_detection"
    
    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "safehat" / "vest.engine"
        return str(engine_path)
    
    def detect(self, frame: np.ndarray):
        """
        在图像帧中检测反光衣相关目标
        
        Args:
            frame: 输入图像帧 (BGR格式)
        
        Returns:
            tuple: (处理后的帧, 检测信息列表, 检测结果详情)
        """
        if not self.is_enabled:
            return frame, [], {"vest": 0, "no_vest": 0, "person": 0}
        
        if not self._validate_frame(frame):
            return frame, [], {"vest": 0, "no_vest": 0, "person": 0}
        
        try:
            detections = self._infer(frame)
            detection_info = []
            frame_detections = {"vest": 0, "no_vest": 0, "person": 0}
            processed_frame = frame.copy()
            
            for det in detections:
                class_id = det['class_id']
                confidence = det['confidence']
                bbox = det['bbox']
                
                if class_id not in self.class_config:
                    continue
                
                if not self._validate_bbox(bbox, frame.shape):
                    continue
                
                x1, y1, x2, y2 = bbox
                class_info = self.class_config[class_id]
                
                processed_frame = self._draw_detection_box(
                    processed_frame, x1, y1, x2, y2, confidence, class_info
                )
                
                if class_id == 7:  # 安全背心
                    frame_detections["vest"] += 1
                    detection_info.append(f"✅ {class_info['name']} ({confidence:.1%})")
                elif class_id == 4:  # 未穿安全背心
                    frame_detections["no_vest"] += 1
                    detection_info.append(f"❌ {class_info['name']} ({confidence:.1%})")
                elif class_id == 5:  # 人员
                    frame_detections["person"] += 1
                    detection_info.append(f"👤 {class_info['name']} ({confidence:.1%})")
            
            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]
            
            return processed_frame, detection_info, frame_detections
        
        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {str(e)}")
            return frame, [], {"vest": 0, "no_vest": 0, "person": 0}
    
    def get_status_info(self) -> dict:
        """获取检测器状态信息"""
        total_count = sum(self.detection_count.values())
        return {
            'name': "反光衣检测",
            'enabled': self.is_enabled,
            'count': total_count,
            'details': self.detection_count,
            'emoji': '🦺'
        }
    
    def reset_count(self):
        """重置检测计数"""
        self.detection_count = {"vest": 0, "no_vest": 0, "person": 0}
    
    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, class_info):
        """绘制检测框和标签"""
        color = class_info["color"]
        name = class_info["name"]
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

        thickness = 3
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

        corner_length = 25
        corner_thickness = 6
        cv2.line(frame, (x1, y1), (x1 + corner_length, y1), color, corner_thickness)
        cv2.line(frame, (x1, y1), (x1, y1 + corner_length), color, corner_thickness)
        cv2.line(frame, (x2, y1), (x2 - corner_length, y1), color, corner_thickness)
        cv2.line(frame, (x2, y1), (x2, y1 + corner_length), color, corner_thickness)
        cv2.line(frame, (x1, y2), (x1 + corner_length, y2), color, corner_thickness)
        cv2.line(frame, (x1, y2), (x1, y2 - corner_length), color, corner_thickness)
        cv2.line(frame, (x2, y2), (x2 - corner_length, y2), color, corner_thickness)
        cv2.line(frame, (x2, y2), (x2, y2 - corner_length), color, corner_thickness)

        if "未穿" in name:
            label = f"[!] NO VEST: {confidence:.1%}"
        elif "安全背心" in name:
            label = f"[V] VEST: {confidence:.1%}"
        else:
            label = f"[P] PERSON: {confidence:.1%}"
        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]

        overlay = frame.copy()
        bg_y1 = max(0, y1 - label_size[1] - 20)
        bg_y2 = y1
        bg_x1 = x1
        bg_x2 = x1 + label_size[0] + 20

        cv2.rectangle(overlay, (bg_x1, bg_y1), (bg_x2, bg_y2), color, -1)
        cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)
        cv2.rectangle(frame, (bg_x1, bg_y1), (bg_x2, bg_y2), color, 2)

        text_color = (255, 255, 255) if sum(color) < 400 else (0, 0, 0)
        cv2.putText(frame, label, (x1 + 10, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, text_color, 2)

        return frame
