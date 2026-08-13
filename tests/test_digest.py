"""Тесты дайджеста: чистые функции без БД и сети."""

from src.services.digest import _line
from src.services.financial_api import FxQuote, StockQuote


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
