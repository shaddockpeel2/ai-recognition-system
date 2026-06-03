"""
疲劳驾驶检测模块 - 基于 BaseAlgorithm
负责检测驾驶员的疲劳状态

【简化的检测逻辑】
1. 直接检测闭眼(close_eyes)和哈欠(yawn)状态
2. 触发条件（满足任一即触发）：
   - 闭眼 ≥ 2帧（2秒窗口内）
   - 或 哈欠 ≥ 1次
   - 或 连续2帧检测到闭眼
3. 疲劳评分：0.5-1.0（至少0.5表示检测到疲劳）

【优化说明】
- 降低置信度阈值至0.4，提高检测灵敏度
- 缩短时间窗口至2秒，更快响应
- 移除必须先检测人脸的限制
- 加粗检测框和标签，提高可见性
- 醒目的告警提示界面
"""
import cv2
import numpy as np
from .base_algorithm import BaseAlgorithm
from pathlib import Path
import time
from collections import deque
from typing import Dict, List, Tuple


class FatigueDrivingDetector(BaseAlgorithm):
    """疲劳驾驶检测器 - 继承 BaseAlgorithm"""
    
    def __init__(self, confidence_threshold: float = 0.4, 
                 close_eyes_threshold: int = 2,
                 time_window: float = 2.0):
        """
        初始化疲劳驾驶检测器
        
        Args:
            confidence_threshold: 置信度阈值（0.1-0.95，默认0.4，降低以提高检测灵敏度）
            close_eyes_threshold: 闭眼帧数阈值（默认2帧，降低以更快触发）
            time_window: 时间窗口（秒，默认2秒，缩短以更快响应）
        """
        super().__init__(confidence_threshold)
        
        # 类别ID配置 - 根据模型训练结果
        self.face_class_id = 0  # 人脸
        self.close_eyes_class_id = 1  # 闭眼
        self.yawn_class_id = 2  # 哈欠
        
        self.class_config = {
            0: {"name": "FACE", "color": (0, 255, 0), "status": "正常", "level": "low"},        # 绿色 - 明亮
            1: {"name": "CLOSE_EYES", "color": (0, 140, 255), "status": "疲劳", "level": "medium"},  # 橙色
            2: {"name": "YAWN", "color": (0, 0, 255), "status": "疲劳", "level": "high"}        # 红色
        }
        
        # 检测计数
        self.detection_count = {
            "face": 0,
            "close_eyes": 0,
            "yawn": 0,
            "fatigue": 0
        }
        
        # 疲劳检测参数（降低阈值，提高灵敏度）
        self.close_eyes_threshold = close_eyes_threshold
        self.time_window = time_window
        
        # 时间窗口内的状态记录
        # 使用 deque 存储 (timestamp, event_type) 元组
        self.event_history = deque(maxlen=100)  # 保留最近100个事件
        
        # 连续检测计数器（简化逻辑）
        self.continuous_close_eyes_count = 0
        self.continuous_yawn_count = 0
        self.last_detection_time = 0
    
    def get_algorithm_name(self) -> str:
        """返回算法唯一标识"""
        return "fatigue_driving_detection"
    
    def get_engine_path(self) -> str:
        """返回 TensorRT engine 文件路径"""
        engine_path = Path(__file__).parent.parent / "models" / "fatigue_driving" / "fatigue_driving.engine"
        return str(engine_path)
    
    def _clean_old_events(self, current_time: float):
        """清理时间窗口外的旧事件"""
        cutoff_time = current_time - self.time_window
        while self.event_history and self.event_history[0][0] < cutoff_time:
            self.event_history.popleft()
    
    def _count_events_in_window(self, event_type: str) -> int:
        """统计时间窗口内指定类型的事件数量"""
        return sum(1 for _, etype in self.event_history if etype == event_type)
    
    def _calculate_fatigue_score(self, close_eyes_count: int, yawn_count: int) -> float:
        """
        简化的疲劳评分计算
        评分 = 0.7 * 闭眼时长 + 0.3 * 哈欠次数
        
        Args:
            close_eyes_count: 闭眼帧数
            yawn_count: 哈欠次数
        
        Returns:
            float: 疲劳评分 (0.5-1.0)，至少0.5表示检测到疲劳
        """
        # 闭眼时长归一化 (降低最大值，让分数更容易达到高位)
        # 2帧闭眼 = 0.2, 5帧 = 0.5, 10帧 = 1.0
        close_eyes_score = min(close_eyes_count / 10.0, 1.0)
        
        # 哈欠次数归一化 (1次哈欠就是0.5，2次=1.0)
        yawn_score = min(yawn_count / 2.0, 1.0)
        
        # 加权求和（至少0.5起步，表示检测到了疲劳）
        score = 0.5 + (0.7 * close_eyes_score + 0.3 * yawn_score) * 0.5
        
        return min(score, 1.0)
    
    def detect(self, frame: np.ndarray):
        """
        在图像帧中检测疲劳驾驶
        
        Args:
            frame: 输入图像帧 (BGR格式)
        
        Returns:
            tuple: (处理后的帧, 检测信息列表, 检测结果详情, 疲劳告警列表)
        """
        if not self.is_enabled:
            return frame, [], {"face": 0, "close_eyes": 0, "yawn": 0, "fatigue": 0}, []
        
        if not self._validate_frame(frame):
            return frame, [], {"face": 0, "close_eyes": 0, "yawn": 0, "fatigue": 0}, []
        
        try:
            current_time = time.time()
            
            # 清理旧事件
            self._clean_old_events(current_time)
            
            # 执行推理
            detections = self._infer(frame)
            detection_info = []
            frame_detections = {"face": 0, "close_eyes": 0, "yawn": 0, "fatigue": 0}
            processed_frame = frame.copy()
            fatigue_alerts = []
            
            # 检测各类状态
            face_detected = False
            close_eyes_detected = False
            yawn_detected = False
            
            for det in detections:
                class_id = det['class_id']
                confidence = det['confidence']
                bbox = det['bbox']
                
                if class_id not in self.class_config:
                    continue
                
                if not self._validate_bbox(bbox, frame.shape):
                    continue
                
                x1, y1, x2, y2 = map(int, bbox)
                config = self.class_config[class_id]
                color = config["color"]
                
                # 绘制检测框（加粗边框，提高可见性）
                thickness = 3 if class_id != self.face_class_id else 2
                cv2.rectangle(processed_frame, (x1, y1), (x2, y2), color, thickness)
                
                # 绘制标签（放大字体，增加背景）
                label = f"{config['name']} {confidence:.2f}"
                font_scale = 0.7 if class_id != self.face_class_id else 0.6
                label_size, baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 2)
                
                # 标签背景
                cv2.rectangle(processed_frame, 
                            (x1, y1 - label_size[1] - baseline - 4),
                            (x1 + label_size[0] + 4, y1),
                            color, -1)
                # 标签文字
                cv2.putText(processed_frame, label, (x1 + 2, y1 - baseline - 2),
                          cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 2)
                
                # 更新检测计数和状态
                if class_id == self.face_class_id:
                    face_detected = True
                    self.detection_count["face"] += 1
                    frame_detections["face"] += 1
                    self.last_detection_time = current_time
                    
                elif class_id == self.close_eyes_class_id:
                    close_eyes_detected = True
                    self.detection_count["close_eyes"] += 1
                    frame_detections["close_eyes"] += 1
                    # 记录闭眼事件
                    self.event_history.append((current_time, "close_eyes"))
                    self.continuous_close_eyes_count += 1
                    
                elif class_id == self.yawn_class_id:
                    yawn_detected = True
                    self.detection_count["yawn"] += 1
                    frame_detections["yawn"] += 1
                    # 记录哈欠事件
                    self.event_history.append((current_time, "yawn"))
                    self.continuous_yawn_count += 1
                
                detection_info.append({
                    "class": config["name"],
                    "confidence": confidence,
                    "bbox": (x1, y1, x2, y2),
                    "status": config["status"]
                })
            
            # 简化的疲劳检测逻辑 - 不需要先检测到人脸
            # 只要检测到闭眼或哈欠就判断疲劳
            is_fatigue = False
            fatigue_reason = []
            fatigue_score = 0.0
            
            # 统计时间窗口内的事件
            close_eyes_count = self._count_events_in_window("close_eyes")
            yawn_count = self._count_events_in_window("yawn")
            
            # 简化的触发条件：
            # 1. 检测到闭眼 >= 阈值帧（2帧）
            # 2. 或者检测到哈欠 >= 1次
            # 3. 或者连续检测到闭眼/哈欠
            if close_eyes_count >= self.close_eyes_threshold:
                is_fatigue = True
                fatigue_reason.append(f"CLOSE_EYES:{close_eyes_count}f")
                fatigue_score = 0.7
            
            if yawn_count >= 1:
                is_fatigue = True
                fatigue_reason.append(f"YAWN:{yawn_count}x")
                fatigue_score = max(fatigue_score, 0.8)
            
            if close_eyes_detected and self.continuous_close_eyes_count >= 2:
                is_fatigue = True
                if "CLOSE_EYES" not in " ".join(fatigue_reason):
                    fatigue_reason.append("CONSEC_CLOSE_EYES")
                fatigue_score = max(fatigue_score, 0.75)
            
            # 重置连续计数器（如果当前帧没有检测到）
            if not close_eyes_detected:
                self.continuous_close_eyes_count = 0
            if not yawn_detected:
                self.continuous_yawn_count = 0
            
            # 如果触发疲劳告警
            if is_fatigue:
                # 如果没有通过其他方式计算分数，使用简化的评分
                if fatigue_score == 0.0:
                    fatigue_score = self._calculate_fatigue_score(close_eyes_count, yawn_count)
                
                self.detection_count["fatigue"] += 1
                frame_detections["fatigue"] += 1
                
                # 在画面上绘制醒目的疲劳警告
                h, w = processed_frame.shape[:2]
                warning_text = "!!! FATIGUE ALERT !!!"
                score_text = f"Fatigue Level: {int(fatigue_score * 100)}%"
                reason_text = " | ".join(fatigue_reason)
                
                # 绘制半透明红色背景（更大的区域）
                overlay = processed_frame.copy()
                cv2.rectangle(overlay, (5, 5), (w-5, 140), (0, 0, 255), -1)
                cv2.addWeighted(overlay, 0.4, processed_frame, 0.6, 0, processed_frame)
                
                # 绘制警告边框
                cv2.rectangle(processed_frame, (5, 5), (w-5, 140), (0, 0, 255), 3)
                
                # 绘制警告文字（更大更醒目）
                cv2.putText(processed_frame, warning_text, (15, 50),
                          cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 4)
                cv2.putText(processed_frame, score_text, (15, 90),
                          cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)
                cv2.putText(processed_frame, reason_text, (15, 125),
                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                
                # 生成告警
                fatigue_alerts.append({
                    "type": "fatigue_driving_violation",
                    "message": f"检测到疲劳驾驶（危险等级: {int(fatigue_score * 100)}%）",
                    "confidence": fatigue_score,
                    "location": "驾驶位",
                    "details": {
                        "close_eyes_count": close_eyes_count,
                        "yawn_count": yawn_count,
                        "continuous_close_eyes": self.continuous_close_eyes_count,
                        "fatigue_score": fatigue_score,
                        "reason": reason_text
                    }
                })
                
                print(f"🚨 【疲劳驾驶告警】危险等级:{int(fatigue_score * 100)}%, 原因:{reason_text}")
            
            # 在画面右上角显示实时统计信息（更醒目）
            h, w = processed_frame.shape[:2]
            stats_x = w - 280
            stats_y = 30
            
            # 绘制半透明背景
            if frame_detections["face"] > 0 or frame_detections["close_eyes"] > 0 or frame_detections["yawn"] > 0:
                overlay = processed_frame.copy()
                cv2.rectangle(overlay, (stats_x - 10, 10), (w - 10, stats_y + 80), (50, 50, 50), -1)
                cv2.addWeighted(overlay, 0.6, processed_frame, 0.4, 0, processed_frame)
            
            # 显示各类检测统计
            if frame_detections["face"] > 0:
                cv2.putText(processed_frame, f"Face: {frame_detections['face']}", 
                          (stats_x, stats_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                stats_y += 30
            if frame_detections["close_eyes"] > 0:
                cv2.putText(processed_frame, f"Close Eyes: {frame_detections['close_eyes']}", 
                          (stats_x, stats_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 140, 255), 2)
                stats_y += 30
            if frame_detections["yawn"] > 0:
                cv2.putText(processed_frame, f"Yawn: {frame_detections['yawn']}", 
                          (stats_x, stats_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
            return processed_frame, detection_info, frame_detections, fatigue_alerts
        
        except Exception as e:
            print(f"[{self.algorithm_name}] 检测处理异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return frame, [], {"face": 0, "close_eyes": 0, "yawn": 0, "fatigue": 0}, []
    
    def get_status_info(self) -> Dict:
        """获取检测器状态信息"""
        return {
            'name': "疲劳驾驶检测",
            'enabled': self.is_enabled,
            'count': self.detection_count["fatigue"],
            'details': self.detection_count,
            'config': {
                'close_eyes_threshold': self.close_eyes_threshold,
                'time_window': self.time_window
            },
            'emoji': '😴'
        }
    
    def reset_count(self):
        """重置检测计数"""
        self.detection_count = {
            "face": 0,
            "close_eyes": 0,
            "yawn": 0,
            "fatigue": 0
        }
        self.event_history.clear()
        self.continuous_close_eyes_count = 0
        self.continuous_yawn_count = 0
        self.last_detection_time = 0
    
    def update_config(self, confidence_threshold: float = None, 
                     close_eyes_threshold: int = None,
                     time_window: float = None, **kwargs):
        """
        更新检测器配置
        
        Args:
            confidence_threshold: 置信度阈值
            close_eyes_threshold: 闭眼帧数阈值
            time_window: 时间窗口（秒）
            **kwargs: 其他配置参数
        """
        super().update_config(confidence_threshold, **kwargs)
        
        if close_eyes_threshold is not None:
            self.close_eyes_threshold = max(1, min(30, int(close_eyes_threshold)))
            print(f"[{self.algorithm_name}] 闭眼帧数阈值已更新为: {self.close_eyes_threshold}")
        
        if time_window is not None:
            self.time_window = max(1.0, min(10.0, float(time_window)))
            print(f"[{self.algorithm_name}] 时间窗口已更新为: {self.time_window}秒")

