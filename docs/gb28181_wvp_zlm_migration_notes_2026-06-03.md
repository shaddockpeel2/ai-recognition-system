# GB28181 / WVP / ZLM 移植操作记录

日期：2026-06-03

## 主要操作

1. 统一 RK3568 开发板网络地址
   - 项目从 RK3588 移植到 RK3568 后，开发板地址调整为 `192.168.1.17`。
   - 检查 GB28181、WVP、ZLM 相关配置，发现仍存在旧板地址残留。
   - 将 WVP SIP 地址、ZLM 对外地址、ZLM 推流地址统一到当前开发板 IP。

   ```bash
   cd /home/admin/Desktop/safehat
   grep -R "192\.168\.1\.11\|192\.168\.1\.19\|192\.168\.1\.17" -n .env deploy/wvp/application-safehat.yml Release/config.ini server/services 2>/dev/null
   grep -R "SAFEHAT_GB_SIP_SERVER_IP\|SAFEHAT_ZLM_PUBLIC_HOST\|SAFEHAT_ZLM_PUSH_HOST" -n .env server/services 2>/dev/null
   ```

   ```text
   当前开发板 IP：192.168.1.17
   原残留地址示例：192.168.1.11、192.168.1.19
   ```

2. 修正 GB28181 与 WVP 配置
   - 修改 `.env`，补齐 GB28181 SIP 与 ZLM 地址覆盖项。
   - 修改 `deploy/wvp/application-safehat.yml`，将 `sip.ip` 和 `media.ip` 调整为当前开发板 IP。
   - 按要求保留 GB28181 传输方式为 `UDP`，未切换为 TCP。

   ```text
   SAFEHAT_GB_SIP_SERVER_IP=192.168.1.17
   SAFEHAT_ZLM_PUBLIC_HOST=192.168.1.17
   SAFEHAT_ZLM_PUSH_HOST=192.168.1.17

   sip.ip: 192.168.1.17
   sip.transport: UDP
   media.ip: 192.168.1.17
   ```

3. 核对 ZLM 运行配置
   - 确认 ZLM 实际运行配置文件为 `Release/config.ini`。
   - 确认 `.env` 中的 `SAFEHAT_ZLM_SECRET` 与 `Release/config.ini` 的 `http.secret` 一致。
   - ZLM 已可启动，HTTP API 端口 `8080` 可连接。

   ```bash
   cd /home/admin/Desktop/safehat
   grep -n "^secret\|^port\|^http" Release/config.ini
   grep -n "SAFEHAT_ZLM_SECRET\|SAFEHAT_ZLM_PUBLIC_HOST\|SAFEHAT_ZLM_PUSH_HOST" .env
   ps -ef | grep -E "MediaServer|ZLMediaKit" | grep -v grep
   curl -s "http://127.0.0.1:8080/index/api/getServerConfig?secret=eECYnFPVN66GI3b0g7mvXNtSF5rkR4xk" | head -c 300
   ```

   ```text
   ZLM 配置文件：/home/admin/Desktop/safehat/Release/config.ini
   ZLM secret：eECYnFPVN66GI3b0g7mvXNtSF5rkR4xk
   ZLM API：http://127.0.0.1:8080
   ZLM 对外地址：192.168.1.17
   ```

4. 修复 WVP Java 启动环境
   - WVP 初始失败原因包含 `No such file or directory: 'java'`。
   - 使用项目内 JDK：`/home/admin/Desktop/safehat/jdk-21.0.11/bin/java`。
   - 修复 JDK 执行权限，解决 `Permission denied` 问题。
   - 修改 WVP 启动逻辑，默认优先使用项目内 JDK，同时保留环境变量覆盖能力。

   ```bash
   cd /home/admin/Desktop/safehat
   ls -l jdk-21.0.11/bin/java
   ./jdk-21.0.11/bin/java -version
   chmod -R u+X "/home/admin/Desktop/safehat/jdk-21.0.11/bin" "/home/admin/Desktop/safehat/jdk-21.0.11/lib"
   chmod u+x "/home/admin/Desktop/safehat/jdk-21.0.11/bin/java"
   ./jdk-21.0.11/bin/java -version
   ```

   ```text
   SAFEHAT_WVP_JAVA_BIN=/home/admin/Desktop/safehat/jdk-21.0.11/bin/java
   ```

5. 验证 WVP 启动脚本与服务模块
   - 修改 `server/services/wvp_process_service.py`，默认优先查找项目内 JDK。
   - 修改 `deploy/wvp/start_wvp.sh`，手动启动 WVP 时也优先使用项目内 JDK。
   - 已完成脚本语法和 Python 模块编译检查。

   ```bash
   cd /home/admin/Desktop/safehat
   bash -n deploy/wvp/start_wvp.sh
   python3 -m py_compile server/services/wvp_process_service.py
   ```

6. 定位 WVP 当前阻塞点
   - Java 路径问题已解决后，WVP 仍未监听 `18080`。
   - WVP 日志显示 MySQL 连接失败：`127.0.0.1:3306 Connection refused`。
   - 同时检查发现 Redis、WVP Web/API、WVP SIP 端口均未进入监听状态。
   - 当前 `/api/v1/device/list` 请求失败的直接原因是 WVP 未启动完成。

   ```bash
   cd /home/admin/Desktop/safehat
   ps -ef | grep -E "wvp|java|MediaServer|app.py" | grep -v grep
   grep -n "Connection refused\|3306\|6379\|18080\|15060\|Exception\|ERROR" logs/wvp.log | tail -n 80
   curl -sS "http://127.0.0.1:18080/api/v1/device/list" || true
   ```

   ```bash
   python3 - <<'PY'
   import socket

   for host, port in [
       ('127.0.0.1', 3306),
       ('127.0.0.1', 6379),
       ('127.0.0.1', 18080),
       ('127.0.0.1', 15060),
   ]:
       sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       sock.settimeout(1)
       ok = sock.connect_ex((host, port)) == 0
       sock.close()
       print(f'{host}:{port} tcp_listen={ok}')
   PY
   ```

   ```text
   127.0.0.1:3306  tcp_listen=False
   127.0.0.1:6379  tcp_listen=False
   127.0.0.1:18080 tcp_listen=False
   127.0.0.1:15060 tcp_listen=False
   ```

   ```text
   WVP 请求失败：HTTPConnectionPool(host='127.0.0.1', port=18080)
   根因：WVP 依赖的 MySQL / Redis 未就绪，导致 WVP 未正常监听 18080。
   ```

7. 整理 WVP 数据库与 Redis 目标配置
   - WVP 当前配置使用本机 MySQL 和本机 Redis。
   - MySQL 目标数据库为 `wvp`，账号为 `wvp`，密码为 `wvp123456`。
   - Redis 使用 `127.0.0.1:6379`，数据库编号为 `7`。
   - 已确认项目内存在 WVP MySQL 初始化 SQL 和更新 SQL。

   ```bash
   cd /home/admin/Desktop/safehat
   grep -n "jdbc:mysql\|redis:\|database:\|username:\|password:" deploy/wvp/application-safehat.yml
   find wvp-GB28181-pro/数据库 -maxdepth 3 -type f | sort
   grep -n "mysql\|redis\|3306\|6379" wvp-GB28181-pro/docker/docker-compose.yml
   ```

   ```text
   MySQL：127.0.0.1:3306
   Database：wvp
   Username：wvp
   Password：wvp123456

   Redis：127.0.0.1:6379
   Redis database：7
   ```

   ```text
   /home/admin/Desktop/safehat/wvp-GB28181-pro/数据库/2.7.4/初始化-mysql-2.7.4.sql
   /home/admin/Desktop/safehat/wvp-GB28181-pro/数据库/2.7.4/更新-mysql-2.7.4.sql
   /home/admin/Desktop/safehat/wvp-GB28181-pro/数据库/2.7.4-h2/h2-schema.sql
   /home/admin/Desktop/safehat/wvp-GB28181-pro/数据库/2.7.4-h2/h2-data.sql
   ```

## 当前状态

- Flask 主服务已运行。
- ZLM 已运行，`8080` API 可连接。
- GB28181 配置已统一到 RK3568 当前 IP：`192.168.1.17`。
- GB28181 传输方式保持 `UDP`。
- WVP 的 Java 启动路径问题已修复。
- WVP 当前未正常监听 `18080`，接口请求仍会返回连接拒绝。
- 当前主要阻塞点是 MySQL 和 Redis 未启动或未完成初始化。

## 后续建议

1. 安装并启动本机 MySQL 与 Redis
   - MySQL 监听 `127.0.0.1:3306`。
   - Redis 监听 `127.0.0.1:6379`。
   - 如果 Redis 设置密码，需要同步调整 WVP 配置。
   - Debian / Ubuntu / ARM Linux 环境优先使用系统包安装，避免额外引入不必要的运行方式。

   ```bash
   sudo apt update
   sudo apt install -y default-mysql-server redis-server
   ```

   - 如果系统包名不同，可先查询可用包名。

   ```bash
   apt-cache search mysql-server | head
   apt-cache search redis-server | head
   ```

   - 启动 MySQL 和 Redis，并设置开机自启。

   ```bash
   sudo systemctl enable --now mysql
   sudo systemctl enable --now redis-server
   ```

   - 如果当前系统没有 `systemctl`，可使用服务脚本启动。

   ```bash
   sudo service mysql start
   sudo service redis-server start
   ```

   - 检查 MySQL 与 Redis 是否已监听本机端口。

   ```bash
   ss -lntp | grep -E ':3306|:6379'
   mysqladmin ping -h127.0.0.1
   redis-cli -h 127.0.0.1 -p 6379 ping
   ```

   - MySQL 建议只监听本机地址，与 WVP 当前配置保持一致。

   ```bash
   sudo grep -R "^bind-address" -n /etc/mysql /etc/my.cnf 2>/dev/null
   ```

   目标配置：

   ```text
   bind-address = 127.0.0.1
   ```

   修改后重启 MySQL：

   ```bash
   sudo systemctl restart mysql || sudo service mysql restart
   ```

   - Redis 建议只监听本机地址，且当前 WVP 配置默认无密码。

   ```bash
   sudo grep -n "^bind\|^port\|^requirepass\|^databases" /etc/redis/redis.conf
   ```

   目标配置：

   ```text
   bind 127.0.0.1 ::1
   port 6379
   databases 16
   # requirepass 不启用
   ```

   修改后重启 Redis：

   ```bash
   sudo systemctl restart redis-server || sudo service redis-server restart
   redis-cli -h 127.0.0.1 -p 6379 ping
   ```

2. 初始化 WVP 数据库
   - 创建 `wvp` 数据库和 `wvp` 用户。
   - 授权 `wvp` 用户访问 `wvp` 数据库。
   - 导入 WVP 2.7.4 MySQL 初始化 SQL。

   ```bash
   sudo mysql
   ```

   ```sql
   CREATE DATABASE wvp DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
   CREATE USER 'wvp'@'127.0.0.1' IDENTIFIED BY 'wvp123456';
   GRANT ALL PRIVILEGES ON wvp.* TO 'wvp'@'127.0.0.1';
   FLUSH PRIVILEGES;
   ```

   - 也可以直接用一条命令完成数据库和用户初始化。

   ```bash
   sudo mysql <<'SQL'
   CREATE DATABASE IF NOT EXISTS wvp DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
   CREATE USER IF NOT EXISTS 'wvp'@'127.0.0.1' IDENTIFIED BY 'wvp123456';
   GRANT ALL PRIVILEGES ON wvp.* TO 'wvp'@'127.0.0.1';
   FLUSH PRIVILEGES;
   SQL
   ```

   ```bash
   mysql -h127.0.0.1 -uwvp -pwvp123456 wvp < "/home/admin/Desktop/safehat/wvp-GB28181-pro/数据库/2.7.4/初始化-mysql-2.7.4.sql"
   ```

   - 导入后验证 WVP 表是否已创建。

   ```bash
   mysql -h127.0.0.1 -uwvp -pwvp123456 -e "USE wvp; SHOW TABLES;"
   ```

3. 重启 WVP 并检查端口
   - 重启 WVP 后检查 `18080` 是否监听。
   - 检查 SIP 端口 `15060` 是否监听。
   - 再通过 SafeHat 页面或接口检查 GB28181 设备列表。

   ```bash
   cd /home/admin/Desktop/safehat
   bash deploy/wvp/start_wvp.sh
   curl -sS "http://127.0.0.1:18080/api/v1/device/list" || true
   ```

   ```text
   WVP Web/API：127.0.0.1:18080
   WVP SIP：192.168.1.17:15060/UDP
   ZLM API：http://127.0.0.1:8080
   ```

4. 注册摄像头 GB28181 平台参数
   - 摄像头平台 IP 填写 `192.168.1.17`。
   - 平台 SIP 端口填写 `15060`。
   - 传输协议选择 `UDP`。
   - 平台编号、设备编号、认证密码需与 WVP 配置保持一致。