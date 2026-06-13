"""YouTubeMessageDispatcher 单元测试

测试消息分发器的核心功能：
- 各种消息类型的解析与转换
- 消息去重
- 消息过滤（emoji、hashtag、长度截断）
- UserRole 逻辑
- 货币转换集成
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dispatcher import (
    LIVE_VIRTUAL_GROUP_ID,
    PLATFORM,
    YouTubeMessageDispatcher,
)

# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _make_text_action(
    message_id: str = "msg_001",
    user_id: str = "UC_test",
    nickname: str = "TestUser",
    text: str = "Hello!",
    timestamp_usec: str = "1700000000000000",
) -> dict[str, Any]:
    """构造普通弹幕 action dict。"""
    return {
        "addChatItemAction": {
            "item": {
                "liveChatTextMessageRenderer": {
                    "id": message_id,
                    "authorExternalChannelId": user_id,
                    "authorName": {"simpleText": nickname},
                    "message": {"runs": [{"text": text}]},
                    "timestampUsec": timestamp_usec,
                },
            },
        },
    }


def _make_sc_action(
    message_id: str = "sc_001",
    user_id: str = "UC_sc_user",
    nickname: str = "SCUser",
    text: str = "Nice stream!",
    amount_micros: int = 1_000_000,
    amount_display: str = "$1.00",
    currency: str = "USD",
    timestamp_usec: str = "1700000000000000",
) -> dict[str, Any]:
    """构造 Super Chat action dict。"""
    return {
        "addChatItemAction": {
            "item": {
                "liveChatSuperChatRenderer": {
                    "id": message_id,
                    "authorExternalChannelId": user_id,
                    "authorName": {"simpleText": nickname},
                    "message": {"runs": [{"text": text}]},
                    "timestampUsec": timestamp_usec,
                    "amountMicros": amount_micros,
                    "amountDisplayString": amount_display,
                    "currency": currency,
                },
            },
        },
    }


def _make_member_milestone_action(
    message_id: str = "mile_001",
    user_id: str = "UC_member",
    nickname: str = "MemberUser",
    text: str = "6 months!",
) -> dict[str, Any]:
    """构造会员里程碑 action dict。"""
    return {
        "addChatItemAction": {
            "item": {
                "liveChatMemberMilestoneChatRenderer": {
                    "id": message_id,
                    "authorExternalChannelId": user_id,
                    "authorName": {"simpleText": nickname},
                    "message": {"runs": [{"text": text}]},
                    "timestampUsec": "1700000000000000",
                },
            },
        },
    }


def _make_super_sticker_action(
    message_id: str = "stk_001",
    user_id: str = "UC_stk",
    nickname: str = "StickerUser",
    amount_display: str = "¥10",
) -> dict[str, Any]:
    """构造 Super Sticker action dict。"""
    return {
        "addChatItemAction": {
            "item": {
                "liveChatSuperStickerRenderer": {
                    "id": message_id,
                    "authorExternalChannelId": user_id,
                    "authorName": {"simpleText": nickname},
                    "timestampUsec": "1700000000000000",
                    "amountDisplayString": amount_display,
                    "sticker": {
                        "accessibility": {
                            "accessibilityData": {"label": "Heart"},
                        },
                    },
                },
            },
        },
    }


def _make_gift_purchase_action(
    message_id: str = "gift_001",
    user_id: str = "UC_gift",
    nickname: str = "GiftUser",
) -> dict[str, Any]:
    """构造会员礼物购买 action dict。"""
    return {
        "addChatItemAction": {
            "item": {
                "liveChatMembershipGiftPurchaseRenderer": {
                    "id": message_id,
                    "authorExternalChannelId": user_id,
                    "authorName": {"simpleText": nickname},
                    "timestampUsec": "1700000000000000",
                },
            },
        },
    }


def _make_paid_sticker_action(
    message_id: str = "paid_stk_001",
    user_id: str = "UC_paid",
    nickname: str = "PaidUser",
    amount_text: str = "¥5",
) -> dict[str, Any]:
    """构造付费 Sticker action dict。"""
    return {
        "addChatItemAction": {
            "item": {
                "liveChatPaidStickerRenderer": {
                    "id": message_id,
                    "authorExternalChannelId": user_id,
                    "authorName": {"simpleText": nickname},
                    "timestampUsec": "1700000000000000",
                    "purchaseAmountText": {"simpleText": amount_text},
                },
            },
        },
    }


def _make_paid_message_action(
    message_id: str = "paid_msg_001",
    user_id: str = "UC_paid_msg",
    nickname: str = "PaidMsgUser",
    text: str = "Great stream!",
    amount_micros: int = 10_000_000,
    amount_display: str ="¥10",
    currency: str = "CNY",
    timestamp_usec: str = "1700000000000000",
) -> dict[str, Any]:
    """构造付费消息（liveChatPaidMessageRenderer）action dict。"""
    return {
        "addChatItemAction": {
            "item": {
                "liveChatPaidMessageRenderer": {
                    "id": message_id,
                    "authorExternalChannelId": user_id,
                    "authorName": {"simpleText": nickname},
                    "message": {"runs": [{"text": text}]},
                    "timestampUsec": timestamp_usec,
                    "amountMicros": amount_micros,
                    "amountDisplayString": amount_display,
                    "currency": currency,
                },
            },
        },
    }


# ──────────────────────────────────────────────
# 测试：普通弹幕
# ──────────────────────────────────────────────


class TestTextMessage:
    """普通弹幕解析测试。"""

    @pytest.mark.asyncio
    async def test_basic_text_message(self) -> None:
        """基本弹幕消息应正确解析。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_text_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["text"] == "Hello!"
        assert envelope["platform"] == PLATFORM
        assert envelope["direction"] == "incoming"

    @pytest.mark.asyncio
    async def test_text_message_with_id(self) -> None:
        """弹幕应携带 message_id。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_text_action(message_id="unique_123")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["message_id"] == "unique_123"

    @pytest.mark.asyncio
    async def test_text_message_user_info(self) -> None:
        """弹幕应包含用户信息。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_text_action(user_id="UC_abc", nickname="Alice")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        user_info = envelope["from_user"]
        assert user_info["user_id"] == "UC_abc"
        assert user_info["nickname"] == "Alice"
        # 普通弹幕 → MEMBER
        assert user_info["role"] == "member"

    @pytest.mark.asyncio
    async def test_text_message_group_info(self) -> None:
        """弹幕应包含群组信息。"""
        dispatcher = YouTubeMessageDispatcher(video_id="vid_123")

        envelope = await dispatcher.dispatch(_make_text_action())

        assert envelope is not None
        group_info = envelope["from_group"]
        assert group_info["group_id"] == LIVE_VIRTUAL_GROUP_ID
        assert group_info["platform"] == PLATFORM

    @pytest.mark.asyncio
    async def test_empty_message_returns_none(self) -> None:
        """空消息应返回 None。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_text_action(text="")

        envelope = await dispatcher.dispatch(action)

        assert envelope is None

    @pytest.mark.asyncio
    async def test_simple_text_message(self) -> None:
        """simpleText 格式的消息应正确解析。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = {
            "addChatItemAction": {
                "item": {
                    "liveChatTextMessageRenderer": {
                        "id": "msg_simple",
                        "authorExternalChannelId": "UC_test",
                        "authorName": {"simpleText": "User"},
                        "message": {"simpleText": "Simple message"},
                        "timestampUsec": "1700000000000000",
                    },
                },
            },
        }

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["text"] == "Simple message"


# ──────────────────────────────────────────────
# 测试：Super Chat
# ──────────────────────────────────────────────


class TestSuperChat:
    """Super Chat 解析测试。"""

    @pytest.mark.asyncio
    async def test_basic_super_chat(self) -> None:
        """基本 SC 消息应正确解析。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_sc_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "[SC $1.00]" in envelope["text"]
        assert "Nice stream!" in envelope["text"]

    @pytest.mark.asyncio
    async def test_sc_user_role_is_operator(self) -> None:
        """SC 用户角色应为 OPERATOR。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_sc_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["from_user"]["role"] == "operator"

    @pytest.mark.asyncio
    async def test_sc_without_message_text(self) -> None:
        """无消息内容的 SC 应只显示金额标签。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_sc_action(text="")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["text"] == "[SC $1.00]"

    @pytest.mark.asyncio
    async def test_sc_currency_conversion(self) -> None:
        """启用货币转换时，SC 应显示 CNY 金额。"""
        mock_converter = MagicMock(spec=["convert_to_cny"])
        mock_converter.convert_to_cny = AsyncMock(return_value=7.25)

        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            currency_converter=mock_converter,
        )
        action = _make_sc_action(currency="USD")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "≈ ¥7.25 CNY" in envelope["text"]

    @pytest.mark.asyncio
    async def test_sc_currency_conversion_cny_no_double_convert(self) -> None:
        """CNY 货币的 SC 不应进行转换。"""
        mock_converter = MagicMock(spec=["convert_to_cny"])
        mock_converter.convert_to_cny = AsyncMock(return_value=100.0)

        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            currency_converter=mock_converter,
        )
        action = _make_sc_action(currency="CNY", amount_display="¥100")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        # CNY 不应显示转换后金额
        assert "≈" not in envelope["text"]
        # convert_to_cny 不应被调用
        mock_converter.convert_to_cny.assert_not_called()

    @pytest.mark.asyncio
    async def test_sc_currency_conversion_failure(self) -> None:
        """货币转换失败时，SC 应正常显示原始金额。"""
        mock_converter = MagicMock(spec=["convert_to_cny"])
        mock_converter.convert_to_cny = AsyncMock(return_value=None)

        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            currency_converter=mock_converter,
        )
        action = _make_sc_action(currency="JPY", amount_display="¥1000")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "[SC ¥1000]" in envelope["text"]
        assert "≈" not in envelope["text"]


# ──────────────────────────────────────────────
# 测试：会员里程碑
# ──────────────────────────────────────────────


class TestMemberMilestone:
    """会员里程碑消息测试。"""

    @pytest.mark.asyncio
    async def test_milestone_user_role_is_operator(self) -> None:
        """会员里程碑用户角色应为 OPERATOR。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_member_milestone_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["from_user"]["role"] == "operator"

    @pytest.mark.asyncio
    async def test_milestone_content(self) -> None:
        """会员里程碑消息应包含 [会员里程碑] 前缀。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_member_milestone_action(text="6 months!")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "[会员里程碑]" in envelope["text"]
        assert "6 months!" in envelope["text"]


# ──────────────────────────────────────────────
# 测试：Super Sticker
# ──────────────────────────────────────────────


class TestSuperSticker:
    """Super Sticker 消息测试。"""

    @pytest.mark.asyncio
    async def test_super_sticker_content(self) -> None:
        """Super Sticker 应包含金额和标签。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_super_sticker_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "[Super Sticker ¥10]" in envelope["text"]
        assert "Heart" in envelope["text"]

    @pytest.mark.asyncio
    async def test_super_sticker_role_is_operator(self) -> None:
        """Super Sticker 用户角色应为 OPERATOR。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_super_sticker_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["from_user"]["role"] == "operator"


# ──────────────────────────────────────────────
# 测试：付费 Sticker
# ──────────────────────────────────────────────


class TestPaidSticker:
    """付费 Sticker 消息测试。"""

    @pytest.mark.asyncio
    async def test_paid_sticker_content(self) -> None:
        """付费 Sticker 应包含金额。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_paid_sticker_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "[Paid Sticker ¥5]" in envelope["text"]


# ──────────────────────────────────────────────
# 测试：付费消息（liveChatPaidMessageRenderer）
# ──────────────────────────────────────────────


class TestPaidMessage:
    """付费消息（liveChatPaidMessageRenderer）测试。"""

    @pytest.mark.asyncio
    async def test_paid_message_content(self) -> None:
        """付费消息应包含 [付费消息] 前缀和消息内容。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_paid_message_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "[付费消息 ¥10]" in envelope["text"]
        assert "Great stream!" in envelope["text"]

    @pytest.mark.asyncio
    async def test_paid_message_role_is_operator(self) -> None:
        """付费消息用户角色应为 OPERATOR（is_sc=True）。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_paid_message_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["from_user"]["role"] == "operator"

    @pytest.mark.asyncio
    async def test_paid_message_with_currency_conversion(self) -> None:
        """付费消息应支持货币转换。"""
        mock_converter = MagicMock()
        mock_converter.convert_to_cny = AsyncMock(return_value=36.0)

        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            currency_converter=mock_converter,
        )
        action = _make_paid_message_action(
            amount_micros=5_000_000,
            amount_display="$5.00",
            currency="USD",
        )

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "[付费消息 $5.00 ≈ ¥36.0 CNY]" in envelope["text"]

    @pytest.mark.asyncio
    async def test_paid_message_no_text(self) -> None:
        """无消息文本的付费消息应只显示金额标签。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_paid_message_action(text="")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["text"] == "[付费消息 ¥10]"

    @pytest.mark.asyncio
    async def test_paid_message_cny_no_double_convert(self) -> None:
        """CNY 货币的付费消息不应进行转换。"""
        mock_converter = MagicMock()
        mock_converter.convert_to_cny = AsyncMock(return_value=10.0)

        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            currency_converter=mock_converter,
        )
        action = _make_paid_message_action(currency="CNY", amount_display="¥10")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        # CNY 不应显示转换后金额
        assert "≈" not in envelope["text"]
        # convert_to_cny 不应被调用
        mock_converter.convert_to_cny.assert_not_called()


# ──────────────────────────────────────────────
# 测试：礼物购买
# ──────────────────────────────────────────────


class TestGiftPurchase:
    """会员礼物购买消息测试。"""

    @pytest.mark.asyncio
    async def test_gift_purchase_content(self) -> None:
        """礼物购买消息应包含 [会员礼物] 前缀。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_gift_purchase_action(nickname="Bob")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "[会员礼物]" in envelope["text"]
        assert "Bob" in envelope["text"]


# ──────────────────────────────────────────────
# 测试：消息去重
# ──────────────────────────────────────────────


class TestDeduplication:
    """消息去重测试。"""

    @pytest.mark.asyncio
    async def test_duplicate_message_returns_none(self) -> None:
        """重复消息应返回 None。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_text_action(message_id="dup_001")

        # 第一次应正常返回
        envelope1 = await dispatcher.dispatch(action)
        assert envelope1 is not None

        # 第二次应返回 None（去重）
        envelope2 = await dispatcher.dispatch(action)
        assert envelope2 is None

    @pytest.mark.asyncio
    async def test_different_messages_not_deduplicated(self) -> None:
        """不同消息不应被去重。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")

        envelope1 = await dispatcher.dispatch(_make_text_action(message_id="msg_a"))
        envelope2 = await dispatcher.dispatch(_make_text_action(message_id="msg_b"))

        assert envelope1 is not None
        assert envelope2 is not None

    @pytest.mark.asyncio
    async def test_empty_id_not_deduplicated(self) -> None:
        """空 message_id 不应触发去重。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_text_action(message_id="")

        # 空 ID 不去重，但空消息文本会返回 None
        # 所以用有文本但空 ID 的消息
        action["addChatItemAction"]["item"]["liveChatTextMessageRenderer"]["message"] = {
            "runs": [{"text": "test"}],
        }

        envelope1 = await dispatcher.dispatch(action)
        envelope2 = await dispatcher.dispatch(action)

        # 空 ID 不去重，两次都应返回
        assert envelope1 is not None
        assert envelope2 is not None


# ──────────────────────────────────────────────
# 测试：消息过滤
# ──────────────────────────────────────────────


class TestFilters:
    """消息过滤测试。"""

    @pytest.mark.asyncio
    async def test_emoji_filter(self) -> None:
        """启用 emoji 过滤时应移除 emoji。"""
        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            filter_emoji=True,
        )
        action = _make_text_action(text="Hello 😀 World 🎉")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        # emoji 应被移除
        assert "😀" not in envelope["text"]
        assert "🎉" not in envelope["text"]

    @pytest.mark.asyncio
    async def test_hashtag_filter(self) -> None:
        """启用 hashtag 过滤时应移除 hashtag。"""
        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            remove_hashtags=True,
        )
        action = _make_text_action(text="Check #gaming #stream")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "#gaming" not in envelope["text"]
        assert "#stream" not in envelope["text"]

    @pytest.mark.asyncio
    async def test_message_truncation(self) -> None:
        """超长消息应被截断。"""
        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            max_message_length=10,
        )
        long_text = "A" * 100
        action = _make_text_action(text=long_text)

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert len(envelope["text"]) <= 13  # 10 + "..."
        assert envelope["text"].endswith("...")

    @pytest.mark.asyncio
    async def test_no_filter_by_default(self) -> None:
        """默认不过滤 emoji 和 hashtag。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_text_action(text="Hello 😀 #test")

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert "😀" in envelope["text"]
        assert "#test" in envelope["text"]


# ──────────────────────────────────────────────
# 测试：未识别消息
# ──────────────────────────────────────────────


class TestUnrecognizedMessages:
    """未识别消息类型测试。"""

    @pytest.mark.asyncio
    async def test_unknown_action_returns_none(self) -> None:
        """未识别的 action 类型应返回 None。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = {"someOtherAction": {}}

        envelope = await dispatcher.dispatch(action)

        assert envelope is None

    @pytest.mark.asyncio
    async def test_unknown_renderer_returns_none(self) -> None:
        """未识别的 renderer 类型应返回 None。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = {
            "addChatItemAction": {
                "item": {
                    "unknownRenderer": {"id": "test"},
                },
            },
        }

        envelope = await dispatcher.dispatch(action)

        assert envelope is None

    @pytest.mark.asyncio
    async def test_debug_mode_logs_unknown_action(self) -> None:
        """debug_mode 下未识别的 action 应记录日志。"""
        dispatcher = YouTubeMessageDispatcher(
            video_id="test_video",
            debug_mode=True,
        )
        action = {"unknownAction": {}}

        # 不应抛出异常
        envelope = await dispatcher.dispatch(action)
        assert envelope is None


# ──────────────────────────────────────────────
# 测试：UserRole 逻辑
# ──────────────────────────────────────────────


class TestUserRoleLogic:
    """UserRole 分配逻辑测试。"""

    @pytest.mark.asyncio
    async def test_normal_user_is_member(self) -> None:
        """普通弹幕用户应为 MEMBER。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_text_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["from_user"]["role"] == "member"

    @pytest.mark.asyncio
    async def test_sc_user_is_operator(self) -> None:
        """SC 用户应为 OPERATOR。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_sc_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["from_user"]["role"] == "operator"

    @pytest.mark.asyncio
    async def test_member_milestone_is_operator(self) -> None:
        """会员里程碑用户应为 OPERATOR。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_member_milestone_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["from_user"]["role"] == "operator"

    @pytest.mark.asyncio
    async def test_super_sticker_is_operator(self) -> None:
        """Super Sticker 用户应为 OPERATOR。"""
        dispatcher = YouTubeMessageDispatcher(video_id="test_video")
        action = _make_super_sticker_action()

        envelope = await dispatcher.dispatch(action)

        assert envelope is not None
        assert envelope["from_user"]["role"] == "operator"


# ──────────────────────────────────────────────
# 出站消息 segment 解析测试
# ──────────────────────────────────────────────


class TestExtractTextFromSegment:
    """_extract_text_from_segment 方法测试。"""

    def test_text_segment(self) -> None:
        """text 类型 segment 应直接提取 data。"""
        from plugin import YouTubeLiveAdapter

        seg = {"type": "text", "data": "Hello World"}
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == "Hello World"

    def test_text_segment_empty_data(self) -> None:
        """text 类型 segment data 为空应返回空字符串。"""
        from plugin import YouTubeLiveAdapter

        seg = {"type": "text", "data": ""}
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == ""

    def test_text_segment_none_data(self) -> None:
        """text 类型 segment data 为 None 应返回空字符串。"""
        from plugin import YouTubeLiveAdapter

        seg = {"type": "text", "data": None}
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == ""

    def test_seglist_segment(self) -> None:
        """seglist 类型 segment 应拼接所有 text 子 segment。"""
        from plugin import YouTubeLiveAdapter

        seg = {
            "type": "seglist",
            "data": [
                {"type": "text", "data": "Hello"},
                {"type": "text", "data": "World"},
            ],
        }
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == "Hello World"

    def test_seglist_mixed_types(self) -> None:
        """seglist 中混合类型应只提取 text，忽略 image/command。"""
        from plugin import YouTubeLiveAdapter

        seg = {
            "type": "seglist",
            "data": [
                {"type": "text", "data": "Hello"},
                {"type": "image", "data": "http://example.com/img.png"},
                {"type": "command", "data": "some_command"},
                {"type": "text", "data": "World"},
            ],
        }
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == "Hello World"

    def test_seglist_empty(self) -> None:
        """seglist 为空列表应返回空字符串。"""
        from plugin import YouTubeLiveAdapter

        seg = {"type": "seglist", "data": []}
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == ""

    def test_image_segment(self) -> None:
        """image 类型 segment 应返回空字符串。"""
        from plugin import YouTubeLiveAdapter

        seg = {"type": "image", "data": "http://example.com/img.png"}
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == ""

    def test_command_segment(self) -> None:
        """command 类型 segment 应返回空字符串。"""
        from plugin import YouTubeLiveAdapter

        seg = {"type": "command", "data": "some_command"}
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == ""

    def test_unknown_segment_type(self) -> None:
        """未知类型 segment 应返回空字符串。"""
        from plugin import YouTubeLiveAdapter

        seg = {"type": "audio", "data": "some_audio"}
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == ""

    def test_seglist_non_dict_items(self) -> None:
        """seglist 中非 dict 项应被跳过。"""
        from plugin import YouTubeLiveAdapter

        seg = {
            "type": "seglist",
            "data": [
                "not_a_dict",
                123,
                {"type": "text", "data": "Valid"},
            ],
        }
        result = YouTubeLiveAdapter._extract_text_from_segment(seg)
        assert result == "Valid"

