"""
通道管理模块

此模块作为 Flask 旧版接口与新的服务层之间的兼容层，实际逻辑由
`ChannelService` 提供。
"""

from server.services.channel_service import ChannelService


class ChannelManager(ChannelService):
    """
    ChannelService 的轻量别名，保留原有类名和导入路径，避免大量改动。
    """

    pass


# 向外暴露与之前相同的全局实例
channel_manager = ChannelManager()


