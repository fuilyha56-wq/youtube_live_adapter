"""YouTubeInnerTubeAPI 单元测试

测试 API 客户端的核心功能：
- continuation token 提取
- 请求 payload 构建
- 多客户端 fallback 逻辑
- Token 过期检测
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.api import (
    CLIENT_CONFIGS,
    TokenExpiredError,
    YouTubeApiError,
    YouTubeInnerTubeAPI,
)

# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _make_api(
    proxy: str = "",
    timeout: float = 15.0,
    language: str = "zh",
    client_name: str = "IOS",
) -> YouTubeInnerTubeAPI:
    """创建 API 实例（不发起真实请求）。"""
    return YouTubeInnerTubeAPI(
        proxy=proxy,
        timeout=timeout,
        language=language,
        client_name=client_name,
    )


# ──────────────────────────────────────────────
# 测试：初始化
# ──────────────────────────────────────────────


class TestInit:
    """API 客户端初始化测试。"""

    def test_default_client_is_ios(self) -> None:
        """默认客户端应为 IOS。"""
        api = _make_api()
        assert api._preferred_client == "IOS"

    def test_custom_client_name(self) -> None:
        """自定义客户端名称应生效。"""
        api = _make_api(client_name="WEB")
        assert api._preferred_client == "WEB"

    def test_invalid_client_falls_back_to_ios(self) -> None:
        """无效客户端名称应回退到 IOS。"""
        api = _make_api(client_name="INVALID")
        assert api._preferred_client == "IOS"

    def test_client_order_starts_with_preferred(self) -> None:
        """客户端顺序应以首选客户端开头。"""
        api = _make_api(client_name="TV")
        assert api._client_order[0] == "TV"
        # 其余按 _FALLBACK_ORDER 排列
        assert len(api._client_order) == 3

    def test_client_order_no_duplicates(self) -> None:
        """客户端顺序不应有重复。"""
        api = _make_api(client_name="IOS")
        assert len(api._client_order) == len(set(api._client_order))


# ──────────────────────────────────────────────
# 测试：Payload 构建
# ──────────────────────────────────────────────


class TestPayloadBuilding:
    """请求 payload 构建测试。"""

    def test_init_payload_structure(self) -> None:
        """初始请求 payload 应包含 videoId 和 context。"""
        api = _make_api()
        payload = api._build_init_payload("test_video_id", "IOS")

        assert payload["videoId"] == "test_video_id"
        assert "context" in payload
        assert "client" in payload["context"]
        assert payload["context"]["client"]["clientName"] == "IOS"
        assert payload["context"]["client"]["hl"] == "zh"

    def test_init_payload_uses_client_config(self) -> None:
        """初始请求 payload 应使用对应客户端配置。"""
        api = _make_api(language="en")
        payload = api._build_init_payload("vid", "WEB")

        cfg = CLIENT_CONFIGS["WEB"]
        assert payload["context"]["client"]["clientName"] == cfg["client_name"]
        assert payload["context"]["client"]["clientVersion"] == cfg["client_version"]
        assert payload["context"]["client"]["hl"] == "en"

    def test_poll_payload_structure(self) -> None:
        """轮询请求 payload 应包含 continuation 和 context。"""
        api = _make_api()
        payload = api._build_poll_payload("token_abc", "IOS")

        assert payload["continuation"] == "token_abc"
        assert "context" in payload
        assert payload["context"]["client"]["clientName"] == "IOS"


# ──────────────────────────────────────────────
# 测试：Continuation Token 提取
# ──────────────────────────────────────────────


class TestTokenExtraction:
    """Continuation token 提取测试。"""

    def test_extract_live_chat_continuation_token(self) -> None:
        """应优先提取 liveChatContinuationToken。"""
        continuations = [
            {"liveChatContinuationToken": "token_live"},
            {"timedContinuationData": {"continuation": "token_timed"}},
        ]

        result = YouTubeInnerTubeAPI._extract_continuation_token(continuations)

        assert result == "token_live"

    def test_extract_timed_continuation_data(self) -> None:
        """无 liveChatContinuationToken 时应提取 timedContinuationData。"""
        continuations = [
            {"timedContinuationData": {"continuation": "token_timed"}},
        ]

        result = YouTubeInnerTubeAPI._extract_continuation_token(continuations)

        assert result == "token_timed"

    def test_extract_invalidation_continuation_data(self) -> None:
        """应兼容 invalidationContinuationData。"""
        continuations = [
            {"invalidationContinuationData": {"continuation": "token_inv"}},
        ]

        result = YouTubeInnerTubeAPI._extract_continuation_token(continuations)

        assert result == "token_inv"

    def test_extract_empty_continuations(self) -> None:
        """空 continuations 应返回 None。"""
        result = YouTubeInnerTubeAPI._extract_continuation_token([])
        assert result is None

    def test_extract_no_matching_token(self) -> None:
        """无匹配 token 格式应返回 None。"""
        continuations = [{"unknownData": {"token": "xxx"}}]
        result = YouTubeInnerTubeAPI._extract_continuation_token(continuations)
        assert result is None

    def test_extract_empty_string_token(self) -> None:
        """空字符串 token 应被忽略。"""
        continuations = [
            {"liveChatContinuationToken": ""},
            {"timedContinuationData": {"continuation": "fallback"}},
        ]

        result = YouTubeInnerTubeAPI._extract_continuation_token(continuations)

        assert result == "fallback"


# ──────────────────────────────────────────────
# 测试：get_live_chat_messages
# ──────────────────────────────────────────────


class TestGetLiveChatMessages:
    """轮询消息测试。"""

    @pytest.mark.asyncio
    async def test_successful_poll(self) -> None:
        """成功轮询应返回 actions、new_token 和 viewer_count。"""
        api = _make_api()

        mock_response = {
            "continuationContents": {
                "liveChatContinuation": {
                    "actions": [
                        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {}}}},
                    ],
                    "continuations": [
                        {"liveChatContinuationToken": "new_token"},
                    ],
                },
            },
        }

        with patch.object(api, "_post", new_callable=AsyncMock, return_value=mock_response):
            actions, new_token, viewer_count = await api.get_live_chat_messages("old_token")

        assert len(actions) == 1
        assert new_token == "new_token"
        assert viewer_count is None

    @pytest.mark.asyncio
    async def test_token_expired_on_400(self) -> None:
        """HTTP 400 应抛出 TokenExpiredError。"""
        api = _make_api()

        mock_response = MagicMock()
        mock_response.status_code = 400
        http_error = httpx.HTTPStatusError(
            "Bad Request",
            request=MagicMock(),
            response=mock_response,
        )

        with patch.object(api, "_post", new_callable=AsyncMock, side_effect=http_error), \
             pytest.raises(TokenExpiredError):
            await api.get_live_chat_messages("expired_token")

    @pytest.mark.asyncio
    async def test_token_expired_on_403(self) -> None:
        """HTTP 403 应抛出 TokenExpiredError。"""
        api = _make_api()

        mock_response = MagicMock()
        mock_response.status_code = 403
        http_error = httpx.HTTPStatusError(
            "Forbidden",
            request=MagicMock(),
            response=mock_response,
        )

        with patch.object(api, "_post", new_callable=AsyncMock, side_effect=http_error), \
             pytest.raises(TokenExpiredError):
            await api.get_live_chat_messages("expired_token")

    @pytest.mark.asyncio
    async def test_missing_continuation_contents(self) -> None:
        """缺少 continuationContents 应抛出 TokenExpiredError。"""
        api = _make_api()

        with patch.object(api, "_post", new_callable=AsyncMock, return_value={}), \
             pytest.raises(TokenExpiredError):
            await api.get_live_chat_messages("token")

    @pytest.mark.asyncio
    async def test_missing_new_token(self) -> None:
        """缺少新 continuation token 应抛出 TokenExpiredError。"""
        api = _make_api()

        mock_response = {
            "continuationContents": {
                "liveChatContinuation": {
                    "actions": [],
                    "continuations": [],
                },
            },
        }

        with patch.object(api, "_post", new_callable=AsyncMock, return_value=mock_response), \
             pytest.raises(TokenExpiredError):
            await api.get_live_chat_messages("token")

    @pytest.mark.asyncio
    async def test_empty_actions(self) -> None:
        """无消息时应返回空 actions 列表。"""
        api = _make_api()

        mock_response = {
            "continuationContents": {
                "liveChatContinuation": {
                    "actions": [],
                    "continuations": [
                        {"liveChatContinuationToken": "next_token"},
                    ],
                },
            },
        }

        with patch.object(api, "_post", new_callable=AsyncMock, return_value=mock_response):
            actions, new_token, viewer_count = await api.get_live_chat_messages("token")

        assert actions == []
        assert new_token == "next_token"
        assert viewer_count is None

    @pytest.mark.asyncio
    async def test_viewer_count_extraction(self) -> None:
        """应正确提取观众人数（含逗号格式化）。"""
        api = _make_api()

        mock_response = {
            "continuationContents": {
                "liveChatContinuation": {
                    "actions": [],
                    "continuations": [
                        {"liveChatContinuationToken": "next_token"},
                    ],
                    "viewerCountRenderer": {
                        "viewCount": "1,234",
                    },
                },
            },
        }

        with patch.object(api, "_post", new_callable=AsyncMock, return_value=mock_response):
            _, _, viewer_count = await api.get_live_chat_messages("token")

        assert viewer_count == 1234

    @pytest.mark.asyncio
    async def test_viewer_count_invalid_format(self) -> None:
        """观众人数格式无效时应返回 None。"""
        api = _make_api()

        mock_response = {
            "continuationContents": {
                "liveChatContinuation": {
                    "actions": [],
                    "continuations": [
                        {"liveChatContinuationToken": "next_token"},
                    ],
                    "viewerCountRenderer": {
                        "viewCount": "not_a_number",
                    },
                },
            },
        }

        with patch.object(api, "_post", new_callable=AsyncMock, return_value=mock_response):
            _, _, viewer_count = await api.get_live_chat_messages("token")

        assert viewer_count is None


# ──────────────────────────────────────────────
# 测试：get_initial_continuation
# ──────────────────────────────────────────────


class TestGetInitialContinuation:
    """获取初始 continuation token 测试。"""

    @pytest.mark.asyncio
    async def test_api_success(self) -> None:
        """API 方式获取 token 成功。"""
        api = _make_api()

        with patch.object(
            api, "_get_continuation_via_api", new_callable=AsyncMock, return_value="init_token"
        ):
            token = await api.get_initial_continuation("video_id")

        assert token == "init_token"

    @pytest.mark.asyncio
    async def test_api_fails_html_succeeds(self) -> None:
        """API 失败后应回退到 HTML 解析。"""
        api = _make_api()

        with patch.object(
            api, "_get_continuation_via_api", new_callable=AsyncMock, side_effect=Exception("API fail")
        ), patch.object(
            api, "_get_continuation_via_html", new_callable=AsyncMock, return_value="html_token"
        ):
            token = await api.get_initial_continuation("video_id")

        assert token == "html_token"

    @pytest.mark.asyncio
    async def test_all_strategies_fail(self) -> None:
        """所有策略失败应抛出 YouTubeApiError。"""
        api = _make_api()

        with patch.object(
            api, "_get_continuation_via_api", new_callable=AsyncMock, side_effect=Exception("fail")
        ), patch.object(
            api, "_get_continuation_via_html", new_callable=AsyncMock, side_effect=Exception("fail")
        ), pytest.raises(YouTubeApiError):
            await api.get_initial_continuation("video_id")
