"""YouTube Live Adapter - YouTube 直播间弹幕接入适配器

将 YouTube Live Chat（Inner Tube API）的弹幕/SC/会员消息
转换为 MessageEnvelope 投递至 Neo-MoFox 消息总线。
"""

__version__ = "0.1.0"
__author__ = "MoFox Team"

__plugin_meta__ = {
    "name": "youtube_live_adapter",
    "version": __version__,
    "author": __author__,
    "description": "YouTube 直播间弹幕/SC/会员消息接入适配器",
    "entry_point": "plugin.py",
}
