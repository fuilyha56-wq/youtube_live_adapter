"""CurrencyConverter 单元测试

测试货币转换器的核心功能：
- 汇率获取与缓存
- CNY 转换计算
- 缓存 TTL 过期刷新
- 转换失败容错
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.currency import CurrencyConverter

# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _make_converter(
    api_url: str = "https://api.example.com/latest/USD",
    cache_ttl: float = 3600.0,
) -> CurrencyConverter:
    """创建 CurrencyConverter 实例。"""
    return CurrencyConverter(api_url=api_url, cache_ttl=cache_ttl)


def _mock_rates_response(
    base: str = "USD",
    rates: dict[str, float] | None = None,
) -> dict[str, Any]:
    """构造模拟的汇率 API 响应。"""
    if rates is None:
        rates = {"USD": 1.0, "CNY": 7.25, "JPY": 155.0, "EUR": 0.92, "TWD": 32.5}
    return {"base": base, "rates": rates}


# ──────────────────────────────────────────────
# 测试：初始化
# ──────────────────────────────────────────────


class TestInit:
    """初始化测试。"""

    def test_default_cache_ttl(self) -> None:
        """默认缓存 TTL 应为 3600 秒。"""
        converter = _make_converter()
        assert converter._cache_ttl == 3600.0

    def test_custom_cache_ttl(self) -> None:
        """自定义缓存 TTL 应生效。"""
        converter = _make_converter(cache_ttl=600.0)
        assert converter._cache_ttl == 600.0

    def test_initial_cache_is_empty(self) -> None:
        """初始缓存应为空。"""
        converter = _make_converter()
        assert converter._rates == {}
        assert converter._cache_time == 0.0


# ──────────────────────────────────────────────
# 测试：CNY 转换
# ──────────────────────────────────────────────


class TestConvertToCNY:
    """CNY 转换测试。"""

    @pytest.mark.asyncio
    async def test_usd_to_cny(self) -> None:
        """USD → CNY 转换应正确计算。"""
        converter = _make_converter()
        # 预填充缓存
        converter._rates = {"USD": 1.0, "CNY": 7.25}
        converter._base_currency = "USD"
        converter._cache_time = time.monotonic()

        result = await converter.convert_to_cny(100.0, "USD")

        assert result == 725.0

    @pytest.mark.asyncio
    async def test_jpy_to_cny(self) -> None:
        """JPY → CNY 转换应正确计算。"""
        converter = _make_converter()
        converter._rates = {"USD": 1.0, "CNY": 7.25, "JPY": 155.0}
        converter._base_currency = "USD"
        converter._cache_time = time.monotonic()

        # 100 JPY = 100 * (7.25 / 155.0) ≈ 4.68
        result = await converter.convert_to_cny(100.0, "JPY")

        assert result is not None
        assert abs(result - 4.68) < 0.1

    @pytest.mark.asyncio
    async def test_cny_to_cny_no_conversion(self) -> None:
        """CNY → CNY 应直接返回原金额。"""
        converter = _make_converter()

        result = await converter.convert_to_cny(100.0, "CNY")

        assert result == 100.0

    @pytest.mark.asyncio
    async def test_unsupported_currency_returns_none(self) -> None:
        """不支持的货币应返回 None。"""
        converter = _make_converter()
        converter._rates = {"USD": 1.0, "CNY": 7.25}
        converter._base_currency = "USD"
        converter._cache_time = time.monotonic()

        result = await converter.convert_to_cny(100.0, "XXX")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_rates_returns_none(self) -> None:
        """空汇率缓存应返回 None。"""
        converter = _make_converter()
        # 不填充缓存，且 _get_rates 会尝试获取但失败

        with patch.object(converter, "_fetch_rates", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await converter.convert_to_cny(100.0, "USD")

        assert result is None

    @pytest.mark.asyncio
    async def test_result_rounded_to_2_decimals(self) -> None:
        """转换结果应四舍五入到两位小数。"""
        converter = _make_converter()
        converter._rates = {"USD": 1.0, "CNY": 7.253}
        converter._base_currency = "USD"
        converter._cache_time = time.monotonic()

        result = await converter.convert_to_cny(1.0, "USD")

        assert result == 7.25


# ──────────────────────────────────────────────
# 测试：缓存机制
# ──────────────────────────────────────────────


class TestCache:
    """缓存机制测试。"""

    @pytest.mark.asyncio
    async def test_cache_prevents_repeated_requests(self) -> None:
        """缓存有效期内不应重复请求。"""
        converter = _make_converter()
        converter._rates = {"USD": 1.0, "CNY": 7.25}
        converter._base_currency = "USD"
        converter._cache_time = time.monotonic()

        with patch.object(converter, "_fetch_rates", new_callable=AsyncMock) as mock_fetch:
            # 多次调用应使用缓存
            await converter._get_rates()
            await converter._get_rates()
            await converter._get_rates()

            mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_expiry_triggers_refresh(self) -> None:
        """缓存过期应触发刷新。"""
        converter = _make_converter(cache_ttl=1.0)
        converter._rates = {"USD": 1.0, "CNY": 7.0}
        converter._base_currency = "USD"
        # 设置缓存时间为很久以前
        converter._cache_time = time.monotonic() - 10.0

        new_rates = {"USD": 1.0, "CNY": 7.5}
        with patch.object(
            converter, "_fetch_rates", new_callable=AsyncMock, return_value=new_rates
        ) as mock_fetch:
            rates = await converter._get_rates()

            mock_fetch.assert_called_once()
            assert rates["CNY"] == 7.5

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_stale_cache(self) -> None:
        """获取失败时应返回过期缓存。"""
        converter = _make_converter(cache_ttl=1.0)
        converter._rates = {"USD": 1.0, "CNY": 7.0}
        converter._base_currency = "USD"
        converter._cache_time = time.monotonic() - 10.0

        with patch.object(
            converter, "_fetch_rates", new_callable=AsyncMock, side_effect=Exception("Network error")
        ):
            rates = await converter._get_rates()

            # 应返回过期缓存
            assert rates["CNY"] == 7.0


# ──────────────────────────────────────────────
# 测试：汇率获取
# ──────────────────────────────────────────────


class TestFetchRates:
    """汇率获取测试。"""

    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        """成功获取汇率应更新缓存。"""
        converter = _make_converter()
        mock_response = _mock_rates_response()

        mock_http_response = MagicMock()
        mock_http_response.json.return_value = mock_response
        mock_http_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_http_response)

        with patch.object(converter, "_ensure_client", new_callable=AsyncMock, return_value=mock_client):
            rates = await converter._fetch_rates()

        assert "CNY" in rates
        assert rates["CNY"] == 7.25
        assert rates["USD"] == 1.0
        assert converter._base_currency == "USD"
        assert converter._cache_time > 0

    @pytest.mark.asyncio
    async def test_fetch_http_error(self) -> None:
        """HTTP 请求失败应抛出异常。"""
        converter = _make_converter()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))

        with patch.object(converter, "_ensure_client", new_callable=AsyncMock, return_value=mock_client), \
             pytest.raises(httpx.HTTPError):
            await converter._fetch_rates()


# ──────────────────────────────────────────────
# 测试：资源释放
# ──────────────────────────────────────────────


class TestAclose:
    """资源释放测试。"""

    @pytest.mark.asyncio
    async def test_aclose_closes_client(self) -> None:
        """aclose() 应关闭 HTTP 客户端。"""
        converter = _make_converter()
        mock_client = AsyncMock()
        converter._client = mock_client

        await converter.aclose()

        mock_client.aclose.assert_called_once()
        assert converter._client is None

    @pytest.mark.asyncio
    async def test_aclose_idempotent(self) -> None:
        """多次调用 aclose() 不应报错。"""
        converter = _make_converter()

        await converter.aclose()
        await converter.aclose()  # 不应抛出异常
