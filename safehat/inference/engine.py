"""
推理接口层 - Python 唯一允许调用模型推理的模块

职责：
1. 自动检测运行平台（瑞芯微 / NVIDIA / CPU）
2. 加载对应的推理引擎（RKNN / TensorRT / ultralytics）
3. 管理多个模型实例的生命周期
4. 提供统一的推理接口
5. 不向上层暴露任何模型细节

支持的格式（按加载优先级排序）：
1. .so  - 每模型独立共享库（最高优先级，跨平台通用）
2. .rknn - RKNN 格式（瑞芯微平台专用）
3. .engine - TensorRT 序列化引擎（NVIDIA 平台，通过 libinfer.so 加载）
4. .pt  - ultralytics YOLO（仅用于开发/测试，不允许在生产环境使用）

.so 模型文件 C 接口规范（模型 .so 必须导出以下函数）：
    void* model_init();
        初始化模型，返回模型句柄。失败返回 NULL。

    int model_infer(void* handle,
                    unsigned char* bgr_data, int width, int height,
                    float conf_threshold,
                    int* out_class_ids,     // 输出：类别ID数组（调用方分配）
                    float* out_confidences, // 输出：置信度数组（调用方分配）
                    float* out_boxes,       // 输出：坐标数组 x1,y1,x2,y2 顺序（调用方分配）
                    int max_detections);    // 输入：数组最大容量
        返回实际检测数量。失败返回 -1。

    void model_release(void* handle);
        释放模型资源。

支持的平台：
- 瑞芯微 RK3588/RK3566/RK3568/RK3576: .so > .rknn > .pt
- NVIDIA GPU: .so > .engine > .pt
- CPU: .so > .pt

禁止：
- 算法层直接 import 此模块中的 InferenceEngine 类
- 算法层直接操作 ctypes / so 库
- 算法层感知模型文件路径
"""
import ctypes
import logging
import os
import sys
import traceback
import threading
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except Exception:
        pass

# 修复被污染的 logging 等级映射
logging.addLevelName(logging.DEBUG, "DEBUG")
logging.addLevelName(logging.INFO, "INFO")
logging.addLevelName(logging.WARNING, "WARNING")
logging.addLevelName(logging.ERROR, "ERROR")
logging.addLevelName(logging.CRITICAL, "CRITICAL")

logging._nameToLevel["DEBUG"] = logging.DEBUG
logging._nameToLevel["INFO"] = logging.INFO
logging._nameToLevel["WARNING"] = logging.WARNING
logging._nameToLevel["ERROR"] = logging.ERROR
logging._nameToLevel["CRITICAL"] = logging.CRITICAL

logging._levelToName[logging.DEBUG] = "DEBUG"
logging._levelToName[logging.INFO] = "INFO"
logging._levelToName[logging.WARNING] = "WARNING"
logging._levelToName[logging.ERROR] = "ERROR"
logging._levelToName[logging.CRITICAL] = "CRITICAL"

INFER_VERBOSE_DEBUG = os.getenv("SAFEHAT_INFER_VERBOSE_DEBUG", "").lower() in {"1", "true", "yes"}

# 如果 torch 之前导入失败过，先清掉半初始化模块
if "torch" in sys.modules:
    del sys.modules["torch"]

# ==================== 平台检测 ====================

def detect_platform() -> str:
    """
    检测运行平台

    Returns:
        'rockchip': 瑞芯微平台
        'nvidia': NVIDIA GPU 平台
        'cpu': CPU 平台
    """
    # 检测瑞芯微平台
    try:
        with open('/proc/device-tree/compatible', 'r') as f:
            if 'rockchip' in f.read().lower():
                return 'rockchip'
    except:
        pass

    # 检测 NVIDIA GPU
    try:
        import torch
        if torch.cuda.is_available():
            return 'nvidia'
    except:
        pass

    # 默认 CPU
    return 'cpu'


def get_rockchip_target() -> str:
    """
    获取瑞芯微平台型号

    Returns:
        'rk3588' / 'rk3576' / 'rk3568' / 'rk3566'
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
    return 'rk3588'  # 默认


class InferenceEngine:
    """
    推理引擎 - 单例模式

    自动检测平台并加载对应的推理引擎：
    - 瑞芯微: RKNN Runtime
    - NVIDIA: TensorRT (libinfer.so)
    - CPU: ultralytics (临时方案)
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """单例模式 - 确保全局只有一个推理引擎"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化推理引擎"""
        if hasattr(self, '_initialized'):
            return

        self._initialized = True
        self.lib = None
        self.models = {}       # {engine_id: model_handle / YOLO / rknn / dict}
        self.model_types = {}  # {engine_id: 'ultralytics'/'rknn'/'tensorrt'/'model_so'}
        self._model_lock = threading.Lock()
        self._infer_lock = threading.Lock()

        # 检测平台
        self.platform = detect_platform()
        print(f"[InferEngine] 🔍 检测到平台: {self.platform}")

        if self.platform == 'rockchip':
            self.target = get_rockchip_target()
            print(f"[InferEngine] 🎯 瑞芯微型号: {self.target}")

        # 加载推理引擎
        self._load_engine()

        print("[InferEngine] ✅ 推理引擎初始化完成")

    def _load_engine(self):
        """加载推理引擎（根据平台选择）"""
        if self.platform == 'rockchip':
            self._load_rknn_engine()
        elif self.platform == 'nvidia':
            self._load_tensorrt_engine()
        else:
            self._load_fallback_engine()

    def _load_rknn_engine(self):
        """加载 RKNN 推理引擎（瑞芯微平台）"""
        try:
            # 检查 RKNN Runtime 是否可用
            from rknnlite.api import RKNNLite
            print("[InferEngine] ✅ RKNN Runtime 可用")
            self.engine_type = 'rknn'
            self.lib = 'rknn'  # 标记使用 RKNN

        except ImportError:
            print("[InferEngine] ❌ RKNN Runtime 未安装")
            print("[InferEngine] 安装方法:")
            print("  sudo apt-get install -y librknnrt")
            print("  pip3 install rknn-toolkit-lite2")
            print("[InferEngine] 将使用临时方案（ultralytics）")
            self._load_fallback_engine()

    def _load_tensorrt_engine(self):
        """加载 TensorRT 推理引擎（NVIDIA 平台）"""
        try:
            # 尝试不同的路径查找 libinfer.so
            possible_paths = [
                Path(__file__).parent / 'libinfer.so',           # 项目根目录
                Path(__file__).parent / 'lib' / 'libinfer.so',   # lib子目录
                Path('/usr/local/lib/libinfer.so'),              # 系统库路径
            ]

            lib_path = None
            for path in possible_paths:
                if path.exists():
                    lib_path = str(path)
                    break

            if lib_path is None:
                print("[InferEngine] ⚠️ libinfer.so 未找到，将使用临时方案（ultralytics）")
                print("[InferEngine] 📍 预期路径: ./libinfer.so 或 ./lib/libinfer.so")
                self._load_fallback_engine()
                return

            # 加载动态库
            self.lib = ctypes.CDLL(lib_path)
            print(f"[InferEngine] ✅ libinfer.so 加载成功: {lib_path}")
            self.engine_type = 'tensorrt'

            # 设置 C++ 函数接口
            self._setup_ctypes_interface()

        except Exception as e:
            print(f"[InferEngine] ❌ libinfer.so 加载失败: {e}")
            print("[InferEngine] 将使用临时方案（ultralytics）")
            self._load_fallback_engine()

    def _load_fallback_engine(self):
        """加载临时方案（ultralytics）"""
        try:
            from ultralytics import YOLO
            print("[InferEngine] ⚠️ 使用临时方案（ultralytics）")
            self.lib = None
            self.engine_type = 'ultralytics'
        except ImportError:
            print("[InferEngine] ❌ ultralytics 未安装")
            raise RuntimeError("无可用的推理引擎")

    def _setup_ctypes_interface(self):
        """
        设置 C++ 接口的参数和返回值类型

        假设 libinfer.so 提供以下接口：
        - void* load_engine(const char* engine_path);
        - DetectionResult* infer(void* handle, uint8_t* image_data, int width, int height, float conf_threshold);
        - void free_results(DetectionResult* results);
        - void release_engine(void* handle);
        """
        if self.lib is None:
            return

        try:
            # load_engine: 加载 TensorRT engine 文件
            # 参数: engine_path (char*)
            # 返回: model_handle (void*)
            self.lib.load_engine.argtypes = [ctypes.c_char_p]
            self.lib.load_engine.restype = ctypes.c_void_p

            # infer: 执行推理
            # 参数: model_handle, image_data, width, height, conf_threshold
            # 返回: DetectionResult* (包含检测结果的指针)
            self.lib.infer.argtypes = [
                ctypes.c_void_p,  # model_handle
                ctypes.POINTER(ctypes.c_uint8),  # image_data
                ctypes.c_int,     # width
                ctypes.c_int,     # height
                ctypes.c_float    # conf_threshold
            ]
            self.lib.infer.restype = ctypes.c_void_p

            # get_num_detections: 获取检测结果数量
            self.lib.get_num_detections.argtypes = [ctypes.c_void_p]
            self.lib.get_num_detections.restype = ctypes.c_int

            # get_detection: 获取单个检测结果
            # 参数: results, index, out_class_id, out_confidence, out_bbox (x1,y1,x2,y2)
            self.lib.get_detection.argtypes = [
                ctypes.c_void_p,  # results
                ctypes.c_int,     # index
                ctypes.POINTER(ctypes.c_int),    # class_id
                ctypes.POINTER(ctypes.c_float),  # confidence
                ctypes.POINTER(ctypes.c_float * 4)  # bbox
            ]
            self.lib.get_detection.restype = None

            # free_results: 释放推理结果
            self.lib.free_results.argtypes = [ctypes.c_void_p]
            self.lib.free_results.restype = None

            # release_engine: 释放模型
            self.lib.release_engine.argtypes = [ctypes.c_void_p]
            self.lib.release_engine.restype = None

            print("[InferEngine] C++ 接口设置完成")

        except AttributeError as e:
            print(f"[InferEngine] ⚠️ C++ 接口设置失败: {e}")
            print("[InferEngine] 请确认 libinfer.so 导出了正确的函数")
            self.lib = None

    def load_engine(self, engine_id: str, engine_path: str) -> bool:
        """
        加载模型 - 按文件扩展名直接驱动，不依赖全局 engine_type 猜格式

        支持格式：
          .so     → 每模型独立共享库（任意平台）
          .engine → TensorRT engine（需要 libinfer.so）
          .rknn   → RKNN 模型（需要瑞芯微平台）
          .pt     → PyTorch/ultralytics（任意平台，开发/测试用）

        调用方（base_algorithm）负责找到实际存在的文件再传入，
        本方法只负责"给什么格式就加载什么格式"。
        """
        with self._model_lock:
            if engine_id in self.models:
                print(f"[InferEngine] 引擎 {engine_id} 已加载，复用现有实例")
                return True

            if not Path(engine_path).exists():
                print(f"[InferEngine] ❌ 模型文件不存在: {engine_path}")
                return False

            try:
                ext = Path(engine_path).suffix.lower()

                if ext == '.so':
                    return self._load_model_so(engine_id, engine_path)

                if ext == '.rknn':
                    # 优先尝试 C++ RKNN 库（librknn_yolo.so），跨平台/跨版本兼容性更好
                    if self._load_rknn_via_cpp(engine_id, engine_path):
                        return True
                    # 回退：Python rknnlite（仅瑞芯微平台）
                    if self.engine_type == 'rknn':
                        return self._load_rknn_model(engine_id, engine_path)
                    print(f"[InferEngine] ⚠️ .rknn 需要瑞芯微平台（当前: {self.platform}），跳过")
                    return False

                if ext == '.engine':
                    if self.engine_type == 'tensorrt':
                        return self._load_tensorrt_model(engine_id, engine_path)
                    print(f"[InferEngine] ⚠️ .engine 需要 TensorRT（当前: {self.platform}），跳过")
                    return False

                if ext == '.pt':
                    return self._load_pt_model(engine_id, engine_path)

                # 未知扩展名 → 尝试 ultralytics 兜底
                print(f"[InferEngine] ⚠️ 未知格式: {ext}，尝试 ultralytics 兜底")
                return self._load_pt_model(engine_id, engine_path)

            except Exception as e:
                print(f"[InferEngine] ❌ 模型加载失败: {engine_id}, 错误: {e}")
                import traceback
                traceback.print_exc()
                return False

    def _find_model_so(self, engine_path: str) -> Optional[str]:
        """
        在模型目录中查找 .so 模型文件

        查找顺序：
        1. 与 engine_path 同名但扩展名改为 .so
        2. 同目录下的 model.so

        Returns:
            .so 文件路径，未找到返回 None
        """
        model_dir = Path(engine_path).parent
        stem = Path(engine_path).stem

        candidates = [
            model_dir / f"{stem}.so",  # 同名 .so
            model_dir / "model.so",    # 通用命名
        ]

        for path in candidates:
            if path.exists():
                print(f"[InferEngine] 🔍 发现 .so 模型文件: {path}")
                return str(path)

        return None

    def _load_model_so(self, engine_id: str, so_path: str) -> bool:
        """
        加载每模型独立 .so 文件

        要求 .so 导出接口：
            void* model_init()
            int   model_infer(void* handle, uint8_t* bgr, int w, int h, float conf,
                              int* class_ids, float* confs, float* boxes, int max_n)
            void  model_release(void* handle)
        """
        try:
            print(f"[InferEngine] 正在加载模型 .so: {engine_id} <- {so_path}")
            lib = ctypes.CDLL(so_path)

            # 绑定 C 接口类型
            lib.model_init.argtypes = []
            lib.model_init.restype = ctypes.c_void_p

            lib.model_infer.argtypes = [
                ctypes.c_void_p,                          # handle
                ctypes.POINTER(ctypes.c_uint8),           # bgr_data
                ctypes.c_int,                             # width
                ctypes.c_int,                             # height
                ctypes.c_float,                           # conf_threshold
                ctypes.POINTER(ctypes.c_int),             # out_class_ids
                ctypes.POINTER(ctypes.c_float),           # out_confidences
                ctypes.POINTER(ctypes.c_float),           # out_boxes (x1,y1,x2,y2 * n)
                ctypes.c_int                              # max_detections
            ]
            lib.model_infer.restype = ctypes.c_int

            lib.model_release.argtypes = [ctypes.c_void_p]
            lib.model_release.restype = None

            # 初始化模型
            handle = lib.model_init()
            if handle is None or handle == 0:
                print(f"[InferEngine] ❌ model_init() 返回 NULL: {engine_id}")
                return False

            self.models[engine_id] = {'lib': lib, 'handle': handle, 'type': 'model_so'}
            self.model_types[engine_id] = 'model_so'
            print(f"[InferEngine] ✅ .so 模型加载成功: {engine_id}")
            return True

        except Exception as e:
            print(f"[InferEngine] ❌ .so 模型加载失败: {so_path}, 错误: {e}")
            return False

    def _find_rknn_cpp_lib(self) -> Optional[str]:
        """
        查找 C++ RKNN 推理库 librknn_yolo.so

        查找顺序：
          1. safehat/inference 目录
          2. 项目根目录（CMake 构建后会复制到这里）
          3. cpp/build 构建目录
          2. /usr/local/lib/
          3. /usr/lib/
        """
        project_root = Path(__file__).resolve().parents[2]
        candidates = [
            Path(__file__).parent / 'librknn_yolo.so',
            project_root / 'librknn_yolo.so',
            project_root / 'librknn_yolo.so.1',
            project_root / 'cpp' / 'build' / 'librknn_yolo.so',
            project_root / 'cpp' / 'build' / 'librknn_yolo.so.1',
            Path('/usr/local/lib/librknn_yolo.so'),
            Path('/usr/lib/librknn_yolo.so'),
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return None

    def _load_rknn_via_cpp(self, engine_id: str, rknn_path: str) -> bool:
        """
        使用 C++ librknn_yolo.so 加载 .rknn 模型

        C 接口：
          void* model_init(const char* rknn_path)
          int   model_infer(void* h, uint8_t* bgr, int w, int h, float conf,
                            int* cls, float* conf_arr, float* boxes, int max_n)
          void  model_release(void* h)
        """
        so_path = self._find_rknn_cpp_lib()
        if so_path is None:
            return False  # 库不存在，静默跳过

        try:
            print(f"[InferEngine] 使用 C++ RKNN 库: {so_path}")
            lib = ctypes.CDLL(so_path)

            lib.model_init.argtypes = [ctypes.c_char_p]
            lib.model_init.restype  = ctypes.c_void_p

            lib.model_infer.argtypes = [
                ctypes.c_void_p,               # handle
                ctypes.POINTER(ctypes.c_uint8), # bgr_data
                ctypes.c_int,                   # width
                ctypes.c_int,                   # height
                ctypes.c_float,                 # conf_threshold
                ctypes.POINTER(ctypes.c_int),   # out_class_ids
                ctypes.POINTER(ctypes.c_float), # out_confidences
                ctypes.POINTER(ctypes.c_float), # out_boxes
                ctypes.c_int,                   # max_detections
            ]
            lib.model_infer.restype = ctypes.c_int

            lib.model_release.argtypes = [ctypes.c_void_p]
            lib.model_release.restype  = None

            handle = lib.model_init(rknn_path.encode('utf-8'))
            if handle is None or handle == 0:
                print(f"[InferEngine] ❌ C++ model_init 失败: {rknn_path}")
                return False

            self.models[engine_id]      = {'lib': lib, 'handle': handle, 'type': 'model_so'}
            self.model_types[engine_id] = 'model_so'
            print(f"[InferEngine] ✅ C++ RKNN 加载成功: {engine_id}")
            return True

        except Exception as e:
            print(f"[InferEngine] ❌ C++ RKNN 库加载异常: {e}")
            return False

    def _load_rknn_model(self, engine_id, engine_path):
        from rknnlite.api import RKNNLite

        print(f"[InferEngine] 正在加载 RKNN 模型: {engine_id} <- {engine_path}")

        rknn = RKNNLite()

        ret = rknn.load_rknn(engine_path)
        if ret != 0:
            raise RuntimeError(f"load_rknn failed: {ret}")

        try:
            ret = rknn.init_runtime(target_platform=self.target)
        except TypeError:
            print("[InferEngine] 当前 RKNNLite 不支持 target_platform 参数，改用默认 init_runtime()")
            ret = rknn.init_runtime()

        if ret != 0:
            raise RuntimeError(f"init_runtime failed: {ret}")

        self.models[engine_id] = rknn
        self.model_types[engine_id] = 'rknn'

        print(f"[InferEngine] ✅ RKNN 模型加载成功: {engine_id}")
        return True

    def _load_tensorrt_model(self, engine_id: str, engine_path: str) -> bool:
        """加载 TensorRT 模型（传入路径已是 .engine 文件）"""
        print(f"[InferEngine] 正在加载 TensorRT engine: {engine_id} <- {engine_path}")
        model_handle = self.lib.load_engine(engine_path.encode('utf-8'))

        if model_handle is None or model_handle == 0:
            print(f"[InferEngine] ❌ Engine 加载失败: {engine_id}")
            return False

        self.models[engine_id] = model_handle
        self.model_types[engine_id] = 'tensorrt'
        print(f"[InferEngine] ✅ Engine 加载成功: {engine_id}")
        return True

    def _load_pt_model(self, engine_id: str, pt_path: str) -> bool:
        try:
            import logging

            print(f"[InferEngine] 正在加载 .pt 模型: {engine_id} <- {pt_path}")

            # 先修复 logging
            logging._nameToLevel.clear()
            logging._nameToLevel.update({
                "CRITICAL": 50,
                "FATAL": 50,
                "ERROR": 40,
                "WARN": 30,
                "WARNING": 30,
                "INFO": 20,
                "DEBUG": 10,
                "NOTSET": 0,
            })

            logging._levelToName.clear()
            logging._levelToName.update({
                50: "CRITICAL",
                40: "ERROR",
                30: "WARNING",
                20: "INFO",
                10: "DEBUG",
                0: "NOTSET",
            })

            logging.addLevelName(logging.CRITICAL, "CRITICAL")
            logging.addLevelName(logging.ERROR, "ERROR")
            logging.addLevelName(logging.WARNING, "WARNING")
            logging.addLevelName(logging.INFO, "INFO")
            logging.addLevelName(logging.DEBUG, "DEBUG")
            logging.addLevelName(logging.NOTSET, "NOTSET")

            import torch
            from ultralytics import YOLO

            print(f"[InferEngine] torch version: {torch.__version__}")

            model = YOLO(pt_path)

            self.models[engine_id] = model
            self.model_types[engine_id] = 'ultralytics'
            print(f"[InferEngine] ✅ .pt 模型加载成功: {engine_id}")
            return True

        except Exception as e:
            import traceback
            print(f"[InferEngine] ❌ .pt 模型加载失败: {repr(e)}")
            traceback.print_exc()
            return False

    def _load_fallback_model(self, engine_id: str, engine_path: str) -> bool:
        """兼容旧调用路径，新代码请使用 _load_pt_model()"""
        pt_path = engine_path.replace('.engine', '.pt').replace('.rknn', '.pt')

        if not Path(pt_path).exists():
            model_dir = Path(engine_path).parent
            best_pt = model_dir / 'best.pt'
            if best_pt.exists():
                pt_path = str(best_pt)
            else:
                print(f"[InferEngine] ❌ .pt 文件不存在: {pt_path}")
                return False

        return self._load_pt_model(engine_id, pt_path)

    def infer(self, engine_id: str, image: np.ndarray,
              conf_threshold: float = 0.5) -> List[Dict]:
        """
        执行推理（统一接口）

        Args:
            engine_id: 引擎标识符
            image: 输入图像 (numpy数组, HWC, BGR格式)
            conf_threshold: 置信度阈值

        Returns:
            检测结果列表: [
                {
                    'class_id': int,
                    'confidence': float,
                    'bbox': (x1, y1, x2, y2)
                },
                ...
            ]
        """
        # 检查引擎是否已加载
        if engine_id not in self.models:
            print(f"[InferEngine] ❌ 引擎未加载: {engine_id}")
            return []

        try:
            # 很关键：先复制一份稳定帧，避免上层线程复用同一块内存
            image = np.ascontiguousarray(image.copy())

            model_type = self.model_types.get(engine_id, self.engine_type)
            if INFER_VERBOSE_DEBUG:
                print(f"[InferEngine][{engine_id}] request model_type={model_type} threshold={float(conf_threshold):.4f}")

            if model_type == 'model_so':
                return self._infer_model_so(engine_id, image, conf_threshold)

            elif model_type == 'rknn':
                # 很关键：RKNNLite 推理加锁，避免多线程串帧/串结果
                with self._infer_lock:
                    return self._infer_rknn(engine_id, image, conf_threshold)

            elif model_type == 'tensorrt':
                return self._infer_tensorrt(engine_id, image, conf_threshold)

            else:
                return self._infer_ultralytics(engine_id, image, conf_threshold)

        except Exception as e:
            print(f"[InferEngine] ❌ 推理失败: {engine_id}, 错误: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _infer_rknn(self, engine_id: str, image: np.ndarray, conf_threshold: float) -> List[Dict]:
        rknn = self.models[engine_id]

        input_image, scale, (pad_w, pad_h) = self._preprocess_rknn(image)
        input_image = np.ascontiguousarray(input_image, dtype=np.uint8)

        outputs = rknn.inference(inputs=[input_image])

        if outputs is None or len(outputs) == 0:
            return []

        detections = self._postprocess_rknn(engine_id, outputs, scale, (pad_w, pad_h), conf_threshold)
        return detections

    def _preprocess_rknn(self, image: np.ndarray):
        """RKNN 图像预处理"""
        img_h, img_w = image.shape[:2]
        input_h, input_w = 640, 640  # RKNN 输入尺寸

        # 计算缩放比例（保持宽高比）
        scale = min(input_h / img_h, input_w / img_w)
        new_h, new_w = int(img_h * scale), int(img_w * scale)

        # Resize
        import cv2
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Padding
        padded = np.full((input_h, input_w, 3), 114, dtype=np.uint8)
        pad_h = (input_h - new_h) // 2
        pad_w = (input_w - new_w) // 2
        padded[pad_h:pad_h + new_h, pad_w:pad_w + new_w] = resized

        # BGR -> RGB
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)

        # RKNNLite 需要 4 维输入: NHWC -> [1, H, W, C]
        rgb = np.expand_dims(rgb, axis=0)
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)

        return rgb, scale, (pad_w, pad_h)

    def _postprocess_rknn(self, engine_id, outputs, scale, padding, conf_threshold):
        """
        RKNN 输出后处理（简化稳定版）

        约定：
        - 前4列一定是框: [x, y, w, h] 或 [x1, y1, x2, y2]
        - 第5列及以后全部视为类别分数
        - 5通道: 4框 + 1类分数
        - 6通道: 4框 + 2类分数
        - 7+通道: 4框 + 多类分数
        """
        import cv2
        import numpy as np

        pad_w, pad_h = padding

        def _safe_array(x):
            arr = np.array(x, dtype=np.float32)
            arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
            return arr

        def _normalize_output_shape(out):
            """
            统一整理成 (N, C)
            支持:
            - (1, C, N, 1)
            - (1, C, N)
            - (C, N)
            - (N, C)
            """
            out = _safe_array(out)

            if out.ndim == 4:
                if out.shape[0] == 1:
                    out = np.squeeze(out, axis=0)
                if out.ndim == 3 and out.shape[-1] == 1:
                    out = np.squeeze(out, axis=-1)
                if out.ndim == 2 and out.shape[0] <= 64 and out.shape[1] > 100:
                    out = out.transpose(1, 0)

            elif out.ndim == 3:
                if out.shape[0] == 1:
                    out = np.squeeze(out, axis=0)
                if out.ndim == 2 and out.shape[0] <= 64 and out.shape[1] > 100:
                    out = out.transpose(1, 0)

            elif out.ndim == 2:
                if out.shape[0] <= 64 and out.shape[1] > 100:
                    out = out.transpose(1, 0)

            return out

        def _decode_boxes_xywh(raw_boxes):
            x_center = raw_boxes[:, 0]
            y_center = raw_boxes[:, 1]
            w = raw_boxes[:, 2]
            h = raw_boxes[:, 3]

            x1 = ((x_center - w / 2) - pad_w) / scale
            y1 = ((y_center - h / 2) - pad_h) / scale
            x2 = ((x_center + w / 2) - pad_w) / scale
            y2 = ((y_center + h / 2) - pad_h) / scale

            return np.stack([x1, y1, x2, y2], axis=1)

        def _decode_boxes_xyxy(raw_boxes):
            x1 = (raw_boxes[:, 0] - pad_w) / scale
            y1 = (raw_boxes[:, 1] - pad_h) / scale
            x2 = (raw_boxes[:, 2] - pad_w) / scale
            y2 = (raw_boxes[:, 3] - pad_h) / scale

            return np.stack([x1, y1, x2, y2], axis=1)

        def _sanitize_boxes(boxes_xyxy):
            boxes_xyxy = _safe_array(boxes_xyxy)
            boxes_xyxy[:, 0] = np.maximum(0, boxes_xyxy[:, 0])
            boxes_xyxy[:, 1] = np.maximum(0, boxes_xyxy[:, 1])
            boxes_xyxy[:, 2] = np.maximum(0, boxes_xyxy[:, 2])
            boxes_xyxy[:, 3] = np.maximum(0, boxes_xyxy[:, 3])
            return boxes_xyxy

        def _valid_box_mask(boxes_xyxy, min_size=2.0):
            w = boxes_xyxy[:, 2] - boxes_xyxy[:, 0]
            h = boxes_xyxy[:, 3] - boxes_xyxy[:, 1]
            return (w > min_size) & (h > min_size)

        def _choose_box_format(raw_boxes):
            """
            同时尝试 xywh / xyxy，自动选更合理的一种
            """
            boxes_xywh = _sanitize_boxes(_decode_boxes_xywh(raw_boxes))
            boxes_xyxy = _sanitize_boxes(_decode_boxes_xyxy(raw_boxes))

            valid_xywh = int(np.sum(_valid_box_mask(boxes_xywh)))
            valid_xyxy = int(np.sum(_valid_box_mask(boxes_xyxy)))

            if valid_xyxy > valid_xywh * 1.2:
                chosen = "xyxy"
                boxes = boxes_xyxy
            else:
                chosen = "xywh"
                boxes = boxes_xywh

            if INFER_VERBOSE_DEBUG:
                print(f"[InferEngine][{engine_id}] box_format={chosen}, valid_xywh={valid_xywh}, valid_xyxy={valid_xyxy}")
            return boxes

        def _per_class_nms(boxes_xyxy, confidences, class_ids, score_threshold, nms_threshold=0.30):
            detections = []
            if len(boxes_xyxy) == 0:
                return detections

            unique_classes = np.unique(class_ids)
            for cls_id in unique_classes:
                cls_mask = class_ids == cls_id
                cls_boxes = boxes_xyxy[cls_mask]
                cls_scores = confidences[cls_mask]

                if INFER_VERBOSE_DEBUG:
                    print(f"[InferEngine][{engine_id}] class={cls_id} before_nms={len(cls_boxes)}")
                if len(cls_boxes) == 0:
                    continue

                boxes_for_nms = []
                for b in cls_boxes:
                    x1, y1, x2, y2 = b
                    boxes_for_nms.append([
                        float(x1),
                        float(y1),
                        float(max(0.0, x2 - x1)),
                        float(max(0.0, y2 - y1)),
                    ])

                indices = cv2.dnn.NMSBoxes(
                    bboxes=boxes_for_nms,
                    scores=cls_scores.tolist(),
                    score_threshold=float(score_threshold),
                    nms_threshold=float(nms_threshold),
                )

                if indices is None or len(indices) == 0:
                    if INFER_VERBOSE_DEBUG:
                        print(f"[InferEngine][{engine_id}] class={cls_id} after_nms=0")
                    continue

                indices = np.array(indices).reshape(-1)
                if INFER_VERBOSE_DEBUG:
                    print(f"[InferEngine][{engine_id}] class={cls_id} after_nms={len(indices)}")

                kept_boxes = cls_boxes[indices]
                kept_scores = cls_scores[indices]

                for i in range(len(indices)):
                    x1, y1, x2, y2 = kept_boxes[i]
                    if x2 <= x1 or y2 <= y1:
                        continue

                    detections.append({
                        "class_id": int(cls_id),
                        "confidence": float(kept_scores[i]),
                        "bbox": (int(x1), int(y1), int(x2), int(y2)),
                    })

            return detections

        # -------------------------
        # 0. 基本检查
        # -------------------------
        if outputs is None or len(outputs) == 0:
            return []

        output = _normalize_output_shape(outputs[0])

        if output.ndim != 2 or output.shape[1] < 5:
            print(f"[InferEngine] ❌ RKNN 输出格式异常: {output.shape}")
            return []

        output = _safe_array(output)
        num_rows, num_cols = output.shape

        if INFER_VERBOSE_DEBUG:
            print(f"[InferEngine][{engine_id}] RKNN normalized output shape: {output.shape}")
            try:
                print(f"[InferEngine][{engine_id}] RKNN first row: {output[0][:min(10, num_cols)]}")
            except Exception:
                pass

        # -------------------------
        # 1. 拆分框和类别分数
        # -------------------------
        raw_boxes = output[:, :4].astype(np.float32)
        cls_scores = output[:, 4:].astype(np.float32)

        if cls_scores.ndim == 1:
            cls_scores = cls_scores[:, None]

        cls_scores = _safe_array(cls_scores)

        # 统一处理：后面所有列都是类别分数
        class_ids = np.argmax(cls_scores, axis=1).astype(np.int32)
        confidences = cls_scores[np.arange(num_rows), class_ids]

        parse_mode = f"{num_cols}col_box_plus_{cls_scores.shape[1]}cls"

        confidences = _safe_array(confidences).reshape(-1)
        class_ids = np.nan_to_num(class_ids, nan=0).astype(np.int32).reshape(-1)

        conf_min = float(confidences.min()) if len(confidences) > 0 else 0.0
        conf_max = float(confidences.max()) if len(confidences) > 0 else 0.0
        if INFER_VERBOSE_DEBUG:
            print(
                f"[InferEngine][{engine_id}] parse={parse_mode} "
                f"conf_range=({conf_min:.4f}, {conf_max:.4f}) "
                f"threshold={float(conf_threshold):.4f}"
            )

        # -------------------------
        # 2. 阈值过滤
        # -------------------------
        effective_conf = float(conf_threshold)

        # 如果你觉得 RKNN 分数整体偏低，这里可以先固定成 0.15 或 0.1
        # effective_conf = min(effective_conf, 0.15)

        mask = confidences > effective_conf
        if not np.any(mask):
            if INFER_VERBOSE_DEBUG:
                print(
                    f"[InferEngine][{engine_id}] threshold_filtered_all "
                    f"(threshold={effective_conf:.4f}, max_conf={conf_max:.4f})"
                )
            return []

        raw_boxes = raw_boxes[mask]
        confidences = confidences[mask]
        class_ids = class_ids[mask]

        if INFER_VERBOSE_DEBUG:
            print(f"[InferEngine][{engine_id}] candidates_after_threshold={len(raw_boxes)}")
        if INFER_VERBOSE_DEBUG:
            try:
                print(f"[InferEngine][{engine_id}] boxes sample(before decode): {raw_boxes[:3]}")
            except Exception:
                pass

        # -------------------------
        # 3. 框解码
        # -------------------------
        boxes_xyxy = _choose_box_format(raw_boxes)

        valid_mask = _valid_box_mask(boxes_xyxy, min_size=2.0)
        boxes_xyxy = boxes_xyxy[valid_mask]
        confidences = confidences[valid_mask]
        class_ids = class_ids[valid_mask]

        if len(boxes_xyxy) == 0:
            if INFER_VERBOSE_DEBUG:
                print(f"[InferEngine][{engine_id}] invalid_boxes_after_decode")
            return []

        # -------------------------
        # 4. NMS
        # -------------------------
        detections = _per_class_nms(
            boxes_xyxy=boxes_xyxy,
            confidences=confidences,
            class_ids=class_ids,
            score_threshold=effective_conf,
            nms_threshold=0.30,
        )

        if INFER_VERBOSE_DEBUG:
            print(f"[InferEngine][{engine_id}] final_detections={len(detections)}")
        if INFER_VERBOSE_DEBUG and len(detections) > 0:
            print(f"[InferEngine][{engine_id}] first_detection={detections[0]}")

        return detections

    def _infer_model_so(self, engine_id: str, image: np.ndarray,
                        conf_threshold: float) -> List[Dict]:
        """使用每模型 .so 文件执行推理"""
        entry = self.models[engine_id]
        lib = entry['lib']
        handle = entry['handle']

        h, w = image.shape[:2]
        image_c = np.ascontiguousarray(image, dtype=np.uint8)
        image_ptr = image_c.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))

        max_det = 200
        out_class_ids  = (ctypes.c_int   * max_det)()
        out_confs      = (ctypes.c_float  * max_det)()
        out_boxes      = (ctypes.c_float  * (max_det * 4))()

        num = lib.model_infer(
            handle, image_ptr, w, h, conf_threshold,
            out_class_ids, out_confs, out_boxes, max_det
        )

        if num < 0:
            print(f"[InferEngine] ⚠️ model_infer 返回错误: {engine_id}")
            return []

        detections = []
        for i in range(num):
            detections.append({
                'class_id':   int(out_class_ids[i]),
                'confidence': float(out_confs[i]),
                'bbox': (
                    max(0, float(out_boxes[i * 4])),
                    max(0, float(out_boxes[i * 4 + 1])),
                    max(0, float(out_boxes[i * 4 + 2])),
                    max(0, float(out_boxes[i * 4 + 3]))
                )
            })

        return detections

    def _infer_tensorrt(self, engine_id: str, image: np.ndarray,
                       conf_threshold: float) -> List[Dict]:
        """使用 TensorRT (libinfer.so) 执行推理"""
        model_handle = self.models[engine_id]

        # 准备图像数据
        height, width = image.shape[:2]
        image_data = image.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))

        # 调用 C++ 推理
        results_ptr = self.lib.infer(
            model_handle,
            image_data,
            width,
            height,
            conf_threshold
        )

        if results_ptr is None or results_ptr == 0:
            return []

        # 解析结果
        num_detections = self.lib.get_num_detections(results_ptr)
        detections = []

        for i in range(num_detections):
            class_id = ctypes.c_int()
            confidence = ctypes.c_float()
            bbox = (ctypes.c_float * 4)()

            self.lib.get_detection(
                results_ptr, i,
                ctypes.byref(class_id),
                ctypes.byref(confidence),
                ctypes.byref(bbox)
            )

            detections.append({
                'class_id': class_id.value,
                'confidence': confidence.value,
                'bbox': (bbox[0], bbox[1], bbox[2], bbox[3])
            })

        # 释放 C++ 内存
        self.lib.free_results(results_ptr)

        return detections

    def _infer_ultralytics(self, engine_id: str, image: np.ndarray,
                          conf_threshold: float) -> List[Dict]:
        """临时方案: 使用 ultralytics 推理"""
        model = self.models[engine_id]

        # 执行推理
        results = model(image, verbose=False, conf=conf_threshold)

        # 统一格式化输出
        detections = []
        for result in results:
            boxes = result.boxes
            if boxes is not None:
                for box in boxes:
                    detections.append({
                        'class_id': int(box.cls[0]),
                        'confidence': float(box.conf[0]),
                        'bbox': tuple(float(x) for x in box.xyxy[0].cpu().numpy())
                    })

        return detections

    def release_engine(self, engine_id: str):
        """释放指定引擎资源"""
        with self._model_lock:
            if engine_id not in self.models:
                return

            try:
                model_type = self.model_types.get(engine_id, self.engine_type)
                model_entry = self.models[engine_id]

                if model_type == 'model_so':
                    model_entry['lib'].model_release(model_entry['handle'])
                elif model_type == 'rknn':
                    if hasattr(model_entry, 'release'):
                        model_entry.release()
                elif model_type == 'tensorrt':
                    if self.lib is not None:
                        self.lib.release_engine(model_entry)
                # ultralytics: 依赖 Python GC，无需显式释放

                del self.models[engine_id]
                self.model_types.pop(engine_id, None)
                print(f"[InferEngine] 引擎已释放: {engine_id}")

            except Exception as e:
                print(f"[InferEngine] 释放引擎失败: {engine_id}, 错误: {e}")

    def release_all(self):
        """释放所有引擎资源"""
        with self._model_lock:
            engine_ids = list(self.models.keys())
            for engine_id in engine_ids:
                self.release_engine(engine_id)

        print("[InferEngine] 所有引擎已释放")


# ============================================
# 对外暴露的简化接口（算法层只能使用这些函数）
# ============================================

_engine = InferenceEngine()


def load_engine(engine_id: str, engine_path: str) -> bool:
    """
    加载 TensorRT engine 模型

    Args:
        engine_id: 引擎唯一标识符
        engine_path: .engine 文件路径

    Returns:
        是否加载成功
    """
    return _engine.load_engine(engine_id, engine_path)


def infer(engine_id: str, image: np.ndarray, conf_threshold: float = 0.5) -> List[Dict]:
    """
    执行推理

    Args:
        engine_id: 引擎标识符
        image: 输入图像 (BGR格式)
        conf_threshold: 置信度阈值

    Returns:
        检测结果: [{'class_id': int, 'confidence': float, 'bbox': (x1,y1,x2,y2)}, ...]
    """
    return _engine.infer(engine_id, image, conf_threshold)


def release_engine(engine_id: str):
    """释放指定引擎"""
    _engine.release_engine(engine_id)


def release_all():
    """释放所有引擎"""
    _engine.release_all()


# ============================================
# 模块级别禁止事项
# ============================================

# ❌ 禁止算法层直接实例化 InferenceEngine
# ❌ 禁止算法层访问 _engine 私有变量
# ❌ 禁止算法层直接操作 ctypes / so 库

# ✅ 算法层只能调用: load_engine(), infer(), release_engine()
