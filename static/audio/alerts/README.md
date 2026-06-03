# 告警音频文件

此文件夹存放系统告警音频文件，支持中文和英文两种语言。

## 音频文件命名规范

### 中文音频
- 格式：`{告警类型}_warning.mp3`
- 示例：`helmet_warning.mp3`（安全帽告警）

### 英文音频
- 格式：`{告警类型}_warning_en.mp3`
- 示例：`helmet_warning_en.mp3`（Helmet warning）

## 已有音频文件

### 中文音频
- ✅ helmet_warning.mp3 - 安全帽告警音频
- 其他txt文件为占位文件，需要生成实际MP3

### 英文音频
- ⚠️ 需要生成（见下方"如何生成英文音频"）

## 需要添加的音频文件

### 主要告警类型

| 告警类型 | 中文文件名 | 英文文件名 | 建议内容（中文） | 建议内容（英文） |
|---------|----------|----------|----------------|----------------|
| 安全帽检测 | helmet_warning.mp3 | helmet_warning_en.mp3 | 发现未佩戴安全帽 | Helmet not detected |
| 反光衣检测 | vest_warning.mp3 | vest_warning_en.mp3 | 发现未穿戴反光衣 | Safety vest not detected |
| 烟雾检测 | smoke_warning.mp3 | smoke_warning_en.mp3 | 检测到烟雾火情 | Smoke detected |
| 火灾检测 | fire_warning.mp3 | fire_warning_en.mp3 | 检测到火灾 | Fire detected |
| 裂缝检测 | crack_warning.mp3 | crack_warning_en.mp3 | 发现结构裂缝 | Structural crack detected |
| 人员入侵 | intrusion_warning.mp3 | intrusion_warning_en.mp3 | 检测到人员入侵 | Intrusion detected |
| 人员跌倒 | fall_warning.mp3 | fall_warning_en.mp3 | 检测到人员跌倒 | Person fall detected |
| 试剂瓶检测 | reagent_bottle_warning.mp3 | reagent_bottle_warning_en.mp3 | 检测到试剂瓶异常 | Reagent bottle abnormality |
| 摄像头遮挡 | camera_occlusion_warning.mp3 | camera_occlusion_warning_en.mp3 | 检测到摄像头遮挡 | Camera occlusion detected |
| 打架斗殴 | fight_warning.mp3 | fight_warning_en.mp3 | 检测到打架斗殴 | Fighting detected |
| 手套检测 | glove_warning.mp3 | glove_warning_en.mp3 | 未佩戴防护手套 | Gloves not detected |
| 宠物检测 | dog_warning.mp3 | dog_warning_en.mp3 | 检测到宠物 | Pet detected |

## 音频文件要求

- **格式**: MP3
- **采样率**: 44.1kHz 或 48kHz
- **比特率**: 128kbps 或以上
- **声道**: 单声道或立体声
- **音量**: 标准化音量，避免过大或过小
- **时长**: 3-5秒

## 如何生成英文音频

### 方法1：使用Python脚本（推荐）

```bash
# 在项目根目录运行
python generate_alert_audio_en.py

# 根据提示选择生成方式：
# 1 - 使用gTTS（需要网络）
# 2 - 使用pyttsx3（离线）
# 3 - 创建占位符（手动生成）
```

### 方法2：在线TTS工具

#### 推荐工具
1. **TTSMaker** (免费，无需注册)
   - 网址：https://ttsmaker.com/
   - 支持多种英文口音
   - 直接下载MP3

2. **Google Cloud Text-to-Speech**
   - 网址：https://cloud.google.com/text-to-speech
   - 音质最佳
   - 需要Google账号

3. **Natural Reader**
   - 网址：https://www.naturalreaders.com/online/
   - 界面友好
   - 支持多种声音

#### 使用步骤
1. 访问在线TTS工具
2. 复制对应的英文告警文本（见上表）
3. 选择英语（美音或英音）
4. 生成并下载MP3
5. 重命名为对应的文件名
6. 放置到本目录

### 方法3：Python库生成

#### 安装依赖
```bash
# 方案1：使用gTTS（需要网络）
pip install gtts

# 方案2：使用pyttsx3（离线）
pip install pyttsx3
```

#### 生成代码示例
```python
from gtts import gTTS

# 英文告警文本
text = "Warning! Helmet not detected. Please wear safety helmet immediately."

# 生成音频
tts = gTTS(text=text, lang='en', slow=False)
tts.save('helmet_warning_en.mp3')
```

## 临时方案（自动降级）

如果没有音频文件，系统会自动降级：

1. **英文音频缺失** → 尝试播放中文音频
2. **所有音频缺失** → 使用浏览器TTS（文字转语音）
3. **TTS不支持** → 仅显示可视化告警弹窗

语音合成内容格式：
- 中文：`发现{告警类型}告警`
- 英文：`{Alert type} alert detected`

## 系统语言切换

在后台管理界面可以切换系统语言：

1. 访问：http://localhost:5000/admin
2. 点击左侧菜单：**系统设置**
3. 在 **系统语言设置** 中选择：
   - 中文简体（默认）
   - English

切换语言后，告警语音会自动使用对应语言的音频文件。

## 告警声音开关

可以在系统设置中控制是否播放告警声音：

1. 访问：http://localhost:5000/admin
2. 点击左侧菜单：**系统设置**
3. 在 **检测参数配置** 中找到 **告警声音** 开关
4. 点击Toggle按钮切换开启/关闭

## 相关文档

- 📖 [系统语言和告警声音控制说明.md](../../../系统语言和告警声音控制说明.md)
- 📖 [快速使用指南.md](../../../快速使用指南.md)
- 📖 [更新日志.md](../../../更新日志.md)

## 音频编辑工具

如需编辑或优化音频：

1. **Audacity** (免费开源)
   - 下载：https://www.audacityteam.org/
   - 功能：剪辑、调整音量、添加效果

2. **Adobe Audition** (专业)
   - 功能：专业音频编辑
   - 适合高质量音频制作

## 文件清单示例

```
static/audio/alerts/
├── README.md                           (本文件)
├── helmet_warning.mp3                  ✅ 中文-安全帽
├── helmet_warning_en.mp3              ⚠️ 英文-安全帽（待生成）
├── vest_warning.mp3                   ⚠️ 中文-反光衣（待生成）
├── vest_warning_en.mp3                ⚠️ 英文-反光衣（待生成）
├── smoke_warning.mp3                  ⚠️ 中文-烟雾（待生成）
├── smoke_warning_en.mp3               ⚠️ 英文-烟雾（待生成）
└── ...
```

## 测试音频

生成音频后，可以使用测试脚本验证：

```bash
python test_language_and_sound.py
```

---

**更新日期**：2024-02-26  
**版本**：v2.1.0
