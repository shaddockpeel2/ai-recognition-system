"""
裂缝检测模块 - 重构版本
负责检测图像中的裂缝
检测逻辑：检测到裂缝（crack类别）就报警
"""
import cv2
import numpy as np
from pathlib import Path
from .base_algorithm import BaseAlgorithm


class CrackDetector(BaseAlgorithm):
    """裂缝检测器 - 继承 BaseAlgorithm"""
    
    def __init__(self, confidence_threshold: float = 0.5):
        """
        初始化裂缝检测器

        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.5）
        """
        super().__init__(confidence_threshold)
        
        # 类别配置 - 根据模型输出
        # 假设模型输出 class_id=0: crack（裂缝）
        self.crack_class_id = 0
        
        self.class_config = {
            0: {"name": "CRACK", "color": (0, 0, 255), "status": "检测到裂缝", "level": "high"}  # 红色 - 高危
        }

        self.detection_count = {"crack": 0}

    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "crack_detection"

    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "缝隙检测" / "crack.engine"
        return str(engine_path)

    def detect(self, frame: np.ndarray):
        """
        在图像帧中检测裂缝

        Args:
            frame: 输入图像帧

        Returns:
            tuple: (处理后的帧, 检测信息列表, 检测结果详情, 裂缝告警列表)
        """
        if not self.is_enabled:
            return frame, [], {"crack": 0}, []

        try:
            detections = self._infer(frame)
            detection_info = []
            frame_detections = {"crack": 0}
            processed_frame = frame.copy()
            crack_alerts = []  # 裂缝告警列表

            for det in detections:
                class_id = det['class_id']
                
                # 只处理裂缝类别（class_id=0）
                if class_id == self.crack_class_id:
                    x1, y1, x2, y2 = map(int, det['bbox'])
                    confidence = det['confidence']

                    # 增加检测计数
                    self.detection_count["crack"] += 1
                    frame_detections["crack"] += 1

                    # 绘制检测框和标签
                    config = self.class_config[self.crack_class_id]
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

                    # 添加告警信息
                    detection_info.append({
                        "class": config["name"],
                        "confidence": confidence,
                        "bbox": (x1, y1, x2, y2),
                        "status": config["status"]
                    })

                    # 生成告警（检测到裂缝就告警）
                    crack_alerts.append({
                        "type": "crack_violation",
                        "message": f"检测到裂缝（置信度: {confidence:.2%}）",
                        "confidence": confidence,
                        "location": f"({(x1+x2)//2},{(y1+y2)//2})",
                        "bbox": (x1, y1, x2, y2),
                        "class_name": "CRACK"
                    })
                    
                    # 打印检测信息用于调试
                    print(f"⚡ 检测到裂缝 - 置信度:{confidence:.2%}, 位置:({(x1+x2)//2},{(y1+y2)//2})")

            # 在画面角落显示检测统计
            if frame_detections["crack"] > 0:
                status_text = f"CRACK: {frame_detections['crack']}"
                cv2.putText(processed_frame, status_text, (10, 30),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            return processed_frame, detection_info, frame_detections, crack_alerts

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {str(e)}")
            import traceback
            traceback.print_exc()
            return frame, [], {"crack": 0}, []

    def get_status_info(self):
        """
        获取检测器状态信息

        Returns:
            dict: 包含enabled、count、config等状态信息
        """
        return {
            "name": "裂缝检测",
            "enabled": self.is_enabled,
            "model_path": self.engine_path,
            "confidence_threshold": self.confidence_threshold,
            "detection_count": self.detection_count.copy(),
            "emoji": "⚡"
        }


# 便捷函数
def create_crack_detector(confidence_threshold=0.5):
    """创建裂缝检测器实例"""
    return CrackDetector(confidence_threshold)
