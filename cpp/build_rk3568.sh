#!/bin/bash
# 在 RK3568 设备上编译 librknn_yolo.so 的一键脚本
# 运行: bash cpp/build_rk3568.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== 编译 librknn_yolo.so (RK3568 NPU 推理库) ==="

# 检查编译器
if ! command -v g++ &>/dev/null; then
    echo "[错误] 未找到 g++，请先安装: sudo apt install build-essential"
    exit 1
fi

# 确认 cmake
if ! command -v cmake &>/dev/null; then
    echo "[错误] 未找到 cmake，请先安装: sudo apt install cmake"
    exit 1
fi

BUILD_DIR="$SCRIPT_DIR/build"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PROJECT_ROOT"

make -j$(nproc)

# 将 .so 复制到项目根目录（infer.py 会在那里自动找到它）
cp librknn_yolo.so "$PROJECT_ROOT/librknn_yolo.so"

echo ""
echo "=== 编译完成 ==="
echo "已将 librknn_yolo.so 复制到: $PROJECT_ROOT/"
echo ""
echo "接下来："
echo "  1. 将 best.rknn 放入 $PROJECT_ROOT/models/灭火器识别/ 目录"
echo "  2. 启动 Python 服务，infer.py 会自动使用 C++ NPU 推理"
