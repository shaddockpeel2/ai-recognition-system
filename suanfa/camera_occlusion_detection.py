"""
摄像头遮挡检测模块 - 重构版本
检测图像中的摄像头遮挡情况
功能：检测到摄像头遮挡即告警，未检测到不告警
"""
import cv2
import numpy as np
from pathlib import Path
from .base_algorithm import BaseAlgorithm


class CameraOcclusionDetector(BaseAlgorithm):
    """摄像头遮挡检测器 - 继承 BaseAlgorithm"""
    
    def __init__(self, confidence_threshold: float = 0.2):
        """
        初始化摄像头遮挡检测器

        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.5）
        """
        super().__init__(confidence_threshold)
        
        # 类别配置 - 根据data.yaml
        self.class_config = {
            0: {"name": "摄像头遮挡", "color": (0, 0, 255), "status": "检测到遮挡"}  # 红色 - 危险
        }

        self.detection_count = {"occlusion": 0}

    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "camera_occlusion_detection"

    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "摄像头遮挡" / "occlusion.engine"
        return str(engine_path)

    def detect(self, frame: np.ndarray):
        """
        在图像帧中检测摄像头遮挡

        Args:
            frame: 输入图像帧

        Returns:
            tuple: (处理后的帧, 检测信息列表, 检测结果详情)
        """
        if not self.is_enabled:
            return frame, [], {"occlusion": 0}

        try:
            detections = self._infer(frame)
            detection_info = []
            frame_detections = {"occlusion": 0}
            processed_frame = frame.copy()

            for det in detections:
                class_id = det['class_id']
                
                # 只处理摄像头遮挡类别
                if class_id in self.class_config:
                    x1, y1, x2, y2 = det['bbox']
                    confidence = det['confidence']

                    # 获取类别配置
                    class_info = self.class_config[class_id]

                    # 绘制检测框
                    processed_frame = self._draw_detection_box(
                        processed_frame, x1, y1, x2, y2, confidence, class_info
                    )

                    # 更新计数
                    frame_detections["occlusion"] += 1
                    detection_info.append(f"⚠️ {class_info['name']} ({confidence:.1%})")

            # 更新总检测计数
            for key in frame_detections:
                self.detection_count[key] += frame_detections[key]

            return processed_frame, detection_info, frame_detections

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {str(e)}")
            import traceback
            traceback.print_exc()
            return frame, [], {"occlusion": 0}

    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, class_info):
        """绘制检测框和标签"""
        color = class_info["color"]
        name = class_info["name"]

        # 绘制主边界框（更粗的边框突出危险性）
        thickness = 4
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, thickness)

        # 绘制边角装饰（科技感 + 警示效果）
        corner_length = 30
        corner_thickness = 7
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

        # 添加警示图标和标签
        label = f"[!] OCCLUSION: {confidence:.1%}"
        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]

        # 绘制标签背景（半透明红色）
        overlay = frame.copy()
        bg_y1 = max(0, int(y1) - label_size[1] - 25)
        bg_y2 = int(y1)
        bg_x1 = int(x1)
        bg_x2 = int(x1) + label_size[0] + 25

        cv2.rectangle(overlay, (bg_x1, bg_y1), (bg_x2, bg_y2), color, -1)
        cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)

        # 添加边框
        cv2.rectangle(frame, (bg_x1, bg_y1), (bg_x2, bg_y2), color, 3)

        # 添加文字（白色文字确保可见性）
        cv2.putText(frame, label, (int(x1) + 12, int(y1) - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        # 添加闪烁效果的警示标记（在框的中心上方）
        center_x = int((x1 + x2) / 2)
        warning_y = max(30, int(y1) - 40)
        cv2.circle(frame, (center_x, warning_y), 15, (0, 0, 255), -1)
        cv2.putText(frame, "!", (center_x - 5, warning_y + 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 3)

        return frame

    def get_status_info(self):
        """获取检测器状态信息"""
        return {
            'name': "摄像头遮挡检测",
            'enabled': self.is_enabled,
            'count': self.detection_count["occlusion"],
            'details': self.detection_count,
            'emoji': '📵'
        }

    def check_alert(self, detections):
        """
        检查是否需要告警
        检测到摄像头遮挡即告警

        Args:
            detections: 检测结果字典

        Returns:
            tuple: (是否告警, 告警消息)
        """
        occlusion_count = detections.get("occlusion", 0)
        
        if occlusion_count > 0:
            return True, f"检测到摄像头遮挡"
        
        return False, ""


# 便捷函数
def create_camera_occlusion_detector(confidence_threshold=0.5):
    """创建摄像头遮挡检测器实例"""
    return CameraOcclusionDetector(confidence_threshold)
