"""
车辆违停检测模块 - 重构版本
负责检测图像中的车辆违停情况
检测逻辑：画面中检测到车辆且停放超过设定时长（默认30秒）即告警
"""
import cv2
import numpy as np
import time
from pathlib import Path
from .base_algorithm import BaseAlgorithm


class VehicleParkingDetector(BaseAlgorithm):
    """车辆违停检测器 - 继承 BaseAlgorithm"""
    
    def __init__(self, confidence_threshold: float = 0.5, parking_duration_threshold: float = 30.0):
        """
        初始化车辆违停检测器

        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.5）
            parking_duration_threshold: 触发告警的停放时长（秒，默认30秒）
        """
        super().__init__(confidence_threshold)
        
        # 使用小汽车识别模型的car类别
        # names: ['car']
        # class_id=0: car（车辆）
        self.car_class_id = 0
        
        # 配置参数（可配置）
        self.parking_duration_threshold = parking_duration_threshold  # 违停时长阈值（秒）
        
        # 车辆追踪状态
        self.tracked_vehicles = {}  # 追踪的车辆 {vehicle_id: {start_time, bbox, last_seen}}
        self.next_vehicle_id = 0
        self.distance_threshold = 50  # 相同车辆判定距离阈值（像素）
        
        self.class_config = {
            0: {"name": "车辆", "color": (0, 165, 255), "status": "车辆违停", "level": "high"}  # 橙色
        }

        self.detection_count = {"vehicles": 0, "parking_violations": 0}

    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "vehicle_parking_detection"

    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "小汽车识别" / "vehicle.engine"
        return str(engine_path)

    def enable(self):
        """启用车辆违停检测"""
        super().enable()
        self.detection_count = {"vehicles": 0, "parking_violations": 0}
        self.tracked_vehicles = {}
        self.next_vehicle_id = 0
        print(f"[{self.algorithm_name}] 违停时长: {self.parking_duration_threshold}秒")

    def disable(self):
        """禁用车辆违停检测"""
        super().disable()
        self.tracked_vehicles = {}

    def reset_count(self):
        """重置检测计数"""
        super().reset_count()
        self.detection_count = {"vehicles": 0, "parking_violations": 0}
        self.tracked_vehicles = {}
        self.next_vehicle_id = 0

    def update_config(self, confidence_threshold=None, parking_duration_threshold=None, **kwargs):
        """
        更新配置参数
        
        Args:
            confidence_threshold: 置信度阈值
            parking_duration_threshold: 违停时长阈值（秒）
        """
        super().update_config(confidence_threshold=confidence_threshold, **kwargs)
        if parking_duration_threshold is not None:
            self.parking_duration_threshold = max(5.0, float(parking_duration_threshold))  # 至少5秒
            print(f"[{self.algorithm_name}] 违停时长已更新为: {self.parking_duration_threshold}秒")

    def _calculate_center(self, bbox):
        """计算边界框中心点"""
        x1, y1, x2, y2 = bbox
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def _calculate_distance(self, center1, center2):
        """计算两个中心点之间的距离"""
        return np.sqrt((center1[0] - center2[0])**2 + (center1[1] - center2[1])**2)

    def _match_vehicle(self, bbox, confidence):
        """
        匹配或创建车辆追踪ID
        
        Args:
            bbox: 边界框
            confidence: 置信度
            
        Returns:
            vehicle_id: 车辆ID
        """
        current_center = self._calculate_center(bbox)
        current_time = time.time()
        
        # 查找最近的已追踪车辆
        best_match_id = None
        min_distance = float('inf')
        
        for vehicle_id, vehicle_data in list(self.tracked_vehicles.items()):
            # 清理超过5秒未见到的车辆
            if current_time - vehicle_data['last_seen'] > 5.0:
                del self.tracked_vehicles[vehicle_id]
                continue
            
            tracked_center = self._calculate_center(vehicle_data['bbox'])
            distance = self._calculate_distance(current_center, tracked_center)
            
            if distance < self.distance_threshold and distance < min_distance:
                min_distance = distance
                best_match_id = vehicle_id
        
        if best_match_id is not None:
            # 更新已有车辆
            self.tracked_vehicles[best_match_id]['bbox'] = bbox
            self.tracked_vehicles[best_match_id]['last_seen'] = current_time
            self.tracked_vehicles[best_match_id]['confidence'] = confidence
            return best_match_id
        else:
            # 创建新车辆
            vehicle_id = self.next_vehicle_id
            self.next_vehicle_id += 1
            self.tracked_vehicles[vehicle_id] = {
                'start_time': current_time,
                'bbox': bbox,
                'last_seen': current_time,
                'confidence': confidence
            }
            return vehicle_id

    def detect(self, frame: np.ndarray):
        """
        在图像帧中检测车辆违停

        Args:
            frame: 输入图像帧

        Returns:
            tuple: (处理后的帧, 检测信息列表, 检测结果详情, 违停告警列表)
        """
        if not self.is_enabled:
            return frame, [], {"vehicles": 0, "parking_violations": 0}, []

        try:
            detections = self._infer(frame)
            detection_info = []
            frame_detections = {"vehicles": 0, "parking_violations": 0}
            processed_frame = frame.copy()
            parking_violations = []
            
            current_time = time.time()
            detected_vehicles = []  # 当前帧检测到的车辆

            # 收集所有检测到的车辆
            for det in detections:
                class_id = det['class_id']

                # 只处理car类别（class_id = 0）
                if class_id == self.car_class_id:
                    x1, y1, x2, y2 = det['bbox']
                    confidence = det['confidence']
                    
                    bbox = (float(x1), float(y1), float(x2), float(y2))
                    
                    # 匹配或创建车辆ID
                    vehicle_id = self._match_vehicle(bbox, float(confidence))
                    
                    detected_vehicles.append({
                        "vehicle_id": vehicle_id,
                        "bbox": bbox,
                        "confidence": float(confidence)
                    })
                    
                    frame_detections["vehicles"] += 1

            # 绘制检测结果并检查违停
            for vehicle in detected_vehicles:
                vehicle_id = vehicle["vehicle_id"]
                bbox = vehicle["bbox"]
                confidence = vehicle["confidence"]
                x1, y1, x2, y2 = bbox
                
                # 计算停放时长
                vehicle_data = self.tracked_vehicles.get(vehicle_id)
                if vehicle_data:
                    parking_duration = current_time - vehicle_data['start_time']
                    
                    # 检查是否违停
                    is_violation = parking_duration >= self.parking_duration_threshold
                    
                    if is_violation:
                        frame_detections["parking_violations"] += 1
                        
                        # 生成违停告警
                        parking_violations.append({
                            "vehicle_id": vehicle_id,
                            "duration": parking_duration,
                            "bbox": bbox,
                            "confidence": confidence,
                            "message": f"车辆违停 {parking_duration:.1f}秒",
                            "status": "车辆违停"
                        })
                    
                    # 根据停放时长选择颜色
                    if is_violation:
                        color = (0, 0, 255)  # 红色 - 违停告警
                        status = f"VIOLATION {parking_duration:.1f}s"
                    elif parking_duration > self.parking_duration_threshold * 0.7:
                        color = (0, 255, 255)  # 黄色 - 警告状态
                        status = f"Warning {parking_duration:.1f}s"
                    else:
                        color = (0, 255, 0)  # 绿色 - 正常状态
                        status = f"Parked {parking_duration:.1f}s"
                    
                    # 绘制边界框
                    processed_frame = self._draw_detection_box(
                        processed_frame, x1, y1, x2, y2, confidence, color, status, vehicle_id
                    )

            # 绘制顶部统计信息
            if frame_detections["vehicles"] > 0:
                info_text = f"Vehicles: {frame_detections['vehicles']}"
                if frame_detections["parking_violations"] > 0:
                    info_text += f" | Violations: {frame_detections['parking_violations']}"
                
                # 绘制信息栏
                cv2.rectangle(processed_frame, (0, 0), (processed_frame.shape[1], 40), (0, 0, 0), -1)
                cv2.putText(processed_frame, info_text, (10, 25),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # 更新总检测计数
            self.detection_count["vehicles"] += frame_detections["vehicles"]
            self.detection_count["parking_violations"] += frame_detections["parking_violations"]
            
            return processed_frame, detection_info, frame_detections, parking_violations

        except Exception as e:
            print(f"[{self.algorithm_name}] 检测错误: {str(e)}")
            return frame, [], {"vehicles": 0, "parking_violations": 0}, []

    def _draw_detection_box(self, frame, x1, y1, x2, y2, confidence, color, status, vehicle_id):
        """绘制检测框和标签"""
        # 绘制主边界框
        thickness = 2
        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, thickness)

        # 绘制标签
        label = f"Car#{vehicle_id} {confidence:.1%} - {status}"
        label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]

        # 绘制标签背景
        bg_y1 = max(0, int(y1) - label_size[1] - 10)
        bg_y2 = int(y1)
        bg_x1 = int(x1)
        bg_x2 = int(x1) + label_size[0] + 10

        cv2.rectangle(frame, (bg_x1, bg_y1), (bg_x2, bg_y2), color, -1)
        cv2.putText(frame, label, (int(x1) + 5, int(y1) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

        return frame

    def check_parking_violation(self, frame):
        """
        检查是否发生车辆违停
        
        Args:
            frame: 输入图像帧
            
        Returns:
            tuple: (是否违停, 车辆数量, 违停列表)
        """
        if not self.is_enabled:
            return False, 0, []
        
        try:
            _, _, frame_detections, parking_violations = self.detect(frame)
            
            has_violation = len(parking_violations) > 0
            vehicle_count = frame_detections["vehicles"]
            
            return has_violation, vehicle_count, parking_violations
            
        except Exception as e:
            print(f"[{self.algorithm_name}] 检查车辆违停失败: {str(e)}")
            return False, 0, []

    def get_status_info(self):
        """获取检测器状态信息"""
        return {
            'name': "车辆违停检测",
            'enabled': self.is_enabled,
            'count': self.detection_count["vehicles"],
            'violations': self.detection_count["parking_violations"],
            'details': self.detection_count,
            'config': {
                'confidence_threshold': self.confidence_threshold,
                'parking_duration_threshold': self.parking_duration_threshold
            },
            'emoji': '🚗'
        }


# 便捷函数
def create_vehicle_parking_detector(confidence_threshold=0.5, parking_duration_threshold=30.0):
    """创建车辆违停检测器实例"""
    return VehicleParkingDetector(confidence_threshold, parking_duration_threshold)
