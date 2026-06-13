"""YouTube Live Chat 消息发送器

通过 YouTube Data API v3 向直播间发送文本消息。

核心流程：
1. 使用 OAuth2 refresh_token 获取 access_token
2. 从 video_id 解析 live_chat_id
3. 通过 POST /liveChat/messages 发送文本消息

OAuth2 认证流程：
- 使用 client_id + client_secret + refresh_token 换取 access_token
- access_token 默认有效期约 3600 秒，过期前自动刷新
- 所有 Data API v3 请求均需 Bearer token 授权

配额注意：
- YouTube Data API v3 每日配额约 10,000 单位
- liveChatMessages.insert 消耗约 50 单位/次
- 理论上每天可发送约 200 条消息
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from src.kernel.logger import get_logger

logger = get_logger("youtube_live_adapter.sender")

# OAuth2 token 刷新端点
_TOKEN_URL = "https://oauth2.googleapis.com/token"

# YouTube Data API v3 端点
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_LIVE_CHAT_MESSAGES_URL = "https://www.googleapis.com/youtube/v3/liveChat/messages"

# access_token 提前刷新的缓冲时间（秒），避免临界过期
_TOKEN_REFRESH_BUFFER = 60


class YouTubeLiveChatSender:
    """YouTube Live Chat 消息发送器。

    通过 OAuth2 认证 + YouTube Data API v3 向直播间发送纯文本消息。

    Usage::

        sender = YouTubeLiveChatSender(
            client_id="...",
            client_secret="...",
            refresh_token="...",
        )
        await sender.start(video_id="dQw4w9WgXcQ")
        await sender.send_text_message("Hello from bot!")
        await sender.aclose()
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        proxy: str = "",
        timeout: float = 15.0,
    ) -> None:
        """初始化发送器。

        Args:
            client_id: OAuth2 Client ID
            client_secret: OAuth2 Client Secret
            refresh_token: OAuth2 Refresh Token
            proxy: HTTP 代理地址，留空则直连
            timeout: HTTP 请求超时（秒）
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token

        proxy_url = proxy.strip() if proxy else None
        self._http = httpx.AsyncClient(proxy=proxy_url, timeout=timeout)

        self._access_token: str = ""
        self._token_expires_at: float = 0.0  # Unix timestamp
        self._live_chat_id: str = ""

    # ──────────────────────────────────────────────
    # 公共接口
    # ──────────────────────────────────────────────

    async def start(self, video_id: str, live_chat_id: str = "") -> None:
        """启动发送器：获取 access_token 和 live_chat_id。

        如果提供了 live_chat_id 则直接使用，否则从 video_id 自动获取。

        Args:
            video_id: YouTube 视频 ID
            live_chat_id: 可选的 Live Chat ID，留空则自动从 video_id 获取

        Raises:
            RuntimeError: access_token 获取失败或 live_chat_id 获取失败
        """
        # 获取 access_token
        await self._refresh_access_token()
        logger.info("OAuth2 access_token 获取成功")

        # 获取 live_chat_id
        if live_chat_id:
            self._live_chat_id = live_chat_id
            logger.info(f"使用配置的 live_chat_id: {live_chat_id}")
        else:
            await self._fetch_live_chat_id(video_id)
            logger.info(f"从 video_id={video_id} 获取 live_chat_id 成功")

    async def send_text_message(self, text: str) -> dict[str, Any]:
        """发送文本消息到直播间。

        如果 access_token 已过期，会自动刷新后重试一次。

        Args:
            text: 要发送的文本内容

        Returns:
            API 响应 dict

        Raises:
            RuntimeError: live_chat_id 未初始化
        """
        if not self._live_chat_id:
            raise RuntimeError("live_chat_id 未初始化，请先调用 start()")

        # 检查 token 是否需要刷新
        if self._is_token_expired():
            try:
                await self._refresh_access_token()
            except Exception as exc:
                logger.error(f"刷新 access_token 失败: {exc}")
                return {}

        body = {
            "snippet": {
                "liveChatId": self._live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {
                    "messageText": text,
                },
            },
        }

        try:
            resp = await self._http.post(
                _LIVE_CHAT_MESSAGES_URL,
                json=body,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
            logger.debug(f"消息发送成功: {text[:30]}...")
            return result

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 403:
                # 配额耗尽或权限不足
                logger.error(f"发送消息被拒绝 (HTTP 403)，可能配额耗尽或权限不足: {exc}")
            elif status == 401:
                # token 无效，尝试刷新后重试一次
                logger.warning("access_token 无效 (HTTP 401)，尝试刷新后重试...")
                try:
                    await self._refresh_access_token()
                    resp = await self._http.post(
                        _LIVE_CHAT_MESSAGES_URL,
                        json=body,
                        headers=self._auth_headers(),
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    logger.debug(f"重试发送消息成功: {text[:30]}...")
                    return result
                except Exception as retry_exc:
                    logger.error(f"重试发送消息失败: {retry_exc}")
            else:
                logger.warning(f"发送消息失败 (HTTP {status}): {exc}")
            return {}

        except httpx.TimeoutException:
            logger.warning(f"发送消息超时: {text[:30]}...")
            return {}

        except Exception as exc:
            logger.warning(f"发送消息异常: {exc}")
            return {}

    async def aclose(self) -> None:
        """关闭 HTTP 客户端，释放资源。"""
        await self._http.aclose()
        logger.debug("YouTubeLiveChatSender 已关闭")

    # ──────────────────────────────────────────────
    # OAuth2 Token 管理
    # ──────────────────────────────────────────────

    async def _refresh_access_token(self) -> None:
        """使用 refresh_token 获取新的 access_token。

        Raises:
            RuntimeError: token 刷新失败
        """
        body = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            resp = await self._http.post(
                _TOKEN_URL,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()

            self._access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self._token_expires_at = time.time() + expires_in - _TOKEN_REFRESH_BUFFER

            logger.debug(f"access_token 刷新成功，有效期 {expires_in}s")

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"OAuth2 token 刷新失败 (HTTP {exc.response.status_code})") from exc
        except Exception as exc:
            raise RuntimeError(f"OAuth2 token 刷新失败: {exc}") from exc

    def _is_token_expired(self) -> bool:
        """检查 access_token 是否已过期或即将过期。"""
        return time.time() >= self._token_expires_at

    def _auth_headers(self) -> dict[str, str]:
        """构建带 Bearer token 的请求头。"""
        return {"Authorization": f"Bearer {self._access_token}"}

    # ──────────────────────────────────────────────
    # live_chat_id 获取
    # ──────────────────────────────────────────────

    async def _fetch_live_chat_id(self, video_id: str) -> None:
        """从 video_id 获取 live_chat_id。

        调用 YouTube Data API v3 的 videos.list 接口，
        从 liveStreamingDetails.activeLiveChatId 提取。

        Raises:
            RuntimeError: live_chat_id 获取失败
        """
        params = {
            "id": video_id,
            "part": "liveStreamingDetails",
        }

        try:
            resp = await self._http.get(
                _VIDEOS_URL,
                params=params,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("items", [])
            if not items:
                raise RuntimeError(f"video_id={video_id} 未找到视频信息")

            live_details = items[0].get("liveStreamingDetails", {})
            chat_id = live_details.get("activeLiveChatId")
            if not chat_id:
                raise RuntimeError(
                    f"video_id={video_id} 的 liveStreamingDetails 中未找到 activeLiveChatId，"
                    "可能不是正在直播的视频"
                )

            self._live_chat_id = chat_id

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"获取 live_chat_id 失败 (HTTP {exc.response.status_code})"
            ) from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"获取 live_chat_id 失败: {exc}") from exc


__all__ = ["YouTubeLiveChatSender"]
