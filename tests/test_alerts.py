"""Тесты сервиса алертов: правила срабатывания, маппинг id, батч-цены.

Без сети: HTTP-вызовы мокаются, чистые функции тестируются напрямую.
"""

from unittest.mock import AsyncMock, patch

from src.services.alerts import (
    _fetch_prices,
    _gecko_id,
    alert_triggered,
)


class TestAlertTriggered:
    def test_above_reached(self) -> None:
        assert alert_triggered(70000, 70000, "above") is True
        assert alert_triggered(70001, 70000, "above") is True

    def test_above_not_reached(self) -> None:
        assert alert_triggered(65000, 70000, "above") is False

    def test_below_reached(self) -> None:
        assert alert_triggered(3400, 3500, "below") is True
        assert alert_triggered(3500, 3500, "below") is True

    def test_below_not_reached(self) -> None:
        assert alert_triggered(3600, 3500, "below") is False

    def test_unknown_direction(self) -> None:
        assert alert_triggered(100, 50, "sideways") is False


class TestGeckoId:
    def test_known_coin(self) -> None:
        assert _gecko_id("BTC") == "bitcoin"
        assert _gecko_id("ETH") == "ethereum"

    def test_fallback_lowercase(self) -> None:
        assert _gecko_id("PEPE") == "pepe"


class TestFetchPrices:
    async def test_crypto_batch(self) -> None:
        """Крипта запрашивается одним батчем (экономия лимита)."""
        gecko_mock = AsyncMock()
        gecko_mock.get_prices_batch.return_value = {
            "bitcoin": 70000.0,
            "ethereum": 3500.0,
        }
        with patch(
            "src.services.alerts.CoinGeckoClient", return_value=gecko_mock
        ), patch("src.services.alerts.make_session") as make_session_mock:
            async with AsyncMock() as ctx:
                make_session_mock.return_value = ctx
                prices = await _fetch_prices("crypto", ["BTC", "ETH", "SOL"])
        assert prices == {"BTC": 70000.0, "ETH": 3500.0}
        gecko_mock.get_prices_batch.assert_awaited_once()

    async def test_empty_symbols(self) -> None:
        assert await _fetch_prices("crypto", []) == {}

    async def test_fx_fetch(self) -> None:
        with patch("src.services.alerts.fetch_fx") as fetch_fx_mock:
            fetch_fx_mock.return_value = AsyncMock(value=82.5)
            prices = await _fetch_prices("fx", ["USD"])
        assert prices == {"USD": 82.5}

    async def test_fx_fetch_error_skipped(self) -> None:
        with patch("src.services.alerts.fetch_fx", side_effect=RuntimeError):
            prices = await _fetch_prices("fx", ["USD"])
        assert prices == {}

    async def test_stock_fetch(self) -> None:
        with patch("src.services.alerts.fetch_stock") as fetch_stock_mock:
            fetch_stock_mock.return_value = AsyncMock(price=210.5)
            prices = await _fetch_prices("stock", ["AAPL"])
        assert prices == {"AAPL": 210.5}
