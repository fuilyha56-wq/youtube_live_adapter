"""YouTubePollClient 单元测试

测试轮询客户端的核心功能：
- 自适应轮询间隔
- Token 过期重获取
- 健康状态管理
- start/stop 生命周期
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api import TokenExpiredError, YouTubeInnerTubeAPI
from src.client import YouTubePollClient

# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _make_client(
    poll_interval: float = 2.5,
    max_poll_interval: float = 60.0,
    on_viewer_count: Any | None = None,
) -> YouTubePollClient:
    """创建轮询客户端实例（使用 mock API）。"""
    mock_api = MagicMock(spec=YouTubeInnerTubeAPI)
    mock_api.get_initial_continuation = AsyncMock(return_value="init_token")
    mock_api.get_live_chat_messages = AsyncMock(return_value=([], "next_token", None))

    async def mock_on_event(action: dict[str, Any]) -> None:
        pass

    return YouTubePollClient(
        api=mock_api,
        on_event=mock_on_event,
        video_id="test_video",
        poll_interval=poll_interval,
        max_poll_interval=max_poll_interval,
        on_viewer_count=on_viewer_count,
    )


# ──────────────────────────────────────────────
# 测试：初始化
# ──────────────────────────────────────────────


class TestInit:
    """客户端初始化测试。"""

    def test_default_running_is_false(self) -> None:
        """初始化时 _running 应为 False。"""
        client = _make_client()
        assert client._running is False

    def test_default_healthy_is_false(self) -> None:
        """初始化时健康状态应为 False。"""
        client = _make_client()
        assert client.is_healthy is False

    def test_start_sets_running_true(self) -> None:
        """start() 应设置 _running 为 True。"""
        client = _make_client()
        client.start()
        assert client._running is True


# ──────────────────────────────────────────────
# 测试：健康状态
# ──────────────────────────────────────────────


class TestHealthStatus:
    """健康状态测试。"""

    def test_healthy_requires_running_and_healthy(self) -> None:
        """is_healthy 需要 _running 和 _healthy 都为 True。"""
        client = _make_client()

        # 初始状态：_running=False, _healthy=False
        assert client.is_healthy is False

        # 只有 _running=True
        client._running = True
        client._healthy = False
        assert client.is_healthy is False

        # 只有 _healthy=True
        client._running = False
        client._healthy = True
        assert client.is_healthy is False

        # 两者都为 True
        client._running = True
        client._healthy = True
        assert client.is_healthy is True

    @pytest.mark.asyncio
    async def test_stop_sets_unhealthy(self) -> None:
        """stop() 应设置不健康状态。"""
        client = _make_client()
        client._running = True
        client._healthy = True

        await client.stop()

        assert client.is_healthy is False
        assert client._running is False


# ──────────────────────────────────────────────
# 测试：轮询循环
# ──────────────────────────────────────────────


class TestPollLoop:
    """轮询循环测试。"""

    @pytest.mark.asyncio
    async def test_run_sets_healthy_on_start(self) -> None:
        """run() 成功获取初始 token 后应设置健康状态。"""
        client = _make_client()

        # 让轮询循环只执行一次然后停止
        call_count = 0

        async def mock_get_messages(token: str) -> tuple[list[dict[str, Any]], str, int | None]:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                client._running = False
            return [], "next_token", None

        client._api.get_live_chat_messages = mock_get_messages

        await client.run()

        assert client._healthy is True

    @pytest.mark.asyncio
    async def test_run_processes_actions(self) -> None:
        """run() 应将 actions 逐条传递给 on_event 回调。"""
        received_actions: list[dict[str, Any]] = []

        async def capture_event(action: dict[str, Any]) -> None:
            received_actions.append(action)

        mock_api = MagicMock(spec=YouTubeInnerTubeAPI)
        mock_api.get_initial_continuation = AsyncMock(return_value="init_token")

        call_count = 0

        async def mock_get_messages(token: str) -> tuple[list[dict[str, Any]], str, int | None]:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                client._running = False
            return [{"action": "test1"}, {"action": "test2"}], "next_token", None

        mock_api.get_live_chat_messages = mock_get_messages

        client = YouTubePollClient(
            api=mock_api,
            on_event=capture_event,
            video_id="test_video",
        )

        await client.run()

        assert len(received_actions) == 2
        assert received_actions[0] == {"action": "test1"}
        assert received_actions[1] == {"action": "test2"}


# ──────────────────────────────────────────────
# 测试：Token 过期重获取
# ──────────────────────────────────────────────


class TestTokenExpired:
    """Token 过期重获取测试。"""

    @pytest.mark.asyncio
    async def test_token_expired_triggers_reget(self) -> None:
        """Token 过期时应重新获取 continuation token。"""
        client = _make_client()

        call_count = 0
        reget_called = False

        async def mock_get_messages(token: str) -> tuple[list[dict[str, Any]], str, int | None]:
            nonlocal call_count, reget_called
            call_count += 1
            if call_count == 1:
                raise TokenExpiredError("Token expired")
            # 第二次调用（重获取后）
            client._running = False
            return [], "new_token", None

        client._api.get_live_chat_messages = mock_get_messages
        client._api.get_initial_continuation = AsyncMock(
            return_value="reget_token",
        )

        # 标记重新获取被调用
        original_reget = client._api.get_initial_continuation

        async def tracked_reget(video_id: str) -> str:
            nonlocal reget_called
            reget_called = True
            return await original_reget(video_id)

        client._api.get_initial_continuation = tracked_reget

        await client.run()

        assert reget_called is True

    @pytest.mark.asyncio
    async def test_token_reget_failure_raises(self) -> None:
        """重获取 token 失败应标记不健康并抛出异常。"""
        client = _make_client()

        async def mock_get_messages(token: str) -> tuple[list[dict[str, Any]], str, int | None]:
            raise TokenExpiredError("Token expired")

        client._api.get_live_chat_messages = mock_get_messages
        client._api.get_initial_continuation = AsyncMock(
            side_effect=Exception("Network error"),
        )

        with pytest.raises(Exception, match="Network error"):
            await client.run()

        assert client._healthy is False


# ──────────────────────────────────────────────
# 测试：HTTP 错误处理
# ──────────────────────────────────────────────


class TestHTTPErrors:
    """HTTP 错误处理测试。"""

    @pytest.mark.asyncio
    async def test_http_error_marks_unhealthy(self) -> None:
        """HTTP 错误应标记不健康并抛出异常。"""
        client = _make_client()

        mock_response = MagicMock()
        mock_response.status_code = 500

        async def mock_get_messages(token: str) -> tuple[list[dict[str, Any]], str, int | None]:
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=MagicMock(),
                response=mock_response,
            )

        client._api.get_live_chat_messages = mock_get_messages

        with pytest.raises(httpx.HTTPStatusError):
            await client.run()

        assert client._healthy is False


# ──────────────────────────────────────────────
# 测试：观众人数回调
# ──────────────────────────────────────────────


class TestViewerCountCallback:
    """观众人数回调测试。"""

    @pytest.mark.asyncio
    async def test_on_viewer_count_called(self) -> None:
        """有观众人数时应调用 on_viewer_count 回调。"""
        received_counts: list[int] = []

        async def mock_on_viewer_count(count: int) -> None:
            received_counts.append(count)

        mock_api = MagicMock(spec=YouTubeInnerTubeAPI)
        mock_api.get_initial_continuation = AsyncMock(return_value="init_token")

        call_count = 0

        async def mock_get_messages(token: str) -> tuple[list[dict[str, Any]], str, int | None]:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                client._running = False
            return [], "next_token", 1234

        mock_api.get_live_chat_messages = mock_get_messages

        client = YouTubePollClient(
            api=mock_api,
            on_event=AsyncMock(),
            video_id="test_video",
            on_viewer_count=mock_on_viewer_count,
        )

        await client.run()

        assert received_counts == [1234]

    @pytest.mark.asyncio
    async def test_on_viewer_count_not_called_when_none(self) -> None:
        """观众人数为 None 时不应调用 on_viewer_count 回调。"""
        received_counts: list[int] = []

        async def mock_on_viewer_count(count: int) -> None:
            received_counts.append(count)

        mock_api = MagicMock(spec=YouTubeInnerTubeAPI)
        mock_api.get_initial_continuation = AsyncMock(return_value="init_token")

        call_count = 0

        async def mock_get_messages(token: str) -> tuple[list[dict[str, Any]], str, int | None]:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                client._running = False
            return [], "next_token", None

        mock_api.get_live_chat_messages = mock_get_messages

        client = YouTubePollClient(
            api=mock_api,
            on_event=AsyncMock(),
            video_id="test_video",
            on_viewer_count=mock_on_viewer_count,
        )

        await client.run()

        assert received_counts == []

    @pytest.mark.asyncio
    async def test_on_viewer_count_not_called_when_no_callback(self) -> None:
        """未设置回调时，观众人数应被忽略。"""
        mock_api = MagicMock(spec=YouTubeInnerTubeAPI)
        mock_api.get_initial_continuation = AsyncMock(return_value="init_token")

        call_count = 0

        async def mock_get_messages(token: str) -> tuple[list[dict[str, Any]], str, int | None]:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                client._running = False
            return [], "next_token", 5678

        mock_api.get_live_chat_messages = mock_get_messages

        client = YouTubePollClient(
            api=mock_api,
            on_event=AsyncMock(),
            video_id="test_video",
        )

        await client.run()

        # 正常完成，无异常
