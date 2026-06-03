# SafeHat - AI交通安全隐患识别系统

> **商用级AI安全检测系统 - 专业桌面版**

基于YOLOv8的实时安全检测系统，专业级界面，支持安全帽和反光衣检测。

---

## 🎯 系统特点

- ✅ **专业界面** - 精美的数据大屏设计，商用级体验
- ✅ **实时检测** - 支持摄像头实时视频流检测
- ✅ **数据可视化** - ECharts动态图表，趋势分析
- ✅ **多摄像头** - 支持多摄像头管理和切换
- ✅ **原生体验** - 桌面窗口，无需浏览器
- ✅ **ARM优化** - 专门为瑞芯微RK3586优化

---

## 📌 硬件要求

| 项目 | 要求 |
|-----|------|
| **处理器** | 瑞芯微RK3586 (ARM64架构) |
| **操作系统** | Ubuntu 18.04+ (ARM版) |
| **Python** | 3.8 |
| **内存** | 建议 4GB+ |
| **摄像头** | USB摄像头或网络摄像头 |

---

## 🚀 快速开始（推荐）

### 方案A：在线安装（最简单，10-15分钟）

```bash
# 1. 将项目传输到Ubuntu

# 2. 赋予执行权限
chmod +x install_online_arm_fast.sh

# 3. 运行安装脚本
./install_online_arm_fast.sh

# 4. 启动系统
python3 safety_detection_gui.py
```

**优势：**
- ✅ 使用清华大学镜像，速度快
- ✅ 自动处理权限问题
- ✅ 可选择CPU版PyTorch（更小更快）
- ✅ 总耗时：10-15分钟

---

### 方案B：离线安装（需要网络不好时）

**步骤1：在有网络的机器上下载依赖**

```bash
# 在Windows或任何有网络的机器上运行
python download_dependencies_arm.py
```

**步骤2：传输到Ubuntu**

将整个项目文件夹（包含 `nodules_arm/`）传输到Ubuntu。

**步骤3：在Ubuntu上安装**

```bash
chmod +x install_offline_arm.sh
./install_offline_arm.sh
```

---

## 📖 详细文档

| 文档 | 说明 |
|-----|------|
| [桌面版使用说明.md](桌面版使用说明.md) | 详细使用指南 ⭐ |
| [商用部署指南.md](商用部署指南.md) | 商业化部署方案 |
| [快速开始_ARM版.md](快速开始_ARM版.md) | 快速入门指南 |
| [ARM架构安装说明.md](ARM架构安装说明.md) | 完整技术文档 |

---

## 🎮 使用方法

### 一键启动

```bash
# Linux/Ubuntu
chmod +x run.sh
./run.sh

# Windows
run.bat
```

**或直接运行：**
```bash
python3 safety_detection_desktop.py
```

**系统特点：**
- ✅ **专业界面** - 精美的Web风格设计
- ✅ **无需浏览器** - 原生桌面窗口
- ✅ **数据大屏** - ECharts动态图表
- ✅ **实时统计** - 检测数据可视化
- ✅ **摄像头管理** - 多摄像头配置
- ✅ **商用级体验** - 适合专业部署

---

## 🔧 常见问题

### Q1: 安装时提示"Permission denied"

**解决：** 脚本已自动使用 `--user` 参数，将包安装到用户目录。

如果仍有问题：
```bash
python3 -m pip install --user <package_name>
```

### Q2: OpenCV下载太慢

**解决：** 使用清华镜像：
```bash
pip3 install --user -i https://pypi.tuna.tsinghua.edu.cn/simple opencv-python
```

### Q3: PyTorch包太大（800MB）

**解决：** 使用CPU版本（只有200MB）：
```bash
pip3 install --user torch torchvision --extra-index-url https://download.pytorch.org/whl/cpu
```

### Q4: 检查架构是否正确

```bash
# 应该显示 aarch64
uname -m

# 检查Python包
python3 -c "import numpy; print(numpy.__file__)"
```

### Q5: 程序代码需要修改吗？

**不需要！** Python代码是跨平台的，只要依赖库安装正确（ARM版本），代码无需修改。

---

## 📁 项目结构

```
safehat/
├── app.py                          # Flask API + 模板
├── safehat/
│   ├── core/                       # 检测核心（YOLO + OpenCV）
│   │   ├── real_detection.py
│   │   ├── helmet_detection.py
│   │   └── vest_detection.py
│   └── managers/                   # 管理器（通道/告警/拼屏）
│       ├── alert_manager.py
│       ├── channel_manager.py
│       └── merge_manager.py
├── dashboard/                      # 数据大屏（React/Vite 展示层）
│   └── src/...
├── server/                         # 预留扩展的API分层
│   ├── routers/
│   └── services/
├── runs/detect/train/weights/      # YOLO模型文件
│   └── best.pt
├── safety_detection_gui.py         # GUI主程序
├── safety_detection_desktop.py     # 桌面版入口
├── install_online_arm_fast.sh      # 在线安装脚本（推荐）
├── install_offline_arm.sh          # 离线安装脚本
├── download_dependencies_arm.py    # ARM依赖下载工具
├── requirements.txt                # 依赖列表
├── README.md                       # 本文档
├── 快速开始_ARM版.md               # 快速指南
└── ARM架构安装说明.md              # 详细文档
```

---

## ⚡ 性能优化建议

### 1. 使用CPU版PyTorch（推荐）

```bash
pip3 install --user torch torchvision --extra-index-url https://download.pytorch.org/whl/cpu
```

**优势：**
- ✅ 体积小4倍（200MB vs 800MB）
- ✅ 下载快
- ✅ RK3586 CPU性能足够

### 2. 配置永久镜像源

```bash
mkdir -p ~/.pip
cat > ~/.pip/pip.conf << 'EOF'
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
EOF
```

### 3. 优化摄像头参数

在 `safety_detection_gui.py` 中调整：

```python
# 降低分辨率以提高FPS
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
```

---

## 🔍 系统验证

安装完成后，运行以下命令验证：

```python
python3 << 'EOF'
import platform
import numpy, cv2, torch, ultralytics

print(f"系统架构: {platform.machine()}")  # 应显示 aarch64
print(f"NumPy版本: {numpy.__version__}")
print(f"OpenCV版本: {cv2.__version__}")
print(f"PyTorch版本: {torch.__version__}")
print(f"Ultralytics版本: {ultralytics.__version__}")
print("\n✅ 所有依赖已正确安装！")
EOF
```

---

## 📊 版本信息

- **系统版本**: v2.0 (ARM优化版)
- **目标平台**: 瑞芯微RK3586 (ARM64)
- **Python版本**: 3.8
- **YOLO版本**: YOLOv8

---

## 🎉 开始使用

```bash
# 最快方式
chmod +x install_online_arm_fast.sh
./install_online_arm_fast.sh

# 启动
python3 safety_detection_gui.py
```

**预计总耗时：10-15分钟** ⚡

---

## 📞 技术支持

如遇问题，请提供：
1. `uname -m` 输出（系统架构）
2. `python3 --version` 输出
3. 完整错误信息
4. `pip3 list | grep -E 'numpy|opencv|torch|ultralytics'` 输出

---

**祝使用愉快！🎉**
