"""YouTube Live Adapter 配置定义

使用 BaseConfig + @config_section 模式，与 bilibili_live_adapter 保持一致。

配置分区：
- plugin: 插件开关与调试
- youtube: YouTube 连接配置（video_id、语言、代理等）
- connection: 轮询与重连参数
- filter: 消息过滤规则
- superchat: Super Chat 货币转换
- outbound: 出站消息配置（OAuth2 凭证、live_chat_id）
"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class YouTubeLiveAdapterConfig(BaseConfig):
    """YouTube Live Adapter 配置"""

    config_name: ClassVar[str] = "config"
    config_description: ClassVar[str] = "YouTube 直播间弹幕接入适配器配置"

    @config_section("plugin", title="插件设置")
    class PluginSection(SectionBase):
        """插件开关与调试选项"""

        enabled: bool = Field(
            default=True,
            description="是否启用 YouTube Live Adapter",
        )
        debug_mode: bool = Field(
            default=False,
            description="调试模式，输出未处理的消息类型和原始数据",
        )

    @config_section("youtube", title="YouTube 连接")
    class YouTubeSection(SectionBase):
        """YouTube 直播间连接配置

        video_id 为必填项，可在 YouTube 直播间 URL 中获取。
        client_name 决定 Inner Tube API 的客户端标识，不同客户端的风控等级不同。
        """

        video_id: str = Field(
            default="",
            description="直播视频 ID（必填），从 YouTube 直播间 URL 中提取",
        )
        language: str = Field(
            default="zh",
            description="界面语言代码，影响 API 返回的部分本地化字段",
        )
        proxy_url: str = Field(
            default="",
            description="HTTP 代理地址，留空则直连。格式：http://host:port 或 socks5://host:port",
        )
        client_name: str = Field(
            default="IOS",
            description="Inner Tube API 客户端标识：WEB / IOS / TV。IOS 和 TV 通常不需要 PO Token",
            input_type="select",
            choices=["WEB", "IOS", "TV"],
        )

    @config_section("connection", title="轮询与重连")
    class ConnectionSection(SectionBase):
        """HTTP 轮询与自动重连参数

        轮询间隔会自适应调整：有消息时保持常规间隔，无消息时逐步增大至上限。
        重连使用指数退避 + 随机抖动策略，避免雪崩。
        """

        poll_interval: float = Field(
            default=2.5,
            description="基础轮询间隔（秒），有新消息时保持此间隔",
            ge=1.0,
            le=10.0,
        )
        max_poll_interval: float = Field(
            default=60.0,
            description="最大轮询间隔（秒），无消息时逐步增大至此上限",
            ge=10.0,
            le=120.0,
        )
        auto_reconnect: bool = Field(
            default=True,
            description="连接断开后是否自动重连",
        )
        reconnect_initial_delay: float = Field(
            default=2.0,
            description="重连初始延迟（秒）",
            ge=1.0,
            le=30.0,
        )
        reconnect_max_delay: float = Field(
            default=60.0,
            description="重连最大延迟（秒）",
            ge=10.0,
            le=300.0,
        )
        reconnect_backoff_multiplier: float = Field(
            default=2.0,
            description="重连退避倍数，每次失败延迟乘以此值",
            ge=1.5,
            le=5.0,
        )
        request_timeout: float = Field(
            default=15.0,
            description="HTTP 请求超时（秒）",
            ge=5.0,
            le=60.0,
        )

    @config_section("filter", title="消息过滤")
    class FilterSection(SectionBase):
        """消息过滤规则"""

        filter_emoji: bool = Field(
            default=False,
            description="是否过滤消息中的 emoji",
        )
        remove_hashtags: bool = Field(
            default=False,
            description="是否移除消息中的 hashtag",
        )
        max_message_length: int = Field(
            default=500,
            description="消息最大长度，超过则截断",
            ge=50,
            le=5000,
        )
        ignored_message_types: list[str] = Field(
            default_factory=lambda: ["messageDeletedEvent", "userBannedEvent"],
            description="忽略的消息类型列表",
            input_type="list",
            item_type="str",
        )

    @config_section("superchat", title="Super Chat")
    class SuperChatSection(SectionBase):
        """Super Chat 货币转换配置"""

        enable_currency_conversion: bool = Field(
            default=False,
            description="是否启用 SC 金额货币转换（转为 CNY）",
        )
        exchange_rate_api_url: str = Field(
            default="https://api.exchangerate-api.com/v4/latest/USD",
            description="汇率 API 地址，启用货币转换时使用",
        )

    @config_section("outbound", title="出站消息")
    class OutboundSection(SectionBase):
        """出站消息配置

        出入站消息必须同时配置完整才可启用适配器，缺一不可。
        如果 outbound.enabled = True 但 OAuth2 凭证任一为空，启动时报错。
        如果 outbound.enabled = False，适配器整体不启动（包括入站）。
        """

        enabled: bool = Field(
            default=True,
            description="是否启用出站消息（发送消息到直播间），出入站必须同时启用",
        )
        client_id: str = Field(
            default="",
            description="OAuth2 Client ID（必填，从 Google Cloud Console 获取）",
        )
        client_secret: str = Field(
            default="",
            description="OAuth2 Client Secret（必填，从 Google Cloud Console 获取）",
        )
        refresh_token: str = Field(
            default="",
            description="OAuth2 Refresh Token（必填，通过 OAuth2 授权流程获取）",
        )
        live_chat_id: str = Field(
            default="",
            description="Live Chat ID（留空则自动从 video_id 获取）",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    youtube: YouTubeSection = Field(default_factory=YouTubeSection)
    connection: ConnectionSection = Field(default_factory=ConnectionSection)
    filter: FilterSection = Field(default_factory=FilterSection)
    superchat: SuperChatSection = Field(default_factory=SuperChatSection)
    outbound: OutboundSection = Field(default_factory=OutboundSection)


__all__ = ["YouTubeLiveAdapterConfig"]
