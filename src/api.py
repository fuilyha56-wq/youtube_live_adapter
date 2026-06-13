"""YouTube Inner Tube API 客户端

封装 YouTube Inner Tube API 的 HTTP 请求细节，包括：
- 多客户端配置（WEB / iOS / TV）及 fallback 策略
- 获取初始 continuation token（从 API 或 HTML 页面）
- 轮询直播间消息
- 代理支持

Inner Tube API 是 YouTube 的非公开内部 API，其请求结构可能随时变更。
建议定期使用 Chrome DevTools 抓取最新请求格式并更新客户端配置。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from src.kernel.logger import get_logger

logger = get_logger("youtube_live_adapter.api")


# ──────────────────────────────────────────────
# 多客户端配置（参考 yt-dlp 的多客户端 fallback 策略）
# ──────────────────────────────────────────────

CLIENT_CONFIGS: dict[str, dict[str, Any]] = {
    "WEB": {
        "client_name": "WEB",
        "client_version": "2.20250612.00.00",
        "client_name_header": "1",
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        ),
    },
    "IOS": {
        "client_name": "IOS",
        "client_version": "19.29.1",
        "client_name_header": "5",
        "user_agent": (
            "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 18_5 like Mac OS X;)"
        ),
    },
    "TV": {
        "client_name": "TVHTML5_SIMPLY",
        "client_version": "7.20250612.00.00",
        "client_name_header": "7",
        "user_agent": (
            "Mozilla/5.0 (PlayStation; PlayStation 4/5) AppleWebKit/605.1.15"
        ),
    },
}

# 客户端 fallback 优先级：优先使用配置指定的客户端，失败后按此顺序尝试
_FALLBACK_ORDER = ["IOS", "TV", "WEB"]

# Inner Tube API 端点
_LIVE_CHAT_ENDPOINT = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
_VIDEO_PAGE_URL = "https://www.youtube.com/watch?v={video_id}"


class YouTubeApiError(RuntimeError):
    """YouTube Inner Tube API 请求错误"""


class TokenExpiredError(YouTubeApiError):
    """Continuation token 已失效，需要重新获取"""


class YouTubeInnerTubeAPI:
    """YouTube Inner Tube API HTTP 客户端

    使用 httpx.AsyncClient 发起请求，支持代理和超时配置。
    多客户端 fallback：当首选客户端请求失败时，自动尝试其他客户端。

    Usage::

        api = YouTubeInnerTubeAPI(proxy="http://127.0.0.1:7890", timeout=15.0)
        token = await api.get_initial_continuation("video_id")
        actions, new_token, viewer_count = await api.get_live_chat_messages(token)
        await api.aclose()
    """

    def __init__(
        self,
        *,
        proxy: str = "",
        timeout: float = 15.0,
        language: str = "zh",
        client_name: str = "IOS",
    ) -> None:
        """初始化 API 客户端

        Args:
            proxy: HTTP 代理地址，留空则直连
            timeout: HTTP 请求超时（秒）
            language: 界面语言代码
            client_name: 首选客户端标识（WEB / IOS / TV）
        """
        self._language = language
        self._preferred_client = client_name.upper() if client_name.upper() in CLIENT_CONFIGS else "IOS"

        # 构建客户端 fallback 顺序：首选 → 其余按 _FALLBACK_ORDER
        self._client_order = [self._preferred_client]
        for name in _FALLBACK_ORDER:
            if name not in self._client_order:
                self._client_order.append(name)

        # 创建 httpx 客户端（不设默认 headers，每次请求根据客户端配置动态设置）
        proxy_url = proxy.strip() if proxy else None
        self._client = httpx.AsyncClient(
            proxy=proxy_url,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.youtube.com",
                "Referer": "https://www.youtube.com/",
            },
        )

    async def aclose(self) -> None:
        """关闭 HTTP 客户端"""
        await self._client.aclose()

    # ──────────────────────────────────────────────
    # 公共接口
    # ──────────────────────────────────────────────

    async def get_initial_continuation(self, video_id: str) -> str:
        """获取直播间的初始 continuation token

        尝试策略：
        1. 通过 Inner Tube API 直接请求（传入 videoId）
        2. 如果失败，回退到抓取视频页面 HTML 解析 ytInitialData

        Args:
            video_id: YouTube 视频 ID

        Returns:
            初始 continuation token 字符串

        Raises:
            YouTubeApiError: 所有策略均失败
        """
        # 策略 1：通过 API 获取
        for client_key in self._client_order:
            try:
                token = await self._get_continuation_via_api(video_id, client_key)
                logger.info(f"通过 {client_key} 客户端获取 continuation token 成功")
                return token
            except Exception as exc:
                logger.debug(f"通过 {client_key} 客户端获取 token 失败: {exc}")
                continue

        # 策略 2：通过 HTML 页面解析
        try:
            token = await self._get_continuation_via_html(video_id)
            logger.info("通过 HTML 页面解析获取 continuation token 成功")
            return token
        except Exception as exc:
            logger.debug(f"通过 HTML 页面解析获取 token 失败: {exc}")

        raise YouTubeApiError(f"无法获取 video_id={video_id} 的 continuation token")

    async def get_live_chat_messages(
        self, continuation_token: str, client_key: str | None = None
    ) -> tuple[list[dict[str, Any]], str, int | None]:
        """轮询直播间消息

        Args:
            continuation_token: 上一次返回的 continuation token
            client_key: 指定客户端标识，为 None 时使用首选客户端

        Returns:
            (actions, new_token, viewer_count) 三元组：
            - actions: 消息 action 列表，可能为空
            - new_token: 下一次轮询使用的 continuation token
            - viewer_count: 当前观众人数，无法获取时为 None

        Raises:
            TokenExpiredError: token 已失效
            YouTubeApiError: 请求失败
        """
        ck = client_key or self._preferred_client
        payload = self._build_poll_payload(continuation_token, ck)

        try:
            resp_data = await self._post(payload, ck)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 403):
                raise TokenExpiredError(f"Token 可能已失效 (HTTP {exc.response.status_code})") from exc
            raise YouTubeApiError(f"HTTP {exc.response.status_code}") from exc

        # 解析响应
        continuation_contents = resp_data.get("continuationContents")
        if not continuation_contents:
            raise TokenExpiredError("响应中缺少 continuationContents，token 可能已失效")

        live_chat_continuation = continuation_contents.get("liveChatContinuation")
        if not live_chat_continuation:
            raise TokenExpiredError("响应中缺少 liveChatContinuation")

        actions = live_chat_continuation.get("actions", [])

        # 提取新的 continuation token
        continuations = live_chat_continuation.get("continuations", [])
        new_token = self._extract_continuation_token(continuations)
        if not new_token:
            raise TokenExpiredError("响应中未找到新的 continuation token")

        # 提取观众人数
        viewer_count: int | None = None
        viewer_count_str = live_chat_continuation.get("viewerCountRenderer", {}).get("viewCount")
        if viewer_count_str:
            try:
                viewer_count = int(str(viewer_count_str).replace(",", ""))
            except (ValueError, AttributeError):
                viewer_count = None

        return actions, new_token, viewer_count

    # ──────────────────────────────────────────────
    # 内部实现
    # ──────────────────────────────────────────────

    async def _get_continuation_via_api(self, video_id: str, client_key: str) -> str:
        """通过 Inner Tube API 获取初始 continuation token"""
        payload = self._build_init_payload(video_id, client_key)
        resp_data = await self._post(payload, client_key)

        # 尝试从响应中提取 continuation token
        # 路径 1: continuationContents.liveChatContinuation.continuations
        contents = resp_data.get("continuationContents")
        if contents:
            live_chat = contents.get("liveChatContinuation")
            if live_chat:
                continuations = live_chat.get("continuations", [])
                token = self._extract_continuation_token(continuations)
                if token:
                    return token

        # 路径 2: contents.twoColumnWatchNextResults → itemSectionRenderer
        # （某些客户端返回的初始数据结构不同）
        try:
            tabs = (
                resp_data.get("contents", {})
                .get("twoColumnWatchNextResults", {})
                .get("conversationBar", {})
                .get("liveChatRenderer", {})
                .get("continuations", [])
            )
            token = self._extract_continuation_token(tabs)
            if token:
                return token
        except (KeyError, TypeError, AttributeError):
            pass

        raise YouTubeApiError("API 响应中未找到 continuation token")

    async def _get_continuation_via_html(self, video_id: str) -> str:
        """通过抓取视频页面 HTML 解析 ytInitialData 获取 continuation token

        从 HTML 中提取 ytInitialData JSON，然后解析出 live chat 的 continuation token。
        关键路径：
        ytInitialData → contents.twoColumnWatchNextResults.results.results.contents
        → itemSectionRenderer.contents → continuationItemRenderer
        → continuationEndpoint.continuationCommand.token
        """
        url = _VIDEO_PAGE_URL.format(video_id=video_id)
        # 使用不带 JSON content-type 的请求获取 HTML
        resp = await self._client.get(
            url,
            headers={
                "User-Agent": CLIENT_CONFIGS["WEB"]["user_agent"],
                "Accept": "text/html",
            },
        )
        resp.raise_for_status()
        html = resp.text

        # 提取 ytInitialData（使用更宽松的匹配）
        # YouTube 页面中 ytInitialData 可能跨越多行，需要使用 DOTALL 模式
        match = re.search(r"var\s+ytInitialData\s*=\s*(\{.+?\});", html, re.DOTALL)
        if not match:
            raise YouTubeApiError("HTML 中未找到 ytInitialData")

        data = json.loads(match.group(1))

        # 多路径尝试提取 token
        # 路径 1: conversationBar.liveChatRenderer.continuations
        try:
            continuations = (
                data["contents"]["twoColumnWatchNextResults"]["conversationBar"]
                ["liveChatRenderer"]["continuations"]
            )
            token = self._extract_continuation_token(continuations)
            if token:
                return token
        except (KeyError, TypeError):
            pass

        # 路径 2: results → itemSectionRenderer → continuationItemRenderer
        try:
            contents = (
                data["contents"]["twoColumnWatchNextResults"]["results"]
                ["results"]["contents"]
            )
            for item in contents:
                section = item.get("itemSectionRenderer")
                if not section:
                    continue
                for sub_item in section.get("contents", []):
                    continuation = sub_item.get("continuationItemRenderer")
                    if continuation:
                        token = (
                            continuation.get("continuationEndpoint", {})
                            .get("continuationCommand", {})
                            .get("token")
                        )
                        if token:
                            return token
        except (KeyError, TypeError):
            pass

        raise YouTubeApiError("HTML 页面中未找到 continuation token")

    def _build_init_payload(self, video_id: str, client_key: str) -> dict[str, Any]:
        """构建获取初始数据的请求体"""
        cfg = CLIENT_CONFIGS[client_key]
        return {
            "videoId": video_id,
            "context": {
                "client": {
                    "clientName": cfg["client_name"],
                    "clientVersion": cfg["client_version"],
                    "hl": self._language,
                },
            },
        }

    def _build_poll_payload(self, continuation_token: str, client_key: str) -> dict[str, Any]:
        """构建轮询消息的请求体"""
        cfg = CLIENT_CONFIGS[client_key]
        return {
            "continuation": continuation_token,
            "context": {
                "client": {
                    "clientName": cfg["client_name"],
                    "clientVersion": cfg["client_version"],
                    "hl": self._language,
                },
            },
        }

    async def _post(self, payload: dict[str, Any], client_key: str) -> dict[str, Any]:
        """发送 POST 请求到 Inner Tube API

        Args:
            payload: 请求体
            client_key: 客户端标识

        Returns:
            解析后的 JSON 响应

        Raises:
            httpx.HTTPStatusError: HTTP 错误
        """
        cfg = CLIENT_CONFIGS[client_key]
        headers = {
            "User-Agent": cfg["user_agent"],
            "X-YouTube-Client-Name": cfg["client_name_header"],
            "X-YouTube-Client-Version": cfg["client_version"],
        }
        resp = await self._client.post(_LIVE_CHAT_ENDPOINT, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _extract_continuation_token(continuations: list[dict[str, Any]]) -> str | None:
        """从 continuations 数组中提取 continuation token

        YouTube 的 continuations 数组可能包含多种类型的 token：
        - liveChatContinuationToken: 常规轮询 token
        - invalidationContinuationData: 实时更新 token
        - timedContinuationData: 定时轮询 token

        优先提取 liveChatContinuationToken，其次 timedContinuationData。
        """
        if not continuations:
            return None

        for cont in continuations:
            # 优先：liveChatContinuationToken
            token = cont.get("liveChatContinuationToken")
            if isinstance(token, str) and token:
                return token

            # 次选：timedContinuationData（某些客户端使用此格式）
            timed = cont.get("timedContinuationData")
            if isinstance(timed, dict):
                token = timed.get("continuation")
                if isinstance(token, str) and token:
                    return token

            # 兼容：invalidationContinuationData
            inv = cont.get("invalidationContinuationData")
            if isinstance(inv, dict):
                token = inv.get("continuation")
                if isinstance(token, str) and token:
                    return token

        return None


__all__ = ["CLIENT_CONFIGS", "TokenExpiredError", "YouTubeApiError", "YouTubeInnerTubeAPI"]
