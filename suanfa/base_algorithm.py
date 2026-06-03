"""
算法基类 - 所有视觉识别算法的抽象基类

设计原则：
1. 每个算法有唯一的 name 标识
2. 每个算法关联一个 .engine 文件
3. detect(img) 返回统一结构
4. 算法层不感知模型细节、不依赖 AI 框架

禁止：
- 算法代码中出现 ultralytics / YOLO / torch
- 算法代码中加载 .pt / .onnx 模型
- 算法代码中直接调用模型推理
"""

import cv2
import numpy as np
import json
from abc import ABC, abstractmethod
from typing import List, Dict, Tuple, Optional
from pathlib import Path
from safehat.inference import engine as infer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALGORITHM_CONFIG_PATH = PROJECT_ROOT / 'data' / 'algorithm_config.json'
ALGORITHM_CONFIG_ALIASES = {
    'person_intrusion_detection': 'intrusion_detection',
    'fire_extinguisher_absence': 'fire_extinguisher_absence_detection',
}

class BaseAlgorithm(ABC):
    """
    算法基类 - 所有视觉识别算法必须继承此类
    
    子类必须实现：
    - get_algorithm_name(): 返回算法唯一标识
    - get_engine_path(): 返回 .engine 文件路径
    - detect(): 执行检测逻辑
    - get_status_info(): 返回状态信息
    """
    
    def __init__(self, confidence_threshold: float = 0.5):
        """
        初始化算法基类
        
        Args:
            confidence_threshold: 置信度阈值 (0.1-0.95)
        """
        self.confidence_threshold = max(0.1, min(0.95, confidence_threshold))
        self.is_enabled = False
        self.detection_count = {}
        
        # 获取算法标识和引擎路径（子类实现）
        self.algorithm_name = self.get_algorithm_name()
        self.default_engine_path = self.get_engine_path()
        self.engine_path = self._resolve_engine_path_from_config() or self.default_engine_path
        
        # 加载推理引擎
        self.engine_loaded = self._load_engine()
        self._engine_unavailable_logged = False
    
    # ============================================
    # 抽象方法 - 子类必须实现
    # ============================================
    
    @abstractmethod
    def get_algorithm_name(self) -> str:
        """
        返回算法唯一标识
        
        Returns:
            算法名称（如 'helmet_detection'）
        """
        pass
    
    @abstractmethod
    def get_engine_path(self) -> str:
        """
        返回 TensorRT engine 文件路径
        
        Returns:
            .engine 文件的绝对路径或相对路径
        """
        pass
    
    @abstractmethod
    def detect(self, frame: np.ndarray):
        """
        执行检测（子类必须实现具体业务逻辑）
        
        Args:
            frame: 输入图像帧 (BGR格式)
        
        Returns:
            根据具体算法返回不同格式
            建议返回: (processed_frame, detection_info, frame_detections)
        """
        pass
    
    @abstractmethod
    def get_status_info(self) -> Dict:
        """
        获取算法状态信息
        
        Returns:
            状态字典: {
                'name': str,
                'enabled': bool,
                'count': int,
                'details': dict,
                'emoji': str
            }
        """
        pass
    
    # ============================================
    # 推理接口（子类调用，不可重写）
    # ============================================
    
    def _load_engine(self) -> bool:
        """
        内部方法: 加载推理引擎
        
        自动在模型目录中按优先级搜索可用格式：
          .so > .engine > .rknn > .pt > best.rknn > best.pt
        
        对每个找到的文件调用 infer.load_engine()，首次成功即停止。
        注意: 子类不应重写此方法
        """
        try:
            engine_path = self.engine_path
            model_dir = Path(engine_path).parent
            stem = Path(engine_path).stem

            # 按优先级构建候选文件列表（精确命名优先）
            candidates = [
                model_dir / 'model.so',                  # 通用 per-model so
                model_dir / f'{stem}.so',                 # 同名 .so
                model_dir / f'{stem}.engine',             # TensorRT engine
                model_dir / f'{stem}.rknn',               # RKNN 同名（如 dog.rknn）
                model_dir / f'{stem}.pt',                 # PyTorch 同名
                model_dir / 'best_rk3568.rknn',           # RK3568 导出命名（最常见）
                model_dir / 'best_rk3576.rknn',           # RK3576 导出命名
                model_dir / 'best_rk3588.rknn',           # RK3588 导出命名
                model_dir / 'best.rknn',                  # 通用 RKNN 备选
                model_dir / 'best.pt',                    # PT 最终兜底
            ]

            # 去重（避免同一文件路径被尝试两次）
            seen = set()
            deduped = []
            for c in candidates:
                key = str(c)
                if key not in seen:
                    seen.add(key)
                    deduped.append(c)

            for candidate in deduped:
                if not candidate.exists():
                    continue

                found_path = str(candidate)
                print(f"[{self.algorithm_name}] 发现模型文件: {candidate.name}，尝试加载...")

                success = infer.load_engine(self.algorithm_name, found_path)
                if success:
                    self.loaded_model_path = found_path
                    print(f"[{self.algorithm_name}] ✅ 引擎加载成功: {candidate.name}")
                    return True
                else:
                    print(f"[{self.algorithm_name}] ⚠️ {candidate.name} 加载失败，尝试下一格式")

            # 兜底：扫描目录中所有 .so / .rknn / .pt 文件（不限文件名）
            # 解决模型文件命名不规范的情况（e.g. camera.rknn / obstruction_v2.pt）
            fallback_priority = [
                list(model_dir.glob('*.so')),
                list(model_dir.glob('*.rknn')),
                list(model_dir.glob('*.pt')),
            ]
            for file_list in fallback_priority:
                for candidate in sorted(file_list):               # 按文件名字母序稳定排序
                    key = str(candidate)
                    if key in seen:                               # 已在精确候选中尝试过
                        continue
                    seen.add(key)
                    print(f"[{self.algorithm_name}] 🔍 扫描发现: {candidate.name}，尝试加载...")
                    success = infer.load_engine(self.algorithm_name, str(candidate))
                    if success:
                        self.loaded_model_path = str(candidate)
                        print(f"[{self.algorithm_name}] ✅ 引擎加载成功（扫描兜底）: {candidate.name}")
                        return True
                    else:
                        print(f"[{self.algorithm_name}] ⚠️ {candidate.name} 加载失败，继续扫描")

            print(f"[{self.algorithm_name}] ❌ 未找到可用模型文件")
            print(f"[{self.algorithm_name}] 查找目录: {model_dir}")
            print(f"[{self.algorithm_name}] 支持格式: .so / .engine / .rknn / .pt")
            return False
        except Exception as e:
            print(f"[{self.algorithm_name}] ❌ 引擎加载异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _resolve_engine_path_from_config(self) -> Optional[str]:
        """优先读取 data/algorithm_config.json 中为该算法配置的 model_path。"""
        try:
            if not ALGORITHM_CONFIG_PATH.exists():
                return None

            with open(ALGORITHM_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)

            config_key = ALGORITHM_CONFIG_ALIASES.get(self.algorithm_name, self.algorithm_name)
            algo_config = config.get(config_key, {})
            configured_model_path = (algo_config.get('model_path') or '').strip()
            if not configured_model_path:
                return None

            candidate = Path(configured_model_path)
            if not candidate.is_absolute():
                candidate = PROJECT_ROOT / candidate

            if candidate.exists():
                return str(candidate)

            print(f"[{self.algorithm_name}] 配置中的模型文件不存在: {candidate}")
            return None
        except Exception as e:
            print(f"[{self.algorithm_name}] 读取模型配置失败: {e}")
            return None

    def reload_engine(self, model_path: Optional[str] = None) -> bool:
        """释放当前引擎并按新路径重新加载。"""
        target_path = (model_path or '').strip() if model_path else ''
        if target_path:
            candidate = Path(target_path)
            if not candidate.is_absolute():
                candidate = PROJECT_ROOT / candidate
            self.engine_path = str(candidate)
        else:
            self.engine_path = self._resolve_engine_path_from_config() or self.default_engine_path

        self.release()
        self.engine_loaded = self._load_engine()
        self._engine_unavailable_logged = False
        return self.engine_loaded
    
    def _infer(self, frame: np.ndarray) -> List[Dict]:
        """
        内部方法: 执行推理
        
        子类在 detect() 方法中调用此方法获取原始检测结果
        
        Args:
            frame: 输入图像帧
        
        Returns:
            标准化检测结果: [
                {
                    'class_id': int,      # 类别ID
                    'confidence': float,  # 置信度 [0, 1]
                    'bbox': (x1, y1, x2, y2)  # 边界框坐标
                },
                ...
            ]
        
        注意: 子类不应重写此方法
        """
        if not self.is_enabled:
            return []

        if not getattr(self, 'engine_loaded', False):
            if not getattr(self, '_engine_unavailable_logged', False):
                print(f"[{self.algorithm_name}] ❌ 引擎未加载，跳过推理: {self.engine_path}")
                self._engine_unavailable_logged = True
            return []
        
        try:
            # 调用 infer 模块执行推理
            detections = infer.infer(
                self.algorithm_name, 
                frame, 
                self.confidence_threshold
            )
            return detections
            
        except Exception as e:
            print(f"[{self.algorithm_name}] 推理失败: {e}")
            return []
    
    # ============================================
    # 通用方法（子类可选重写）
    # ============================================
    
    def enable(self):
        """启用算法"""
        self.is_enabled = True
        print(f"[{self.algorithm_name}] 已启用")
    
    def disable(self):
        """禁用算法"""
        self.is_enabled = False
        print(f"[{self.algorithm_name}] 已禁用")
    
    def update_config(self, confidence_threshold: Optional[float] = None, **kwargs):
        """
        更新算法配置
        
        Args:
            confidence_threshold: 置信度阈值
            **kwargs: 其他配置参数（子类可扩展）
        """
        if confidence_threshold is not None:
            self.confidence_threshold = max(0.1, min(0.95, float(confidence_threshold)))
            print(f"[{self.algorithm_name}] 置信度阈值更新为: {self.confidence_threshold}")

        model_path = kwargs.get('model_path')
        if model_path:
            if self.reload_engine(model_path):
                print(f"[{self.algorithm_name}] 已切换到新模型: {self.loaded_model_path}")
            else:
                print(f"[{self.algorithm_name}] 新模型加载失败，当前路径: {model_path}")
    
    def reset_count(self):
        """
        重置检测计数
        
        子类应重写此方法以重置特定的计数器
        """
        self.detection_count = {}
    
    def release(self):
        """释放算法资源"""
        try:
            infer.release_engine(self.algorithm_name)
            print(f"[{self.algorithm_name}] 资源已释放")
        except Exception as e:
            print(f"[{self.algorithm_name}] 资源释放失败: {e}")
    
    # ============================================
    # 辅助方法
    # ============================================
    
    def _validate_frame(self, frame: np.ndarray) -> bool:
        """验证输入图像是否有效"""
        if frame is None:
            print(f"[{self.algorithm_name}] 输入图像为 None")
            return False
        
        if not isinstance(frame, np.ndarray):
            print(f"[{self.algorithm_name}] 输入不是 numpy 数组")
            return False
        
        if len(frame.shape) != 3 or frame.shape[2] != 3:
            print(f"[{self.algorithm_name}] 图像格式错误，期望 HWC(3通道)")
            return False
        
        return True
    
    def _validate_bbox(self, bbox: Tuple[float, float, float, float], 
                      frame_shape: Tuple[int, int]) -> bool:
        """
        验证边界框是否有效
        
        允许坐标轻微超出图像边界（浮点误差），
        只要边界框主体在图像内部且面积有效即可。
        """
        x1, y1, x2, y2 = bbox
        height, width = frame_shape[:2]

        # 框必须与图像有交集（整体不在图像之外）
        if x2 <= 0 or y2 <= 0 or x1 >= width or y1 >= height:
            return False

        # 框必须有正面积
        if x2 <= x1 or y2 <= y1:
            return False

        return True

    def _clamp_bbox(self, bbox: Tuple[float, float, float, float],
                    frame_shape: Tuple[int, int]) -> Tuple[int, int, int, int]:
        """将边界框坐标钳制到图像范围内并转为整数"""
        x1, y1, x2, y2 = bbox
        height, width = frame_shape[:2]
        x1 = max(0, min(int(x1), width - 1))
        y1 = max(0, min(int(y1), height - 1))
        x2 = max(0, min(int(x2), width))
        y2 = max(0, min(int(y2), height))
        return x1, y1, x2, y2
    
    def __repr__(self):
        """字符串表示"""
        return (f"<{self.__class__.__name__} "
                f"name='{self.algorithm_name}' "
                f"enabled={self.is_enabled} "
                f"conf={self.confidence_threshold:.2f}>")


# ============================================
# 使用示例（注释）
# ============================================

"""
使用示例：

from suanfa.base_algorithm import BaseAlgorithm

class HelmetDetector(BaseAlgorithm):
    def __init__(self, confidence_threshold=0.5):
        super().__init__(confidence_threshold)
        
        # 算法特定的配置
        self.class_config = {
            0: {"name": "安全帽", "color": (0, 255, 0)},
            2: {"name": "未戴安全帽", "color": (0, 0, 255)},
        }
        self.detection_count = {"helmet": 0, "no_helmet": 0}
    
    def get_algorithm_name(self) -> str:
        return "helmet_detection"
    
    def get_engine_path(self) -> str:
        return "models/safehat/helmet.engine"
    
    def detect(self, frame):
        if not self.is_enabled:
            return frame, [], {}
        
        # 1. 调用基类推理方法
        detections = self._infer(frame)
        
        # 2. 处理业务逻辑
        processed_frame = frame.copy()
        detection_info = []
        frame_detections = {"helmet": 0, "no_helmet": 0}
        
        for det in detections:
            class_id = det['class_id']
            confidence = det['confidence']
            bbox = det['bbox']
            
            # 业务逻辑: 绘图、计数、告警等
            if class_id == 0:
                frame_detections["helmet"] += 1
                # 绘制检测框...
            elif class_id == 2:
                frame_detections["no_helmet"] += 1
                # 绘制告警框...
        
        return processed_frame, detection_info, frame_detections
    
    def get_status_info(self):
        return {
            'name': "安全帽检测",
            'enabled': self.is_enabled,
            'count': sum(self.detection_count.values()),
            'details': self.detection_count,
            'emoji': '🪖'
        }
"""



