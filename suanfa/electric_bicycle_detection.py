"""
电瓶车检测模块 - 基于 BaseAlgorithm
负责检测图像中的电瓶车
检测逻辑：检测到电瓶车（electric bicycle类别）就报警
"""
import cv2
import numpy as np
from .base_algorithm import BaseAlgorithm
from pathlib import Path


class ElectricBicycleDetector(BaseAlgorithm):
    """电瓶车检测器 - 继承 BaseAlgorithm"""
    
    def __init__(self, confidence_threshold: float = 0.5):
        """
        初始化电瓶车检测器
        
        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.5）
        """
        super().__init__(confidence_threshold)
        
        # 类别ID配置 - 假设模型输出 class_id=0: electric_bicycle（电瓶车）
        self.electric_bicycle_class_id = 0
        
        self.class_config = {
            0: {"name": "电瓶车", "color": (0, 165, 255), "status": "检测到电瓶车", "level": "high"}  # 橙色
        }
        
        self.detection_count = {"electric_bicycle": 0}
    
    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "electric_bicycle_detection"
    
    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "electric bicycle" / "electric_bicycle.engine"
        return str(engine_path)
    
    def detect(self, frame: np.ndarray):
        """
        在图像帧中检测电瓶车
        
        Args:
            frame: 输入图像帧 (BGR格式)
        
        Returns:
            tuple: (处理后的帧, 检测信息列表, 检测结果详情, 电瓶车告警列表)
        """
        if not self.is_enabled:
            return frame, [], {"electric_bicycle": 0}, []
        
        if not self._validate_frame(frame):
            return frame, [], {"electric_bicycle": 0}, []
        
        try:
            detections = self._infer(frame)
            detection_info = []
            frame_detections = {"electric_bicycle": 0}
            processed_frame = frame.copy()
            electric_bicycle_alerts = []
            
            for det in detections:
                class_id = det['class_id']
                confidence = det['confidence']
                bbox = det['bbox']
                
                if class_id != self.electric_bicycle_class_id:
                    continue
                
                if not self._validate_bbox(bbox, frame.shape):
                    continue
                
                x1, y1, x2, y2 = map(int, bbox)
                
                self.detection_count["electric_bicycle"] += 1
                frame_detections["electric_bicycle"] += 1
                
                config = self.class_config[self.electric_bicycle_class_id]
                color = config["color"]
                
                # 绘制检测框
                cv2.rectangle(processed_frame, (x1, y1), (x2, y2), color, 3)
                
                # 绘制标签背景（使用英文避免乱码）
                label = f"E-BIKE {confidence:.2f}"
                label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(processed_frame, 
                            (x1, y1 - label_size[1] - 10),
                            (x1 + label_size[0], y1),
                            color, -1)
                
                # 绘制标签文字（英文：E-BIKE = 电瓶车）
                cv2.putText(processed_frame, label, (x1, y1 - 5),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                
                detection_info.append({
                    "class": config["name"],
                    "confidence": confidence,
                    "bbox": (x1, y1, x2, y2),
                    "status": config["status"]
                })
                
                # 生成告警（检测到电瓶车就告警）
                electric_bicycle_alerts.append({
                    "type": "electric_bicycle_violation",
                    "message": f"检测到电瓶车（置信度: {confidence:.2%}）",
                    "confidence": confidence,
                    "location": f"({(x1+x2)//2},{(y1+y2)//2})",
                    "bbox": (x1, y1, x2, y2),
                    "class_name": "ELECTRIC_BICYCLE"
                })
                
                print(f"🛴 检测到电瓶车 - 置信度:{confidence:.2%}, 位置:({(x1+x2)//2},{(y1+y2)//2})")
            
            # 在画面上显示检测统计
            if frame_detections["electric_bicycle"] > 0:
                status_text = f"Electric Bicycle: {frame_detections['electric_bicycle']}"
                cv2.putText(processed_frame, status_text, (10, 30),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)
            
            return processed_frame, detection_info, frame_detections, electric_bicycle_alerts
        
        except Exception as e:
            print(f"[{self.algorithm_name}] 检测处理异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return frame, [], {"electric_bicycle": 0}, []
    
    def get_status_info(self) -> dict:
        """获取检测器状态信息"""
        return {
            'name': "电瓶车检测",
            'enabled': self.is_enabled,
            'count': self.detection_count["electric_bicycle"],
            'details': self.detection_count,
            'emoji': '🛴'
        }
    
    def reset_count(self):
        """重置检测计数"""
        self.detection_count = {"electric_bicycle": 0}

