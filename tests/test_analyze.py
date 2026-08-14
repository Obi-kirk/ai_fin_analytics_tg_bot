"""AI analysis tests: asset detection, context with news/trends (no network)."""

import pytest

import src.handlers.analyze as module
from src.handlers.analyze import (
    _clean_text,
    _crypto_context,
    _detect_context,
    _format_money,
    _market_context,
    _stock_context,
    _trend_change,
)
from src.services.cache import TTLCache
from src.services.financial_api import StockQuote


@pytest.fixture(autouse=True)
def _use_ru():
    """These tests check Russian text — set the language explicitly."""
    from src.i18n import set_lang

    set_lang("ru")


class _FakeStockFetcher:
    """Replaces fetch_stock/fetch_crypto in the analyze module."""

    def __init__(self, quote: StockQuote | None, raises: type[Exception] | None = None):
        self.quote = quote
        self.raises = raises

    async def __call__(self, symbol: str) -> StockQuote:
        if self.raises:
            raise self.raises(symbol)
        return self.quote or StockQuote(symbol=symbol, price=100.0, change_percent=1.5)


class _FakeFetcher:
    """Returns a stub for any extra request (profile, news, etc.)."""

    def __init__(self, value, raises: type[Exception] | None = None):
        self.value = value
        self.raises = raises

    async def __call__(self, *args, **kwargs):
        if self.raises:
            raise self.raises(*args, **kwargs)
        return self.value


@pytest.fixture
def cache() -> TTLCache:
    return TTLCache()


@pytest.fixture
def fake_fetchers(monkeypatch: pytest.MonkeyPatch, cache: TTLCache) -> None:
    """Successful responses for all external requests."""
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
    monkeypatch.setattr(module, "_fetch_company_profile", _FakeFetcher({}))
    monkeypatch.setattr(module, "_fetch_news", _FakeFetcher([]))
    monkeypatch.setattr(
        module, "_fetch_market_data", _FakeFetcher({"name": "Bitcoin", "rank": 1})
    )
    monkeypatch.setattr(module, "_fetch_price_history", _FakeFetcher([]))


class TestMarketContext:
    async def test_stock_context(self, fake_fetchers, cache) -> None:
        ctx = await _market_context("AAPL", cache)
        assert "акция/индекс" in ctx
        assert "250.5000" in ctx
        assert "+1.25" in ctx

    async def test_crypto_context(self, fake_fetchers, cache) -> None:
        ctx = await _market_context("BTC", cache)
        assert "криптовалюта" in ctx
        assert "100000.0000" in ctx
        assert "-2.00" in ctx


class TestStockContextExtra:
    async def test_profile_and_news_included(
        self, fake_fetchers, cache, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            module,
            "_fetch_company_profile",
            _FakeFetcher(
                {
                    "name": "Apple Inc",
                    "finnhubIndustry": "Technology",
                    "description": "Apple designs  smartphones. " * 10,
                }
            ),
        )
        monkeypatch.setattr(
            module,
            "_fetch_news",
            _FakeFetcher(
                [
                    {"headline": "Apple выпустила новый продукт. Сенсация."},
                    {"headline": "Вторая новость"},
                    {"headline": "Третья новость"},
                    {"headline": "Четвёртая, не влезет"},
                ]
            ),
        )
        ctx = await _stock_context("AAPL", cache)
        assert "Apple Inc (Technology)" in ctx
        assert "designs" in ctx
        assert "Сенсация" in ctx
        assert "Четвёртая" not in ctx  # limit of 3 news items
        assert "Последние новости" in ctx

    async def test_fallback_when_extra_fails(
        self, fake_fetchers, cache, monkeypatch
    ) -> None:
        """Profile/news failure does not break the analysis — the quote remains."""
        monkeypatch.setattr(
            module, "_fetch_company_profile", _FakeFetcher(None, raises=RuntimeError)
        )
        monkeypatch.setattr(
            module, "_fetch_news", _FakeFetcher(None, raises=RuntimeError)
        )
        ctx = await _stock_context("AAPL", cache)
        assert "Цена: 250.5000" in ctx
        assert "Компания:" not in ctx


class TestCryptoContextExtra:
    async def test_market_data_and_trend(
        self, fake_fetchers, cache, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            module,
            "_fetch_market_data",
            _FakeFetcher(
                {
                    "name": "Bitcoin",
                    "rank": 1,
                    "market_cap": 1290000000000,
                    "volume": 21000000000,
                    "ath": 126080,
                    "description": "Bitcoin is first cryptocurrency. ",
                }
            ),
        )
        # 100 prices: first 100, last 130 → 7d/30d trend from the history step
        prices = [100.0 + i * 0.3 for i in range(100)]
        monkeypatch.setattr(module, "_fetch_price_history", _FakeFetcher(prices))
        ctx = await _crypto_context("BTC", cache)
        assert "Bitcoin (rank #1)" in ctx
        assert "Капитализация: $1.29T, объём за 24ч: $21.00B, ATH: $126.1K" in ctx
        assert "Тренд: 7д" in ctx and "30д" in ctx
        assert "криптовалюта" in ctx


class TestDetectContext:
    async def test_known_symbol(self, fake_fetchers, cache) -> None:
        ctx = await _detect_context("nvda", cache)
        assert ctx is not None
        assert "акция/индекс" in ctx

    async def test_unknown_ticker_falls_back_to_crypto(
        self, monkeypatch, cache
    ) -> None:
        monkeypatch.setattr(
            module, "fetch_stock", _FakeStockFetcher(None, raises=LookupError)
        )
        monkeypatch.setattr(
            module,
            "fetch_crypto",
            _FakeStockFetcher(StockQuote(symbol="USDT", price=1.0, change_percent=0.0)),
        )
        ctx = await _detect_context("USDT", cache)
        assert ctx is not None
        assert "криптовалюта" in ctx

    async def test_plain_text_not_an_asset(self, fake_fetchers, cache) -> None:
        assert await _detect_context("стоит", cache) is None
        assert await _detect_context("САМОЛЁТ", cache) is None

    async def test_network_error_propagates(
        self, fake_fetchers, cache, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            module, "fetch_stock", _FakeStockFetcher(None, raises=RuntimeError)
        )
        with pytest.raises(RuntimeError):
            await _detect_context("MSFT", cache)

    async def test_unknown_symbol_returns_none(self, monkeypatch, cache) -> None:
        monkeypatch.setattr(
            module, "fetch_stock", _FakeStockFetcher(None, raises=LookupError)
        )
        monkeypatch.setattr(
            module, "fetch_crypto", _FakeStockFetcher(None, raises=LookupError)
        )
        assert await _detect_context("ZZZZZ", cache) is None


class TestHelpers:
    def test_trend_change(self) -> None:
        prices = [100.0] * 10 + [110.0]
        assert _trend_change(prices, 7) == pytest.approx(10.0)
        assert _trend_change(prices, 30) == pytest.approx(10.0)

    def test_trend_change_short_history(self) -> None:
        assert _trend_change([100.0], 7) is None

    def test_format_money(self) -> None:
        assert _format_money(1290000000000) == "$1.29T"
        assert _format_money(21000000000) == "$21.00B"
        assert _format_money(900000000) == "$900.0M"
        assert _format_money(126080) == "$126.1K"
        assert _format_money(999) == "$999"
        assert _format_money(None) == "—"

    def test_clean_text(self) -> None:
        assert (
            _clean_text("  Много   пробелов\nи переносов ", 40)
            == "Много пробелов и переносов"
        )
        assert len(_clean_text("а" * 500, 300)) == 300
