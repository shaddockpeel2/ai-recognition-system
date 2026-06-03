# 项目移植操作记录

日期：2026-06-03

## 主要操作

1. 清理项目历史数据
   - 盘点项目目录体积，确认 `.git` 为主要占用项。
   - 按要求删除 `.git` 目录，释放约 3.2G 空间。
   - 保留项目源码、模型、配置和运行文件，后续可重新初始化 Git 仓库。

   ```bash
   du -ah . 2>/dev/null | sort -hr | head -n 80
   du -sh ./* ./.??* 2>/dev/null | sort -hr | head -n 80
   rm -rf .git && test ! -d .git
   ```

2. 排查 ONVIF 连接问题
   - 确认 `requirements.txt` 已声明 `onvif-zeep>=0.2.12`。
   - 发现 `onvif-zeep` 可导入，但缺少 `wsdl/devicemgmt.wsdl`，导致 ONVIF 连接失败。
   - 判断原因是 `onvif-zeep` 安装包未携带 WSDL 资源。

   ```bash
   /usr/bin/python3 -m pip show onvif-zeep zeep lxml

   /usr/bin/python3 - <<'PY'
   import importlib.util
   from pathlib import Path

   spec = importlib.util.find_spec('onvif')
   print('onvif:', spec.origin if spec else 'NOT_FOUND')
   if spec:
       base = Path(spec.origin).parent
       print('onvif/wsdl:', (base / 'wsdl').exists())
       print('site-packages/wsdl:', (base.parent / 'wsdl').exists())
       print('devicemgmt:', (base.parent / 'wsdl' / 'devicemgmt.wsdl').exists())
   PY
   ```

3. 安装和修复 ONVIF 依赖
   - 安装 `lxml`，解决 `zeep` XML 解析依赖。
   - 普通用户安装 `onvif-zeep` 时遇到 `/usr/local/bin/onvif-cli` 权限不足，改用 `sudo`。
   - 从 GitHub 源码安装后，仍确认 WSDL 未被打包进安装目录。
   - 最终建议手动复制 `wsdl` 资源到 Python 安装目录。

   ```bash
   /usr/bin/python3 -m pip install lxml==4.9.2 -i https://pypi.tuna.tsinghua.edu.cn/simple
   /usr/bin/python3 -m pip install 'onvif-zeep>=0.2.12' -i https://pypi.tuna.tsinghua.edu.cn/simple
   sudo /usr/bin/python3 -m pip install 'onvif-zeep>=0.2.12' -i https://pypi.tuna.tsinghua.edu.cn/simple

   sudo /usr/bin/python3 -m pip uninstall -y onvif-zeep
   sudo /usr/bin/python3 -m pip install --no-cache-dir git+https://github.com/FalkTannhaeuser/python-onvif-zeep.git

   cd /tmp
   rm -rf python-onvif-zeep
   git clone https://github.com/FalkTannhaeuser/python-onvif-zeep.git
   sudo cp -r python-onvif-zeep/wsdl /usr/local/lib/python3.8/dist-packages/
   sudo chmod -R a+rX /usr/local/lib/python3.8/dist-packages/wsdl
   ```

4. 处理 Python 3.8 兼容问题
   - ONVIF 依赖报错：`module 'datetime' has no attribute 'UTC'`。
   - 在 `server/services/onvif_service.py` 增加兼容处理，将 `datetime.UTC` 映射到 `datetime.timezone.utc`。
   - 已验证 ONVIF 服务模块可正常导入。

   ```bash
   /usr/bin/python3 - <<'PY'
   from server.services.onvif_service import onvif_service
   import datetime

   print('datetime.UTC:', getattr(datetime, 'UTC', None))
   print('onvif available:', onvif_service.is_available())
   PY
   ```

   验证结果：

   ```text
   datetime.UTC: UTC
   onvif available: True
   ```

5. 记录 Swap 操作
   - 已开始创建 `/mnt/swapfile`。
   - 后续需执行 `chmod`、`mkswap`、`swapon` 并通过 `free -h` 验证。

   ```bash
   sudo fallocate -l 1G /mnt/swapfile
   sudo chmod 600 /mnt/swapfile
   sudo mkswap /mnt/swapfile
   sudo swapon /mnt/swapfile
   free -h
   ```

## 当前状态

- Git 历史已清除，可重新建仓。
- ONVIF 依赖已进入可导入状态。
- 剩余阻塞点主要是 WSDL 资源补齐和真实摄像头连接验证。
- 如急需接入视频，可先手动填写 RTSP 地址绕过 ONVIF 自动解析。
