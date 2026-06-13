"""YouTubeLiveChatSender 单元测试

测试发送器的核心功能：
- 初始化参数验证
- OAuth2 token 刷新流程
- live_chat_id 获取（成功/失败）
- 消息发送（成功/失败/网络错误/token 过期重试）
- 资源释放
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.sender import YouTubeLiveChatSender

# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _make_sender(**overrides: Any) -> YouTubeLiveChatSender:
    """构造 YouTubeLiveChatSender 实例，提供默认参数。"""
    defaults = {
        "client_id": "test_client_id",
        "client_secret": "test_client_secret",
        "refresh_token": "test_refresh_token",
        "proxy": "",
        "timeout": 5.0,
    }
    defaults.update(overrides)
    return YouTubeLiveChatSender(**defaults)


def _mock_token_response(access_token: str = "ya29.test_token", expires_in: int = 3600) -> MagicMock:
    """构造模拟的 OAuth2 token 刷新响应。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "access_token": access_token,
        "expires_in": expires_in,
        "token_type": "Bearer",
    }
    return resp


def _mock_videos_response(live_chat_id: str = "QWERTY12345") -> MagicMock:
    """构造模拟的 videos.list 响应。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "items": [
            {
                "liveStreamingDetails": {
                    "activeLiveChatId": live_chat_id,
                },
            },
        ],
    }
    return resp


def _mock_send_response(message_id: str = "msg_001") -> MagicMock:
    """构造模拟的 liveChatMessages.insert 响应。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "id": message_id,
        "snippet": {
            "liveChatId": "QWERTY12345",
            "type": "textMessageEvent",
        },
    }
    return resp


# ──────────────────────────────────────────────
# TestInit: 初始化参数验证
# ──────────────────────────────────────────────


class TestInit:
    """初始化参数验证测试。"""

    def test_default_parameters(self) -> None:
        """默认参数应正确存储。"""
        sender = _make_sender()
        assert sender._client_id == "test_client_id"
        assert sender._client_secret == "test_client_secret"
        assert sender._refresh_token == "test_refresh_token"
        assert sender._access_token == ""
        assert sender._live_chat_id == ""

    def test_custom_parameters(self) -> None:
        """自定义参数应正确存储。"""
        sender = _make_sender(proxy="http://127.0.0.1:7890", timeout=30.0)
        assert sender._client_id == "test_client_id"
        assert sender._http is not None


# ──────────────────────────────────────────────
# TestTokenRefresh: OAuth2 token 刷新流程
# ──────────────────────────────────────────────


class TestTokenRefresh:
    """OAuth2 token 刷新流程测试。"""

    @pytest.mark.asyncio
    async def test_refresh_success(self) -> None:
        """token 刷新成功应更新 access_token 和过期时间。"""
        sender = _make_sender()

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _mock_token_response()

            await sender._refresh_access_token()

            assert sender._access_token == "ya29.test_token"
            assert sender._token_expires_at > time.time()

    @pytest.mark.asyncio
    async def test_refresh_http_error(self) -> None:
        """token 刷新 HTTP 错误应抛出 RuntimeError。"""
        sender = _make_sender()

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.text = "Bad Request"
            mock_post.side_effect = httpx.HTTPStatusError(
                "400 Bad Request", request=MagicMock(), response=mock_resp
            )

            with pytest.raises(RuntimeError, match="OAuth2 token 刷新失败"):
                await sender._refresh_access_token()

    @pytest.mark.asyncio
    async def test_is_token_expired_initially(self) -> None:
        """初始状态下 token 应视为已过期。"""
        sender = _make_sender()
        assert sender._is_token_expired() is True

    @pytest.mark.asyncio
    async def test_is_token_expired_after_refresh(self) -> None:
        """刷新后 token 应未过期。"""
        sender = _make_sender()

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _mock_token_response(expires_in=3600)
            await sender._refresh_access_token()

            assert sender._is_token_expired() is False

    @pytest.mark.asyncio
    async def test_auth_headers(self) -> None:
        """auth_headers 应包含正确的 Bearer token。"""
        sender = _make_sender()
        sender._access_token = "test_token_123"

        headers = sender._auth_headers()
        assert headers["Authorization"] == "Bearer test_token_123"


# ──────────────────────────────────────────────
# TestGetLiveChatId: live_chat_id 获取
# ──────────────────────────────────────────────


class TestGetLiveChatId:
    """live_chat_id 获取测试。"""

    @pytest.mark.asyncio
    async def test_fetch_success(self) -> None:
        """从 video_id 获取 live_chat_id 成功。"""
        sender = _make_sender()
        sender._access_token = "test_token"

        with patch.object(sender._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _mock_videos_response("CHAT_ID_999")

            await sender._fetch_live_chat_id("video_123")

            assert sender._live_chat_id == "CHAT_ID_999"

    @pytest.mark.asyncio
    async def test_fetch_no_video(self) -> None:
        """video_id 不存在时应抛出 RuntimeError。"""
        sender = _make_sender()
        sender._access_token = "test_token"

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"items": []}

        with patch.object(sender._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = resp

            with pytest.raises(RuntimeError, match="未找到视频信息"):
                await sender._fetch_live_chat_id("invalid_id")

    @pytest.mark.asyncio
    async def test_fetch_no_active_chat(self) -> None:
        """视频没有活跃直播时应抛出 RuntimeError。"""
        sender = _make_sender()
        sender._access_token = "test_token"

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"items": [{"liveStreamingDetails": {}}]}

        with patch.object(sender._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = resp

            with pytest.raises(RuntimeError, match="未找到 activeLiveChatId"):
                await sender._fetch_live_chat_id("video_123")

    @pytest.mark.asyncio
    async def test_fetch_http_error(self) -> None:
        """HTTP 错误应抛出 RuntimeError。"""
        sender = _make_sender()
        sender._access_token = "test_token"

        with patch.object(sender._http, "get", new_callable=AsyncMock) as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_get.side_effect = httpx.HTTPStatusError(
                "403 Forbidden", request=MagicMock(), response=mock_resp
            )

            with pytest.raises(RuntimeError, match="获取 live_chat_id 失败"):
                await sender._fetch_live_chat_id("video_123")


# ──────────────────────────────────────────────
# TestSendMessage: 消息发送
# ──────────────────────────────────────────────


class TestSendMessage:
    """消息发送测试。"""

    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        """文本消息发送成功。"""
        sender = _make_sender()
        sender._live_chat_id = "CHAT_123"
        sender._access_token = "test_token"
        sender._token_expires_at = time.time() + 3600

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _mock_send_response()

            result = await sender.send_text_message("Hello!")

            assert result.get("id") == "msg_001"
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_no_live_chat_id(self) -> None:
        """live_chat_id 未初始化时应抛出 RuntimeError。"""
        sender = _make_sender()
        sender._access_token = "test_token"
        sender._token_expires_at = time.time() + 3600

        with pytest.raises(RuntimeError, match="live_chat_id 未初始化"):
            await sender.send_text_message("Hello!")

    @pytest.mark.asyncio
    async def test_send_http_403_quota_exceeded(self) -> None:
        """HTTP 403 应记录错误并返回空 dict。"""
        sender = _make_sender()
        sender._live_chat_id = "CHAT_123"
        sender._access_token = "test_token"
        sender._token_expires_at = time.time() + 3600

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_post.side_effect = httpx.HTTPStatusError(
                "403 Forbidden", request=MagicMock(), response=mock_resp
            )

            result = await sender.send_text_message("Hello!")

            assert result == {}

    @pytest.mark.asyncio
    async def test_send_http_401_retry(self) -> None:
        """HTTP 401 应自动刷新 token 并重试一次。"""
        sender = _make_sender()
        sender._live_chat_id = "CHAT_123"
        sender._access_token = "expired_token"
        sender._token_expires_at = time.time() + 3600

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            mock_resp_401 = MagicMock()
            mock_resp_401.status_code = 401
            error = httpx.HTTPStatusError(
                "401 Unauthorized", request=MagicMock(), response=mock_resp_401
            )
            mock_post.side_effect = [
                error,
                _mock_token_response(),  # token 刷新请求
                _mock_send_response(),  # 重试发送请求
            ]

            result = await sender.send_text_message("Hello!")

            assert result.get("id") == "msg_001"

    @pytest.mark.asyncio
    async def test_send_timeout(self) -> None:
        """网络超时应返回空 dict。"""
        sender = _make_sender()
        sender._live_chat_id = "CHAT_123"
        sender._access_token = "test_token"
        sender._token_expires_at = time.time() + 3600

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.TimeoutException("timeout")

            result = await sender.send_text_message("Hello!")

            assert result == {}

    @pytest.mark.asyncio
    async def test_send_auto_refresh_expired_token(self) -> None:
        """token 过期时应自动刷新后发送。"""
        sender = _make_sender()
        sender._live_chat_id = "CHAT_123"
        sender._access_token = "expired_token"
        sender._token_expires_at = time.time() - 100  # 已过期

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            # 第一次调用是 token 刷新，第二次是消息发送
            mock_post.side_effect = [
                _mock_token_response("ya29.new_token"),
                _mock_send_response(),
            ]

            result = await sender.send_text_message("Hello!")

            assert result.get("id") == "msg_001"
            assert sender._access_token == "ya29.new_token"


# ──────────────────────────────────────────────
# TestStart: start() 方法测试
# ──────────────────────────────────────────────


class TestStart:
    """start() 方法测试。"""

    @pytest.mark.asyncio
    async def test_start_with_auto_chat_id(self) -> None:
        """start() 不提供 live_chat_id 时应自动获取。"""
        sender = _make_sender()

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post, \
             patch.object(sender._http, "get", new_callable=AsyncMock) as mock_get:
            mock_post.return_value = _mock_token_response()
            mock_get.return_value = _mock_videos_response("AUTO_CHAT_ID")

            await sender.start(video_id="video_123")

            assert sender._access_token == "ya29.test_token"
            assert sender._live_chat_id == "AUTO_CHAT_ID"

    @pytest.mark.asyncio
    async def test_start_with_provided_chat_id(self) -> None:
        """start() 提供 live_chat_id 时应直接使用。"""
        sender = _make_sender()

        with patch.object(sender._http, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = _mock_token_response()

            await sender.start(video_id="video_123", live_chat_id="PROVIDED_CHAT_ID")

            assert sender._live_chat_id == "PROVIDED_CHAT_ID"


# ──────────────────────────────────────────────
# TestAclose: 资源释放
# ──────────────────────────────────────────────


class TestAclose:
    """资源释放测试。"""

    @pytest.mark.asyncio
    async def test_aclose(self) -> None:
        """aclose 应关闭 HTTP 客户端。"""
        sender = _make_sender()

        with patch.object(sender._http, "aclose", new_callable=AsyncMock) as mock_aclose:
            await sender.aclose()

            mock_aclose.assert_called_once()
