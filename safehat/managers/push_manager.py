"""
数据推送管理器

负责将告警信息推送到手机APP端和硬件传感器端
支持多种协议: APP、MODBUS、UART等
"""

import json
import logging
import time
import threading
import socket
# ======================
# 设备 & 客户身份配置
# ======================
CUSTOMER_ID = "cust_001"
DEVICE_ID   = "rk3568_0001"
PROJECT_ID  = "gate_01"
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime
import queue

logger = logging.getLogger(__name__)
_MQTT_CONFIG_PATH = Path(__file__).resolve().parents[2] / 'data' / 'mqtt_config.json'


def _load_global_mqtt_defaults() -> Dict[str, Any]:
    defaults = {
        'MQBroker': 'localhost',
        'MQPort': 1883,
        'MQRootTopic': 'safehat',
        'EnableLogUploader': False,
        'SecPingInterval': 60,
        'MQIdPattern': '',
        'MQClientId': '',
        'MQUsername': '',
        'MQPassword': '',
        'MQUniqueSubscribe': False,
        'LogLevel': 'ERROR'
    }
    try:
        if _MQTT_CONFIG_PATH.exists():
            with open(_MQTT_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                defaults.update(data)
    except Exception:
        pass
    return defaults


def _save_legacy_mqtt_config(config: Dict[str, Any]) -> None:
    try:
        _MQTT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_MQTT_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error(f"保存兼容 MQTT 配置失败: {exc}")


def _build_client_id(pattern: str) -> str:
    host = socket.gethostname()
    if not pattern:
        return f'safehat-{host}'
    return pattern.replace('{hostname}', host).replace('{host}', host)


class PushTarget:
    """推送目标配置"""
    def __init__(self, config: Dict[str, Any]):
        self.id = config.get('id', '')
        self.name = config.get('name', '')
        self.enabled = config.get('enabled', True)
        self.type = config.get('type', 'app')  # app / modbus / uart
        self.connection = config.get('connection', {})
        self.filter = config.get('filter', {})
        self.retry = config.get('retry', {'enabled': True, 'max_retries': 3, 'retry_interval': 5})
        self.rate_limit = config.get('rate_limit', {'enabled': True, 'max_per_minute': 10})
        self.last_push_time = 0
        self.push_count = 0
        self.push_count_reset_time = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'enabled': self.enabled,
            'type': self.type,
            'connection': self.connection,
            'filter': self.filter,
            'retry': self.retry,
            'rate_limit': self.rate_limit
        }


class BasePushHandler:
    """推送处理器基类"""
    
    def __init__(self, target: PushTarget):
        self.target = target
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.last_error: str = ''
    
    def should_push(self, alert_data: Dict[str, Any]) -> bool:
        """判断是否应该推送"""
        if not self.target.enabled:
            return False
        
        # 检查告警类型过滤
        filter_config = self.target.filter
        alert_types = filter_config.get('alert_types', [])
        if alert_types and alert_data.get('type') not in alert_types:
            return False
        
        # 检查风险等级过滤
        risk_levels = filter_config.get('risk_levels', [])
        if risk_levels and alert_data.get('risk_level') not in risk_levels:
            return False
        
        # 检查视频源过滤
        video_sources = filter_config.get('video_sources', [])
        if video_sources and alert_data.get('video_source_id') not in video_sources:
            return False
        
        # 检查时间范围过滤
        time_range = filter_config.get('time_range', {})
        if time_range.get('enabled'):
            current_time = datetime.now().strftime('%H:%M')
            start_time = time_range.get('start', '00:00')
            end_time = time_range.get('end', '23:59')
            if not (start_time <= current_time <= end_time):
                return False
        
        # 检查推送频率限制
        rate_limit = self.target.rate_limit
        if rate_limit.get('enabled'):
            current_time = time.time()
            # 每分钟重置计数
            if current_time - self.target.push_count_reset_time > 60:
                self.target.push_count = 0
                self.target.push_count_reset_time = current_time
            
            max_per_minute = rate_limit.get('max_per_minute', 10)
            if self.target.push_count >= max_per_minute:
                self.logger.warning(f"推送目标 {self.target.name} 达到频率限制")
                return False
        
        return True
    
    def push(self, alert_data: Dict[str, Any]) -> bool:
        """推送数据（子类实现）"""
        raise NotImplementedError
    
    def test_connection(self) -> Dict[str, Any]:
        """测试连接（子类实现）"""
        raise NotImplementedError
    
    def format_payload(self, alert_data: Dict[str, Any]) -> Any:
        """格式化推送数据（子类实现）"""
        raise NotImplementedError


class AppPushHandler(BasePushHandler):
    """手机APP推送处理器"""
    
    def __init__(self, target: PushTarget):
        super().__init__(target)
        self.method = target.connection.get('method', 'webhook')  # webhook / websocket / mqtt
    
    def push(self, alert_data: Dict[str, Any]) -> bool:
        """推送到APP"""
        try:
            payload = self.format_payload(alert_data)
            
            if self.method == 'webhook':
                return self._push_webhook(payload)
            elif self.method == 'websocket':
                return self._push_websocket(payload)
            elif self.method == 'mqtt':
                return self._push_mqtt(payload)
            else:
                self.logger.error(f"不支持的推送方式: {self.method}")
                return False
            
        except Exception as e:
            self.logger.error(f"APP推送失败: {e}")
            return False
    
    def _push_webhook(self, payload: Dict) -> bool:
        """HTTP Webhook推送"""
        import requests
        
        url = self.target.connection.get('url')
        headers = self.target.connection.get('headers', {})
        timeout = self.target.connection.get('timeout', 10)
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code == 200:
                self.logger.info(f"Webhook推送成功: {self.target.name}")
                return True
            else:
                self.logger.error(f"Webhook推送失败: {response.status_code}")
                return False
        except requests.RequestException as e:
            self.logger.error(f"Webhook请求失败: {e}")
            return False
    
    def _push_websocket(self, payload: Dict) -> bool:
        """WebSocket推送"""
        # TODO: 实现WebSocket推送
        self.logger.warning("WebSocket推送尚未实现")
        return False
    
    def _push_mqtt(self, payload: Dict) -> bool:
        """MQTT推送"""
        try:
            import paho.mqtt.client as mqtt
            
            broker = self.target.connection.get('broker', 'localhost')
            port = self.target.connection.get('port', 1883)
            topic = self.target.connection.get('topic', 'safehat')
            client_id = self.target.connection.get('client_id', '').strip()
            username = self.target.connection.get('username')
            password = self.target.connection.get('password')
            if not client_id:
                global_mqtt = _load_global_mqtt_defaults()
                client_id = _build_client_id(global_mqtt.get('MQIdPattern', ''))
            
            mqtt_payload = self._format_mqtt_payload(payload)

            client = mqtt.Client(client_id=client_id)
            if username and password:
                client.username_pw_set(username, password)
            
            client.connect(broker, port, 60)
            client.loop_start()
            try:
                result = client.publish(topic, json.dumps(mqtt_payload, ensure_ascii=False), qos=1)
                result.wait_for_publish(timeout=5)
                if result.rc == mqtt.MQTT_ERR_SUCCESS and result.is_published():
                    self.logger.info(f"MQTT推送成功: {self.target.name}")
                    return True
                self.logger.error(f"MQTT推送失败: rc={result.rc}, published={result.is_published()}")
                return False
            finally:
                try:
                    client.disconnect()
                finally:
                    client.loop_stop()
                
        except Exception as e:
            self.logger.error(f"MQTT推送失败: {e}")
            return False

    def _format_mqtt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """格式化 MQTT 短报文，不携带 base64 大字段。"""
        mqtt_payload = {
            "push_type": payload.get("push_type", "alert"),
            "ts": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            "customer_id": payload.get("customer_id", CUSTOMER_ID),
            "device_id": payload.get("device_id", DEVICE_ID),
            "project_id": payload.get("project_id", PROJECT_ID),
            "alert_id": payload.get("alert_id") or payload.get("id", ""),
            "id": payload.get("id", ""),
            "type": payload.get("type", ""),
            "type_name": payload.get("type_name", ""),
            "message": payload.get("message", ""),
            "risk_level": payload.get("risk_level", ""),
            "confidence": payload.get("confidence", 0),
            "location": payload.get("location", ""),
            "time": payload.get("time") or payload.get("timestamp", ""),
            "video_source_id": payload.get("video_source_id", ""),
            "video_source_name": payload.get("video_source_name", ""),
            "video_source_type": payload.get("video_source_type", ""),
            "video_url": payload.get("video_url", ""),
            "snapshot_url": payload.get("snapshot_url", ""),
            "detection_details": payload.get("detection_details", {}),
            "snapshot_filename": payload.get("snapshot_filename", "")
        }
        return mqtt_payload
    
    def format_payload(self, alert_data: Dict[str, Any]) -> Dict:
        """格式化APP推送数据"""
        # 🔥 扁平化结构，直接传递所有字段（兼容阿里云服务器）
        return {
            # ⭐ 设备身份信息
            "customer_id": CUSTOMER_ID,
            "device_id": DEVICE_ID,
            "project_id": PROJECT_ID,
            
            # 基础告警信息
            "push_type": "alert",
            "timestamp": datetime.now().isoformat(),
            "alert_id": alert_data.get('id', ''),
            "id": alert_data.get('id', ''),
            "type": alert_data.get('type', ''),
            "type_name": alert_data.get('type_name', ''),
            "message": alert_data.get('message', ''),
            "risk_level": alert_data.get('risk_level', ''),
            "confidence": alert_data.get('confidence', 0),
            "location": alert_data.get('location', ''),
            "time": alert_data.get('timestamp', ''),
            
            # 视频源信息
            "video_source_id": alert_data.get('video_source_id', ''),
            "video_source_name": alert_data.get('video_source_name', ''),
            "video_source_type": alert_data.get('video_source_type', ''),
            "video_url": alert_data.get('video_url', ''),
            
            # 🔥 图片信息（关键！直接放在顶层）
            "snapshot_url": alert_data.get('snapshot_url', ''),
            "snapshot_base64": alert_data.get('snapshot_base64', ''),  # Base64图片数据
            "snapshot_filename": alert_data.get('snapshot_filename', ''),  # 图片文件名
            
            # 检测详情
            "detection_details": alert_data.get('detection_details', {})
        }
    
    def test_connection(self) -> Dict[str, Any]:
        """测试APP连接"""
        test_payload = {
            # ⭐ 设备身份信息
            "customer_id": CUSTOMER_ID,
            "device_id": DEVICE_ID,
            "project_id": PROJECT_ID,
            
            "push_type": "test",
            "timestamp": datetime.now().isoformat(),
            "message": "这是一条测试推送"
        }
        
        try:
            if self.method == 'webhook':
                url = self.target.connection.get('url')
                if not url:
                    return {
                        "success": False,
                        "message": "Webhook URL未配置"
                    }
                
                success = self._push_webhook(test_payload)
                return {
                    "success": success,
                    "message": f"Webhook连接{'成功' if success else '失败'} ({url})"
                }
                
            elif self.method == 'mqtt':
                broker = self.target.connection.get('broker')
                if not broker:
                    return {
                        "success": False,
                        "message": "MQTT Broker未配置"
                    }
                
                success = self._push_mqtt(test_payload)
                return {
                    "success": success,
                    "message": f"MQTT连接{'成功' if success else '失败'} ({broker})"
                }
                
            elif self.method == 'websocket':
                return {
                    "success": False,
                    "message": "WebSocket推送暂未实现"
                }
            else:
                return {
                    "success": False,
                    "message": f"不支持的推送方式: {self.method}"
                }
                
        except Exception as e:
            return {
                "success": False,
                "message": f"测试失败: {str(e)}"
            }


class GpioPushHandler(BasePushHandler):
    """GPIO控制处理器"""
    
    def __init__(self, target: PushTarget):
        super().__init__(target)
        self.gpio_available = False
        try:
            import RPi.GPIO as GPIO
            self.GPIO = GPIO
            self.gpio_available = True
            # 设置GPIO模式
            pin_mode = target.connection.get('pin_mode', 'BCM')
            if pin_mode == 'BCM':
                GPIO.setmode(GPIO.BCM)
            else:
                GPIO.setmode(GPIO.BOARD)
            GPIO.setwarnings(False)
        except ImportError:
            self.logger.warning("RPi.GPIO 未安装，GPIO功能不可用")
        except Exception as e:
            self.logger.error(f"GPIO初始化失败: {e}")
    
    def push(self, alert_data: Dict[str, Any]) -> bool:
        """GPIO控制"""
        if not self.gpio_available:
            self.logger.error("GPIO不可用")
            return False
        
        try:
            payload = self.format_payload(alert_data)
            pins = payload.get('pins', [])
            
            for pin_config in pins:
                pin = pin_config.get('pin')
                action = pin_config.get('action', 'high')
                duration = pin_config.get('duration', 0)
                
                # 设置引脚为输出模式
                self.GPIO.setup(pin, self.GPIO.OUT)
                
                if action == 'high':
                    self.GPIO.output(pin, self.GPIO.HIGH)
                    if duration > 0:
                        time.sleep(duration / 1000.0)  # 毫秒转秒
                        self.GPIO.output(pin, self.GPIO.LOW)
                
                elif action == 'low':
                    self.GPIO.output(pin, self.GPIO.LOW)
                    if duration > 0:
                        time.sleep(duration / 1000.0)
                        self.GPIO.output(pin, self.GPIO.HIGH)
                
                elif action == 'toggle':
                    current = self.GPIO.input(pin)
                    self.GPIO.output(pin, not current)
                
                elif action == 'pulse':
                    pulse_count = pin_config.get('pulse_count', 1)
                    pulse_interval = pin_config.get('pulse_interval', 500)
                    for _ in range(pulse_count):
                        self.GPIO.output(pin, self.GPIO.HIGH)
                        time.sleep(pulse_interval / 2000.0)
                        self.GPIO.output(pin, self.GPIO.LOW)
                        time.sleep(pulse_interval / 2000.0)
                
                self.logger.info(f"GPIO {pin} 执行 {action} 成功")
            
            return True
            
        except Exception as e:
            self.logger.error(f"GPIO控制失败: {e}")
            return False
    
    def format_payload(self, alert_data: Dict[str, Any]) -> Dict:
        """格式化GPIO控制数据"""
        # 从配置中获取GPIO动作
        default_pins = self.target.connection.get('pins', [])
        
        return {
            "protocol": "gpio",
            "device_id": self.target.id,
            "pins": default_pins,
            "alert_info": {
                "type": alert_data.get('type', ''),
                "level": alert_data.get('risk_level', ''),
                "location": alert_data.get('location', '')
            }
        }
    
    def test_connection(self) -> Dict[str, Any]:
        """测试GPIO"""
        if not self.gpio_available:
            return {
                "success": False, 
                "message": "GPIO库未安装。ARM平台请安装: pip install RPi.GPIO"
            }
        
        # 检查是否有GPIO权限
        try:
            import os
            # 检查用户是否在gpio组
            if os.name != 'nt':  # 非Windows系统
                import subprocess
                result = subprocess.run(['groups'], capture_output=True, text=True)
                if 'gpio' not in result.stdout:
                    return {
                        "success": False,
                        "message": "GPIO权限不足。请执行: sudo usermod -aG gpio $USER 然后重新登录"
                    }
        except:
            pass
        
        return {
            "success": True, 
            "message": "GPIO可用（注意：实际控制时需要配置具体引脚）"
        }


class ModbusPushHandler(BasePushHandler):
    """MODBUS推送处理器"""
    
    def __init__(self, target: PushTarget):
        super().__init__(target)
        self.modbus_available = False
        try:
            from pymodbus.client import ModbusTcpClient, ModbusSerialClient
            self.ModbusTcpClient = ModbusTcpClient
            self.ModbusSerialClient = ModbusSerialClient
            self.modbus_available = True
        except ImportError:
            self.logger.warning("pymodbus 未安装，MODBUS功能不可用")
    
    def push(self, alert_data: Dict[str, Any]) -> bool:
        """MODBUS推送"""
        self.last_error = ''

        if not self.modbus_available:
            self.last_error = 'pymodbus 未安装，请执行: pip install pymodbus'
            self.logger.error(self.last_error)
            return False

        connection = self.target.connection
        conn_type = connection.get('type', 'rtu')
        slave_id = connection.get('slave_id', 1)
        serial_port = connection.get('serial_port', '').strip()
        operation_mode = str(connection.get('operation_mode', 'fc6')).upper()

        if not serial_port:
            self.last_error = '串口设备路径未配置，请编辑推送目标填写串口路径（如 /dev/ttyUSB0）'
            self.logger.error(self.last_error)
            return False

        try:
            if conn_type == 'tcp':
                client = self.ModbusTcpClient(
                    host=serial_port,
                    port=connection.get('port', 502),
                    timeout=connection.get('timeout', 3)
                )
            else:
                client = self.ModbusSerialClient(
                    port=serial_port,
                    baudrate=connection.get('baudrate', 9600),
                    bytesize=connection.get('bytesize', 8),
                    parity=connection.get('parity', 'N'),
                    stopbits=connection.get('stopbits', 1),
                    timeout=connection.get('timeout', 1)
                )

            if not client.connect():
                self.last_error = f'无法打开 {serial_port}，请检查设备是否存在及权限（ls /dev/tty*）'
                self.logger.error(f"MODBUS连接失败: {self.last_error}")
                return False

            payload = self.format_payload(alert_data)
            operations = payload.get('operations', [])

            success = True
            for op in operations:
                function_code = op.get('function_code')
                address = op.get('address', 0)

                if function_code == 5:
                    result = client.write_coil(address, op.get('value', True), slave=slave_id)
                elif function_code == 6:
                    result = client.write_register(address, op.get('value', 0), slave=slave_id)
                elif function_code == 3:
                    result = client.read_holding_registers(address, count=1, slave=slave_id)
                    self.logger.info(f"MODBUS读取寄存器[{address}]: {result.registers if not result.isError() else result}")
                    continue
                elif function_code == 15:
                    result = client.write_coils(address, op.get('values', [True]), slave=slave_id)
                elif function_code == 16:
                    result = client.write_registers(address, op.get('values', [0]), slave=slave_id)
                else:
                    self.last_error = f'不支持的功能码: {function_code}'
                    self.logger.error(self.last_error)
                    success = False
                    continue

                if result.isError():
                    self.last_error = f'FC{function_code}@{address} 操作失败: {result}'
                    self.logger.error(f"MODBUS操作失败: {self.last_error}")
                    success = False
                else:
                    self.logger.info(f"MODBUS操作成功 FC{function_code}@{address}")

            client.close()
            return success

        except Exception as e:
            self.last_error = str(e)
            self.logger.error(f"MODBUS推送异常: {e}")
            return False
    
    def format_payload(self, alert_data: Dict[str, Any]) -> Dict:
        """根据操作模式生成 MODBUS 操作"""
        connection = self.target.connection
        operation_mode = str(connection.get('operation_mode', '')).lower()
        legacy_mode = str(connection.get('mode', '')).lower()
        register_addr = connection.get('register_addr', connection.get('register_address', 0))

        risk_level_map = {'低': 1, '中': 2, '高': 3}
        level_value = risk_level_map.get(alert_data.get('risk_level', '低'), 1)

        if operation_mode in ('fc5', '5') or legacy_mode == 'write_coil':
            operations = [{'function_code': 5, 'address': register_addr, 'value': True}]
        elif operation_mode in ('fc3', '3') or legacy_mode == 'read':
            operations = [{'function_code': 3, 'address': register_addr}]
        elif operation_mode in ('fc6', '6') or legacy_mode in ('write', 'write_register') or not operation_mode:
            operations = [{'function_code': 6, 'address': register_addr, 'value': level_value}]
        else:
            operations = connection.get('operations', [])

        return {
            "protocol": "modbus",
            "operations": operations,
            "alert_context": {
                "type": alert_data.get('type', ''),
                "level": level_value,
                "location": alert_data.get('location', ''),
                "timestamp": int(time.time())
            }
        }
    
    def test_connection(self) -> Dict[str, Any]:
        """测试MODBUS连接"""
        if not self.modbus_available:
            return {
                "success": False, 
                "message": "MODBUS库未安装，请安装: pip install pymodbus"
            }
        
        try:
            connection = self.target.connection
            conn_type = connection.get('type', 'rtu')
            serial_port = connection.get('serial_port', '').strip()
            baudrate = connection.get('baudrate', 9600)
            slave_id = connection.get('slave_id', 1)
            operation_mode = str(connection.get('operation_mode', 'fc6')).upper()

            if not serial_port:
                return {
                    "success": False,
                    "message": "串口设备路径为空，请填写（如 /dev/ttyUSB0）"
                }
            
            if conn_type == 'tcp':
                port = connection.get('port', 502)
                client = self.ModbusTcpClient(
                    host=serial_port,
                    port=port,
                    timeout=connection.get('timeout', 3)
                )
                if client.connect():
                    client.close()
                    return {"success": True, "message": f"Modbus TCP 连接成功 ({serial_port}:{port})"}
                else:
                    return {"success": False, "message": f"无法连接到 {serial_port}:{port}，请检查网络和设备状态"}
            else:
                client = self.ModbusSerialClient(
                    port=serial_port,
                    baudrate=baudrate,
                    bytesize=connection.get('bytesize', 8),
                    parity=connection.get('parity', 'N'),
                    stopbits=connection.get('stopbits', 1),
                    timeout=connection.get('timeout', 3)
                )
                if client.connect():
                    client.close()
                    register_addr = connection.get('register_addr', connection.get('register_address', 0))
                    return {"success": True, "message": f"Modbus RTU 串口已打开：{serial_port} @ {baudrate}bps，从站地址 {slave_id}，操作模式 {operation_mode}，目标地址 {register_addr}（打开后可用 stty -F {serial_port} 确认波特率）"}
                else:
                    return {"success": False, "message": f"无法打开串口 {serial_port}，请检查设备是否存在及权限"}
                
        except Exception as e:
            error_msg = str(e)
            if "No such file" in error_msg or "could not open port" in error_msg:
                return {"success": False, "message": f"串口设备不存在或权限不足: {error_msg}"}
            elif "Connection refused" in error_msg:
                return {"success": False, "message": "连接被拒绝，请检查目标设备是否运行 Modbus 服务"}
            else:
                return {"success": False, "message": f"测试失败: {error_msg}"}


class SerialPushHandler(BasePushHandler):
    """串口推送处理器基类(RS232/RS485)"""
    
    def __init__(self, target: PushTarget):
        super().__init__(target)
        self.serial_available = False
        try:
            import serial
            self.serial = serial
            self.serial_available = True
        except ImportError:
            self.logger.warning("pyserial 未安装，串口功能不可用")
    
    def push(self, alert_data: Dict[str, Any]) -> bool:
        """串口推送"""
        if not self.serial_available:
            self.logger.error("串口不可用")
            return False
        
        try:
            connection = self.target.connection
            
            # 打开串口
            ser = self.serial.Serial(
                port=connection.get('port', '/dev/ttyUSB0'),
                baudrate=connection.get('baudrate', 9600),
                bytesize=connection.get('bytesize', 8),
                parity=connection.get('parity', 'N'),
                stopbits=connection.get('stopbits', 1),
                timeout=connection.get('timeout', 1)
            )
            
            # 格式化数据
            payload = self.format_payload(alert_data)
            data_format = payload.get('format', 'text')
            
            if data_format == 'text':
                data = payload.get('data', '')
                encoding = payload.get('encoding', 'utf-8')
                ser.write(data.encode(encoding))
            elif data_format == 'hex':
                hex_data = payload.get('hex_data', '')
                # 将hex字符串转换为字节 "A1 02 03" -> b'\xa1\x02\x03'
                bytes_data = bytes.fromhex(hex_data.replace(' ', ''))
                ser.write(bytes_data)
            else:
                binary_data = payload.get('binary_data', b'')
                ser.write(binary_data)
            
            ser.close()
            self.logger.info(f"串口推送成功: {self.target.name}")
            return True
            
        except Exception as e:
            self.logger.error(f"串口推送失败: {e}")
            return False
    
    def format_payload(self, alert_data: Dict[str, Any]) -> Dict:
        """格式化串口数据"""
        # 生成文本格式数据
        alert_type = alert_data.get('type', 'UNKNOWN')
        location = alert_data.get('location', 'UNKNOWN')
        risk_level = alert_data.get('risk_level', 'UNKNOWN')
        
        text_data = f"ALERT:{alert_type}:{location}:{risk_level}\r\n"
        
        return {
            "protocol": self.target.type,
            "device_id": self.target.id,
            "format": self.target.connection.get('format', 'text'),
            "data": text_data,
            "encoding": self.target.connection.get('encoding', 'utf-8'),
            "alert_info": {
                "type": alert_type,
                "location": location,
                "level": risk_level,
                "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        }
    
    def test_connection(self) -> Dict[str, Any]:
        """测试串口连接"""
        if not self.serial_available:
            return {
                "success": False, 
                "message": "串口库未安装，请安装: pip install pyserial"
            }
        
        try:
            connection = self.target.connection
            port = connection.get('port', '')
            
            # 验证串口路径
            if not port:
                return {
                    "success": False, 
                    "message": "串口设备路径为空，请填写如 /dev/ttyUSB0"
                }
            
            baudrate = connection.get('baudrate', 9600)
            
            # 尝试打开串口
            ser = self.serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=connection.get('bytesize', 8),
                parity=connection.get('parity', 'N'),
                stopbits=connection.get('stopbits', 1),
                timeout=1
            )
            
            # 成功打开，关闭串口
            ser.close()
            
            return {
                "success": True, 
                "message": f"串口 {port} 可用 (波特率: {baudrate})"
            }
            
        except self.serial.SerialException as e:
            error_msg = str(e)
            
            # 提供更友好的错误提示
            if "could not open port" in error_msg or "No such file" in error_msg:
                # 检查常见串口设备
                import os
                available_ports = []
                for device in ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyS0', '/dev/ttyS1', 
                              '/dev/ttyAMA0', 'COM1', 'COM2', 'COM3', 'COM4']:
                    if os.path.exists(device):
                        available_ports.append(device)
                
                hint = ""
                if available_ports:
                    hint = f" 可用设备: {', '.join(available_ports)}"
                
                return {
                    "success": False,
                    "message": f"串口设备 {port} 不存在。{hint}"
                }
            elif "Permission denied" in error_msg:
                return {
                    "success": False,
                    "message": f"串口权限不足。请执行: sudo usermod -aG dialout $USER 然后重新登录"
                }
            elif "Device or resource busy" in error_msg:
                return {
                    "success": False,
                    "message": f"串口设备 {port} 被占用，请检查是否有其他程序正在使用"
                }
            else:
                return {
                    "success": False,
                    "message": f"串口测试失败: {error_msg}"
                }
                
        except Exception as e:
            return {
                "success": False, 
                "message": f"串口测试异常: {str(e)}"
            }


class UartPushHandler(SerialPushHandler):
    """UART推送处理器（统一的串口通信处理器）"""
    pass


# 兼容旧配置：保留 RS232/RS485 别名，避免历史 push_config.json 中的类型导致启动失败
class Rs232PushHandler(SerialPushHandler):
    """RS232推送处理器（已废弃，保留以兼容旧配置）"""
    pass


class Rs485PushHandler(SerialPushHandler):
    """RS485推送处理器（已废弃，保留以兼容旧配置）"""

    def format_payload(self, alert_data: Dict[str, Any]) -> Dict:
        payload = super().format_payload(alert_data)
        payload['slave_address'] = self.target.connection.get('slave_address', 0x01)
        return payload


class PushManager:
    """推送管理器"""
    
    def __init__(self, config_file: str = 'data/push_config.json'):
        self.config_file = config_file
        self.targets: Dict[str, PushTarget] = {}
        self.handlers: Dict[str, BasePushHandler] = {}
        self.push_queue = queue.Queue()
        self.push_thread = None
        self.running = False
        self.push_logs: List[Dict] = []
        self.max_logs = 1000
        
        self.load_config()
        self.start()
    
    def load_config(self):
        """加载配置"""
        config_path = Path(self.config_file)
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    targets_data = data.get('targets', [])
                    for target_data in targets_data:
                        target = PushTarget(target_data)
                        self.targets[target.id] = target
                        self.create_handler(target)
                logger.info(f"✅ 推送配置加载成功，共 {len(self.targets)} 个推送目标")
            except Exception as e:
                logger.error(f"❌ 加载推送配置失败: {e}")
        else:
            logger.info(f"📝 首次运行，创建推送配置文件: {config_path}")
            self.save_config()
            logger.info(f"✅ 推送配置文件已创建，可在管理后台添加推送目标")

        self.sync_system_mqtt()
    
    def save_config(self):
        """保存配置"""
        config_path = Path(self.config_file)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            data = {
                'targets': [target.to_dict() for target in self.targets.values()]
            }
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.sync_system_mqtt()
            logger.info("推送配置已保存")
        except Exception as e:
            logger.error(f"保存推送配置失败: {e}")
    
    def get_system_mqtt_targets(self) -> List[PushTarget]:
        enabled_targets = [target for target in self.targets.values() if target.type == 'mqtt' and target.enabled]
        if enabled_targets:
            return enabled_targets

        mqtt_targets = [target for target in self.targets.values() if target.type == 'mqtt']
        return mqtt_targets[:1]

    def get_system_mqtt_target(self) -> Optional[PushTarget]:
        targets = self.get_system_mqtt_targets()
        return targets[0] if targets else None

    def build_system_mqtt_config(self) -> Dict[str, Any]:
        legacy = _load_global_mqtt_defaults()
        targets = self.get_system_mqtt_targets()

        config = {
            'MQBroker': legacy.get('MQBroker', 'localhost'),
            'MQPort': int(legacy.get('MQPort', 1883)),
            'MQRootTopic': legacy.get('MQRootTopic', 'safehat') or 'safehat',
            'EnableLogUploader': bool(legacy.get('EnableLogUploader', False)),
            'SecPingInterval': int(legacy.get('SecPingInterval', 60)),
            'MQIdPattern': legacy.get('MQIdPattern', ''),
            'MQClientId': legacy.get('MQClientId', ''),
            'MQUsername': legacy.get('MQUsername', ''),
            'MQPassword': legacy.get('MQPassword', ''),
            'MQUniqueSubscribe': bool(legacy.get('MQUniqueSubscribe', False)),
            'LogLevel': str(legacy.get('LogLevel', 'ERROR')).upper(),
            'MQTargets': []
        }

        if targets:
            first_conn = targets[0].connection or {}
            unified_ping_interval = int(first_conn.get('ping_interval') or config['SecPingInterval'])
            unified_log_level = str(first_conn.get('log_level') or config['LogLevel']).upper()
            unified_unique_subscribe = bool(first_conn.get('unique_subscribe', config['MQUniqueSubscribe']))

            config.update({
                'MQBroker': (first_conn.get('broker') or config['MQBroker']).strip() or config['MQBroker'],
                'MQPort': int(first_conn.get('port') or config['MQPort']),
                'MQRootTopic': (first_conn.get('topic') or config['MQRootTopic']).strip() or 'safehat',
                'EnableLogUploader': any(bool(target.enabled) for target in targets),
                'SecPingInterval': unified_ping_interval,
                'MQClientId': (first_conn.get('client_id') or '').strip(),
                'MQUsername': (first_conn.get('username') or '').strip(),
                'MQPassword': first_conn.get('password') or '',
                'MQUniqueSubscribe': unified_unique_subscribe,
                'LogLevel': unified_log_level,
            })

            mqtt_targets = []
            for target in targets:
                conn = target.connection or {}
                mqtt_targets.append({
                    'MQBroker': (conn.get('broker') or config['MQBroker']).strip() or config['MQBroker'],
                    'MQPort': int(conn.get('port') or config['MQPort']),
                    'MQRootTopic': (conn.get('topic') or config['MQRootTopic']).strip() or 'safehat',
                    'EnableLogUploader': bool(target.enabled),
                    'SecPingInterval': unified_ping_interval,
                    'MQIdPattern': legacy.get('MQIdPattern', ''),
                    'MQClientId': (conn.get('client_id') or '').strip(),
                    'MQUsername': (conn.get('username') or '').strip(),
                    'MQPassword': conn.get('password') or '',
                    'MQUniqueSubscribe': unified_unique_subscribe,
                    'LogLevel': unified_log_level,
                })
            config['MQTargets'] = mqtt_targets

        return config

    def sync_system_mqtt(self) -> Dict[str, Any]:
        config = self.build_system_mqtt_config()
        _save_legacy_mqtt_config({k: v for k, v in config.items() if k != 'MQTargets'})
        try:
            from safehat.managers import mqtt_log_handler as _mqtt_log
            _mqtt_log.setup(config)
        except Exception as exc:
            logger.error(f"同步系统 MQTT 运行配置失败: {exc}")
        return config

    def apply_legacy_mqtt_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        targets = self.get_system_mqtt_targets()
        if not targets:
            import uuid
            target_id = f"push_{uuid.uuid4().hex[:8]}"
            target = PushTarget({
                'id': target_id,
                'name': '系统 MQTT',
                'enabled': True,
                'type': 'mqtt',
                'connection': {
                    'method': 'mqtt',
                    'broker': 'localhost',
                    'port': 1883,
                    'topic': 'safehat'
                },
                'filter': {
                    'alert_types': [],
                    'risk_levels': [],
                    'video_sources': [],
                    'time_range': {'enabled': False}
                },
                'retry': {'enabled': True, 'max_retries': 3, 'retry_interval': 5},
                'rate_limit': {'enabled': True, 'max_per_minute': 10}
            })
            self.targets[target_id] = target
            targets = [target]

        unified_ping_interval = int(data.get('SecPingInterval', 60)) if 'SecPingInterval' in data else None
        unified_unique_subscribe = bool(data['MQUniqueSubscribe']) if 'MQUniqueSubscribe' in data else None
        unified_log_level = str(data['LogLevel']).upper() if 'LogLevel' in data else None

        for target in targets:
            conn = dict(target.connection or {})
            conn['method'] = 'mqtt'
            if 'MQBroker' in data and len(targets) == 1:
                conn['broker'] = str(data['MQBroker']).strip()
            if 'MQPort' in data and len(targets) == 1:
                conn['port'] = int(data['MQPort'])
            if 'MQRootTopic' in data and len(targets) == 1:
                conn['topic'] = str(data['MQRootTopic']).strip() or 'safehat'
            if unified_ping_interval is not None:
                conn['ping_interval'] = unified_ping_interval
            if unified_unique_subscribe is not None:
                conn['unique_subscribe'] = unified_unique_subscribe
            if unified_log_level is not None:
                conn['log_level'] = unified_log_level
            if 'MQClientId' in data and len(targets) == 1:
                conn['client_id'] = str(data['MQClientId']).strip()
            if 'MQUsername' in data and len(targets) == 1:
                conn['username'] = str(data['MQUsername']).strip()
            if 'MQPassword' in data and len(targets) == 1:
                conn['password'] = str(data['MQPassword'])

            target.connection = conn
            if 'EnableLogUploader' in data:
                target.enabled = bool(data['EnableLogUploader'])

            self.targets[target.id] = target
            self.create_handler(target)

        self.save_config()
        return self.build_system_mqtt_config()

    def create_handler(self, target: PushTarget) -> Optional[BasePushHandler]:
        """创建推送处理器"""
        handler_map = {
            'app': AppPushHandler,
            'mqtt': AppPushHandler,
            'modbus': ModbusPushHandler,
            'uart': UartPushHandler,
            # 兼容旧配置
            'gpio': GpioPushHandler,
            'rs232': Rs232PushHandler,
            'rs485': Rs485PushHandler,
        }
        
        handler_class = handler_map.get(target.type)
        if handler_class:
            handler = handler_class(target)
            self.handlers[target.id] = handler
            return handler
        else:
            logger.error(f"不支持的推送类型: {target.type}")
            return None
    
    def add_target(self, config: Dict[str, Any]) -> str:
        """添加推送目标"""
        import uuid
        target_id = config.get('id') or f"push_{uuid.uuid4().hex[:8]}"
        config['id'] = target_id
        
        target = PushTarget(config)
        self.targets[target_id] = target
        self.create_handler(target)
        self.save_config()
        
        logger.info(f"添加推送目标: {target.name}")
        return target_id
    
    def update_target(self, target_id: str, config: Dict[str, Any]) -> bool:
        """更新推送目标"""
        if target_id not in self.targets:
            return False
        
        config['id'] = target_id
        target = PushTarget(config)
        self.targets[target_id] = target

        if target.type == 'mqtt':
            conn = target.connection or {}
            unified_ping_interval = int(conn.get('ping_interval', 10) or 10)
            unified_log_level = str(conn.get('log_level', 'DEBUG') or 'DEBUG').upper()
            unified_unique_subscribe = bool(conn.get('unique_subscribe', False))

            for other_target_id, other_target in list(self.targets.items()):
                if other_target_id == target_id or other_target.type != 'mqtt':
                    continue
                other_conn = dict(other_target.connection or {})
                other_conn['ping_interval'] = unified_ping_interval
                other_conn['log_level'] = unified_log_level
                other_conn['unique_subscribe'] = unified_unique_subscribe
                other_target.connection = other_conn
                self.targets[other_target_id] = other_target
                self.create_handler(other_target)
        
        self.create_handler(target)
        self.save_config()
        
        logger.info(f"更新推送目标: {target.name}")
        return True
    
    def delete_target(self, target_id: str) -> bool:
        """删除推送目标"""
        if target_id in self.targets:
            del self.targets[target_id]
            if target_id in self.handlers:
                del self.handlers[target_id]
            self.save_config()
            logger.info(f"删除推送目标: {target_id}")
            return True
        return False
    
    def get_targets(self) -> List[Dict]:
        """获取所有推送目标"""
        return [target.to_dict() for target in self.targets.values()]
    
    def get_target(self, target_id: str) -> Optional[Dict]:
        """获取单个推送目标"""
        target = self.targets.get(target_id)
        return target.to_dict() if target else None
    
    def test_target(self, target_id: str) -> Dict[str, Any]:
        """测试推送目标"""
        handler = self.handlers.get(target_id)
        if not handler:
            return {"success": False, "message": "推送目标不存在"}
        
        return handler.test_connection()
    
    def push_alert(self, alert_data: Dict[str, Any]):
        """推送告警（异步）"""
        self.push_queue.put(alert_data)
    
    def _push_worker(self):
        """推送工作线程"""
        while self.running:
            try:
                # 从队列获取告警数据
                alert_data = self.push_queue.get(timeout=1)
                
                # 遍历所有推送目标
                for target_id, handler in self.handlers.items():
                    try:
                        # 判断是否应该推送
                        if not handler.should_push(alert_data):
                            continue
                        
                        # 执行推送
                        start_time = time.time()
                        success = handler.push(alert_data)
                        duration = time.time() - start_time

                        # 更新计数
                        if success:
                            handler.target.push_count += 1

                        # 记录日志，带上具体错误原因
                        error_msg = '' if success else getattr(handler, 'last_error', '')
                        self.log_push(target_id, alert_data, success, duration, error_msg)

                        # 如果失败且启用重试（仅在非配置类错误时重试）
                        if not success and handler.target.retry.get('enabled') and error_msg and '未配置' not in error_msg:
                            self._retry_push(handler, alert_data)
                        
                    except Exception as e:
                        logger.error(f"推送到 {target_id} 失败: {e}")
                        self.log_push(target_id, alert_data, False, 0, str(e))
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"推送工作线程错误: {e}")
    
    def _retry_push(self, handler: BasePushHandler, alert_data: Dict[str, Any]):
        """重试推送"""
        retry_config = handler.target.retry
        max_retries = retry_config.get('max_retries', 3)
        retry_interval = retry_config.get('retry_interval', 5)
        
        for i in range(max_retries):
            time.sleep(retry_interval)
            logger.info(f"重试推送 {handler.target.name} ({i+1}/{max_retries})")
            if handler.push(alert_data):
                logger.info(f"重试推送成功")
                return
        
        logger.error(f"重试推送失败，已达最大重试次数")
    
    def log_push(self, target_id: str, alert_data: Dict, success: bool, duration: float, error: str = ''):
        """记录推送日志"""
        log = {
            'timestamp': datetime.now().isoformat(),
            'target_id': target_id,
            'target_name': self.targets.get(target_id, PushTarget({})).name,
            'alert_type': alert_data.get('type', ''),
            'alert_id': alert_data.get('id', ''),
            'success': success,
            'duration': round(duration, 3),
            'error': error
        }
        
        self.push_logs.append(log)
        
        # 限制日志数量
        if len(self.push_logs) > self.max_logs:
            self.push_logs = self.push_logs[-self.max_logs:]
    
    def get_logs(self, limit: int = 100) -> List[Dict]:
        """获取推送日志"""
        return self.push_logs[-limit:]
    
    def start(self):
        """启动推送服务"""
        if not self.running:
            self.running = True
            self.push_thread = threading.Thread(target=self._push_worker, daemon=True)
            self.push_thread.start()
            
            if len(self.targets) > 0:
                enabled_count = sum(1 for t in self.targets.values() if t.enabled)
                logger.info(f"🚀 推送服务已启动 (共 {len(self.targets)} 个目标，{enabled_count} 个已启用)")
            else:
                logger.info(f"🚀 推送服务已启动 (暂无推送目标，可在管理后台添加)")
    
    def stop(self):
        """停止推送服务"""
        self.running = False
        if self.push_thread:
            self.push_thread.join(timeout=5)
        logger.info("推送服务已停止")


# 全局推送管理器实例
_push_manager: Optional[PushManager] = None


def get_push_manager() -> PushManager:
    """获取推送管理器实例"""
    global _push_manager
    if _push_manager is None:
        _push_manager = PushManager()
    return _push_manager

