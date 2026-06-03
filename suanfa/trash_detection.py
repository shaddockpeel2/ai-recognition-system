"""
垃圾检测模块 - 基于 BaseAlgorithm
负责检测图像中的垃圾
检测逻辑：检测到垃圾（trash类别）就报警
"""
import cv2
import numpy as np
from .base_algorithm import BaseAlgorithm
from pathlib import Path


class TrashDetector(BaseAlgorithm):
    """垃圾检测器 - 继承 BaseAlgorithm"""
    
    def __init__(self, confidence_threshold: float = 0.5):
        """
        初始化垃圾检测器
        
        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.5）
        """
        super().__init__(confidence_threshold)
        
        # 类别ID配置 - 假设模型输出 class_id=0: trash（垃圾）
        self.trash_class_id = 0
        
        self.class_config = {
            0: {"name": "TRASH", "color": (0, 140, 255), "status": "检测到垃圾", "level": "high"}  # 橙红色
        }
        
        self.detection_count = {"trash": 0}
    
    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "trash_detection"
    
    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "trash" / "trash.engine"
        return str(engine_path)
    
    def detect(self, frame: np.ndarray):
        """
        在图像帧中检测垃圾
        
        Args:
            frame: 输入图像帧 (BGR格式)
        
        Returns:
            tuple: (处理后的帧, 检测信息列表, 检测结果详情, 垃圾告警列表)
        """
        if not self.is_enabled:
            return frame, [], {"trash": 0}, []
        
        if not self._validate_frame(frame):
            return frame, [], {"trash": 0}, []
        
        try:
            detections = self._infer(frame)
            detection_info = []
            frame_detections = {"trash": 0}
            processed_frame = frame.copy()
            trash_alerts = []
            
            for det in detections:
                class_id = det['class_id']
                confidence = det['confidence']
                bbox = det['bbox']
                
                if class_id != self.trash_class_id:
                    continue
                
                if not self._validate_bbox(bbox, frame.shape):
                    continue
                
                x1, y1, x2, y2 = map(int, bbox)
                
                self.detection_count["trash"] += 1
                frame_detections["trash"] += 1
                
                config = self.class_config[self.trash_class_id]
                color = config["color"]
                
                # 绘制检测框
                cv2.rectangle(processed_frame, (x1, y1), (x2, y2), color, 3)
                
                # 绘制标签背景
                label = f"{config['name']} {confidence:.2f}"
                label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(processed_frame, 
                            (x1, y1 - label_size[1] - 10),
                            (x1 + label_size[0], y1),
                            color, -1)
                
                # 绘制标签文字
                cv2.putText(processed_frame, label, (x1, y1 - 5),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                
                detection_info.append({
                    "class": config["name"],
                    "confidence": confidence,
                    "bbox": (x1, y1, x2, y2),
                    "status": config["status"]
                })
                
                # 生成告警（检测到垃圾就告警）
                trash_alerts.append({
                    "type": "trash_violation",
                    "message": f"检测到垃圾（置信度: {confidence:.2%}）",
                    "confidence": confidence,
                    "location": f"({(x1+x2)//2},{(y1+y2)//2})",
                    "bbox": (x1, y1, x2, y2),
                    "class_name": "TRASH"
                })
                
                print(f"🗑️ 检测到垃圾 - 置信度:{confidence:.2%}, 位置:({(x1+x2)//2},{(y1+y2)//2})")
            
            # 在画面上显示检测统计
            if frame_detections["trash"] > 0:
                status_text = f"Trash: {frame_detections['trash']}"
                cv2.putText(processed_frame, status_text, (10, 30),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 140, 255), 2)
            
            return processed_frame, detection_info, frame_detections, trash_alerts
        
        except Exception as e:
            print(f"[{self.algorithm_name}] 检测处理异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return frame, [], {"trash": 0}, []
    
    def get_status_info(self) -> dict:
        """获取检测器状态信息"""
        return {
            'name': "垃圾检测",
            'enabled': self.is_enabled,
            'count': self.detection_count["trash"],
            'details': self.detection_count,
            'emoji': '🗑️'
        }
    
    def reset_count(self):
        """重置检测计数"""
        self.detection_count = {"trash": 0}

