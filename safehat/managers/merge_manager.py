"""
合流通道管理模块 - 管理数据大屏的拼屏配置
"""
import json
import os
from datetime import datetime

class MergeManager:
    """合流通道管理器"""
    
    def __init__(self, data_file='data/merge_configs.json'):
        self.data_file = data_file
        self.configs = []
        self.load_configs()
    
    def load_configs(self):
        """加载配置"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.configs = json.load(f)
            except:
                self.configs = []
        else:
            os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
            self.configs = []
    
    def save_configs(self):
        """保存配置"""
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.configs, f, ensure_ascii=False, indent=2)
    
    def add_config(self, config_data):
        """添加合流配置"""
        # 生成新ID
        if self.configs:
            new_id = max(c['id'] for c in self.configs) + 1
        else:
            new_id = 1
        
        config = {
            'id': new_id,
            'name': config_data.get('name', f'配置{new_id}'),
            'layout': config_data.get('layout', '4'),  # 1/4/9分屏
            'channels': config_data.get('channels', []),  # [{'channel_id': 1, 'position': 0}, ...]
            'is_active': False,
            'created_at': datetime.now().isoformat()
        }
        
        self.configs.append(config)
        self.save_configs()
        return config
    
    def update_config(self, config_id, config_data):
        """更新配置"""
        config = self.get_config(config_id)
        if not config:
            return None
        
        config['name'] = config_data.get('name', config['name'])
        config['layout'] = config_data.get('layout', config['layout'])
        config['channels'] = config_data.get('channels', config['channels'])
        
        self.save_configs()
        return config
    
    def delete_config(self, config_id):
        """删除配置"""
        self.configs = [c for c in self.configs if c['id'] != config_id]
        self.save_configs()
        return True
    
    def get_config(self, config_id):
        """获取单个配置"""
        for config in self.configs:
            if config['id'] == config_id:
                return config
        return None
    
    def get_all_configs(self):
        """获取所有配置"""
        return self.configs
    
    def activate_config(self, config_id):
        """激活配置"""
        # 先将所有配置设为非激活
        for config in self.configs:
            config['is_active'] = False
        
        # 激活指定配置
        config = self.get_config(config_id)
        if config:
            config['is_active'] = True
            self.save_configs()
            return config
        return None
    
    def get_active_config(self):
        """获取当前激活的配置"""
        for config in self.configs:
            if config.get('is_active', False):
                return config
        return None


# 全局合流管理器实例
merge_manager = MergeManager()


