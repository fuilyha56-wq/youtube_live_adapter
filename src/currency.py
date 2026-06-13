"""YouTube Live Adapter 货币转换器

使用汇率 API 获取实时汇率，将 Super Chat 的外币金额转换为 CNY。

特性：
- 异步获取汇率（httpx.AsyncClient）
- 简单 TTL 缓存（默认 1 小时），避免频繁请求
- 转换失败时返回 None，不影响主流程

Usage::

    converter = CurrencyConverter(api_url="https://api.exchangerate-api.com/v4/latest/USD")
    cny_amount = await converter.convert_to_cny(100.0, "JPY")
    # cny_amount ≈ 4.87 (示例)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from src.kernel.logger import get_logger

logger = get_logger("youtube_live_adapter.currency")

# 默认缓存 TTL（秒）
_DEFAULT_CACHE_TTL = 3600


class CurrencyConverter:
    """异步货币转换器，将外币金额转换为 CNY。

    使用汇率 API 获取实时汇率，带 TTL 缓存。

    Attributes:
        api_url: 汇率 API 地址
        cache_ttl: 缓存有效期（秒）
    """

    def __init__(
        self,
        *,
        api_url: str = "https://api.exchangerate-api.com/v4/latest/USD",
        cache_ttl: float = _DEFAULT_CACHE_TTL,
    ) -> None:
        """初始化货币转换器。

        Args:
            api_url: 汇率 API 地址，需返回 {"rates": {"CNY": 7.25, "JPY": 155.0, ...}} 格式
            cache_ttl: 缓存有效期（秒），默认 1 小时
        """
        self._api_url = api_url
        self._cache_ttl = cache_ttl

        # 缓存：{currency_code: rate_to_base}
        self._rates: dict[str, float] = {}
        self._base_currency: str = ""
        self._cache_time: float = 0.0

        # 独立的 httpx 客户端，不与 API 客户端共享
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """确保 httpx 客户端已创建（带并发保护）。"""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def aclose(self) -> None:
        """关闭 HTTP 客户端，释放资源。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_rates(self) -> dict[str, float]:
        """从汇率 API 获取最新汇率。

        Returns:
            汇率字典 {currency_code: rate}，base 货币的汇率为 1.0

        Raises:
            httpx.HTTPError: 请求失败
        """
        client = await self._ensure_client()
        resp = await client.get(self._api_url)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        base = str(data.get("base", "USD"))
        rates: dict[str, float] = data.get("rates", {})
        # 确保 base 货币自身汇率为 1.0
        rates[base] = 1.0

        self._rates = rates
        self._base_currency = base
        self._cache_time = time.monotonic()

        logger.debug(f"汇率缓存已更新 (base={base}, currencies={len(rates)})")
        return rates

    async def _get_rates(self) -> dict[str, float]:
        """获取汇率，缓存过期时自动刷新。"""
        now = time.monotonic()
        if self._rates and (now - self._cache_time) < self._cache_ttl:
            return self._rates

        try:
            return await self._fetch_rates()
        except Exception as exc:
            logger.warning(f"获取汇率失败: {exc}")
            # 返回过期缓存（如果有），否则返回空字典
            return self._rates

    async def convert_to_cny(self, amount: float, currency: str) -> float | None:
        """将外币金额转换为 CNY。

        Args:
            amount: 原始金额
            currency: 原始货币代码（如 "JPY", "USD", "TWD"）

        Returns:
            转换后的 CNY 金额，转换失败时返回 None
        """
        if currency == "CNY":
            return amount

        rates = await self._get_rates()
        if not rates:
            return None

        # 汇率 API 以 base 货币为基准，rates[CNY] 表示 1 base = rates[CNY] CNY
        # 转换公式：amount_cny = amount * (rates["CNY"] / rates[currency])
        rate_cny = rates.get("CNY")
        rate_src = rates.get(currency)

        if rate_cny is None or rate_src is None:
            logger.debug(f"不支持 {currency} → CNY 转换 (rate_cny={rate_cny}, rate_src={rate_src})")
            return None

        if rate_src == 0:
            return None

        cny_amount = amount * (rate_cny / rate_src)
        return round(cny_amount, 2)


__all__ = ["CurrencyConverter"]
