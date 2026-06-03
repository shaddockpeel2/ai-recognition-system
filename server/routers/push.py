"""
数据推送API路由
"""

from flask import Blueprint, request, jsonify
from safehat.managers.push_manager import get_push_manager
import logging

logger = logging.getLogger(__name__)

push_bp = Blueprint('push', __name__, url_prefix='/api/push')


@push_bp.route('/targets', methods=['GET'])
def get_targets():
    """获取所有推送目标"""
    try:
        manager = get_push_manager()
        targets = manager.get_targets()
        
        return jsonify({
            'success': True,
            'data': targets,
            'total': len(targets)
        })
    except Exception as e:
        logger.error(f"获取推送目标失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500


@push_bp.route('/targets', methods=['POST'])
def create_target():
    """创建推送目标"""
    try:
        data = request.get_json()
        
        # 验证必填字段
        if not data.get('name'):
            return jsonify({
                'success': False,
                'message': '请输入推送目标名称'
            }), 400
        
        if not data.get('type'):
            return jsonify({
                'success': False,
                'message': '请选择推送类型'
            }), 400
        
        manager = get_push_manager()
        target_id = manager.add_target(data)
        
        return jsonify({
            'success': True,
            'message': '创建成功',
            'data': {'id': target_id}
        })
    except Exception as e:
        logger.error(f"创建推送目标失败: {e}")
        return jsonify({
            'success': False,
            'message': f'创建失败: {str(e)}'
        }), 500


@push_bp.route('/targets/<target_id>', methods=['GET'])
def get_target(target_id):
    """获取单个推送目标"""
    try:
        manager = get_push_manager()
        target = manager.get_target(target_id)
        
        if not target:
            return jsonify({
                'success': False,
                'message': '推送目标不存在'
            }), 404
        
        return jsonify({
            'success': True,
            'data': target
        })
    except Exception as e:
        logger.error(f"获取推送目标失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500


@push_bp.route('/targets/<target_id>', methods=['PUT'])
def update_target(target_id):
    """更新推送目标"""
    try:
        data = request.get_json()
        
        manager = get_push_manager()
        success = manager.update_target(target_id, data)
        
        if not success:
            return jsonify({
                'success': False,
                'message': '推送目标不存在'
            }), 404
        
        return jsonify({
            'success': True,
            'message': '更新成功'
        })
    except Exception as e:
        logger.error(f"更新推送目标失败: {e}")
        return jsonify({
            'success': False,
            'message': f'更新失败: {str(e)}'
        }), 500


@push_bp.route('/targets/<target_id>', methods=['DELETE'])
def delete_target(target_id):
    """删除推送目标"""
    try:
        manager = get_push_manager()
        success = manager.delete_target(target_id)
        
        if not success:
            return jsonify({
                'success': False,
                'message': '推送目标不存在'
            }), 404
        
        return jsonify({
            'success': True,
            'message': '删除成功'
        })
    except Exception as e:
        logger.error(f"删除推送目标失败: {e}")
        return jsonify({
            'success': False,
            'message': f'删除失败: {str(e)}'
        }), 500


@push_bp.route('/targets/<target_id>/test', methods=['POST'])
def test_target(target_id):
    """测试推送连接"""
    try:
        manager = get_push_manager()
        result = manager.test_target(target_id)
        
        return jsonify({
            'success': result.get('success', False),
            'message': result.get('message', '')
        })
    except Exception as e:
        logger.error(f"测试推送连接失败: {e}")
        return jsonify({
            'success': False,
            'message': f'测试失败: {str(e)}'
        }), 500


@push_bp.route('/test_connection', methods=['POST'])
def test_connection():
    """测试推送连接（不保存配置）"""
    try:
        from safehat.managers.push_manager import PushTarget, BasePushHandler
        from safehat.managers.push_manager import (
            AppPushHandler, ModbusPushHandler, UartPushHandler,
            GpioPushHandler, Rs232PushHandler, Rs485PushHandler,
        )
        
        data = request.get_json()
        
        # 验证必填字段
        if not data.get('name') or not data.get('type'):
            return jsonify({
                'success': False,
                'message': '缺少必填字段'
            }), 400
        
        # 创建临时推送目标
        target = PushTarget({
            'id': 'test_temp',
            'name': data.get('name'),
            'type': data.get('type'),
            'enabled': True,
            'connection': data.get('connection', {}),
            'filter': data.get('filter', {}),
            'retry': data.get('retry', {}),
            'rate_limit': data.get('rate_limit', {})
        })
        
        # 创建对应的处理器
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
        if not handler_class:
            return jsonify({
                'success': False,
                'message': f'不支持的推送类型: {target.type}'
            }), 400
        
        # 测试连接
        handler = handler_class(target)
        result = handler.test_connection()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"测试连接失败: {e}")
        return jsonify({
            'success': False,
            'message': f'测试失败: {str(e)}'
        }), 500


@push_bp.route('/targets/<target_id>/toggle', methods=['POST'])
def toggle_target(target_id):
    """启用/停用推送目标"""
    try:
        data = request.get_json()
        enabled = data.get('enabled', True)
        
        manager = get_push_manager()
        target = manager.targets.get(target_id)
        
        if not target:
            return jsonify({
                'success': False,
                'message': '推送目标不存在'
            }), 404
        
        target.enabled = enabled
        manager.save_config()
        
        return jsonify({
            'success': True,
            'message': '已启用' if enabled else '已停用'
        })
    except Exception as e:
        logger.error(f"切换推送状态失败: {e}")
        return jsonify({
            'success': False,
            'message': f'操作失败: {str(e)}'
        }), 500


@push_bp.route('/logs', methods=['GET'])
def get_logs():
    """获取推送日志"""
    try:
        limit = request.args.get('limit', 100, type=int)
        
        manager = get_push_manager()
        logs = manager.get_logs(limit)
        
        # 统计成功率
        total = len(logs)
        success_count = sum(1 for log in logs if log['success'])
        success_rate = (success_count / total * 100) if total > 0 else 0
        
        return jsonify({
            'success': True,
            'data': logs,
            'meta': {
                'total': total,
                'success_count': success_count,
                'failed_count': total - success_count,
                'success_rate': round(success_rate, 2)
            }
        })
    except Exception as e:
        logger.error(f"获取推送日志失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500


@push_bp.route('/send', methods=['POST'])
def send_test():
    """手动发送测试推送"""
    try:
        data = request.get_json()
        
        # 构造测试告警数据
        test_alert = {
            'id': 'test_alert_001',
            'type': data.get('type', 'helmet_violation'),
            'type_name': data.get('type_name', '测试告警'),
            'message': data.get('message', '这是一条测试告警'),
            'risk_level': data.get('risk_level', '高'),
            'confidence': 0.95,
            'location': data.get('location', '测试区域'),
            'timestamp': '2024-12-16 15:00:00',
            'video_source_id': 'test_camera',
            'video_source_name': '测试摄像头',
            'video_source_type': 'camera',
            'snapshot_url': 'http://localhost:5000/static/test.jpg'
        }
        
        manager = get_push_manager()
        manager.push_alert(test_alert)
        
        return jsonify({
            'success': True,
            'message': '测试推送已发送'
        })
    except Exception as e:
        logger.error(f"发送测试推送失败: {e}")
        return jsonify({
            'success': False,
            'message': f'发送失败: {str(e)}'
        }), 500


@push_bp.route('/stats', methods=['GET'])
def get_stats():
    """获取推送统计"""
    try:
        manager = get_push_manager()
        targets = manager.get_targets()
        logs = manager.get_logs(1000)
        
        # 统计数据
        total_targets = len(targets)
        enabled_targets = sum(1 for t in targets if t.get('enabled', True))
        total_pushes = len(logs)
        success_pushes = sum(1 for log in logs if log['success'])
        failed_pushes = total_pushes - success_pushes
        
        # 按类型统计
        type_stats = {}
        for target in targets:
            t_type = target.get('type', 'unknown')
            if t_type not in type_stats:
                type_stats[t_type] = {'count': 0, 'enabled': 0}
            type_stats[t_type]['count'] += 1
            if target.get('enabled', True):
                type_stats[t_type]['enabled'] += 1
        
        return jsonify({
            'success': True,
            'data': {
                'total_targets': total_targets,
                'enabled_targets': enabled_targets,
                'disabled_targets': total_targets - enabled_targets,
                'total_pushes': total_pushes,
                'success_pushes': success_pushes,
                'failed_pushes': failed_pushes,
                'success_rate': round(success_pushes / total_pushes * 100, 2) if total_pushes > 0 else 0,
                'type_stats': type_stats
            }
        })
    except Exception as e:
        logger.error(f"获取推送统计失败: {e}")
        return jsonify({
            'success': False,
            'message': f'获取失败: {str(e)}'
        }), 500

