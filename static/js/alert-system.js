// 告警系统JavaScript - 语音和弹窗功能

class AlertSystem {
    constructor() {
        this.config = {
            voice_enabled: true,
            popup_enabled: true,
            voice_volume: 0.8,
            language: 'zh-CN'  // 默认中文
        };
        this.audioCache = {};
        this.playedAlertIds = new Set();
        this.init();
    }

    getVoiceVolume() {
        const rawVolume = Number(this.config.voice_volume);
        if (!Number.isFinite(rawVolume)) return 0.8;
        return Math.max(0, Math.min(1, rawVolume > 1 ? rawVolume / 100 : rawVolume));
    }
    
    async init() {
        // 加载配置
        await this.loadConfig();
        
        // 创建弹窗容器
        this.createAlertContainer();
        
        // 预加载音频文件
        this.preloadAudio();
    }
    
    async loadConfig() {
        try {
            const response = await fetch('/api/alerts/config');
            const data = await response.json();
            if (data.success) {
                this.config = data.data;
            }
        } catch (error) {
            console.error('加载告警配置失败:', error);
        }
    }
    
    createAlertContainer() {
        if (document.getElementById('alert-popup-container')) return;
        
        const container = document.createElement('div');
        container.id = 'alert-popup-container';
        container.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 9999;
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-width: 400px;
        `;
        document.body.appendChild(container);
    }
    
    preloadAudio() {
        const audioFiles = {
            helmet_warning: '/static/audio/alerts/helmet_warning.MP3',
            safehat_vest_no_helmet: '/static/audio/safehat-vest/1s/no-helmet1s.mp3',
            safehat_vest_no_vest: '/static/audio/safehat-vest/1s/no-vest1s.mp3',
            safehat_vest_entry: '/static/audio/safehat-vest/1s/in1s.mp3',
            crack_warning: '/static/audio/alerts/crack_warning.mp3',
            intrusion_warning: '/static/audio/alerts/intrusion_warning.mp3',
            fall_warning: '/static/audio/alerts/fall_warning.mp3',
            reagent_bottle_warning: '/static/audio/alerts/reagent_bottle_warning.mp3',
            camera_occlusion_warning: '/static/audio/alerts/camera_occlusion_warning.mp3',
            fight_warning: '/static/audio/alerts/fight_warning.mp3',
            dog_warning: '/static/audio/alerts/dog_warning.mp3',
            cat_warning: '/static/audio/alerts/cat_warning.mp3'
        };
        
        Object.entries(audioFiles).forEach(([key, src]) => {
            const audio = new Audio(src);
            audio.preload = 'auto';
            audio.volume = this.getVoiceVolume();
            this.audioCache[key] = audio;
        });
    }
    
    // 显示告警
    showAlert(alertData) {
        if (alertData && alertData.id) {
            if (this.playedAlertIds.has(alertData.id)) {
                return;
            }
            this.playedAlertIds.add(alertData.id);
            if (this.playedAlertIds.size > 200) {
                const [oldestId] = this.playedAlertIds;
                this.playedAlertIds.delete(oldestId);
            }
        }

        // 播放语音
        let voicePlayed = false;
        if (this.config.voice_enabled) {
            voicePlayed = this.playVoice(alertData);
        }

        if (this.config.voice_enabled && !voicePlayed && typeof this.speakAlert === 'function') {
            this.speakAlert(alertData.voice_text || alertData.message || alertData.type_name);
        }
        
        // 显示弹窗
        if (this.config.popup_enabled) {
            this.showPopup(alertData);
        }
    }
    
    // 播放告警语音
    playVoice(alertData) {
        // 检查是否启用语音
        if (!this.config.voice_enabled) {
            console.log('告警语音已被禁用');
            return false;
        }
        
        const customFile = alertData.audio_file;
        const audioKey = alertData.audio_type || `${alertData.type}_warning`;
        const fallbackText = alertData.voice_text || alertData.message || alertData.type_name;
        
        if (customFile) {
            const audio = new Audio(customFile);
            audio.volume = this.getVoiceVolume();
            audio.play().catch(error => {
                console.error('播放告警语音失败:', error);
                this.speakAlert(fallbackText);
            });
            return true;
        }

        const cached = this.audioCache[audioKey];
        if (cached) {
            const playableAudio = cached.cloneNode(true);
            playableAudio.volume = this.getVoiceVolume();
            playableAudio.play().catch(error => {
                console.error('播放告警语音失败:', error);
                this.speakAlert(fallbackText);
            });
            return true;
        }

        return false;
    }

    speakAlert(text) {
        // 检查是否启用语音
        if (!this.config.voice_enabled) {
            console.log('告警语音已被禁用');
            return;
        }
        
        if (!('speechSynthesis' in window) || !text) return;
        
        // 根据语言设置不同的文本，避免“告警告警”重复播报
        let message = text;
        if (this.config.language === 'en-US') {
            message = `${text} alert detected`;
        } else {
            const normalizedText = String(text).trim();
            if (normalizedText === '安全帽告警') {
                message = '发现未佩戴安全帽告警';
            } else if (normalizedText === '反光衣告警') {
                message = '发现未穿戴反光衣告警';
            } else if (normalizedText === '未穿工作服' || normalizedText === '工作服告警') {
                message = '发现未穿工作服告警';
            } else if (/^(发现|检测到)/.test(normalizedText)) {
                message = normalizedText.endsWith('告警') ? normalizedText : `${normalizedText}告警`;
            } else {
                message = normalizedText.endsWith('告警')
                    ? `发现${normalizedText}`
                    : `发现${normalizedText}告警`;
            }
        }
        
        const utter = new SpeechSynthesisUtterance(message);
        utter.lang = this.config.language || 'zh-CN';
        utter.pitch = 1;
        utter.rate = 1;
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utter);
    }
    
    // 显示告警弹窗
    showPopup(alertData) {
        const container = document.getElementById('alert-popup-container');
        if (!container) return;
        
        const popup = document.createElement('div');
        popup.className = 'alert-popup';
        popup.style.cssText = `
            background: linear-gradient(135deg, ${alertData.color}22, ${alertData.color}11);
            border: 2px solid ${alertData.color};
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5), 0 0 20px ${alertData.color}44;
            animation: slideInRight 0.3s ease-out, pulse 0.5s ease-in-out 3;
            min-width: 300px;
        `;
        
        // 告警等级对应的图标
        const levelIcons = {
            'critical': '🚨',
            'high': '⚠️',
            'medium': '🔔',
            'low': 'ℹ️'
        };
        
        popup.innerHTML = `
            <div style="display: flex; align-items: start; gap: 12px;">
                <div style="font-size: 32px;">
                    ${levelIcons[alertData.level] || '⚠️'}
                </div>
                <div style="flex: 1;">
                    <div style="font-size: 18px; font-weight: bold; color: ${alertData.color}; margin-bottom: 8px;">
                        ${alertData.type_name}
                    </div>
                    <div style="color: #fff; margin-bottom: 4px;">
                        ${alertData.message}
                    </div>
                    <div style="font-size: 12px; color: #aaa;">
                        📹 ${alertData.channel_name} | ${alertData.time_str}
                    </div>
                </div>
                <button onclick="this.parentElement.parentElement.remove()" 
                        style="background: none; border: none; color: #fff; font-size: 20px; cursor: pointer; opacity: 0.6; padding: 0;">
                    ✕
                </button>
            </div>
        `;
        
        container.appendChild(popup);
        
        // 5秒后自动移除
        setTimeout(() => {
            if (popup.parentElement) {
                popup.style.animation = 'slideOutRight 0.3s ease-out';
                setTimeout(() => popup.remove(), 300);
            }
        }, 5000);
    }
    
    // 更新配置
    async updateConfig(newConfig) {
        try {
            const response = await fetch('/api/alerts/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newConfig)
            });
            
            const data = await response.json();
            if (data.success) {
                const previousLanguage = this.config.language;
                this.config = { ...this.config, ...newConfig };
                
                // 更新音量
                Object.values(this.audioCache).forEach(audio => {
                    audio.volume = this.getVoiceVolume();
                });
                
                // 如果语言变化，重新加载音频
                if (newConfig.language && newConfig.language !== previousLanguage) {
                    this.preloadAudio();
                }
                
                return true;
            }
            return false;
        } catch (error) {
            console.error('更新告警配置失败:', error);
            return false;
        }
    }
}

// 添加CSS动画
const style = document.createElement('style');
style.textContent = `
@keyframes slideInRight {
    from {
        transform: translateX(100%);
        opacity: 0;
    }
    to {
        transform: translateX(0);
        opacity: 1;
    }
}

@keyframes slideOutRight {
    from {
        transform: translateX(0);
        opacity: 1;
    }
    to {
        transform: translateX(100%);
        opacity: 0;
    }
}

@keyframes pulse {
    0%, 100% {
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
    }
    50% {
        box-shadow: 0 4px 24px rgba(255, 68, 68, 0.8), 0 0 30px rgba(255, 68, 68, 0.6);
    }
}
`;
document.head.appendChild(style);

// 全局实例
window.alertSystem = new AlertSystem();

// 测试函数（可以在控制台调用）
window.testAlert = function(type = 'helmet') {
    const testData = {
        type: type,
        type_name: '测试告警',
        message: '这是一个测试告警消息',
        level: 'high',
        color: '#ff4444',
        channel_name: '测试通道',
        time_str: new Date().toLocaleString('zh-CN'),
        audio_file: `/static/audio/alerts/${type}_warning.mp3`
    };
    
    window.alertSystem.showAlert(testData);
};
