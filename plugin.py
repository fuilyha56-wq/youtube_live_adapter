"""YouTube Live Adapter 插件入口

包含插件注册（YouTubeLiveAdapterPlugin）和适配器生命周期管理（YouTubeLiveAdapter）。

YouTubeLiveAdapterPlugin：
- 使用 @register_plugin 装饰器注册
- 声明配置类和组件列表

YouTubeLiveAdapter：
- 继承 BaseAdapter，不传 transport（非 WebSocket 模式）
- 通过 HTTP 轮询获取 YouTube Live Chat 消息（入站）
- 通过 YouTube Data API v3 发送消息到直播间（出站）
- 使用 YouTubePollClient 管理轮询循环
- 使用 YouTubeMessageDispatcher 分发消息
- 使用 YouTubeLiveChatSender 发送出站消息
- 指数退避 + 随机抖动重连策略
- system_reminder 机制：定期将直播间在线人数推送到 prompt

出入站绑定约束：
- 适配器只有在入站（video_id）+ 出站（OAuth2 凭证）都配置完整时才运行
- outbound.enabled = False 时适配器整体不启动
- outbound.enabled = True 但 OAuth2 凭证缺一不可，否则拒绝启动
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from typing import Any, ClassVar, cast

from mofox_wire import CoreSink, MessageEnvelope

from config import YouTubeLiveAdapterConfig
from src.api import YouTubeInnerTubeAPI
from src.app.plugin_system.api import prompt_api
from src.app.plugin_system.api.log_api import get_logger
from src.client import YouTubePollClient
from src.core.components.base import BaseAdapter, BasePlugin
from src.core.components.loader import register_plugin
from src.core.prompt import SystemReminderBucket, SystemReminderInsertType
from src.currency import CurrencyConverter
from src.dispatcher import (
    PLATFORM,
    YouTubeMessageDispatcher,
)
from src.kernel.concurrency import get_task_manager
from src.sender import YouTubeLiveChatSender

logger = get_logger("youtube_live_adapter")


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

# system_reminder 名称（观众人数状态）
_VIEWER_COUNT_REMINDER_NAME = "youtube_live_room_status"

# 观众人数刷新间隔（秒）
_VIEWER_COUNT_REFRESH_INTERVAL = 5.0


# ──────────────────────────────────────────────
# 插件注册
# ──────────────────────────────────────────────


@register_plugin
class YouTubeLiveAdapterPlugin(BasePlugin):
    """YouTube Live Adapter 插件。

    负责注册适配器组件和声明配置。
    """

    plugin_name = "youtube_live_adapter"
    plugin_version = "0.1.0"
    plugin_author = "MoFox Team"
    plugin_description = "YouTube 直播间弹幕/SC/会员消息接入适配器"
    configs: ClassVar[list[type]] = [YouTubeLiveAdapterConfig]

    def get_components(self) -> list[type]:
        """返回插件提供的组件列表。"""
        return [YouTubeLiveAdapter]


# ──────────────────────────────────────────────
# 适配器
# ──────────────────────────────────────────────


class YouTubeLiveAdapter(BaseAdapter):
    """YouTube 直播间弹幕接入适配器。

    通过 HTTP 轮询 YouTube Inner Tube API 获取直播间消息，
    经 Dispatcher 转换为 MessageEnvelope 后投递至消息总线。

    不使用 WebSocket 传输层，轮询和重连逻辑完全由自身管理。
    """

    adapter_name = "youtube_live_adapter"
    adapter_version = "0.1.0"
    adapter_author = "MoFox Team"
    adapter_description = "YouTube 直播间弹幕接入适配器"
    platform = "live"  # 与 bilibili 共享
    source_platform = "youtube_live"  # 唯一标识
    run_in_subprocess = False

    def __init__(
        self,
        core_sink: CoreSink,
        plugin: YouTubeLiveAdapterPlugin | None = None,
        **kwargs: Any,
    ) -> None:
        """初始化 YouTube Live 适配器。

        不传 transport（非 WebSocket 模式），轮询由 _session_loop 管理。

        Args:
            core_sink: 核心消息入口
            plugin: 插件实例
            **kwargs: 传递给 BaseAdapter 的额外参数
        """
        # 不传 transport（非 WebSocket）
        super().__init__(core_sink, plugin=plugin, **kwargs)

        self._api: YouTubeInnerTubeAPI | None = None
        self._client: YouTubePollClient | None = None
        self._dispatcher: YouTubeMessageDispatcher | None = None
        self._sender: YouTubeLiveChatSender | None = None
        self._poll_task_info: Any | None = None
        self._stopping = False
        self._consecutive_failures: int = 0

        # 观众人数 system_reminder 相关状态
        self._viewer_count_reminder_task_info: Any | None = None
        self._last_published_viewer_count: int = -1
        self._current_viewer_count: int = 0

    # ──────────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────────

    async def on_adapter_loaded(self) -> None:
        """适配器加载回调：验证配置，创建 API/Dispatcher/Sender 实例。

        出入站绑定校验：
        1. outbound.enabled 必须为 True（否则适配器整体不启动）
        2. OAuth2 凭证（client_id / client_secret / refresh_token）必须完整
        3. 任一不满足 → raise ValueError，适配器不启动
        """
        logger.info("YouTube Live Adapter 加载中...")
        config = self._get_config()

        # ── 入站校验 ──
        video_id = config.youtube.video_id.strip()
        if not video_id:
            raise ValueError("youtube.video_id 不能为空，请在配置中设置直播视频 ID")

        # ── 出站校验（出入站绑定） ──
        if not config.outbound.enabled:
            raise ValueError(
                "outbound.enabled 为 False，适配器整体不启动。"
                "出入站消息必须同时启用，请将 outbound.enabled 设为 True 并填写 OAuth2 凭证"
            )

        oauth_fields = {
            "client_id": config.outbound.client_id.strip(),
            "client_secret": config.outbound.client_secret.strip(),
            "refresh_token": config.outbound.refresh_token.strip(),
        }
        missing = [name for name, val in oauth_fields.items() if not val]
        if missing:
            raise ValueError(
                f"outbound 已启用但 OAuth2 凭证不完整，缺少: {', '.join(missing)}。"
                "出入站消息必须同时配置完整才可启用适配器"
            )

        # 创建 API 客户端
        self._api = YouTubeInnerTubeAPI(
            proxy=config.youtube.proxy_url,
            timeout=config.connection.request_timeout,
            language=config.youtube.language,
            client_name=config.youtube.client_name,
        )

        # 创建货币转换器（仅在启用时）
        currency_converter: CurrencyConverter | None = None
        if config.superchat.enable_currency_conversion:
            currency_converter = CurrencyConverter(
                api_url=config.superchat.exchange_rate_api_url,
            )
            logger.info("SC 货币转换已启用")

        # 创建 Dispatcher
        self._dispatcher = YouTubeMessageDispatcher(
            video_id=video_id,
            debug_mode=config.plugin.debug_mode,
            filter_emoji=config.filter.filter_emoji,
            remove_hashtags=config.filter.remove_hashtags,
            max_message_length=config.filter.max_message_length,
            ignored_message_types=config.filter.ignored_message_types,
            currency_converter=currency_converter,
        )

        # 创建 Sender（出站消息发送器）
        self._sender = YouTubeLiveChatSender(
            client_id=oauth_fields["client_id"],
            client_secret=oauth_fields["client_secret"],
            refresh_token=oauth_fields["refresh_token"],
            proxy=config.youtube.proxy_url,
            timeout=config.connection.request_timeout,
        )
        await self._sender.start(
            video_id=video_id,
            live_chat_id=config.outbound.live_chat_id.strip(),
        )

        logger.info(f"YouTube Live Adapter 加载完成 (video_id={video_id})")

    async def on_adapter_unloaded(self) -> None:
        """适配器卸载回调：关闭 API 客户端、Sender 和货币转换器，释放资源。"""
        logger.info("YouTube Live Adapter 卸载中...")

        # 清理 viewer count reminder
        self._cancel_viewer_count_reminder_task()
        self._clear_viewer_count_reminder()

        if self._sender:
            await self._sender.aclose()
            self._sender = None
        if self._api:
            await self._api.aclose()
            self._api = None
        if self._dispatcher and self._dispatcher.currency_converter:
            await self._dispatcher._currency_converter.aclose()
        self._dispatcher = None
        logger.info("YouTube Live Adapter 已卸载")

    async def start(self) -> None:
        """启动适配器。

        检查是否启用，重置状态，调用父类 start（触发 on_adapter_loaded + health_check_loop），
        然后启动 session_loop 任务和 viewer count reminder 任务。
        """
        config = self._get_config()
        if not config.plugin.enabled:
            logger.info("YouTube Live Adapter 已禁用，跳过启动")
            return

        # 重置状态
        self._stopping = False
        self._consecutive_failures = 0

        # 调用父类 start（会触发 on_adapter_loaded + health_check_loop）
        await super().start()

        # 启动 session_loop
        tm = get_task_manager()
        self._poll_task_info = tm.create_task(
            self._session_loop(),
            name="youtube_live_adapter_session_loop",
            daemon=True,
        )

        # 启动 viewer count reminder 循环
        self._viewer_count_reminder_task_info = tm.create_task(
            self._viewer_count_reminder_loop(),
            name="youtube_live_adapter.viewer_count_reminder",
            daemon=True,
        )

        logger.info("YouTube Live Adapter 已启动")

    async def stop(self) -> None:
        """停止适配器。

        设置停止标志，停止 client，取消 session_loop 任务和 viewer count reminder，
        清理 reminder store，调用父类 stop。
        """
        self._stopping = True

        # 清理 viewer count reminder
        self._cancel_viewer_count_reminder_task()
        self._clear_viewer_count_reminder()

        # 停止 client
        if self._client:
            await self._client.stop()

        # 取消 session_loop 任务
        if self._poll_task_info:
            tm = get_task_manager()
            with contextlib.suppress(Exception):
                tm.cancel_task(self._poll_task_info.task_id)
            self._poll_task_info = None

        # 调用父类 stop
        await super().stop()
        logger.info("YouTube Live Adapter 已停止")

    # ──────────────────────────────────────────────
    # 消息转换
    # ──────────────────────────────────────────────

    async def from_platform_message(self, raw: Any) -> MessageEnvelope | None:
        """将 YouTube action dict 转换为 MessageEnvelope。

        Args:
            raw: YouTube Inner Tube API 返回的 action dict

        Returns:
            MessageEnvelope 或 None（无法识别的消息类型）
        """
        if self._dispatcher is None:
            logger.warning("Dispatcher 未初始化，跳过消息处理")
            return None
        return await self._dispatcher.dispatch(raw)

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:
        """出站消息处理：从 MessageEnvelope 提取文本并发送到 YouTube 直播间。

        message_segment 解析逻辑：
        - {"type": "text", "data": "hello"} → 直接发送 "hello"
        - {"type": "seglist", "data": [...]} → 遍历提取所有 text segment，拼接发送
        - {"type": "command", ...} → 忽略（YouTube 不支持命令）
        - {"type": "image", ...} → 忽略并记录警告（YouTube 不支持图片）
        - 其他类型 → 忽略并记录调试信息
        """
        if self._sender is None:
            logger.error("Sender 未初始化，无法发送出站消息")
            return

        seg = envelope.get("message_segment")
        if not isinstance(seg, dict):
            logger.debug(f"忽略无 message_segment 的出站消息: {type(seg)}")
            return

        text = self._extract_text_from_segment(seg)
        if not text:
            return

        logger.debug(f"出站消息发送中: {text[:30]}...")
        await self._sender.send_text_message(text)
        logger.debug("出站消息发送成功")

    @staticmethod
    def _extract_text_from_segment(seg: dict[str, Any]) -> str:
        """从 message_segment 中提取文本内容。

        Args:
            seg: message_segment dict，包含 type 和 data 字段

        Returns:
            提取的文本字符串，无有效文本时返回空字符串
        """
        seg_type = seg.get("type")

        if seg_type == "text":
            # 纯文本 segment：直接取 data
            data = seg.get("data")
            return str(data) if data else ""

        if seg_type == "seglist":
            # segment 列表：遍历提取所有 text segment 的文本，拼接
            parts: list[str] = []
            for item in seg.get("data", []):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    data = item.get("data")
                    if data:
                        parts.append(str(data))
                elif item.get("type") == "image":
                    logger.warning("YouTube 直播间不支持图片消息，已忽略")
                elif item.get("type") == "command":
                    logger.debug("YouTube 直播间不支持命令消息，已忽略")
                else:
                    logger.debug(f"忽略不支持的 segment 类型: {item.get('type')}")
            return " ".join(parts)

        if seg_type == "image":
            logger.warning("YouTube 直播间不支持图片消息，已忽略")
            return ""

        if seg_type == "command":
            logger.debug("YouTube 直播间不支持命令消息，已忽略")
            return ""

        logger.debug(f"忽略未知的 message_segment 类型: {seg_type}")
        return ""

    # ──────────────────────────────────────────────
    # 健康检查与重连
    # ──────────────────────────────────────────────

    async def health_check(self) -> bool:
        """返回连接健康状态。"""
        if self._client is None:
            return False
        return self._client.is_healthy

    async def reconnect(self) -> None:
        """重连（no-op，由 session_loop 内部处理）。"""
        pass

    async def get_bot_info(self) -> dict[str, Any]:
        """返回 Bot 信息。"""
        config = self._get_config()
        return {
            "bot_id": config.youtube.video_id,
            "bot_name": "YouTube Live",
            "platform": PLATFORM,
        }

    # ──────────────────────────────────────────────
    # 消息回调
    # ──────────────────────────────────────────────

    async def on_platform_message(self, raw: dict[str, Any]) -> None:
        """YouTubePollClient 的回调，接收原始 action dict。

        将 action 转换为 MessageEnvelope 后投递至消息总线。
        """
        envelope = await self.from_platform_message(raw)
        if envelope:
            await self.core_sink.send(envelope)

    # ──────────────────────────────────────────────
    # 会话管理（内部）
    # ──────────────────────────────────────────────

    async def _session_loop(self) -> None:
        """会话循环：运行轮询会话，异常时自动重连。

        流程：
        1. 运行一次轮询会话（_run_one_session）
        2. 异常时根据配置决定是否重连
        3. 重连使用指数退避 + 随机抖动
        """
        while not self._stopping:
            try:
                await self._run_one_session()
                if self._stopping:
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"会话异常: {exc}")

            if self._stopping:
                break

            config = self._get_config()
            if not config.connection.auto_reconnect:
                logger.info("自动重连已禁用，停止会话循环")
                break

            await self._sleep_with_backoff()

    async def _run_one_session(self) -> None:
        """运行一次轮询会话。

        创建 YouTubePollClient 并运行，阻塞直到断开。
        成功返回后重置连续失败计数。
        """
        config = self._get_config()

        # on_adapter_loaded 已确保 _api 不为 None
        assert self._api is not None

        client = YouTubePollClient(
            api=self._api,
            on_event=self.on_platform_message,
            video_id=config.youtube.video_id,
            poll_interval=config.connection.poll_interval,
            max_poll_interval=config.connection.max_poll_interval,
            on_viewer_count=self._on_viewer_count,
        )
        self._client = client

        try:
            await client.run()
            # 正常返回（非异常断开），重置连续失败计数
            self._consecutive_failures = 0
        finally:
            await client.stop()
            self._client = None

    async def _sleep_with_backoff(self) -> None:
        """指数退避 + 随机抖动等待。

        退避公式：delay = min(base * multiplier^(failures-1), max_delay)
        抖动：±25% 随机偏移
        首次失败 delay=base，与 bilibili 对齐。
        """
        # 递增连续失败计数
        self._consecutive_failures += 1

        config = self._get_config()
        base = config.connection.reconnect_initial_delay
        max_delay = config.connection.reconnect_max_delay
        multiplier = config.connection.reconnect_backoff_multiplier

        delay = min(base * (multiplier ** (self._consecutive_failures - 1)), max_delay)
        # 随机抖动：±25%
        jitter = delay * 0.25 * (2 * random.random() - 1)
        actual_delay = max(1.0, delay + jitter)

        logger.info(f"重连退避 {actual_delay:.1f}s (第 {self._consecutive_failures} 次)")

        await asyncio.sleep(actual_delay)

    # ──────────────────────────────────────────────
    # 观众人数 system_reminder
    # ──────────────────────────────────────────────

    async def _on_viewer_count(self, count: int) -> None:
        """观众人数回调，更新当前观众人数。

        Args:
            count: 当前观众人数
        """
        self._current_viewer_count = count

    async def _viewer_count_reminder_loop(self) -> None:
        """定期将观众人数推送到 prompt 的 system_reminder。

        仅当观众人数变化时更新，避免无意义写入。
        """
        while not self._stopping:
            try:
                await self._publish_viewer_count_reminder()
            except Exception as exc:
                logger.debug(f"发布观众人数 reminder 失败: {exc}")
            await asyncio.sleep(_VIEWER_COUNT_REFRESH_INTERVAL)

    async def _publish_viewer_count_reminder(self) -> None:
        """构建并发布观众人数 system_reminder。

        仅当 viewer count 变化时更新，避免无意义写入。
        """
        count = self._current_viewer_count
        if count == self._last_published_viewer_count:
            return

        content = f"YouTube 直播间在线人数: {count}"
        try:
            prompt_api.add_system_reminder(
                bucket=SystemReminderBucket.ACTOR,
                name=_VIEWER_COUNT_REMINDER_NAME,
                content=content,
                insert_type=SystemReminderInsertType.DYNAMIC,
            )
            self._last_published_viewer_count = count
        except Exception as exc:
            logger.debug(f"添加观众人数 system_reminder 失败: {exc}")

    def _cancel_viewer_count_reminder_task(self) -> None:
        """取消 viewer count reminder 后台任务。"""
        if self._viewer_count_reminder_task_info:
            tm = get_task_manager()
            with contextlib.suppress(Exception):
                tm.cancel_task(self._viewer_count_reminder_task_info.task_id)
            self._viewer_count_reminder_task_info = None

    def _clear_viewer_count_reminder(self) -> None:
        """从 system_reminder store 中移除观众人数 reminder。"""
        try:
            store = self._get_system_reminder_store()
            store.delete(SystemReminderBucket.ACTOR, _VIEWER_COUNT_REMINDER_NAME)
        except Exception as exc:
            logger.debug(f"清除观众人数 system_reminder 失败: {exc}")

    @staticmethod
    def _get_system_reminder_store() -> Any:
        """获取 system_reminder store 实例。"""
        from src.core.prompt import get_system_reminder_store

        return get_system_reminder_store()

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _get_config(self) -> YouTubeLiveAdapterConfig:
        """获取插件配置。

        Returns:
            YouTubeLiveAdapterConfig 实例

        Raises:
            RuntimeError: 配置不可用
        """
        if not self.plugin or not self.plugin.config:
            raise RuntimeError("YouTube Live Adapter 配置不可用")
        return cast(YouTubeLiveAdapterConfig, self.plugin.config)


__all__ = ["YouTubeLiveAdapter", "YouTubeLiveAdapterPlugin"]
