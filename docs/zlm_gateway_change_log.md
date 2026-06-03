# ZLM 网关相关改动说明

本文档记录本轮围绕“视频流协议兼容、可选 ZLMediaKit 网关、Windows 测试入口”所做的代码和配置改动。重点说明改了什么、为什么改、删掉或替换了什么逻辑，以及哪些文件是运行时生成的。

## 目标

原项目的视频流处理主链路是：

`视频源配置 -> OpenCV VideoCapture 直接读原始 URL -> AI 模型处理帧 -> Flask MJPEG 预览`

本轮改动后的目标是：

`默认仍走原逻辑；当视频源勾选“使用 ZLM 网关”时，RTSP/RTMP/HTTP-FLV/HLS 等输入先交给 ZLMediaKit 统一代理，再输出为后台 AI 可读的内部地址。`

也就是说，ZLM 是可选网关，不是强制替换原有 OpenCV 直连逻辑。

## 主动修改的文件

### `.env`

改动内容：

- 修正变量名：
  - 原来是 `AFEHAT_ZLM_ENABLED=1`
  - 改为 `SAFEHAT_ZLM_ENABLED=1`
- 补全 ZLM HTTP API 端口：
  - 原来是 `SAFEHAT_ZLM_API_BASE=http://127.0.0.1`
  - 改为 `SAFEHAT_ZLM_API_BASE=http://127.0.0.1:8080`
- 将 `SAFEHAT_ZLM_SECRET` 改为当前 `Release/config.ini` 中实际 secret：
  - `lrvEH0UVeTtNrFlAvRnbKGNeewS4iidA`

原因：

- 原变量名拼错会导致 ZLM 开关读取不到。
- ZLM HTTP 服务已经改为 `8080`，API 地址必须带端口。
- secret 需要和 ZLM 配置一致，否则 HTTP API 会认证失败。

### `Release/config.ini`

改动内容：

- HTTP 端口：
  - `80` 改为 `8080`
- HTTPS 端口：
  - `443` 改为 `8443`
- RTSP 端口：
  - `554` 改为 `8554`

原因：

- `80/443/554` 是低端口，Linux/RK 板普通用户启动 ZLM 时容易因为权限不足失败。
- 改成 `8080/8443/8554` 后更适合普通部署和开发环境。

### `app.py`

改动内容：

- 新增 `load_dotenv_file()`。
- 在导入项目服务模块之前调用 `load_dotenv_file()`。
- 将原来靠后导入的 `os` 提前到文件顶部。
- 保留原有 Flask 启动逻辑，仍然是：
  - `host='0.0.0.0'`
  - `port=5000`

原因：

- `media_gateway_service.py` 在模块导入时会读取环境变量，如果 `.env` 没提前加载，ZLM 配置不会生效。
- 项目没有使用 `python-dotenv`，所以加了一个简单的 KEY=VALUE 加载函数。

没有删除业务逻辑，只是移动/提前了环境变量加载。

### `server/services/zlm_process_service.py`

改动内容：

- 新增 `configparser`。
- 新增 `_read_zlm_config_value()`，用于读取 `config.ini` 中的端口配置。
- `bootstrap_zlm_for_app()` 中：
  - 原来默认 API 地址是 `http://127.0.0.1`
  - 现在会从 `config.ini` 读取 HTTP 端口，默认拼出 `http://127.0.0.1:<http_port>`
  - 原来默认 RTSP 端口是 `554`
  - 现在从配置读取，默认 `8554`
  - 原来默认 HTTP 端口是 `80`
  - 现在从配置读取，默认 `8080`

原因：

- 避免代码端口和 ZLM 配置端口不一致。
- 让 Linux/RK 和 Windows 都能通过 `SAFEHAT_ZLM_CONFIG` 指向不同配置文件。

### `server/services/media_gateway_service.py`

这是本轮最核心的改动。

改动内容：

- 新增读取 ZLM 配置的能力：
  - `_read_config_value()`
  - `_refresh_settings()`
- 新增局域网 IP 探测函数：
  - `_detect_lan_ip()`
- 新增 `GatewayResult.gateway_key` 字段。
- 移除原先类初始化里的固定 `self.enabled = _truthy(os.getenv("SAFEHAT_ZLM_ENABLED"))` 使用方式。
- `should_proxy()` 改为：
  - 不再只看全局 `SAFEHAT_ZLM_ENABLED`
  - 只要视频源配置里 `config.use_media_gateway=true`，且没有设置 `SAFEHAT_ZLM_DISABLED`，就尝试走 ZLM。
- `ensure_proxy()` 中新增：
  - 调用 `bootstrap_zlm_for_app()`，按需启动 ZLM。
  - 启动后重新刷新配置。
  - 调用 `_add_stream_proxy()` 后保存 ZLM 返回的 `key`。
- 新增 `close_proxy(app, stream, key="")`：
  - 用于删除 ZLM 中的 stream proxy。
  - 优先使用 ZLM `addStreamProxy` 返回的 `key` 删除。
  - 没有 key 时退回 `__defaultVhost__/app/stream` 格式。
- `_add_stream_proxy()` 改为：
  - 添加代理前先尝试清理同名旧代理。
  - 返回 ZLM 代理 key。
- 新增内部读流 host 策略：
  - `SAFEHAT_ZLM_INTERNAL_HOST`：显式指定后台 AI 读流 host。
  - `SAFEHAT_ZLM_INTERNAL_HOST_MODE`：
    - `public`：默认，跟随 `SAFEHAT_ZLM_PUBLIC_HOST`
    - `loopback`：使用 `127.0.0.1`
    - `lan`：自动探测局域网 IP
    - `api`：跟随 `SAFEHAT_ZLM_API_BASE` 的 host
- ZLM 输出 URL 分为：
  - `play_url`：后台 AI 使用，走 `internal_host`
  - `preview_url`：前端/外部预览使用，走 `public_host`

原因：

- 让 ZLM 变成真正的“可选协议网关”。
- 避免更新/删除视频源后，ZLM 内残留旧代理。
- 避免 Windows 虚拟网卡/VPN 把 AI 读流地址误判成 `198.18.x.x` 一类地址。
- 同时又不把 IP 写死，后续可通过环境变量适配 RK/Linux/Windows/其他网络。

替换/删除的逻辑：

- 删除了“只在初始化时读取一次 enabled 状态”的限制。
- 替换了原来只用 `public_host` 构造 `play_url` 的逻辑，现在 `play_url` 和 `preview_url` 使用不同 host 策略。

### `server/services/video_source_service.py`

改动内容：

- `VideoSource` 数据模型新增字段：
  - `gateway_key`
- `VideoSource.from_dict()` 增加 `gateway_key` 读取。
- `_build_source()` 增加 `gateway_key` 写入。
- `_build_source()` 中移除了末尾立即调用 `_sync_runtime_urls(source)` 的逻辑。
- `add_source()` 仍然在分配真实 `source.id` 后调用 `_sync_runtime_urls(source)`。
- `update_source()` 改为：
  - 先记录旧的 `gateway/gateway_app/gateway_stream/gateway_key/origin_url`。
  - 生成 updated 对象并设置原 ID。
  - 再调用 `_sync_runtime_urls(updated)`。
  - 如果旧 ZLM 代理发生变化或关闭，则调用 `media_gateway.close_proxy()` 清理旧代理。
- `delete_source()` 改为：
  - 删除视频源配置后，如果旧源走 ZLM，则调用 `media_gateway.close_proxy()` 删除对应 ZLM 代理。
- `check_connection()` 中对临时源也调用 `_sync_runtime_urls(temp_source)`。
- `_sync_runtime_urls()` 中：
  - ZLM 成功时保存 `gateway_key`。
  - ZLM 未启用或失败时清空 `gateway/gateway_app/gateway_stream/gateway_key`。

原因：

- 新建视频源时，必须等 ID 分配后再生成 `source_真实ID` 的 ZLM stream 名。
- 编辑视频源时如果提前在 `_build_source()` 里同步 URL，可能使用旧 ID/旧状态，逻辑不清晰。
- `gateway_key` 是 ZLM 删除代理最可靠的依据。
- 关闭 ZLM 开关、改原始 URL、删除视频源时，需要清理旧代理。

替换/删除的逻辑：

- 删除了 `_build_source()` 内部自动同步运行时 URL 的行为。
- 改为由 `add_source()`、`update_source()`、`check_connection()` 明确决定何时同步。

### `templates/admin.html`

改动内容：

- 摄像头表单新增复选框：
  - `cameraUseGateway`
  - 显示文字：`使用 ZLM 网关`
- 视频流表单新增复选框：
  - `streamUseGateway`
  - 显示文字：`使用 ZLM 网关`
- 视频源列表中：
  - 地址显示从优先 `video_url` 改为优先 `origin_url`。
  - 如果 `source.gateway === 'zlm'`，在名称旁显示 `ZLM` 标记。
- 编辑摄像头时：
  - 地址输入框优先回填 `origin_url`，避免把内部代理地址回填给用户。
  - 回显 `cameraUseGateway` 状态。
- 编辑视频流时：
  - 地址输入框优先回填 `origin_url`。
  - 回显 `streamUseGateway` 状态。
- 新增摄像头表单重置时关闭 `cameraUseGateway`。
- 测试连接时：
  - 将 `use_media_gateway` 带给后端。
  - 萤石 quick check 也带 `use_media_gateway`。
- 保存摄像头时：
  - 将 `use_media_gateway` 写入 `sourceData.config`。
- 保存视频流时：
  - 将 `use_media_gateway` 写入 `sourceData.config`。
  - 自动根据 URL scheme 写 `sourceData.protocol`，例如 `RTSP`、`RTMP`、`HTTP`。

原因：

- 用户可以逐路选择是否启用 ZLM。
- 默认不勾选时，保持原来的直连逻辑。
- 编辑时必须显示原始地址，否则用户看到的是 `rtsp://127.0.0.1:8554/safehat/source_x` 这类内部代理地址，容易误改。

### `safehat/managers/mqtt_log_handler.py`

改动内容：

- 新增：
  - `from typing import Optional`
- 将 Python 3.10+ 类型写法：
  - `threading.Thread | None`
  - `MqttLogHandler | None`
- 改成 Python 3.8/3.9 兼容写法：
  - `Optional[threading.Thread]`
  - `Optional[MqttLogHandler]`

原因：

- Windows 测试环境运行时报错：
  - `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`
- 说明当前 Python 版本低于 3.10，不支持 PEP 604 的 `|` 类型联合语法。

### `app_windows.py`

新增文件，基于 `app.py` 复制后扩展。

新增内容：

- `detect_lan_ip()`：
  - 尝试探测本机局域网 IP。
  - 用于启动提示和 Windows 版 ZLM public host。
- `configure_windows_zlm()`：
  - 使用 `Release01/MediaServer.exe`。
  - 使用 `Release01/config.ini` 作为模板。
  - 生成运行时配置文件：`data/zlm_windows_config.ini`。
  - 将运行时 ZLM 配置改为：
    - HTTP: `8080`
    - HTTPS: `8443`
    - RTSP: `8554`
  - 将运行时环境变量设置为：
    - `SAFEHAT_ZLM_BIN`
    - `SAFEHAT_ZLM_CONFIG`
    - `SAFEHAT_ZLM_API_BASE`
    - `SAFEHAT_ZLM_SECRET`
    - `SAFEHAT_ZLM_HTTP_PORT`
    - `SAFEHAT_ZLM_RTSP_PORT`
    - `SAFEHAT_ZLM_PUBLIC_HOST`
    - `SAFEHAT_ZLM_INTERNAL_HOST_MODE=loopback`
- Windows 版 Flask 启动端口为 `5050`。
- 启动日志中局域网访问地址改为真实探测到的 IP，而不是 `0.0.0.0`。

原因：

- 用户提供了 `Release01` 作为 Windows 版 ZLM 目录。
- 不希望直接修改 `Release01/config.ini`，所以生成 `data/zlm_windows_config.ini` 作为运行时配置。
- Windows 上可能存在 VPN/虚拟网卡，后台 AI 读流默认用 `loopback` 更稳。

注意：

- `app_windows.py` 是 Windows 测试入口。
- RK/Linux 部署仍然使用 `app.py`。

## 新增或引入但非代码逻辑的文件/目录

### `Release01/`

状态：

- 新增目录，用户提供的 Windows 版 ZLMediaKit Release。
- 本轮没有直接修改其中的文件。

用途：

- `app_windows.py` 会使用其中的 `MediaServer.exe` 和 `config.ini`。

### `data/zlm_windows_config.ini`

状态：

- 运行 `app_windows.py` 时生成。

用途：

- Windows 版 ZLM 运行时配置。
- 由 `Release01/config.ini` 模板生成。
- 端口和 secret 由 `app_windows.py` 写入。

是否需要提交：

- 如果希望 Windows 环境直接复现配置，可以保留。
- 如果只想提交源码，可以把它视为运行时生成文件，不提交。

## 运行或测试过程中产生的变化

### `data/video_sources.json`

状态：

- 当前工作区中该文件有变化。

变化来源：

- 一部分是代码新增字段 `gateway_key` 后，视频源保存/检测时自动写入。
- 一部分是实际在后台新增、删除、编辑视频源导致的业务数据变化，例如测试 RTMP/ZLM 的 `source_19`。

注意：

- 这个文件是业务数据，不全是代码改动。
- 如果只需要提交代码能力，建议人工确认是否保留这些测试视频源变化。

### `data/algorithm_config.json`

状态：

- 当前工作区中该文件有变化。

变化来源：

- 可能由后台页面或运行过程保存算法配置产生。

注意：

- 这属于业务配置数据，不是本轮 ZLM 代码改动的核心。

### `__pycache__/*.pyc`

状态：

- 当前工作区里出现了多个 Python 3.9 的 `.pyc` 文件。

变化来源：

- 运行 `app_windows.py` 或导入模块时自动生成。

建议：

- 不应提交。
- 可后续统一清理或加入 `.gitignore`。

## 行为说明

### 默认行为

未勾选“使用 ZLM 网关”时：

`origin_url = 用户输入地址`

`play_url = origin_url`

`preview_url = play_url`

后台 AI 仍然使用 OpenCV 直接读原始视频地址。

### 启用 ZLM 网关后

勾选“使用 ZLM 网关”时：

1. 后端调用 `bootstrap_zlm_for_app()` 启动或复用 ZLM。
2. 调用 ZLM `/index/api/addStreamProxy`。
3. 保存：
   - `origin_url`：用户输入的原始地址
   - `play_url`：后台 AI 读流地址
   - `preview_url`：前端预览地址
   - `gateway=zlm`
   - `gateway_app`
   - `gateway_stream`
   - `gateway_key`
4. 检测线程继续使用 `play_url`，算法代码不需要关心原协议。

## 环境变量说明

### ZLM 基础配置

```env
SAFEHAT_ZLM_ENABLED=1
SAFEHAT_ZLM_API_BASE=http://127.0.0.1:8080
SAFEHAT_ZLM_SECRET=...
SAFEHAT_ZLM_RTSP_PORT=8554
SAFEHAT_ZLM_HTTP_PORT=8080
```

### ZLM host 策略

```env
SAFEHAT_ZLM_PUBLIC_HOST=192.168.1.100
```

用于生成前端/外部访问的预览地址。

```env
SAFEHAT_ZLM_INTERNAL_HOST=127.0.0.1
```

显式指定后台 AI 读流 host。设置后优先级最高。

```env
SAFEHAT_ZLM_INTERNAL_HOST_MODE=public
```

未显式设置 `SAFEHAT_ZLM_INTERNAL_HOST` 时使用，可选：

- `public`：跟随 `SAFEHAT_ZLM_PUBLIC_HOST`
- `loopback`：使用 `127.0.0.1`
- `lan`：自动探测局域网 IP
- `api`：跟随 `SAFEHAT_ZLM_API_BASE` 的 host

### 禁用 ZLM

```env
SAFEHAT_ZLM_DISABLED=1
```

即使视频源勾选了“使用 ZLM 网关”，也不会走 ZLM。

## Windows 使用方式

启动：

```powershell
python app_windows.py
```

访问：

```text
http://localhost:5050
http://<Windows局域网IP>:5050
```

Windows 入口会：

- 使用 `Release01/MediaServer.exe`
- 生成 `data/zlm_windows_config.ini`
- 将后台 AI 内部读流策略设为 `loopback`

## Linux/RK3576 使用方式

启动：

```bash
python3 app.py
```

建议确认：

```bash
chmod +x Release/MediaServer
```

ZLM 端口已经改为：

- HTTP `8080`
- HTTPS `8443`
- RTSP `8554`

## 已做验证

做过 Python 语法编译检查：

```text
syntax ok
```

检查过 Windows 版 ZLM 日志，确认：

- RTMP 输入源注册成功。
- `addStreamProxy` 返回 `code: 0`。
- ZLM 已生成 RTMP/FMP4/RTSP/TS/HLS 多协议媒体源。

## 已知问题和后续建议

### Windows 模型加载问题

日志中出现：

```text
[InferEngine] ❌ 引擎未加载: helmet_detection
None of PyTorch, TensorFlow >= 2.0, or Flax have been found
```

说明 Windows 环境模型依赖或推理后端未准备好。这和 ZLM 协议代理无关。

建议：

- Windows 端可先作为协议兼容测试环境。
- 真正模型推理可继续放在 RK3576/Linux 上。

### H264 解码告警

日志中出现：

```text
co located POCs unavailable
error while decoding MB
missing picture in access unit
```

可能原因：

- 刚接入时没有完整关键帧。
- RTMP 推流源 GOP/关键帧间隔不稳定。
- OpenCV/FFmpeg 对该流容错较差。

后续可选优化：

- 让推流端固定 H264，GOP 1-2 秒。
- 后台 AI 读流从 RTSP 改为 HTTP-FLV 作为可选策略。
- 增加 `SAFEHAT_ZLM_AI_OUTPUT_PROTOCOL=rtsp|flv` 一类配置。

### 需要清理的非源码文件

建议不要提交：

- `__pycache__/`
- `*.pyc`

是否提交需人工确认：

- `data/video_sources.json`
- `data/algorithm_config.json`
- `data/zlm_windows_config.ini`

