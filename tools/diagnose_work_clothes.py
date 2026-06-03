#!/usr/bin/env python3
"""诊断工作服 RKNN 模型加载与一次推理链路。"""
import os
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    from safehat.inference import engine as infer
    from suanfa.work_clothes_detection import WorkClothesDetector

    print("== SafeHat 工作服检测诊断 ==")
    print(f"cwd: {Path.cwd()}")
    print(f"project: {PROJECT_ROOT}")
    print(f"user: uid={os.getuid()} gid={os.getgid()} groups={os.getgroups()}")
    print(f"SAFEHAT_INFER_VERBOSE_DEBUG={os.getenv('SAFEHAT_INFER_VERBOSE_DEBUG', '')}")

    dri = Path("/dev/dri")
    if dri.exists():
        print("/dev/dri:")
        for p in sorted(dri.glob("*")):
            try:
                st = p.stat()
                print(f"  {p}: mode={oct(st.st_mode & 0o777)} uid={st.st_uid} gid={st.st_gid}")
            except OSError as exc:
                print(f"  {p}: {exc}")
    else:
        print("/dev/dri 不存在")

    detector = WorkClothesDetector()
    print(f"algorithm: {detector.algorithm_name}")
    print(f"engine_path: {detector.engine_path}")
    print(f"engine_loaded: {getattr(detector, 'engine_loaded', None)}")
    print(f"loaded_model_path: {getattr(detector, 'loaded_model_path', None)}")
    print(f"infer platform: {infer._engine.platform}")
    print(f"infer engine_type: {getattr(infer._engine, 'engine_type', None)}")
    print(f"model_type: {infer._engine.model_types.get(detector.algorithm_name)}")

    detector.enable()

    image_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if image_path and not image_path.exists():
        print(f"test image not found: {image_path}")
        return 2

    if image_path:
        frame = cv2.imread(str(image_path))
        print(f"test image: {image_path} shape={None if frame is None else frame.shape}")
        if frame is None:
            print(f"failed to read image: {image_path}")
            return 2
    else:
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        print("test image: empty 1280x720 frame")

    processed, info, stats, alerts = detector.detect(frame)
    print(f"detection_info: {info}")
    print(f"stats: {stats}")
    print(f"alerts: {alerts}")
    print(f"processed_shape: {None if processed is None else processed.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
