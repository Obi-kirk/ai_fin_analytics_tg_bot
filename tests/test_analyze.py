"""Тесты AI-анализа: распознавание актива и сборка контекста (без сети)."""

import pytest

import src.handlers.analyze as module
from src.handlers.analyze import _crypto_context, _detect_context, _market_context
from src.services.financial_api import StockQuote


class _FakeStockFetcher:
    """Подменяет fetch_stock/fetch_crypto в модуле analyze."""

    def __init__(self, quote: StockQuote | None, raises: type[Exception] | None = None):
        self.quote = quote
        self.raises = raises

    async def __call__(self, symbol: str) -> StockQuote:
        if self.raises:
            raise self.raises(symbol)
        return self.quote or StockQuote(symbol=symbol, price=100.0, change_percent=1.5)


@pytest.fixture
def fake_fetchers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешные ответы для акций и монет."""
    monkeypatch.setattr(
        module,
        "fetch_stock",
        _FakeStockFetcher(StockQuote(symbol="AAPL", price=250.5, change_percent=1.25)),
    )
    monkeypatch.setattr(
        module,
        "fetch_crypto",
        _FakeStockFetcher(
            StockQuote(symbol="BTC", price=100000.0, change_percent=-2.0)
        ),
    )


class TestMarketContext:
    async def test_stock_context(self, fake_fetchers) -> None:
        ctx = await _market_context("AAPL")
        assert "акция/индекс" in ctx
        assert "250.5000" in ctx
        assert "+1.25" in ctx

    async def test_crypto_context(self, fake_fetchers) -> None:
        ctx = await _market_context("BTC")
        assert "криптовалюта" in ctx
        assert "100000.0000" in ctx
        assert "-2.00" in ctx


class TestDetectContext:
    async def test_known_symbol(self, fake_fetchers) -> None:
        ctx = await _detect_context("nvda")
        assert ctx is not None
        assert "акция/индекс" in ctx

    async def test_unknown_ticker_falls_back_to_crypto(self, monkeypatch) -> None:
        monkeypatch.setattr(
            module, "fetch_stock", _FakeStockFetcher(None, raises=LookupError)
        )
        monkeypatch.setattr(
            module,
            "fetch_crypto",
            _FakeStockFetcher(StockQuote(symbol="USDT", price=1.0, change_percent=0.0)),
        )
        ctx = await _detect_context("USDT")
        assert ctx is not None
        assert "криптовалюта" in ctx

    async def test_plain_text_not_an_asset(self, fake_fetchers) -> None:
        assert await _detect_context("стоит") is None
        assert await _detect_context("САМОЛЁТ") is None

    async def test_network_error_propagates(self, fake_fetchers, monkeypatch) -> None:
        monkeypatch.setattr(
            module, "fetch_stock", _FakeStockFetcher(None, raises=RuntimeError)
        )
        with pytest.raises(RuntimeError):
            await _detect_context("MSFT")

    async def test_unknown_symbol_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(
            module, "fetch_stock", _FakeStockFetcher(None, raises=LookupError)
        )
        monkeypatch.setattr(
            module, "fetch_crypto", _FakeStockFetcher(None, raises=LookupError)
        )
        assert await _detect_context("ZZZZZ") is None


class TestCryptoContext:
    async def test_sign_for_positive(self, fake_fetchers) -> None:
        ctx = await _crypto_context("BTC")
        assert "-2.00" in ctx
