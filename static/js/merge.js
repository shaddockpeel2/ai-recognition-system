// 合流通道管理JavaScript

let configs = [];
let channels = [];
let selectedChannels = [];
let currentLayout = '1';
let currentEditingId = null;

// 页面加载完成后执行
document.addEventListener('DOMContentLoaded', function() {
    loadConfigs();
    loadChannels();
    
    // 绑定表单提交事件
    document.getElementById('configForm').addEventListener('submit', saveConfig);
});

// 加载配置列表
async function loadConfigs() {
    try {
        const response = await fetch('/api/merge');
        const data = await response.json();
        
        if (data.success) {
            configs = data.data;
            renderConfigs();
        }
    } catch (error) {
        console.error('加载配置列表失败:', error);
    }
}

// 加载通道列表
async function loadChannels() {
    try {
        const response = await fetch('/api/channels');
        const data = await response.json();
        
        if (data.success) {
            channels = data.data.filter(c => c.status === 'online');
        }
    } catch (error) {
        console.error('加载通道列表失败:', error);
    }
}

// 渲染配置列表
function renderConfigs() {
    const tbody = document.getElementById('configsBody');
    
    if (configs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-gray-400">暂无配置数据</td></tr>';
        return;
    }
    
    tbody.innerHTML = configs.map(config => `
        <tr class="border-b border-gray-700 hover:bg-gray-800 transition-all">
            <td class="py-3 px-4">${config.id}</td>
            <td class="py-3 px-4">${config.name}</td>
            <td class="py-3 px-4">
                <span class="px-2 py-1 rounded text-xs" style="background: rgba(0, 212, 255, 0.2); color: #00d4ff">
                    ${config.layout}分屏 (${config.layout === '1' ? '1x1' : config.layout === '4' ? '2x2' : '3x3'})
                </span>
            </td>
            <td class="py-3 px-4">${config.channels?.length || 0} 个通道</td>
            <td class="py-3 px-4">
                ${config.is_active 
                    ? '<span class="px-2 py-1 rounded text-xs bg-green-500 bg-opacity-20 text-green-400">已激活</span>'
                    : '<span class="px-2 py-1 rounded text-xs bg-gray-500 bg-opacity-20 text-gray-400">未激活</span>'
                }
            </td>
            <td class="py-3 px-4">
                <div class="flex space-x-2">
                    ${!config.is_active ? `
                        <button onclick="activateConfig(${config.id})" class="text-cyber-green hover:text-cyber-blue text-sm">
                            激活
                        </button>
                    ` : ''}
                    <button onclick="editConfig(${config.id})" class="text-cyber-blue hover:text-cyber-green text-sm">
                        编辑
                    </button>
                    <button onclick="deleteConfig(${config.id})" class="text-red-400 hover:text-red-500 text-sm">
                        删除
                    </button>
                </div>
            </td>
        </tr>
    `).join('');
}

// 显示新增模态框
function showAddModal() {
    currentEditingId = null;
    selectedChannels = [];
    currentLayout = '1';
    document.getElementById('modalTitle').textContent = '新增合流配置';
    document.getElementById('configForm').reset();
    document.getElementById('configId').value = '';
    
    renderChannelsList();
    updateLayout();
    
    document.getElementById('configModal').classList.remove('hidden');
}

// 编辑配置
function editConfig(id) {
    const config = configs.find(c => c.id === id);
    if (!config) return;
    
    currentEditingId = id;
    currentLayout = config.layout;
    selectedChannels = config.channels ? config.channels.map(c => c.channel_id) : [];
    
    document.getElementById('modalTitle').textContent = '编辑合流配置';
    document.getElementById('configId').value = id;
    document.getElementById('configName').value = config.name;
    
    // 设置布局单选按钮
    document.querySelector(`input[name="layout"][value="${config.layout}"]`).checked = true;
    
    renderChannelsList();
    updateLayout();
    
    document.getElementById('configModal').classList.remove('hidden');
}

// 渲染通道列表
function renderChannelsList() {
    const container = document.getElementById('channelsList');
    
    if (channels.length === 0) {
        container.innerHTML = '<div class="text-gray-400 text-center py-4">暂无在线通道</div>';
        return;
    }
    
    container.innerHTML = channels.map(channel => `
        <div class="flex items-center space-x-3 mb-2 p-2 rounded hover:bg-gray-700 transition-all cursor-pointer"
             onclick="toggleChannel(${channel.id})">
            <input type="checkbox" id="channel-${channel.id}" ${selectedChannels.includes(channel.id) ? 'checked' : ''}
                   class="w-4 h-4">
            <div class="flex-1">
                <div class="text-white">${channel.name}</div>
                <div class="text-xs text-gray-400">${channel.protocol} - ${channel.resolution || '未知分辨率'}</div>
            </div>
            ${selectedChannels.includes(channel.id) ? `
                <span class="px-2 py-1 rounded text-xs bg-cyber-blue bg-opacity-20 text-cyber-blue">
                    位置 ${selectedChannels.indexOf(channel.id) + 1}
                </span>
            ` : ''}
        </div>
    `).join('');
}

// 切换通道选择
function toggleChannel(channelId) {
    const maxChannels = parseInt(currentLayout);
    const index = selectedChannels.indexOf(channelId);
    
    if (index > -1) {
        selectedChannels.splice(index, 1);
    } else {
        if (selectedChannels.length >= maxChannels) {
            showMessage(`${currentLayout}分屏最多支持${maxChannels}个通道`, 'error');
            return;
        }
        selectedChannels.push(channelId);
    }
    
    renderChannelsList();
    updatePreview();
    updateSelectedCount();
}

// 更新布局
function updateLayout() {
    currentLayout = document.querySelector('input[name="layout"]:checked').value;
    
    // 如果选中的通道超过了最大数量，截断
    const maxChannels = parseInt(currentLayout);
    if (selectedChannels.length > maxChannels) {
        selectedChannels = selectedChannels.slice(0, maxChannels);
        renderChannelsList();
    }
    
    updateSelectedCount();
    updatePreview();
}

// 更新选中计数
function updateSelectedCount() {
    document.getElementById('selectedCount').textContent = selectedChannels.length;
    document.getElementById('maxChannels').textContent = currentLayout;
}

// 更新预览
function updatePreview() {
    const container = document.getElementById('layoutPreview');
    const gridSize = currentLayout === '1' ? 1 : currentLayout === '4' ? 2 : 3;
    const maxChannels = parseInt(currentLayout);
    
    container.style.gridTemplateColumns = `repeat(${gridSize}, 1fr)`;
    
    const cells = [];
    for (let i = 0; i < maxChannels; i++) {
        const channelId = selectedChannels[i];
        const channel = channels.find(c => c.id === channelId);
        
        cells.push(`
            <div class="aspect-video rounded flex flex-col items-center justify-center"
                 style="background: ${channelId ? 'rgba(0, 212, 255, 0.1)' : 'rgba(255, 255, 255, 0.05)'}; 
                        border: 1px solid ${channelId ? '#00d4ff' : '#444'}">
                <div class="text-xs text-gray-400 mb-2">位置 ${i + 1}</div>
                ${channel ? `
                    <div class="text-sm text-cyber-blue text-center px-2">${channel.name}</div>
                    <div class="text-xs text-gray-400 mt-1">${channel.resolution || '-'}</div>
                ` : '<div class="text-gray-500 text-sm">空位置</div>'}
            </div>
        `);
    }
    
    container.innerHTML = cells.join('');
}

// 保存配置
async function saveConfig(e) {
    e.preventDefault();
    
    const configData = {
        name: document.getElementById('configName').value,
        layout: currentLayout,
        channels: selectedChannels.map((channelId, index) => ({
            channel_id: channelId,
            position: index
        }))
    };
    
    try {
        const url = currentEditingId 
            ? `/api/merge/${currentEditingId}`
            : '/api/merge';
        
        const method = currentEditingId ? 'PUT' : 'POST';
        
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configData)
        });
        
        const data = await response.json();
        
        if (data.success) {
            showMessage(currentEditingId ? '更新成功' : '添加成功', 'success');
            closeModal();
            loadConfigs();
        } else {
            showMessage(data.message || '操作失败', 'error');
        }
    } catch (error) {
        console.error('保存配置失败:', error);
        showMessage('保存配置失败', 'error');
    }
}

// 激活配置
async function activateConfig(id) {
    try {
        const response = await fetch(`/api/merge/${id}/activate`, {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showMessage('激活成功', 'success');
            loadConfigs();
        } else {
            showMessage(data.message || '激活失败', 'error');
        }
    } catch (error) {
        console.error('激活配置失败:', error);
        showMessage('激活配置失败', 'error');
    }
}

// 删除配置
async function deleteConfig(id) {
    if (!confirm('确定要删除这个配置吗？')) return;
    
    try {
        const response = await fetch(`/api/merge/${id}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showMessage('删除成功', 'success');
            loadConfigs();
        } else {
            showMessage(data.message || '删除失败', 'error');
        }
    } catch (error) {
        console.error('删除配置失败:', error);
        showMessage('删除配置失败', 'error');
    }
}

// 关闭模态框
function closeModal() {
    document.getElementById('configModal').classList.add('hidden');
}

// 显示消息提示
function showMessage(message, type = 'success') {
    const messageEl = document.createElement('div');
    messageEl.className = `fixed top-4 right-4 px-6 py-3 rounded-lg font-bold text-white z-50 ${
        type === 'success' ? 'bg-green-500' : 'bg-red-500'
    }`;
    messageEl.textContent = message;
    
    document.body.appendChild(messageEl);
    
    setTimeout(() => {
        document.body.removeChild(messageEl);
    }, 3000);
}


