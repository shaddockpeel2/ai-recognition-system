"""
烟雾检测模块 - 重构版本
负责检测图像中的烟雾
检测逻辑：检测到烟雾（smoke类别）就报警
"""
import cv2
import numpy as np
from .base_algorithm import BaseAlgorithm
from pathlib import Path


class SmokeDetector(BaseAlgorithm):
    """烟雾检测器 - 继承 BaseAlgorithm"""
    
    def __init__(self, confidence_threshold: float = 0.5):
        """
        初始化烟雾检测器
        
        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.5）
        """
        super().__init__(confidence_threshold)
        
        # 类别配置 - 根据fire.yaml
        # names: [fire, smoke]
        # class_id=0: fire（火灾）- 注意：模型实际输出与配置相反
        self.smoke_class_id = 0
        
        self.class_config = {
            0: {"name": "烟雾", "color": (128, 128, 128), "status": "检测到烟雾", "level": "high"}  # 灰色 - 烟雾
        }
        
        self.detection_count = {"smoke": 0}
    
    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "smoke_detection"
    
    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "烟火检测" / "smoke.engine"
        return str(engine_path)
    
    def detect(self, frame: np.ndarray):
        """
        在图像帧中检测烟雾
        
        Args:
            frame: 输入图像帧 (BGR格式)
        
        Returns:
            tuple: (处理后的帧, 检测信息列表, 检测结果详情, 烟雾告警列表)
        """
        if not self.is_enabled:
            return frame, [], {"smoke": 0}, []
        
        try:
            detections = self._infer(frame)
            detection_info = []
            frame_detections = {"smoke": 0}
            processed_frame = frame.copy()
            smoke_alerts = []  # 烟雾告警列表
            
            for det in detections:
                class_id = det['class_id']
                confidence = det['confidence']
                x1, y1, x2, y2 = det['bbox']
                
                # 只处理smoke类别（class_id = 0，实际为火灾）
                if class_id == self.smoke_class_id:
                    # 获取类别配置
                    class_info = self.class_config[class_id]
                    
                    # 绘制检测框（灰色表示烟雾）
                    processed_frame = self._draw_detection_box(
                        processed_frame, x1, y1, x2, y2, confidence, class_info
                    )
                    
                    frame_detections["smoke"] += 1
                    detection_info.append(f"💨 {class_info['name']} ({confidence:.1%})")
                    
                    # 生成烟雾告警信息
                    smoke_alerts.append({
                        "bbox": (x1, y1, x2, y2),
                        "confidence": float(confidence),
                        "message": "检测到烟雾",
                        "status": "烟雾"
                    })
            
            # 更新总检测计数
            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]
            
            return processed_frame, detection_info, frame_detections, smoke_alerts
            
        except Exception as e:
            print(f"[{self.engine_id}] 检测错误: {str(e)}")
            return frame, [], {"smoke": 0}, []
    
    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, class_info):
        """绘制检测框和标签"""
        color = class_info["color"]
        name = class_info["name"]
        
        # 绘制主边界框（灰色粗线）
        thickness = 3
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, thickness)
        
        # 绘制边角装饰（科技感）
        corner_length = 25
        corner_thickness = 6
        # 左上角
        cv2.line(frame, (int(x1), int(y1)), (int(x1 + corner_length), int(y1)), color, corner_thickness)
        cv2.line(frame, (int(x1), int(y1)), (int(x1), int(y1 + corner_length)), color, corner_thickness)
        # 右上角
        cv2.line(frame, (int(x2), int(y1)), (int(x2 - corner_length), int(y1)), color, corner_thickness)
        cv2.line(frame, (int(x2), int(y1)), (int(x2), int(y1 + corner_length)), color, corner_thickness)
        # 左下角
        cv2.line(frame, (int(x1), int(y2)), (int(x1 + corner_length), int(y2)), color, corner_thickness)
        cv2.line(frame, (int(x1), int(y2)), (int(x1), int(y2 - corner_length)), color, corner_thickness)
        # 右下角
        cv2.line(frame, (int(x2), int(y2)), (int(x2 - corner_length), int(y2)), color, corner_thickness)
        cv2.line(frame, (int(x2), int(y2)), (int(x2), int(y2 - corner_length)), color, corner_thickness)
        
        # 添加状态图标和标签
        label = f"[!] SMOKE: {confidence:.1%}"
        
        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
        
        # 绘制标签背景（灰色半透明）
        overlay = frame.copy()
        bg_y1 = max(0, int(y1) - label_size[1] - 20)
        bg_y2 = int(y1)
        bg_x1 = int(x1)
        bg_x2 = int(x1) + label_size[0] + 20
        
        cv2.rectangle(overlay, (bg_x1, bg_y1), (bg_x2, bg_y2), color, -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
        
        # 添加边框
        cv2.rectangle(frame, (bg_x1, bg_y1), (bg_x2, bg_y2), color, 2)
        
        # 添加文字（白色文字确保可见性）
        cv2.putText(frame, label, (int(x1) + 10, int(y1) - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        return frame
    
    def check_smoke_detected(self, frame):
        """
        检查是否检测到烟雾
        
        检测逻辑：
        1. 运行YOLO模型检测
        2. 如果检测到smoke类别（class_id=0，实际为火灾类），返回 True（有烟雾）
        3. 否则返回 False（无烟雾）
        
        Args:
            frame: 输入图像帧
            
        Returns:
            tuple: (是否检测到烟雾, 烟雾数量, 边界框列表)
        """
        if not self.is_enabled:
            return False, 0, []
        
        try:
            detections = self._infer(frame)
            
            smoke_detected = False
            smoke_count = 0
            bboxes = []
            
            for det in detections:
                class_id = det['class_id']
                
                # 检测到smoke类别（class_id=0，实际为火灾类）
                if class_id == self.smoke_class_id:
                    smoke_detected = True
                    smoke_count += 1
                    
                    x1, y1, x2, y2 = det['bbox']
                    confidence = det['confidence']
                    
                    bboxes.append({
                        "bbox": (float(x1), float(y1), float(x2), float(y2)),
                        "confidence": confidence
                    })
            
            return smoke_detected, smoke_count, bboxes
            
        except Exception as e:
            print(f"[{self.engine_id}] 检查烟雾失败: {str(e)}")
            return False, 0, []
    
    def get_status_info(self):
        """获取检测器状态信息"""
        return {
            'name': "烟雾检测",
            'enabled': self.is_enabled,
            'count': self.detection_count["smoke"],
            'details': self.detection_count,
            'emoji': '💨'
        }
