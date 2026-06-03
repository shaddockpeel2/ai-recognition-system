"""
通道管理路由

提供 /api/channels 相关的 REST 接口
"""
from flask import Blueprint, jsonify, request

from safehat.managers.channel_manager import channel_manager


channels_bp = Blueprint("channels", __name__, url_prefix="/api/channels")


@channels_bp.route("", methods=["GET", "POST"])
def handle_channels():
    if request.method == "GET":
        channels = channel_manager.get_all_channels()
        return jsonify({"success": True, "data": channels})

    data = request.get_json() or {}
    try:
        channel = channel_manager.add_channel(data)
        return jsonify({"success": True, "data": channel})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:  # pragma: no cover
        return jsonify({"success": False, "message": f"创建通道失败: {exc}"}), 500


@channels_bp.route("/<int:channel_id>", methods=["GET", "PUT", "DELETE"])
def handle_channel_detail(channel_id: int):
    if request.method == "GET":
        channel = channel_manager.get_channel(channel_id)
        if channel:
            return jsonify({"success": True, "data": channel})
        return jsonify({"success": False, "message": "通道不存在"}), 404

    if request.method == "DELETE":
        success = channel_manager.delete_channel(channel_id)
        if success:
            return jsonify({"success": True, "message": "删除成功"})
        return jsonify({"success": False, "message": "删除失败"}), 404

    data = request.get_json() or {}
    try:
        channel = channel_manager.update_channel(channel_id, data)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:  # pragma: no cover
        return jsonify({"success": False, "message": f"更新通道失败: {exc}"}), 500

    if channel:
        return jsonify({"success": True, "data": channel})
    return jsonify({"success": False, "message": "通道不存在"}), 404


@channels_bp.route("/<int:channel_id>/check", methods=["POST"])
def check_channel(channel_id: int):
    success = channel_manager.check_channel(channel_id)
    channel = channel_manager.get_channel(channel_id)
    return jsonify({"success": success, "data": channel})


@channels_bp.route("/check-all", methods=["POST"])
def check_all_channels():
    channel_manager.check_all_channels()
    return jsonify({"success": True, "message": "已触发所有通道检测"})


__all__ = ["channels_bp"]

