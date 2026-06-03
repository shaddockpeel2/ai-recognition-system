"""
MQTT 日志上报 + 业务心跳 Handler

运行时配置来源已切换为 MQTT 推送目标；旧的 mqtt_config.json 仅保留兼容镜像。
支持将系统心跳与日志广播到多个 MQTT broker，并共用统一的心跳间隔。
"""
import logging
import json
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


_LOGGER = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


class _BrokerChannel:
    def __init__(self, config: Dict[str, Any], stop_event: threading.Event):
        self._config = config
        self._stop_event = stop_event
        self._client = None
        self._retry_delay = 2

    @property
    def broker(self) -> str:
        return self._config.get('MQBroker', 'localhost').strip() or 'localhost'

    @property
    def port(self) -> int:
        return int(self._config.get('MQPort', 1883))

    @property
    def root_topic(self) -> str:
        return (self._config.get('MQRootTopic', '') or '').rstrip('/')

    @property
    def ping_interval(self) -> int:
        return max(int(self._config.get('SecPingInterval', 60)), 5)

    @property
    def username(self) -> str:
        return (self._config.get('MQUsername') or '').strip()

    @property
    def password(self) -> str:
        return self._config.get('MQPassword') or ''

    @property
    def client_id(self) -> str:
        client_id = (self._config.get('MQClientId') or '').strip()
        if client_id:
            return client_id
        return self._make_client_id(self._config.get('MQIdPattern', ''))

    @staticmethod
    def _make_client_id(pattern: str) -> str:
        host = socket.gethostname()
        if not pattern:
            return f'safehat-{host}'
        return pattern.replace('{hostname}', host).replace('{host}', host)

    @property
    def log_topic(self) -> str:
        return f'{self.root_topic}/logs' if self.root_topic else 'safehat/logs'

    @property
    def heartbeat_topic(self) -> str:
        return f'{self.root_topic}/heartbeat' if self.root_topic else 'safehat/heartbeat'

    @property
    def key(self) -> str:
        return f'{self.broker}:{self.port}:{self.root_topic or "safehat"}:{self.client_id}'

    def ensure_connected(self) -> bool:
        if self._client is not None:
            return True

        try:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(client_id=self.client_id, clean_session=True)
            if self.username:
                client.username_pw_set(self.username, self.password)

            def on_connect(c, userdata, flags, rc):
                if rc == 0:
                    _LOGGER.info(f'[MqttLogHandler] 已连接到 {self.broker}:{self.port}')
                else:
                    _LOGGER.warning(f'[MqttLogHandler] 连接 {self.broker}:{self.port} 失败 rc={rc}')

            def on_disconnect(c, userdata, rc):
                if not self._stop_event.is_set():
                    _LOGGER.warning(f'[MqttLogHandler] 连接 {self.broker}:{self.port} 断开 rc={rc}，等待重连…')

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.connect(self.broker, self.port, keepalive=self.ping_interval)
            client.loop_start()
            self._client = client
            self._retry_delay = 2
            return True
        except Exception as exc:
            _LOGGER.warning(f'[MqttLogHandler] 连接 {self.broker}:{self.port} 失败: {exc}')
            self._client = None
            time.sleep(self._retry_delay)
            self._retry_delay = min(self._retry_delay * 2, 60)
            return False

    def disconnect(self):
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def publish(self, topic: str, payload: str, qos: int = 0) -> bool:
        if not self.ensure_connected():
            return False
        try:
            self._client.publish(topic, payload, qos=qos)
            return True
        except Exception as exc:
            _LOGGER.warning(f'[MqttLogHandler] 发布到 {self.broker}:{self.port} 失败: {exc}')
            self.disconnect()
            return False


class MqttLogHandler(logging.Handler):

    def __init__(self, config: dict):
        super().__init__()
        self._lock = threading.Lock()
        self._log_queue: List[str] = []
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._channels: List[_BrokerChannel] = []
        self._ping_interval = 60
        self._level_name = 'ERROR'
        self._apply_config(config)
        self._start_worker()

    def _normalize_configs(self, config: dict) -> List[Dict[str, Any]]:
        configs = config.get('MQTargets')
        if isinstance(configs, list) and configs:
            normalized = []
            for item in configs:
                if isinstance(item, dict):
                    merged = dict(item)
                    merged.setdefault('EnableLogUploader', config.get('EnableLogUploader', True))
                    merged.setdefault('SecPingInterval', config.get('SecPingInterval', 60))
                    merged.setdefault('LogLevel', config.get('LogLevel', 'ERROR'))
                    merged.setdefault('MQIdPattern', config.get('MQIdPattern', ''))
                    normalized.append(merged)
            if normalized:
                return normalized
        return [config]

    def _apply_config(self, config: dict):
        configs = self._normalize_configs(config)
        self._ping_interval = max(int(config.get('SecPingInterval', 60)), 5)
        self._level_name = str(config.get('LogLevel', 'ERROR')).upper()
        self.setLevel(getattr(logging, self._level_name, logging.ERROR))
        self._channels = [_BrokerChannel(item, self._stop_event) for item in configs if item.get('EnableLogUploader', False)]

    def _start_worker(self):
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._publish_loop,
            name='MqttLogHandler-worker',
            daemon=True
        )
        self._worker.start()

    def _publish_loop(self):
        last_heartbeat = 0.0

        while not self._stop_event.is_set():
            now = time.time()

            if now - last_heartbeat >= self._ping_interval:
                heartbeat = json.dumps({
                    'ts': _utc_now(),
                    'type': 'heartbeat',
                    'status': 'online',
                }, ensure_ascii=False)
                for channel in self._channels:
                    channel.publish(channel.heartbeat_topic, heartbeat, qos=0)
                last_heartbeat = now

            with self._lock:
                batch, self._log_queue = self._log_queue[:50], self._log_queue[50:]

            if batch:
                for channel in self._channels:
                    for payload in batch:
                        channel.publish(channel.log_topic, payload, qos=0)

            time.sleep(0.2)

    def emit(self, record: logging.LogRecord):
        try:
            payload = json.dumps({
                'ts': _utc_now(),
                'level': record.levelname,
                'logger': record.name,
                'msg': self.format(record),
            }, ensure_ascii=False)
            with self._lock:
                if len(self._log_queue) < 500:
                    self._log_queue.append(payload)
        except Exception:
            self.handleError(record)

    def reload(self, config: dict):
        self.close()
        self._stop_event = threading.Event()
        self._apply_config(config)
        self._start_worker()

    def close(self):
        self._stop_event.set()
        for channel in self._channels:
            channel.disconnect()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=3)
        super().close()


_handler: Optional[MqttLogHandler] = None


def setup(config: dict) -> None:
    global _handler
    root_logger = logging.getLogger()

    enabled = bool(config.get('EnableLogUploader', False))
    targets = config.get('MQTargets') or []
    if isinstance(targets, list):
        enabled = enabled and any(bool(item.get('EnableLogUploader', True)) for item in targets if isinstance(item, dict))

    if not enabled:
        _teardown(root_logger)
        return

    if _handler is None:
        _handler = MqttLogHandler(config)
        _handler.setFormatter(logging.Formatter('%(name)s - %(message)s'))
        root_logger.addHandler(_handler)
        _LOGGER.info('[MqttLogHandler] 日志上报已启动')
    else:
        _handler.reload(config)
        _LOGGER.info('[MqttLogHandler] 配置已热重载')


def teardown() -> None:
    _teardown(logging.getLogger())


def _teardown(root_logger: logging.Logger) -> None:
    global _handler
    if _handler is not None:
        root_logger.removeHandler(_handler)
        _handler.close()
        _handler = None