"""YouTube Live Chat 消息分发器。

将 YouTube Inner Tube API 返回的 action dict 转换为 MessageEnvelope，
遵循与 bilibili_live_adapter 相同的 MessageBuilder 模式。

支持的消息类型：
- liveChatTextMessageRenderer → 普通弹幕
- liveChatSuperChatRenderer → Super Chat
- liveChatSuperStickerRenderer → Super Sticker
- liveChatPaidMessageRenderer → 付费消息
- liveChatMembershipGiftPurchaseRenderer → 会员礼物购买
- liveChatSponsorshipsGiftRedemptionRenderer → 会员礼物兑换
- liveChatMemberMilestoneChatRenderer → 会员里程碑
- liveChatPaidStickerRenderer → 付费 Sticker
- liveChatSponsorshipsGiftPurchaseAnnouncementRenderer → 会员礼物购买公告

关键设计：
- platform = "live"（与 bilibili 共享，anima_chatter 依赖此值）
- source_platform = "youtube_live"（唯一标识，写入 additional_config 与 extra）
- group_id = "live_room"（与 bilibili 共享虚拟群组 ID 前缀）

Envelope 字段规范：
- envelope["message_info"]["additional_config"]：包含 event_type、original_type、
  source_platform、source_room_id 等元数据，供下游组件消费。
- envelope["raw_message"]：原始 renderer dict，映射为 Message.raw_data，
  供调试和二次解析使用。
- envelope["message_info"]["extra"]：通过 _inject_source_into_extra 注入
  source_platform / source_room_id，MessageConverter 会将其展开为 Message.extra。
- user_avatar：从 renderer["authorPhotoUrl"] 提取，通过 _apply_user 注入
  user_info.user_avatar，供 chatter 展示用户头像。
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from mofox_wire import MessageBuilder, MessageEnvelope
from mofox_wire.types import UserRole

from src.currency import CurrencyConverter
from src.kernel.logger import get_logger

logger = get_logger("youtube_live_adapter.dispatcher")


# ── 常量 ───────────────────────────────────────────────────────────

# 与 bilibili_live_adapter 共享的 platform 值
# anima_chatter 通过此值识别为 vtb_live 模式
PLATFORM = "live"

# 唯一标识，写入 additional_config.source_platform
# prompt 通过此值区分消息来源
SOURCE_PLATFORM = "youtube_live"

# 与 bilibili_live_adapter 共享的虚拟群组 ID
# stream_manager 使用 SHA256(platform + "_" + group_id) 生成 stream_id
LIVE_VIRTUAL_GROUP_ID = "live_room"

# 消息去重队列最大容量
_DEDUP_QUEUE_SIZE = 200


# ── 分发器 ─────────────────────────────────────────────────────────

class YouTubeMessageDispatcher:
    """将 YouTube Inner Tube API 的 action dict 转换为 MessageEnvelope。

    dispatch() 方法接收单个 action dict，根据其中的 renderer 类型
    调用对应的 _build_*_envelope() 方法构建 MessageEnvelope。
    """

    def __init__(
        self,
        *,
        video_id: str = "",
        debug_mode: bool = False,
        filter_emoji: bool = False,
        remove_hashtags: bool = False,
        max_message_length: int = 500,
        ignored_message_types: list[str] | None = None,
        currency_converter: CurrencyConverter | None = None,
    ) -> None:
        """初始化分发器。

        Args:
            video_id: YouTube 视频 ID，用于构建 group_name 和 additional_config。
            debug_mode: 是否输出未处理的消息类型。
            filter_emoji: 是否过滤消息中的 emoji。
            remove_hashtags: 是否移除消息中的 # 标签。
            max_message_length: 消息最大长度，超出截断。
            ignored_message_types: 忽略的消息类型列表。
            currency_converter: 货币转换器实例，为 None 时不进行转换。
        """
        self._video_id = str(video_id or "")
        self._debug_mode = debug_mode
        self._filter_emoji = filter_emoji
        self._remove_hashtags = remove_hashtags
        self._max_message_length = max_message_length
        self._ignored_types = set(ignored_message_types or [])
        self._currency_converter = currency_converter

        # 消息去重：存储最近的 message_id
        self._recent_ids: deque[str] = deque(maxlen=_DEDUP_QUEUE_SIZE)

    async def dispatch(self, action: dict[str, Any]) -> MessageEnvelope | None:
        """将单个 action dict 转换为 MessageEnvelope。

        Args:
            action: YouTube Inner Tube API 返回的 action dict。

        Returns:
            MessageEnvelope 或 None（无法处理时）。
        """
        # addChatItemAction 包含实际的聊天消息
        add_chat = action.get("addChatItemAction")
        if isinstance(add_chat, dict):
            item = add_chat.get("item", {})
            return await self._dispatch_item(item)

        # addLiveChatTickerItemAction 是置顶消息，暂不处理
        # markChatItemsByAuthorAsDeletedAction / markChatItemAsDeletedAction 暂不处理

        if self._debug_mode:
            logger.debug(f"未处理的 action 类型: {list(action.keys())}")

        return None

    # ── item 分发 ──────────────────────────────────────────────────

    async def _dispatch_item(self, item: dict[str, Any]) -> MessageEnvelope | None:
        """根据 item 中的 renderer key 分发到对应的构建方法。"""
        if "liveChatTextMessageRenderer" in item:
            return self._build_text_envelope(item["liveChatTextMessageRenderer"])
        if "liveChatSuperChatRenderer" in item:
            return await self._build_super_chat_envelope(item["liveChatSuperChatRenderer"])
        if "liveChatSuperStickerRenderer" in item:
            return self._build_super_sticker_envelope(item["liveChatSuperStickerRenderer"])
        if "liveChatMembershipGiftPurchaseRenderer" in item:
            return self._build_gift_purchase_envelope(item["liveChatMembershipGiftPurchaseRenderer"])
        if "liveChatSponsorshipsGiftRedemptionRenderer" in item:
            return self._build_gift_redemption_envelope(item["liveChatSponsorshipsGiftRedemptionRenderer"])
        if "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer" in item:
            return self._build_gift_announcement_envelope(item["liveChatSponsorshipsGiftPurchaseAnnouncementRenderer"])
        if "liveChatMemberMilestoneChatRenderer" in item:
            return self._build_milestone_envelope(item["liveChatMemberMilestoneChatRenderer"])
        if "liveChatPaidStickerRenderer" in item:
            return self._build_paid_sticker_envelope(item["liveChatPaidStickerRenderer"])
        if "liveChatPaidMessageRenderer" in item:
            return await self._build_paid_message_envelope(item["liveChatPaidMessageRenderer"])

        if self._debug_mode:
            logger.debug(f"未处理的 item 类型: {list(item.keys())}")

        return None

    # ── 消息构建 ───────────────────────────────────────────────────

    def _build_text_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """普通弹幕 → MessageEnvelope。"""
        message_id = renderer.get("id", "")
        if self._is_duplicate(message_id):
            return None

        user_id = str(renderer.get("authorExternalChannelId", ""))
        nickname = self._extract_nickname(renderer)
        message_text = self._extract_message_text(renderer)
        timestamp_usec = renderer.get("timestampUsec", "0")
        user_avatar = str(renderer.get("authorPhotoUrl", ""))

        if not message_text:
            return None

        message_text = self._apply_filters(message_text)

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(message_text)
        )

        if message_id:
            builder.message_id(message_id)

        self._apply_timestamp(builder, timestamp_usec)
        self._apply_user(builder, user_id=user_id, nickname=nickname, user_avatar=user_avatar)
        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "textMessageEvent"
        additional["event_type"] = "danmaku"
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        logger.info(f"收到弹幕 [room={self._video_id}] {nickname}({user_id[:8]}...) 说: {message_text}")

        return envelope

    async def _build_super_chat_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """Super Chat → MessageEnvelope。

        当 currency_converter 可用时，自动将外币金额转换为 CNY 并写入 additional_config。
        """
        message_id = renderer.get("id", "")
        if self._is_duplicate(message_id):
            return None

        user_id = str(renderer.get("authorExternalChannelId", ""))
        nickname = self._extract_nickname(renderer)
        message_text = self._extract_message_text(renderer)
        timestamp_usec = renderer.get("timestampUsec", "0")
        user_avatar = str(renderer.get("authorPhotoUrl", ""))

        amount_micros = int(renderer.get("amountMicros", 0))
        amount_display = str(renderer.get("amountDisplayString", ""))
        currency = str(renderer.get("currency", "USD"))

        # 货币转换：将原始金额转为 CNY
        amount = amount_micros / 1_000_000
        cny_amount: float | None = None
        if self._currency_converter and currency != "CNY":
            try:
                cny_amount = await self._currency_converter.convert_to_cny(amount, currency)
            except Exception as exc:
                logger.debug(f"SC 货币转换失败 ({currency} → CNY): {exc}")

        # 格式化显示内容：[SC ¥100] 或 [SC ¥100 ≈ ¥720 CNY] 消息内容
        if cny_amount is not None and currency != "CNY":
            amount_label = f"[SC {amount_display} ≈ ¥{cny_amount} CNY]"
        else:
            amount_label = f"[SC {amount_display}]"
        content = f"{amount_label} {message_text}".strip() if message_text else amount_label
        content = self._apply_filters(content)

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(content)
        )

        if message_id:
            builder.message_id(message_id)

        self._apply_timestamp(builder, timestamp_usec)
        self._apply_user(builder, user_id=user_id, nickname=nickname, is_sc=True, user_avatar=user_avatar)
        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "superChatEvent"
        additional["event_type"] = "super_chat"
        additional["superchat_amount"] = amount
        additional["superchat_amount_display"] = amount_display
        additional["superchat_currency"] = currency
        if cny_amount is not None:
            additional["superchat_amount_cny"] = cny_amount
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        cny_display = f"¥{cny_amount}" if cny_amount is not None else amount_display
        logger.info(f"收到SC [room={self._video_id}] {nickname}({user_id[:8]}...) {cny_display}: {message_text}")

        return envelope

    def _build_super_sticker_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """Super Sticker → MessageEnvelope。"""
        message_id = renderer.get("id", "")
        if self._is_duplicate(message_id):
            return None

        user_id = str(renderer.get("authorExternalChannelId", ""))
        nickname = self._extract_nickname(renderer)
        timestamp_usec = renderer.get("timestampUsec", "0")
        user_avatar = str(renderer.get("authorPhotoUrl", ""))
        amount_display = str(renderer.get("amountDisplayString", ""))

        # 提取 sticker 名称
        sticker_label = ""
        sticker = renderer.get("sticker", {})
        accessibility = sticker.get("accessibility", {})
        access_data = accessibility.get("accessibilityData", {})
        sticker_label = str(access_data.get("label", "Sticker"))

        content = f"[Super Sticker {amount_display}] {sticker_label}"

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(content)
        )

        if message_id:
            builder.message_id(message_id)

        self._apply_timestamp(builder, timestamp_usec)
        self._apply_user(builder, user_id=user_id, nickname=nickname, is_sc=True, user_avatar=user_avatar)
        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "superStickerEvent"
        additional["event_type"] = "super_sticker"
        additional["superchat_amount_display"] = amount_display
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        logger.info(f"收到Super Sticker [room={self._video_id}] {nickname}({user_id[:8]}...) {amount_display}: {sticker_label}")

        return envelope

    def _build_gift_purchase_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """会员礼物购买 → MessageEnvelope。"""
        message_id = renderer.get("id", "")
        if self._is_duplicate(message_id):
            return None

        nickname = self._extract_nickname(renderer)
        user_id = str(renderer.get("authorExternalChannelId", ""))
        timestamp_usec = renderer.get("timestampUsec", "0")
        user_avatar = str(renderer.get("authorPhotoUrl", ""))

        content = f"[会员礼物] {nickname} 购买了会员礼物"

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(content)
        )

        if message_id:
            builder.message_id(message_id)

        self._apply_timestamp(builder, timestamp_usec)
        self._apply_user(builder, user_id=user_id, nickname=nickname, user_avatar=user_avatar)
        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "membershipGiftPurchaseEvent"
        additional["event_type"] = "gift_purchase"
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        logger.info(f"收到会员礼物购买 [room={self._video_id}] {nickname}({user_id[:8]}...)")

        return envelope

    def _build_gift_redemption_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """会员礼物兑换 → MessageEnvelope。"""
        message_id = renderer.get("id", "")
        if self._is_duplicate(message_id):
            return None

        nickname = self._extract_nickname(renderer)
        user_id = str(renderer.get("authorExternalChannelId", ""))
        timestamp_usec = renderer.get("timestampUsec", "0")
        user_avatar = str(renderer.get("authorPhotoUrl", ""))

        content = f"[会员兑换] {nickname} 兑换了会员礼物"

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(content)
        )

        if message_id:
            builder.message_id(message_id)

        self._apply_timestamp(builder, timestamp_usec)
        self._apply_user(builder, user_id=user_id, nickname=nickname, user_avatar=user_avatar)
        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "membershipGiftRedemptionEvent"
        additional["event_type"] = "gift_redemption"
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        logger.info(f"收到会员礼物兑换 [room={self._video_id}] {nickname}({user_id[:8]}...)")

        return envelope

    def _build_gift_announcement_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """会员礼物购买公告 → MessageEnvelope。"""
        # 此 renderer 的结构与上述不同，消息嵌套在 header 或 message 中
        header = renderer.get("header", {})
        live_chat_header = header.get("liveChatSponsorshipsHeaderRenderer", {})
        nickname = self._extract_nickname(live_chat_header)

        content = f"[会员礼物] {nickname} 购买了会员礼物"

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(content)
        )

        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "membershipGiftPurchaseAnnouncementEvent"
        additional["event_type"] = "gift_announcement"
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        logger.info(f"收到会员礼物公告 [room={self._video_id}] {nickname}")

        return envelope

    def _build_milestone_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """会员里程碑 → MessageEnvelope。"""
        message_id = renderer.get("id", "")
        if self._is_duplicate(message_id):
            return None

        user_id = str(renderer.get("authorExternalChannelId", ""))
        nickname = self._extract_nickname(renderer)
        message_text = self._extract_message_text(renderer)
        timestamp_usec = renderer.get("timestampUsec", "0")
        user_avatar = str(renderer.get("authorPhotoUrl", ""))

        content = f"[会员里程碑] {message_text}" if message_text else "[会员里程碑]"
        content = self._apply_filters(content)

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(content)
        )

        if message_id:
            builder.message_id(message_id)

        self._apply_timestamp(builder, timestamp_usec)
        self._apply_user(builder, user_id=user_id, nickname=nickname, is_member=True, user_avatar=user_avatar)
        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "memberMilestoneChatEvent"
        additional["event_type"] = "milestone"
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        logger.info(f"收到会员里程碑 [room={self._video_id}] {nickname}({user_id[:8]}...) 说: {message_text}")

        return envelope

    def _build_paid_sticker_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """付费 Sticker（另一种形式）→ MessageEnvelope。"""
        message_id = renderer.get("id", "")
        if self._is_duplicate(message_id):
            return None

        user_id = str(renderer.get("authorExternalChannelId", ""))
        nickname = self._extract_nickname(renderer)
        timestamp_usec = renderer.get("timestampUsec", "0")
        user_avatar = str(renderer.get("authorPhotoUrl", ""))
        amount_display = str(renderer.get("purchaseAmountText", {}).get("simpleText", ""))

        content = f"[Paid Sticker {amount_display}]"

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(content)
        )

        if message_id:
            builder.message_id(message_id)

        self._apply_timestamp(builder, timestamp_usec)
        self._apply_user(builder, user_id=user_id, nickname=nickname, is_sc=True, user_avatar=user_avatar)
        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "paidStickerEvent"
        additional["event_type"] = "paid_sticker"
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        logger.info(f"收到付费Sticker [room={self._video_id}] {nickname}({user_id[:8]}...) {amount_display}")

        return envelope

    async def _build_paid_message_envelope(self, renderer: dict[str, Any]) -> MessageEnvelope | None:
        """付费消息（liveChatPaidMessageRenderer）→ MessageEnvelope。

        与 Super Chat 类似的付费消息，但属于不同的 renderer 类型。
        当 currency_converter 可用时，自动将外币金额转换为 CNY。
        """
        message_id = renderer.get("id", "")
        if self._is_duplicate(message_id):
            return None

        user_id = str(renderer.get("authorExternalChannelId", ""))
        nickname = self._extract_nickname(renderer)
        message_text = self._extract_message_text(renderer)
        timestamp_usec = renderer.get("timestampUsec", "0")
        user_avatar = str(renderer.get("authorPhotoUrl", ""))

        amount_micros = int(renderer.get("amountMicros", 0))
        amount_display = str(renderer.get("amountDisplayString", ""))
        currency = str(renderer.get("currency", "USD"))

        # 货币转换：将原始金额转为 CNY
        amount = amount_micros / 1_000_000
        cny_amount: float | None = None
        if self._currency_converter and currency != "CNY":
            try:
                cny_amount = await self._currency_converter.convert_to_cny(amount, currency)
            except Exception as exc:
                logger.debug(f"付费消息货币转换失败 ({currency} → CNY): {exc}")

        # 格式化显示内容：[付费消息 ¥100] 或 [付费消息 ¥100 ≈ ¥720 CNY] 消息内容
        if cny_amount is not None and currency != "CNY":
            amount_label = f"[付费消息 {amount_display} ≈ ¥{cny_amount} CNY]"
        else:
            amount_label = f"[付费消息 {amount_display}]"
        content = f"{amount_label} {message_text}".strip() if message_text else amount_label
        content = self._apply_filters(content)

        builder = (
            MessageBuilder()
            .direction("incoming")
            .platform(PLATFORM)
            .text(content)
        )

        if message_id:
            builder.message_id(message_id)

        self._apply_timestamp(builder, timestamp_usec)
        self._apply_user(builder, user_id=user_id, nickname=nickname, is_sc=True, user_avatar=user_avatar)
        self._apply_group(builder)

        envelope = builder.build()

        additional = self._build_common_additional(renderer)
        additional["original_type"] = "paidMessageEvent"
        additional["event_type"] = "paid_message"
        additional["superchat_amount"] = amount
        additional["superchat_amount_display"] = amount_display
        additional["superchat_currency"] = currency
        if cny_amount is not None:
            additional["superchat_amount_cny"] = cny_amount
        self._inject_source_into_extra(envelope, additional)
        envelope["message_info"]["additional_config"] = additional  # type: ignore[typeddict-unknown-key]
        envelope["raw_message"] = renderer

        cny_display = f"¥{cny_amount}" if cny_amount is not None else amount_display
        logger.info(f"收到付费消息 [room={self._video_id}] {nickname}({user_id[:8]}...) {cny_display}: {message_text}")

        return envelope

    # ── 辅助方法 ───────────────────────────────────────────────────

    @staticmethod
    def _extract_nickname(renderer: dict[str, Any]) -> str:
        """从 renderer 中提取用户昵称。"""
        author_name = renderer.get("authorName", {})
        if isinstance(author_name, dict):
            return str(author_name.get("simpleText", ""))
        return str(author_name or "")

    @staticmethod
    def _extract_message_text(renderer: dict[str, Any]) -> str:
        """从 renderer 中提取消息文本，拼接 runs 数组。"""
        message = renderer.get("message", {})
        runs = message.get("runs", [])
        if not runs:
            # 尝试 simpleText
            return str(message.get("simpleText", ""))
        return "".join(str(run.get("text", "")) for run in runs)

    def _apply_filters(self, text: str) -> str:
        """应用消息过滤规则。"""
        if self._filter_emoji:
            # 移除 emoji（覆盖大部分常见 emoji 范围）
            # 包括：表情符号、符号、旗帜、变体选择符等
            text = re.sub(
                r"[\U0001F600-\U0001F64F"  # Emoticons
                r"\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
                r"\U0001F680-\U0001F6FF"  # Transport and Map
                r"\U0001F1E0-\U0001F1FF"  # Flags
                r"\U00002702-\U000027B0"  # Dingbats
                r"\U0000FE00-\U0000FE0F"  # Variation Selectors
                r"\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
                r"\U0001FA00-\U0001FA6F"  # Chess Symbols
                r"\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
                r"\U00002600-\U000026FF"  # Misc Symbols
                r"\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
                r"\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
                r"]",
                "",
                text,
            )

        if self._remove_hashtags:
            text = re.sub(r"#\S+", "", text)

        if len(text) > self._max_message_length:
            text = text[: self._max_message_length] + "..."

        return text.strip()

    def _is_duplicate(self, message_id: str) -> bool:
        """检查消息是否重复。"""
        if not message_id:
            return False
        if message_id in self._recent_ids:
            return True
        self._recent_ids.append(message_id)
        return False

    @staticmethod
    def _apply_timestamp(builder: MessageBuilder, timestamp_usec: str) -> None:
        """将 YouTube 的 timestampUsec（微秒）注入 builder。"""
        try:
            ms = int(timestamp_usec) // 1000
            if ms > 0:
                builder.timestamp_ms(ms)
        except (ValueError, TypeError):
            pass

    @staticmethod
    def _apply_user(
        builder: MessageBuilder,
        *,
        user_id: str,
        nickname: str,
        is_sc: bool = False,
        is_member: bool = False,
        user_avatar: str = "",
    ) -> None:
        """注入用户信息到 builder。

        Args:
            builder: MessageBuilder 实例。
            user_id: 用户 ID。
            nickname: 用户昵称。
            is_sc: 是否为 Super Chat 用户。
            is_member: 是否为会员。
            user_avatar: 用户头像 URL，从 renderer["authorPhotoUrl"] 提取。
        """
        role = UserRole.OPERATOR if (is_sc or is_member) else UserRole.MEMBER
        builder.from_user(
            user_id=user_id or "anon",
            platform=PLATFORM,
            nickname=nickname or "Unknown",
            role=role,
            user_avatar=user_avatar or None,
        )

    def _apply_group(self, builder: MessageBuilder) -> None:
        """注入群组信息到 builder。"""
        name = f"YouTube Live {self._video_id}" if self._video_id else "YouTube Live"
        builder.from_group(
            group_id=LIVE_VIRTUAL_GROUP_ID,
            platform=PLATFORM,
            name=name,
        )

    def _build_common_additional(self, renderer: dict[str, Any]) -> dict[str, Any]:
        """构建通用的 additional_config 字典。"""
        return {
            "source_platform": SOURCE_PLATFORM,
            "source_room_id": self._video_id,
            "youtube_channel_id": renderer.get("authorExternalChannelId", ""),
        }

    @property
    def currency_converter(self) -> CurrencyConverter | None:
        """获取货币转换器实例（公共接口）。"""
        return self._currency_converter

    @staticmethod
    def _inject_source_into_extra(envelope: MessageEnvelope, additional: dict[str, Any]) -> None:
        """将 source_platform / source_room_id 注入 message_info.extra。

        与 bilibili_live_adapter 的 _inject_source_into_extra 保持一致，
        MessageConverter 会将 message_info.extra 映射为 Message.extra。
        """
        info = envelope.get("message_info")
        if not isinstance(info, dict):
            return
        extra_obj = info.get("extra")
        if not isinstance(extra_obj, dict):
            extra_obj = {}
            info["extra"] = extra_obj  # type: ignore[typeddict-unknown-key]
        if "source_platform" in additional:
            extra_obj["source_platform"] = additional["source_platform"]
        if "source_room_id" in additional:
            extra_obj["source_room_id"] = additional["source_room_id"]


__all__ = ["LIVE_VIRTUAL_GROUP_ID", "PLATFORM", "SOURCE_PLATFORM", "YouTubeMessageDispatcher"]
