"""YouTube Live Chat HTTP 轮询客户端

管理轮询循环，自适应调整轮询间隔：
- 有消息时保持基础间隔
- 无消息时逐步增大间隔至上限

Token 过期时自动重新获取，HTTP 错误时抛出异常由上层 session_loop 处理重连。

Usage::

    client = YouTubePollClient(
        api=api,
        on_event=callback,
        video_id="dQw4w9WgXcQ",
    )
    await client.run()  # 阻塞直到 stop() 或不可恢复错误
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.kernel.logger import get_logger

from .api import TokenExpiredError, YouTubeInnerTubeAPI

logger = get_logger("youtube_live_adapter.client")


class YouTubePollClient:
    """YouTube Live Chat HTTP 轮询客户端。

    管理轮询循环，自适应调整轮询间隔：
    - 有消息时保持基础间隔
    - 无消息时逐步增大间隔至上限

    Token 过期时自动重新获取，HTTP 错误时抛出异常由上层 session_loop 处理重连。

    Attributes:
        is_healthy: 连接健康状态
    """

    def __init__(
        self,
        *,
        api: YouTubeInnerTubeAPI,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
        video_id: str,
        poll_interval: float = 2.5,
        max_poll_interval: float = 60.0,
        on_viewer_count: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        """初始化轮询客户端。

        Args:
            api: YouTube Inner Tube API 客户端
            on_event: 消息回调，接收原始 action dict
            video_id: YouTube 视频 ID
            poll_interval: 基础轮询间隔（秒）
            max_poll_interval: 最大轮询间隔（秒）
            on_viewer_count: 观众人数回调，接收当前观众人数
        """
        self._api = api
        self._on_event = on_event
        self._video_id = video_id
        self._poll_interval = poll_interval
        self._max_poll_interval = max_poll_interval
        self._on_viewer_count = on_viewer_count

        self._continuation_token: str | None = None
        self._current_interval = poll_interval
        self._running = False
        self._healthy = False

    async def run(self) -> None:
        """主轮询循环，阻塞直到 stop() 或不可恢复错误。

        流程：
        1. 获取初始 continuation token
        2. 循环轮询消息
        3. 有消息 → 逐条回调 on_event → 恢复基础间隔
        4. 无消息 → 逐步增大间隔
        5. TokenExpiredError → 重新获取 token
        6. 其他异常 → 向上抛出
        """
        # 获取初始 continuation token
        self._running = True
        self._continuation_token = await self._api.get_initial_continuation(self._video_id)
        self._healthy = True
        logger.info(f"轮询客户端启动 (video_id={self._video_id})")

        while self._running:
            try:
                assert self._continuation_token is not None

                # 轮询消息
                actions, new_token, viewer_count = await self._api.get_live_chat_messages(
                    self._continuation_token
                )
                self._continuation_token = new_token

                # 观众人数回调
                if viewer_count is not None and self._on_viewer_count is not None:
                    await self._on_viewer_count(viewer_count)

                if actions:
                    # 有消息：逐条回调，恢复基础间隔
                    for action in actions:
                        await self._on_event(action)
                    self._current_interval = self._poll_interval
                else:
                    # 无消息：逐步增大间隔
                    self._current_interval = min(
                        self._current_interval * 1.2, self._max_poll_interval
                    )

                await asyncio.sleep(self._current_interval)

            except TokenExpiredError:
                # Token 过期：重新获取
                logger.warning("Continuation token 已失效，重新获取...")
                try:
                    self._continuation_token = await self._api.get_initial_continuation(
                        self._video_id
                    )
                    self._current_interval = self._poll_interval
                    logger.info("重新获取 continuation token 成功，继续轮询")
                except Exception as exc:
                    logger.error(f"重新获取 token 失败: {exc}")
                    self._healthy = False
                    raise

            except httpx.HTTPStatusError as exc:
                # HTTP 错误：标记不健康，向上抛出
                logger.error(f"HTTP 请求失败: {exc}")
                self._healthy = False
                raise

            except asyncio.CancelledError:
                # 任务被取消：标记不健康，向上抛出
                self._healthy = False
                raise

    async def stop(self) -> None:
        """停止轮询循环。"""
        self._running = False
        self._healthy = False
        logger.info("轮询客户端已停止")

    @property
    def is_healthy(self) -> bool:
        """连接健康状态。"""
        return self._healthy and self._running


__all__ = ["YouTubePollClient"]
