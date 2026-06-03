"""
合流通道路由
"""
from flask import Blueprint, jsonify, request

from safehat.managers.merge_manager import merge_manager
from safehat.managers.channel_manager import channel_manager


merge_bp = Blueprint("merge", __name__, url_prefix="/api/merge")


@merge_bp.route("", methods=["GET", "POST"])
def manage_merge():
    if request.method == "GET":
        configs = merge_manager.get_all_configs()
        return jsonify({"success": True, "data": configs})

    data = request.get_json() or {}
    config = merge_manager.add_config(data)
    return jsonify({"success": True, "data": config})


@merge_bp.route("/<int:config_id>", methods=["GET", "PUT", "DELETE"])
def merge_detail(config_id: int):
    if request.method == "GET":
        config = merge_manager.get_config(config_id)
        if config:
            return jsonify({"success": True, "data": config})
        return jsonify({"success": False, "message": "配置不存在"}), 404

    if request.method == "DELETE":
        success = merge_manager.delete_config(config_id)
        if success:
            return jsonify({"success": True, "message": "删除成功"})
        return jsonify({"success": False, "message": "删除失败"}), 404

    data = request.get_json() or {}
    config = merge_manager.update_config(config_id, data)
    if config:
        return jsonify({"success": True, "data": config})
    return jsonify({"success": False, "message": "配置不存在"}), 404


@merge_bp.route("/<int:config_id>/activate", methods=["POST"])
def activate_merge(config_id: int):
    config = merge_manager.activate_config(config_id)
    if config:
        return jsonify({"success": True, "data": config})
    return jsonify({"success": False, "message": "配置不存在"}), 404


@merge_bp.route("/active", methods=["GET"])
def get_active_merge():
    config = merge_manager.get_active_config()
    if not config:
        return jsonify({"success": False, "message": "当前没有激活的合流配置"}), 404

    channels_data = []
    for slot in config.get("channels", []):
        channel_id = slot.get("channel_id")
        if not channel_id:
            continue
        channel = channel_manager.get_channel(channel_id)
        if channel:
            channels_data.append(
                {
                    "slot": slot.get("position"),
                    "channel": channel,
                }
            )

    return jsonify({"success": True, "data": {"config": config, "channels": channels_data}})


__all__ = ["merge_bp"]

