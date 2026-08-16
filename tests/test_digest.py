"""Digest tests: pure functions without DB and network."""

import pytest

from src.services.digest import _line
from src.services.financial_api import FxQuote, StockQuote


@pytest.fixture(autouse=True)
def _use_ru():
    """These tests check Russian text — set the language explicitly."""
    from src.i18n import set_lang

    set_lang("ru")


class TestLine:
    def test_fx(self) -> None:
        quote = FxQuote(code="USD", name="Доллар", value=88.5, nominal=1)
        assert _line("fx", "USD", quote) == "USD — 88.50 ₽"

    def test_fx_nominal(self) -> None:
        quote = FxQuote(code="JPY", name="Иена", value=0.5234, nominal=100)
        assert _line("fx", "JPY", quote) == "JPY — 0.5234 ₽"

    def test_stock_positive(self) -> None:
        quote = StockQuote(symbol="AAPL", price=302.25, change_percent=0.5)
        assert _line("stock", "AAPL", quote) == "AAPL — $302.25 (+0.50%)"

    def test_crypto_negative(self) -> None:
        quote = StockQuote(symbol="BTC", price=63588.5, change_percent=-1.58)
        assert _line("crypto", "BTC", quote) == "BTC — $63,588.50 (-1.58%)"


class TestDigestCategories:
    def test_available_has_world_ru_categories(self) -> None:
        from src.services.digest import DIGEST_AVAILABLE, DIGEST_CATEGORIES

        assert "stock_world" in DIGEST_CATEGORIES
        assert "stock_ru" in DIGEST_CATEGORIES
        assert "index" in DIGEST_CATEGORIES
        assert len(DIGEST_AVAILABLE["stock_world"]) == 30
        assert len(DIGEST_AVAILABLE["stock_ru"]) == 46

    def test_page_callback_unpacking(self) -> None:
        """Regression: dg:page:stock_ru:1 has 4 parts — must unpack to 4."""
        data = "dg:page:stock_ru:1"
        parts = data.split(":")
        assert len(parts) == 4
        _, _, asset_type, raw_page = parts
        assert asset_type == "stock_ru"
        assert int(raw_page) == 1

    def test_toggle_callback_unpacking(self) -> None:
        data = "dg:toggle:stock_world:AAPL"
        _, _, asset_type, symbol = data.split(":", 3)
        assert asset_type == "stock_world"
        assert symbol == "AAPL"
