"""
告警管理模块 - 管理告警语音和弹窗
"""
import json
import os
from datetime import datetime
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

class AlertManager:
    """告警管理器"""
    
    def __init__(self, config_file='data/alert_config.json'):
        self.config_file = config_file
        self.push_manager = None  # 推送管理器实例，延迟加载
        self.config = {
            'voice_enabled': True,  # 语音告警开关
            'popup_enabled': True,  # 弹窗告警开关
            'voice_volume': 80,     # 语音音量 (0-100)
            'popup_duration': 5,    # 弹窗持续时间(秒)
            'language': 'zh-CN',    # 系统语言
            'alert_templates': {
                'helmet': {
                    'name': '安全帽检测',
                    'message': '发现未佩戴安全帽，请立即佩戴！',
                    'audio_file': 'static/audio/alerts/helmet_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/helmet_warning_en.mp3',
                    'level': 'high',
                    'color': '#ff4444'
                },
                'vest': {
                    'name': '反光衣检测',
                    'message': '发现未穿戴反光衣，请立即穿戴！',
                    'audio_file': 'static/audio/alerts/vest_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/vest_warning_en.mp3',
                    'level': 'high',
                    'color': '#ff6b35'
                },
                'smoke': {
                    'name': '烟雾检测',
                    'message': '检测到烟雾，请检查现场并确保安全！',
                    'audio_file': 'static/audio/alerts/smoke_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/smoke_warning_en.mp3',
                    'level': 'high',
                    'color': '#808080'
                },
                'fire': {
                    'name': '火灾检测',
                    'message': '检测到火灾！紧急情况，请立即疏散并报警！',
                    'audio_file': 'static/audio/alerts/fire_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/fire_warning_en.mp3',
                    'level': 'critical',
                    'color': '#ff4500'
                },
                'crack': {
                    'name': '裂缝检测',
                    'message': '发现结构裂缝，请及时检修！',
                    'audio_file': 'static/audio/alerts/crack_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/crack_warning_en.mp3',
                    'level': 'medium',
                    'color': '#ff9800'
                },
                'intrusion': {
                    'name': '人员入侵',
                    'message': '检测到人员入侵，请立即处理！',
                    'audio_file': 'static/audio/alerts/intrusion_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/intrusion_warning_en.mp3',
                    'level': 'critical',
                    'color': '#ff0000'
                },
                'fall': {
                    'name': '人员跌倒',
                    'message': '检测到人员跌倒，请紧急救援！',
                    'audio_file': 'static/audio/alerts/fall_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/fall_warning_en.mp3',
                    'level': 'critical',
                    'color': '#ff0000'
                },
                'crowd_gathering': {
                    'name': '人员聚集',
                    'message': '检测到人员聚集，请注意管理！',
                    'audio_file': 'static/audio/alerts/crowd_gathering_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/crowd_gathering_warning_en.mp3',
                    'level': 'high',
                    'color': '#ffa500'
                },
                'person_absence': {
                    'name': '人员离岗',
                    'message': '检测到人员离岗，请及时处理！',
                    'audio_file': 'static/audio/alerts/person_absence_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/person_absence_warning_en.mp3',
                    'level': 'high',
                    'color': '#9370db'
                },
                'vehicle_parking': {
                    'name': '车辆违停',
                    'message': '检测到车辆违停，请立即处理！',
                    'audio_file': 'static/audio/alerts/vehicle_parking_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/vehicle_parking_warning_en.mp3',
                    'level': 'high',
                    'color': '#ffa500'
                },
                'fire_extinguisher_absence': {
                    'name': '灭火器缺失',
                    'message': '检测到灭火器缺失，请紧急处理！',
                    'audio_file': 'static/audio/alerts/fire_extinguisher_absence_warning.mp3',
                    'audio_file_en': 'static/audio/alerts/fire_extinguisher_absence_warning_en.mp3',
                    'level': 'critical',
                    'color': '#ff0000'
                }
            }
        }
        self.load_config()
        self.alert_history = []  # 告警历史
    
    def load_config(self):
        """加载配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    self.config.update(loaded_config)
            except:
                pass
        else:
            self.save_config()
    
    def save_config(self):
        """保存配置"""
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
    
    def update_config(self, config_data: Dict):
        """更新配置"""
        self.config.update(config_data)
        self.save_config()
    
    def get_config(self):
        """获取配置"""
        return self.config
    
    def create_alert(self, alert_type: str, channel_id: int, channel_name: str, extra_data: Dict = None):
        """创建告警"""
        if alert_type not in self.config['alert_templates']:
            return None
        
        template = self.config['alert_templates'][alert_type]
        
        # 生成告警ID
        alert_id = f"alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.alert_history) + 1}"
        
        # 根据语言选择音频文件
        language = self.config.get('language', 'zh-CN')
        audio_file = template.get('audio_file_en') if language == 'en-US' else template.get('audio_file')
        
        alert = {
            'id': alert_id,
            'type': alert_type,
            'type_name': template['name'],
            'message': template['message'],
            'level': template['level'],
            'color': template['color'],
            'channel_id': channel_id,
            'channel_name': channel_name,
            'timestamp': datetime.now().isoformat(),
            'time_str': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'voice_enabled': self.config['voice_enabled'],
            'popup_enabled': self.config['popup_enabled'],
            'audio_file': audio_file if self.config['voice_enabled'] else None,
            'extra_data': extra_data or {}
        }
        
        self.alert_history.append(alert)
        
        # 只保留最近1000条告警
        if len(self.alert_history) > 1000:
            self.alert_history = self.alert_history[-1000:]
        
        # 触发推送
        self._trigger_push(alert)
        
        return alert
    
    def _trigger_push(self, alert: Dict):
        """触发告警推送"""
        try:
            # 延迟导入避免循环依赖
            if self.push_manager is None:
                from safehat.managers.push_manager import get_push_manager
                self.push_manager = get_push_manager()
            
            # 构建推送数据
            push_data = {
                'id': alert['id'],
                'type': f"{alert['type']}_violation",  # 转换为violation类型
                'type_name': alert['type_name'],
                'message': alert['message'],
                'risk_level': self._convert_level_to_chinese(alert['level']),
                'confidence': alert.get('extra_data', {}).get('confidence', 0.9),
                'location': alert.get('extra_data', {}).get('location', '未知区域'),
                'timestamp': alert['timestamp'],
                'video_source_id': str(alert['channel_id']),
                'video_source_name': alert['channel_name'],
                'video_source_type': alert.get('extra_data', {}).get('source_type', 'camera'),
                'video_url': alert.get('extra_data', {}).get('video_url', ''),
                'snapshot_url': alert.get('extra_data', {}).get('snapshot_url', ''),
                'snapshot_base64': alert.get('extra_data', {}).get('snapshot_base64', ''),
                'detection_details': alert.get('extra_data', {}).get('detection_details', {})
            }
            
            # 异步推送
            self.push_manager.push_alert(push_data)
            logger.info(f"触发告警推送: {alert['type_name']} @ {alert['channel_name']}")
            
        except Exception as e:
            logger.error(f"触发告警推送失败: {e}")
    
    def _convert_level_to_chinese(self, level: str) -> str:
        """将告警级别转换为中文"""
        level_map = {
            'low': '低',
            'medium': '中',
            'high': '高',
            'critical': '高'
        }
        return level_map.get(level, '中')
    
    def get_recent_alerts(self, limit: int = 50) -> List[Dict]:
        """获取最近的告警"""
        return self.alert_history[-limit:]
    
    def get_alert_statistics(self):
        """获取告警统计"""
        stats = {}
        for alert in self.alert_history:
            alert_type = alert['type']
            if alert_type not in stats:
                stats[alert_type] = 0
            stats[alert_type] += 1
        return stats
    
    def clear_history(self):
        """清除历史告警"""
        self.alert_history = []


# 全局告警管理器实例
alert_manager = AlertManager()


