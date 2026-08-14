"""Handler tests: input validation, index aliases, response formatting."""

import pytest

from src.handlers.crypto import (
    COIN_RE,
    fetch_crypto,
    format_crypto,
    format_top,
    format_trending,
)
from src.handlers.rate import (
    CONVERT_CURRENCIES,
    CONVERT_OPTIONS,
    _format_money,
    convert_amount,
    convert_kb,
    format_convert,
    format_fx,
    parse_convert_args,
)
from src.handlers.stock import (
    INDEX_ALIASES,
    TICKER_RE,
    _format_news_date,
    format_news,
    format_stock,
    news_kb,
)
from src.services.financial_api import CBR_CURRENCIES, FxQuote, StockQuote


@pytest.mark.parametrize(
    "ticker",
    ["AAPL", "BRK.B", "^GSPC", "NVDA", "JPM", "SP500", "A"],
)
def test_ticker_re_valid(ticker: str) -> None:
    assert TICKER_RE.match(ticker)


@pytest.mark.parametrize(
    "ticker",
    ["aap l", "AA-BB-CC-DD-EE-FF", "…))", "AAPL;", "TOM&JERRY", "ТЕСТ"],
)
def test_ticker_re_invalid(ticker: str) -> None:
    assert not TICKER_RE.match(ticker)


@pytest.mark.parametrize("coin", ["BTC", "ETH", "SOLANA", "XRP", "USDT"])
def test_coin_re_valid(coin: str) -> None:
    assert COIN_RE.match(coin)


@pytest.mark.parametrize("coin", ["btc-eth", "B", "..BTC", "БИТКОИН"])
def test_coin_re_invalid(coin: str) -> None:
    assert not COIN_RE.match(coin)


def test_index_aliases() -> None:
    assert INDEX_ALIASES["SPX"] == "SPY"
    assert INDEX_ALIASES["DJI"] == "DIA"
    assert INDEX_ALIASES["NASDAQ"] == "QQQ"
    assert INDEX_ALIASES["VIX"] == "VIXY"
    unknown = "AAPL"
    assert INDEX_ALIASES.get(unknown, unknown) == "AAPL"


@pytest.fixture(autouse=True)
def _use_ru():
    """These tests check Russian text — set the language explicitly."""
    from src.i18n import set_lang

    set_lang("ru")


def test_format_fx() -> None:
    text = format_fx(FxQuote(code="USD", name="Доллар США", value=82.6145, nominal=1))
    assert "Доллар США" in text
    assert "82.61" in text


def test_format_fx_with_nominal() -> None:
    text = format_fx(
        FxQuote(code="JPY", name="Японская иена", value=0.5234, nominal=100)
    )
    assert "0.5234 ₽" in text
    assert "за 100" not in text


class TestConvert:
    def test_parse_valid(self) -> None:
        assert parse_convert_args("100 USD RUB") == (100.0, "USD", "RUB")
        assert parse_convert_args(" 10.50 eur usd ") == (10.5, "EUR", "USD")
        assert parse_convert_args("1000,25 USD RUB") == (1000.25, "USD", "RUB")

    def test_parse_invalid(self) -> None:
        assert parse_convert_args("100 USD") is None
        assert parse_convert_args("abc USD RUB") is None
        assert parse_convert_args("100 USD R") is None
        assert parse_convert_args("") is None

    def test_convert_usd_to_rub(self) -> None:
        assert convert_amount(100, 82.6, 1.0) == 8260.0

    def test_convert_rub_to_usd(self) -> None:
        assert convert_amount(8260, 1.0, 82.6) == pytest.approx(100.0)

    def test_convert_cross(self) -> None:
        # 100 EUR -> USD: 100 * 98.0 / 82.6
        assert convert_amount(100, 98.0, 82.6) == pytest.approx(118.64, abs=0.01)

    def test_convert_zero_target_raises(self) -> None:
        with pytest.raises(ValueError):
            convert_amount(10, 1.0, 0.0)

    def test_format_money(self) -> None:
        assert _format_money(8260) == "8,260"
        assert _format_money(8260.5) == "8,260"
        assert _format_money(118.64) == "118.64"
        assert _format_money(0.5234) == "0.5234"
        assert _format_money(0.00156857) == "0.001569"
        assert _format_money(0) == "0"

    def test_format_convert(self) -> None:
        text = format_convert(100, "USD", "RUB", 82.6145, 1.0)
        assert "100 USD" in text
        assert "8,261 RUB" in text

    def test_fx_short_line(self) -> None:
        from src.handlers.rate import _fx_short_line

        q = FxQuote(code="USD", name="Доллар", value=88.5, nominal=1)
        assert _fx_short_line(q) == "USD — 88.50 ₽"
        q100 = FxQuote(code="JPY", name="Иена", value=0.5234, nominal=100)
        assert _fx_short_line(q100) == "JPY — 0.5234 ₽"

    def test_convert_kb_swap(self) -> None:
        kb = convert_kb(100.0, "USD", "RUB")
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "conv:swap|100.0|RUB|USD" in data  # "Swap" exchanges the pair
        assert "conv:start" in data  # "Again" starts a new dialog

    def test_convert_currencies_include_rub(self) -> None:
        assert "RUB" in CONVERT_CURRENCIES
        assert {"USD", "EUR", "GBP", "CNY", "JPY"} <= set(CONVERT_CURRENCIES)

    def test_convert_options_include_crypto(self) -> None:
        assert {"BTC", "ETH", "SOL", "XRP"} <= set(CONVERT_OPTIONS)
        assert "RUB" in CONVERT_OPTIONS

    def test_convert_currencies_order(self) -> None:
        # button order: RUB first, then by popularity for the user
        assert CONVERT_CURRENCIES == (
            "RUB",
            "USD",
            "EUR",
            "CNY",
            "AED",
            "VND",
            "THB",
            "TRY",
            "GBP",
            "JPY",
        )

    def test_rate_currencies_extended(self) -> None:
        # AED/THB/VND must be available (were in the menu but were rejected)
        assert {
            "AED",
            "TRY",
            "VND",
            "THB",
            "CHF",
            "KZT",
            "CZK",
        } <= CBR_CURRENCIES

    def test_news_kb(self) -> None:
        kb = news_kb("AAPL")
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "stock:AAPL" in data  # back to the quote
        assert "submenu:stock" in data


def test_format_stock_sign() -> None:
    up = format_stock(StockQuote(symbol="AAPL", price=100.5, change_percent=1.23))
    assert "+1.23" in up
    down = format_stock(StockQuote(symbol="AAPL", price=100.5, change_percent=-2.5))
    assert "-2.50" in down
    assert "$100.50" in up


def test_format_stock_display() -> None:
    # the index is displayed with the original alias (VIX), not the ETF ticker (VIXY)
    text = format_stock(
        StockQuote(symbol="VIXY", price=18.77, change_percent=-2.75),
        display="VIX",
    )
    assert "<b>VIX</b>" in text
    assert "VIXY" not in text


def test_format_crypto() -> None:
    text = format_crypto(
        "BTC", StockQuote(symbol="bitcoin", price=63942.0, change_percent=-1.84)
    )
    assert "<b>BTC</b>" in text
    assert "-1.84" in text


def test_build_chart_png() -> None:
    from src.handlers.crypto import build_chart_png

    png = build_chart_png("BTC", [100.0, 105.0, 103.0, 110.0])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature
    assert len(png) > 1000


async def test_fetch_crypto_uses_alias(monkeypatch) -> None:
    """SOL requests CoinGecko id solana (not sol)."""

    class FakeGecko:
        def __init__(self, api_key: str | None = None) -> None:
            self._api_key = api_key

        async def get_quote(self, coin_id: str, session: object) -> StockQuote:
            assert coin_id == "solana"
            return StockQuote(symbol=coin_id, price=76.25, change_percent=-1.36)

    import src.handlers.crypto as module

    monkeypatch.setattr(module, "CoinGeckoClient", FakeGecko)
    quote = await fetch_crypto("SOL")
    assert quote.price == pytest.approx(76.25)


class TestTrending:
    def test_format_trending(self) -> None:
        coins = [
            {"name": "Bitcoin", "symbol": "BTC", "rank": 1, "price_btc": 1.0},
            {"name": "Ethereum", "symbol": "ETH", "rank": 2, "price_btc": 0.05},
        ]
        text = format_trending(coins)
        assert "Тренды CoinGecko" in text
        assert "Bitcoin <b>(BTC)</b>" in text
        assert "ранг #1" in text

    def test_format_trending_limit_10(self) -> None:
        coins = [
            {"name": f"Coin{i}", "symbol": f"C{i}", "rank": i, "price_btc": 1.0}
            for i in range(1, 15)
        ]
        text = format_trending(coins)
        assert "Coin1" in text
        assert "Coin14" not in text  # only the top 10 is shown


class TestTop:
    def test_format_top(self) -> None:
        coins = [
            {
                "symbol": "BTC",
                "name": "Bitcoin",
                "rank": 1,
                "price": 104000.5,
                "market_cap": 2_050_000_000_000,
                "change_percent": 2.5,
            },
            {
                "symbol": "ETH",
                "name": "Ethereum",
                "rank": 2,
                "price": 3500.0,
                "market_cap": 420_000_000_000,
                "change_percent": -1.2,
            },
        ]
        text = format_top(coins)
        assert "Топ криптовалют по капитализации" in text
        assert "Bitcoin <b>(BTC)</b>" in text
        assert "$104,000.50" in text
        assert "+2.50%" in text
        assert "-1.20%" in text
        assert "$2.05T" in text
        assert "$420.00B" in text

    def test_format_top_limit_10(self) -> None:
        coins = [
            {
                "symbol": f"C{i}",
                "name": f"Coin{i}",
                "rank": i,
                "price": 1.0,
                "market_cap": 1e9,
                "change_percent": None,
            }
            for i in range(1, 15)
        ]
        text = format_top(coins)
        assert "Coin1" in text
        assert "Coin14" not in text

    def test_format_top_no_change(self) -> None:
        coins = [
            {
                "symbol": "BTC",
                "name": "Bitcoin",
                "rank": 1,
                "price": 100.0,
                "market_cap": None,
                "change_percent": None,
            }
        ]
        text = format_top(coins)
        assert "Bitcoin" in text
        assert "%" not in text.split("Капитализация:")[0].rsplit("\n", 1)[1]
        assert "Капитализация: —" in text


class TestNews:
    def test_format_news(self) -> None:
        news = [
            {
                "headline": "Apple выпустила новый продукт",
                "url": "https://example.com/apple",
                "datetime": 1780000000,
            },
            {"headline": "Без ссылки", "url": "", "datetime": None},
        ]
        text = format_news("AAPL", news)
        assert "Новости AAPL" in text
        assert "Apple выпустила новый продукт" in text
        assert "example.com" in text

    def test_format_news_limit(self) -> None:
        news = [
            {"headline": f"Новость {i}", "url": f"https://e.com/{i}", "datetime": None}
            for i in range(8)
        ]
        text = format_news("AAPL", news, limit=3)
        assert "Новость 1" in text
        assert "Новость 5" not in text

    def test_format_news_empty(self) -> None:
        text = format_news("AAPL", [])
        assert "Новостей за этот период нет" in text

    def test_format_news_date(self) -> None:
        assert _format_news_date(None) == "—"
        assert _format_news_date(1780000000) == "28.05"


class TestDisclaimer:
    def test_start_disclaimer_has_key_phrases(self) -> None:
        from src.i18n import t

        text = t("start.disclaimer")

        assert "не является финансовой консультацией" in text
        assert "ЦБ РФ" in text
        assert "Ответственность" in text

    def test_start_disclaimer_is_html_safe(self) -> None:
        from src.i18n import t

        text = t("start.disclaimer")

        assert "**" not in text
        assert "<b>" in text

    def test_ai_disclaimer_appended(self) -> None:
        from src.i18n import t

        text = t("analyze.disclaimer")

        assert "не инвестиционная рекомендация" in text
        assert text.startswith("\n\n")

    def test_help_mentions_disclaimer(self) -> None:
        from src.i18n import t

        assert "не является инвестиционной рекомендацией" in t("start.help_text")
