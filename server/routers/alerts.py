"""
告警管理路由
"""
from flask import Blueprint, jsonify, request

from safehat.managers.alert_manager import alert_manager

alerts_bp = Blueprint("alerts", __name__, url_prefix="/api/alerts")


@alerts_bp.route("/config", methods=["GET", "POST"])
def alert_config():
    if request.method == "GET":
        config = alert_manager.get_config()
        return jsonify({"success": True, "data": config})

    data = request.get_json() or {}
    alert_manager.update_config(data)
    return jsonify({"success": True, "message": "配置更新成功"})


@alerts_bp.route("", methods=["POST"])
def create_alert():
    data = request.get_json() or {}
    alert = alert_manager.create_alert(
        alert_type=data.get("type"),
        channel_id=data.get("channel_id"),
        channel_name=data.get("channel_name"),
        extra_data=data.get("extra_data"),
    )
    return jsonify({"success": True, "data": alert})


@alerts_bp.route("/recent", methods=["GET"])
def recent_alerts():
    limit = request.args.get("limit", 50, type=int)
    alerts = alert_manager.get_recent_alerts(limit)
    return jsonify({"success": True, "data": alerts})


@alerts_bp.route("/statistics", methods=["GET"])
def alert_stats():
    stats = alert_manager.get_alert_statistics()
    return jsonify({"success": True, "data": stats})


__all__ = ["alerts_bp"]

