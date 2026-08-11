"""Тесты хендлеров: валидация ввода, алиасы индексов, форматирование ответов."""

import pytest

from src.handlers.crypto import COIN_RE, fetch_crypto, format_crypto
from src.handlers.rate import format_fx
from src.handlers.stock import INDEX_ALIASES, TICKER_RE, format_stock
from src.services.financial_api import FxQuote, StockQuote


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
    assert INDEX_ALIASES["SPX"] == "^GSPC"
    assert INDEX_ALIASES["DJI"] == "^DJI"
    unknown = "AAPL"
    assert INDEX_ALIASES.get(unknown, unknown) == "AAPL"


def test_format_fx() -> None:
    text = format_fx(FxQuote(code="USD", name="Доллар США", value=82.6145, nominal=1))
    assert "Доллар США" in text
    assert "82.61" in text


def test_format_fx_with_nominal() -> None:
    text = format_fx(
        FxQuote(code="JPY", name="Японская иена", value=0.5234, nominal=100)
    )
    assert "за 100" in text


def test_format_stock_sign() -> None:
    up = format_stock(StockQuote(symbol="AAPL", price=100.5, change_percent=1.23))
    assert "+1.23" in up
    down = format_stock(StockQuote(symbol="AAPL", price=100.5, change_percent=-2.5))
    assert "-2.50" in down
    assert "$100.50" in up


def test_format_crypto() -> None:
    text = format_crypto(
        "BTC", StockQuote(symbol="bitcoin", price=63942.0, change_percent=-1.84)
    )
    assert "<b>BTC</b>" in text
    assert "-1.84" in text


async def test_fetch_crypto_uses_alias(monkeypatch) -> None:
    """SOL запрашивает CoinGecko id solana (не sol)."""

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


class TestDisclaimer:
    def test_start_disclaimer_has_key_phrases(self) -> None:
        from src.handlers.start import DISCLAIMER_TEXT

        assert "не является финансовой консультацией" in DISCLAIMER_TEXT
        assert "ЦБ РФ" in DISCLAIMER_TEXT
        assert "Ответственность" in DISCLAIMER_TEXT

    def test_start_disclaimer_is_html_safe(self) -> None:
        from src.handlers.start import DISCLAIMER_TEXT

        assert "**" not in DISCLAIMER_TEXT
        assert "<b>" in DISCLAIMER_TEXT

    def test_ai_disclaimer_appended(self) -> None:
        from src.handlers.analyze import AI_DISCLAIMER

        assert "не инвестиционная рекомендация" in AI_DISCLAIMER
        assert AI_DISCLAIMER.startswith("\n\n")

    def test_help_mentions_disclaimer(self) -> None:
        from src.handlers.help import HELP_TEXT

        assert "не является инвестиционной рекомендацией" in HELP_TEXT
