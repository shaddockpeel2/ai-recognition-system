"""
手套检测模块 - 重构版本（简化版）
负责检测图像中的手套佩戴情况
检测逻辑：未检测到手套告警，检测到不告警
"""
import cv2
import numpy as np
from .base_algorithm import BaseAlgorithm
from pathlib import Path


class GloveDetector(BaseAlgorithm):
    """手套检测器 - 继承 BaseAlgorithm（简化版，无时间追踪）"""
    
    def __init__(self, confidence_threshold: float = 0.5):
        """
        初始化手套检测器
        
        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.5）
        """
        super().__init__(confidence_threshold)
        
        # 类别配置 - 根据手套检测模型
        # 假设模型输出: 只有"手套"这一个类别（class_id=0）
        self.glove_class_id = 0
        self.class_config = {
            0: {"name": "手套", "color": (0, 255, 0), "status": "已佩戴", "level": "safe"}  # 绿色 - 安全
        }
        
        self.detection_count = {"glove": 0, "no_glove": 0}
    
    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "glove_detection"
    
    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "手套检测" / "glove.engine"
        return str(engine_path)
    
    def detect(self, frame: np.ndarray, video_source_id=None):
        """
        检测手套佩戴情况
        
        检测逻辑：
        - 检测到手套 = 正常（绿色框，不告警）
        - 未检测到手套 = 违规（触发告警）
        
        Args:
            frame: 输入图像帧 (BGR格式)
            video_source_id: 视频源ID（兼容旧接口，但不使用）
        
        Returns:
            tuple: (processed_frame, alerts)
                - processed_frame: 标注后的图像
                - alerts: 告警列表
        """
        if not self.is_enabled:
            return frame, []
        
        alerts = []
        processed_frame = frame.copy()
        
        try:
            # 推理
            detections = self._infer(frame)
            
            glove_detected = False  # 是否检测到手套
            
            # 处理检测结果
            for det in detections:
                cls_id = det['class_id']
                conf = det['confidence']
                x1, y1, x2, y2 = det['bbox']
                
                # 转换为整数
                x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
                
                # 只处理手套类别
                if cls_id == self.glove_class_id:
                    glove_detected = True
                    
                    # 更新计数
                    self.detection_count["glove"] += 1
                    
                    # 绘制绿色检测框（表示已佩戴手套）
                    color = self.class_config[cls_id]["color"]
                    cv2.rectangle(processed_frame, (x1, y1), (x2, y2), color, 2)
                    
                    # 添加标签（英文避免乱码）
                    label = f"GLOVE {conf:.2f}"
                    label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
                    cv2.rectangle(processed_frame, (x1, y1 - label_size[1] - 8), 
                                (x1 + label_size[0], y1), color, -1)
                    cv2.putText(processed_frame, label, (x1, y1 - 5),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            
            # 如果未检测到手套，生成告警
            if not glove_detected:
                self.detection_count["no_glove"] += 1
                
                # 生成告警
                alert = {
                    "type": "glove_violation",
                    "message": "未检测到手套",
                    "timestamp": self._get_current_timestamp(),
                    "confidence": 1.0  # 未检测到的置信度设为1.0
                }
                alerts.append(alert)
            
        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {str(e)}")
            import traceback
            traceback.print_exc()
        
        return processed_frame, alerts
    
    def _get_current_timestamp(self):
        """获取当前时间戳（格式化字符串）"""
        import time
        return time.strftime("%Y-%m-%d %H:%M:%S")
    
    def get_status_info(self):
        """
        获取检测器状态信息
        
        Returns:
            dict: 包含enabled、count、config等状态信息
        """
        return {
            "name": "手套检测",
            "enabled": self.is_enabled,
            "model_path": self.engine_path,
            "confidence_threshold": self.confidence_threshold,
            "detection_count": self.detection_count.copy(),
            "emoji": "🧤"
        }


# 便捷函数
def create_glove_detector(confidence_threshold=0.5):
    """创建手套检测器实例"""
    return GloveDetector(confidence_threshold)
