#!/usr/bin/env python3
"""
RKNN 推理引擎（Python 实现）
适用于瑞芯微 RK3586/RK3588 平台

使用方法:
    from safehat.inference.rknn_backend import load_engine, infer, release_engine
    
    # 加载模型
    handle = load_engine('models/safehat/best.rknn')
    
    # 推理
    results = infer(handle, image, conf_threshold=0.5)
    
    # 释放
    release_engine(handle)
"""

import numpy as np
import cv2
from pathlib import Path
from typing import List, Dict, Tuple, Optional


class RKNNInferEngine:
    """RKNN 推理引擎"""
    
    def __init__(self, model_path: str, target: str = 'rk3588', device_id: Optional[int] = None):
        """
        初始化 RKNN 推理引擎
        
        Args:
            model_path: .rknn 模型文件路径
            target: 目标平台（rk3588/rk3566/rk3568/rk3576）
            device_id: 设备ID（多NPU时使用，None表示自动选择）
        """
        try:
            from rknnlite.api import RKNNLite
        except ImportError:
            raise ImportError(
                "RKNN Runtime 未安装。\n"
                "板载安装方法:\n"
                "  sudo apt-get install -y librknnrt\n"
                "  pip3 install rknn-toolkit-lite2"
            )
        
        self.model_path = Path(model_path)
        self.target = target
        self.device_id = device_id
        
        if not self.model_path.exists():
            raise FileNotFoundError(f"RKNN 模型文件不存在: {self.model_path}")
        
        # 创建 RKNN 对象
        self.rknn = RKNNLite()
        
        # 加载模型
        print(f"[RKNN] 📥 加载模型: {self.model_path}")
        ret = self.rknn.load_rknn(str(self.model_path))
        if ret != 0:
            raise RuntimeError(f"RKNN 模型加载失败，错误码: {ret}")
        
        # 初始化运行时环境
        print(f"[RKNN] 🔧 初始化 Runtime (target={target}, device_id={device_id})")
        ret = self.rknn.init_runtime(target_platform=target, device_id=device_id)
        if ret != 0:
            raise RuntimeError(f"RKNN Runtime 初始化失败，错误码: {ret}")
        
        print(f"[RKNN] ✅ 引擎初始化成功")
        
        # 获取模型输入输出信息
        self.input_attrs = self.rknn.get_input_attrs()
        self.output_attrs = self.rknn.get_output_attrs()
        
        # 解析输入尺寸
        if self.input_attrs and len(self.input_attrs) > 0:
            input_shape = self.input_attrs[0]['shape']  # [1, 3, 640, 640] 或 [1, 640, 640, 3]
            # RKNN 可能使用 NHWC 或 NCHW 格式
            if len(input_shape) == 4:
                if input_shape[1] == 3:  # NCHW
                    self.input_h, self.input_w = input_shape[2], input_shape[3]
                else:  # NHWC
                    self.input_h, self.input_w = input_shape[1], input_shape[2]
            else:
                self.input_h, self.input_w = 640, 640  # 默认
            
            print(f"[RKNN] 📊 输入尺寸: {self.input_w}x{self.input_h}")
        else:
            self.input_h, self.input_w = 640, 640
            print(f"[RKNN] ⚠️ 无法获取输入尺寸，使用默认: {self.input_w}x{self.input_h}")
    
    def preprocess(self, image: np.ndarray) -> Tuple[np.ndarray, float, Tuple[int, int]]:
        """
        预处理图像
        
        Args:
            image: BGR 图像 (H, W, 3)
        
        Returns:
            (preprocessed_image, scale, (pad_w, pad_h))
        """
        img_h, img_w = image.shape[:2]
        
        # 计算缩放比例（保持宽高比）
        scale = min(self.input_h / img_h, self.input_w / img_w)
        new_h, new_w = int(img_h * scale), int(img_w * scale)
        
        # Resize
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        
        # Padding（填充到目标尺寸，使用灰色填充）
        padded = np.full((self.input_h, self.input_w, 3), 114, dtype=np.uint8)
        pad_h = (self.input_h - new_h) // 2
        pad_w = (self.input_w - new_w) // 2
        padded[pad_h:pad_h+new_h, pad_w:pad_w+new_w] = resized
        
        # 转换为 RGB（RKNN 通常期望 RGB）
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        
        return rgb, scale, (pad_w, pad_h)
    
    def infer(self, image: np.ndarray, conf_threshold: float = 0.5) -> List[Dict]:
        """
        执行推理
        
        Args:
            image: BGR 图像 (H, W, 3)
            conf_threshold: 置信度阈值
        
        Returns:
            检测结果列表 [{'class_id': int, 'confidence': float, 'bbox': [x1,y1,x2,y2]}, ...]
        """
        # 预处理
        input_image, scale, (pad_w, pad_h) = self.preprocess(image)
        
        # 推理
        outputs = self.rknn.inference(inputs=[input_image])
        
        if outputs is None or len(outputs) == 0:
            return []
        
        # 后处理
        results = self.postprocess(outputs, scale, (pad_w, pad_h), conf_threshold)
        
        return results
    
    def postprocess(self, outputs: List[np.ndarray], scale: float, 
                    padding: Tuple[int, int], conf_threshold: float) -> List[Dict]:
        """
        后处理 RKNN 输出
        
        Args:
            outputs: RKNN 推理输出
            scale: 缩放比例
            padding: 填充量 (pad_w, pad_h)
            conf_threshold: 置信度阈值
        
        Returns:
            检测结果列表
        """
        pad_w, pad_h = padding
        
        # YOLOv8 输出格式: [1, 84, 8400] 或 [1, 8400, 84]
        output = outputs[0]
        
        # 确保形状为 [8400, 84]
        if len(output.shape) == 3:
            output = output[0]  # 去掉 batch 维度
        
        if output.shape[0] == 84:
            output = output.T  # 转置为 [8400, 84]
        
        # 提取 bbox 和 scores
        boxes = output[:, :4]        # [8400, 4] - (x_center, y_center, w, h)
        scores = output[:, 4:]       # [8400, 80] - 类别置信度
        
        # 获取最大置信度和类别
        class_ids = np.argmax(scores, axis=1)
        confidences = np.max(scores, axis=1)
        
        # 过滤低置信度
        mask = confidences > conf_threshold
        if not np.any(mask):
            return []
        
        boxes = boxes[mask]
        class_ids = class_ids[mask]
        confidences = confidences[mask]
        
        # 转换坐标格式（中心点+宽高 → 左上右下）
        x_center, y_center, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        
        # 去除 padding 并缩放回原图
        x1 = ((x_center - w / 2) - pad_w) / scale
        y1 = ((y_center - h / 2) - pad_h) / scale
        x2 = ((x_center + w / 2) - pad_w) / scale
        y2 = ((y_center + h / 2) - pad_h) / scale
        
        # 转换为列表（NMS 需要）
        boxes_xyxy = [[x1[i], y1[i], x2[i], y2[i]] for i in range(len(x1))]
        
        # NMS（非极大值抑制）
        try:
            indices = cv2.dnn.NMSBoxes(
                boxes_xyxy,
                confidences.tolist(),
                conf_threshold,
                0.45  # IOU 阈值
            )
        except Exception as e:
            print(f"[RKNN] ⚠️ NMS 失败: {e}")
            indices = list(range(len(boxes_xyxy)))
        
        # 构造结果
        results = []
        for i in indices:
            # 确保坐标非负
            x1_val = max(0, int(boxes_xyxy[i][0]))
            y1_val = max(0, int(boxes_xyxy[i][1]))
            x2_val = max(0, int(boxes_xyxy[i][2]))
            y2_val = max(0, int(boxes_xyxy[i][3]))
            
            results.append({
                'class_id': int(class_ids[i]),
                'confidence': float(confidences[i]),
                'bbox': [x1_val, y1_val, x2_val, y2_val]
            })
        
        return results
    
    def release(self):
        """释放资源"""
        if hasattr(self, 'rknn') and self.rknn:
            self.rknn.release()
            print("[RKNN] ✅ 引擎已释放")


# ==================== 全局接口（兼容现有代码） ====================

_engines = {}  # 模型缓存

def load_engine(model_path: str, target: str = 'rk3588', device_id: Optional[int] = None) -> str:
    """
    加载 RKNN 引擎
    
    Args:
        model_path: .rknn 模型路径
        target: 目标平台（rk3588/rk3566/rk3568/rk3576）
        device_id: 设备ID（多NPU时使用）
    
    Returns:
        引擎句柄（字符串）
    """
    model_path = str(model_path)
    
    # 检查缓存
    if model_path in _engines:
        print(f"[RKNN] ♻️ 复用已加载的引擎: {Path(model_path).name}")
        return model_path
    
    # 创建引擎
    try:
        engine = RKNNInferEngine(model_path, target, device_id)
        _engines[model_path] = engine
        return model_path
    except Exception as e:
        print(f"[RKNN] ❌ 引擎加载失败: {e}")
        raise


def infer(handle: str, image: np.ndarray, conf_threshold: float = 0.5) -> List[Dict]:
    """
    执行推理
    
    Args:
        handle: 引擎句柄
        image: BGR 图像 (H, W, 3)
        conf_threshold: 置信度阈值
    
    Returns:
        检测结果列表 [{'class_id': int, 'confidence': float, 'bbox': [x1,y1,x2,y2]}, ...]
    """
    if handle not in _engines:
        raise ValueError(f"无效的引擎句柄: {handle}")
    
    engine = _engines[handle]
    return engine.infer(image, conf_threshold)


def release_engine(handle: str):
    """
    释放引擎
    
    Args:
        handle: 引擎句柄
    """
    if handle in _engines:
        _engines[handle].release()
        del _engines[handle]


def release_all():
    """释放所有引擎"""
    for handle in list(_engines.keys()):
        release_engine(handle)


# ==================== 平台检测 ====================

def is_rockchip_platform() -> bool:
    """
    检测是否为瑞芯微平台
    
    Returns:
        True 如果是瑞芯微平台
    """
    try:
        with open('/proc/device-tree/compatible', 'r') as f:
            content = f.read().lower()
            return 'rockchip' in content
    except:
        return False


def get_rockchip_platform() -> Optional[str]:
    """
    获取瑞芯微平台型号
    
    Returns:
        平台型号（如 'rk3588'）或 None
    """
    try:
        with open('/proc/device-tree/compatible', 'r') as f:
            content = f.read().lower()
            if 'rk3588' in content:
                return 'rk3588'
            elif 'rk3576' in content:
                return 'rk3576'
            elif 'rk3568' in content:
                return 'rk3568'
            elif 'rk3566' in content:
                return 'rk3566'
    except:
        pass
    return None


# ==================== 测试代码 ====================

if __name__ == '__main__':
    import sys
    
    # 检查平台
    if is_rockchip_platform():
        platform = get_rockchip_platform()
        print(f"✅ 检测到瑞芯微平台: {platform}")
    else:
        print("⚠️ 非瑞芯微平台，可能无法运行")
    
    # 测试推理
    if len(sys.argv) < 3:
        print("使用方法: python -m safehat.inference.rknn_backend <model.rknn> <image.jpg>")
        sys.exit(1)
    
    model_path = sys.argv[1]
    image_path = sys.argv[2]
    
    # 加载模型
    handle = load_engine(model_path)
    
    # 读取图像
    image = cv2.imread(image_path)
    if image is None:
        print(f"❌ 无法读取图像: {image_path}")
        sys.exit(1)
    
    print(f"📷 图像尺寸: {image.shape[1]}x{image.shape[0]}")
    
    # 推理
    import time
    start = time.time()
    results = infer(handle, image, conf_threshold=0.5)
    elapsed = time.time() - start
    
    print(f"\n⏱️ 推理耗时: {elapsed*1000:.1f} ms")
    print(f"📊 检测到 {len(results)} 个目标\n")
    
    # 显示结果
    for i, det in enumerate(results, 1):
        class_id = det['class_id']
        confidence = det['confidence']
        x1, y1, x2, y2 = det['bbox']
        
        print(f"[{i}] 类别={class_id}, 置信度={confidence:.2f}, 位置=[{x1},{y1},{x2},{y2}]")
        
        # 绘制边界框
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(image, f'{class_id}: {confidence:.2f}', (x1, y1-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    
    # 保存结果
    output_path = 'result_rknn.jpg'
    cv2.imwrite(output_path, image)
    print(f"\n✅ 结果已保存: {output_path}")
    
    # 释放资源
    release_engine(handle)
