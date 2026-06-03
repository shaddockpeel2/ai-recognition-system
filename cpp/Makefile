# Makefile for libinfer.so
# TensorRT C++ 推理引擎编译脚本

# ============================================
# 配置部分（根据实际环境修改）
# ============================================

# TensorRT 路径
TENSORRT_DIR ?= /usr/local/TensorRT-8.6.1

# CUDA 路径
CUDA_DIR ?= /usr/local/cuda

# 编译器
CXX = g++

# ============================================
# 编译选项
# ============================================

# C++ 标准和优化
CXXFLAGS = -std=c++14 -fPIC -O3 -Wall -Wextra

# 包含路径
INCLUDES = -I$(TENSORRT_DIR)/include \
           -I$(CUDA_DIR)/include

# 链接路径
LDFLAGS = -L$(TENSORRT_DIR)/lib \
          -L$(CUDA_DIR)/lib64

# 链接库
LIBS = -lnvinfer \
       -lnvinfer_plugin \
       -lnvonnxparser \
       -lcudart \
       -lcudnn \
       -shared

# ============================================
# 目标文件
# ============================================

TARGET = ../libinfer.so
SOURCES = infer_engine.cpp
OBJECTS = $(SOURCES:.cpp=.o)
HEADERS = infer_engine.h

# ============================================
# 构建规则
# ============================================

.PHONY: all clean install test help

# 默认目标
all: $(TARGET)
	@echo ""
	@echo "✅ 编译成功！"
	@echo "   输出文件: $(TARGET)"
	@echo ""
	@echo "下一步："
	@echo "   1. 测试: make test"
	@echo "   2. 安装: sudo make install"
	@echo ""

# 编译目标
$(TARGET): $(OBJECTS)
	@echo "🔗 链接 $@..."
	$(CXX) $(OBJECTS) $(LDFLAGS) $(LIBS) -o $(TARGET)

# 编译源文件
%.o: %.cpp $(HEADERS)
	@echo "🔨 编译 $<..."
	$(CXX) $(CXXFLAGS) $(INCLUDES) -c $< -o $@

# 清理
clean:
	@echo "🧹 清理编译产物..."
	rm -f $(OBJECTS) $(TARGET)
	@echo "✅ 清理完成"

# 安装到系统
install: $(TARGET)
	@echo "📦 安装 libinfer.so 到系统..."
	sudo cp $(TARGET) /usr/local/lib/
	sudo ldconfig
	@echo "✅ 安装完成"
	@echo "   位置: /usr/local/lib/libinfer.so"

# 卸载
uninstall:
	@echo "🗑️  卸载 libinfer.so..."
	sudo rm -f /usr/local/lib/libinfer.so
	sudo ldconfig
	@echo "✅ 卸载完成"

# 测试
test: $(TARGET)
	@echo "🧪 测试 libinfer.so..."
	@echo ""
	@echo "1. 检查文件..."
	@ls -lh $(TARGET) || (echo "❌ 文件不存在"; exit 1)
	@echo "   ✅ 文件存在"
	@echo ""
	@echo "2. 检查符号导出..."
	@nm -D $(TARGET) | grep -E "(load_engine|infer|free_results)" > /dev/null \
		&& echo "   ✅ 符号导出正常" \
		|| (echo "   ❌ 符号导出错误"; exit 1)
	@echo ""
	@echo "3. 检查依赖..."
	@ldd $(TARGET) | grep -E "(nvinfer|cudart)" > /dev/null \
		&& echo "   ✅ 依赖库正常" \
		|| (echo "   ⚠️  依赖库可能缺失"; exit 1)
	@echo ""
	@echo "4. Python 加载测试..."
	@cd .. && python -c "import ctypes; ctypes.CDLL('./libinfer.so'); print('   ✅ Python 加载成功')" \
		|| (echo "   ❌ Python 加载失败"; exit 1)
	@echo ""
	@echo "🎉 所有测试通过！"

# 检查环境
check:
	@echo "🔍 检查编译环境..."
	@echo ""
	@echo "1. TensorRT 路径: $(TENSORRT_DIR)"
	@test -d $(TENSORRT_DIR) \
		&& echo "   ✅ 目录存在" \
		|| (echo "   ❌ 目录不存在，请设置正确的 TENSORRT_DIR"; exit 1)
	@echo ""
	@echo "2. CUDA 路径: $(CUDA_DIR)"
	@test -d $(CUDA_DIR) \
		&& echo "   ✅ 目录存在" \
		|| (echo "   ❌ 目录不存在，请设置正确的 CUDA_DIR"; exit 1)
	@echo ""
	@echo "3. 编译器: $(CXX)"
	@which $(CXX) > /dev/null \
		&& echo "   ✅ 编译器可用" \
		|| (echo "   ❌ 编译器不可用"; exit 1)
	@echo ""
	@echo "4. TensorRT 库文件..."
	@test -f $(TENSORRT_DIR)/lib/libnvinfer.so \
		&& echo "   ✅ libnvinfer.so 存在" \
		|| (echo "   ❌ libnvinfer.so 不存在"; exit 1)
	@echo ""
	@echo "5. CUDA 库文件..."
	@test -f $(CUDA_DIR)/lib64/libcudart.so \
		&& echo "   ✅ libcudart.so 存在" \
		|| (echo "   ❌ libcudart.so 不存在"; exit 1)
	@echo ""
	@echo "✅ 环境检查通过！"

# 帮助信息
help:
	@echo "SafeHat AI - libinfer.so 编译工具"
	@echo ""
	@echo "使用方法:"
	@echo "  make              - 编译 libinfer.so"
	@echo "  make check        - 检查编译环境"
	@echo "  make test         - 测试编译结果"
	@echo "  make clean        - 清理编译产物"
	@echo "  make install      - 安装到系统"
	@echo "  make uninstall    - 从系统卸载"
	@echo ""
	@echo "自定义路径:"
	@echo "  make TENSORRT_DIR=/path/to/TensorRT"
	@echo "  make CUDA_DIR=/path/to/cuda"
	@echo ""
	@echo "示例:"
	@echo "  make TENSORRT_DIR=/opt/TensorRT-8.6.1"
	@echo ""

# ============================================
# 高级选项
# ============================================

# Debug 版本
debug: CXXFLAGS += -g -DDEBUG
debug: clean all
	@echo "🐛 Debug 版本编译完成"

# 显示编译命令
verbose: CXXFLAGS += -v
verbose: all

# ARM 交叉编译
arm:
	@echo "🔧 ARM 交叉编译..."
	$(MAKE) CXX=aarch64-linux-gnu-g++ \
		TENSORRT_DIR=/opt/TensorRT-arm \
		CUDA_DIR=/usr/local/cuda-arm

# ============================================
# 信息显示
# ============================================

info:
	@echo "编译配置:"
	@echo "  编译器: $(CXX)"
	@echo "  C++ 标准: C++14"
	@echo "  优化级别: -O3"
	@echo "  TensorRT: $(TENSORRT_DIR)"
	@echo "  CUDA: $(CUDA_DIR)"
	@echo "  目标文件: $(TARGET)"




