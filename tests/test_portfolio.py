"""Portfolio and alert tests: asset type detection, argument parsing.

No DB or network — pure functions only.
"""

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.handlers.portfolio import (
    _alert_line,
    _cache_key,
    _fmt_qty,
    _mark_added,
    _pf_menu_kb,
    _quote_text,
    _short_line,
    _trend_change,
    parse_alert_args,
    resolve_asset_type,
)
from src.services.financial_api import FxQuote, StockQuote


@pytest.fixture(autouse=True)
def _use_ru():
    """These tests check Russian text — set the language explicitly."""
    from src.i18n import set_lang

    set_lang("ru")


class TestResolveAssetType:
    def test_fx_currency(self) -> None:
        assert resolve_asset_type("USD") == "fx"
        assert resolve_asset_type("AED") == "fx"
        assert resolve_asset_type("JPY") == "fx"

    def test_crypto_coin(self) -> None:
        assert resolve_asset_type("BTC") == "crypto"
        assert resolve_asset_type("ETH") == "crypto"
        assert resolve_asset_type("SOL") == "crypto"

    def test_stock_ticker(self) -> None:
        assert resolve_asset_type("AAPL") == "stock"
        assert resolve_asset_type("NVDA") == "stock"
        assert resolve_asset_type("SPX") == "stock"

    def test_unknown(self) -> None:
        assert resolve_asset_type("") is None
        assert resolve_asset_type("АБВ") is None
        assert resolve_asset_type("LONG-NOT-TICKER-NAME") is None


class TestParseAlertArgs:
    def test_default_above(self) -> None:
        assert parse_alert_args("BTC 70000") == ("BTC", "above", 70000.0)

    def test_below(self) -> None:
        assert parse_alert_args("ETH below 3500.5") == ("ETH", "below", 3500.5)

    def test_above_explicit(self) -> None:
        assert parse_alert_args("AAPL above 200") == ("AAPL", "above", 200.0)

    def test_case_insensitive(self) -> None:
        assert parse_alert_args(" btc 70000 ") == ("BTC", "above", 70000.0)

    def test_comma_decimal(self) -> None:
        assert parse_alert_args("BTC 70000,5") == ("BTC", "above", 70000.5)

    def test_invalid_price(self) -> None:
        assert parse_alert_args("BTC abc") is None

    def test_missing_price(self) -> None:
        assert parse_alert_args("BTC") is None

    def test_unknown_symbol(self) -> None:
        assert parse_alert_args("TOOLONGTICKER1234 70000") is None
        assert parse_alert_args("БТЦ 70000") is None

    def test_negative_price(self) -> None:
        assert parse_alert_args("BTC -5") is None

    def test_zero_price(self) -> None:
        assert parse_alert_args("BTC 0") is None

    def test_bad_direction(self) -> None:
        assert parse_alert_args("BTC sideways 70000") is None

    def test_empty(self) -> None:
        assert parse_alert_args("") is None


class TestPortfolioKb:
    def test_menu_counts(self) -> None:
        kb = _pf_menu_kb({"fx": 2, "stock": 5, "crypto": 3})
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "💱 Валюты (2)" in texts
        assert "📈 Акции (5)" in texts
        assert "🪙 Крипта (3)" in texts

    def test_menu_hides_empty_categories(self) -> None:
        kb = _pf_menu_kb({"fx": 0, "stock": 5, "crypto": 0})
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "📈 Акции (5)" in texts
        assert "Валюты" not in texts and "Крипта" not in texts

    def test_menu_callbacks_nonempty(self) -> None:
        kb = _pf_menu_kb({"fx": 2, "stock": 5, "crypto": 3})
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert data == [
            "pf:cat:fx",
            "pf:cat:stock",
            "pf:cat:crypto",
            "pf:add_menu",
            "pf:remove",
            "pf:alerts",
        ]

    def test_menu_empty_portfolio(self) -> None:
        kb = _pf_menu_kb({"fx": 0, "stock": 0, "crypto": 0})
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert data == ["pf:add_menu", "pf:alerts"]

    def test_mark_added_replaces_button(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ В портфель", callback_data="pf:add:AAPL"
                    ),
                    InlineKeyboardButton(text="↩️ Меню", callback_data="submenu:stock"),
                ]
            ]
        )
        new = _mark_added(markup, "AAPL")
        row = new.inline_keyboard[0]
        assert row[0].text == "✅ В портфеле"
        assert row[0].callback_data == "pf:added"
        assert row[1].callback_data == "submenu:stock"

    def test_mark_added_keeps_other_symbol(self) -> None:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ В портфель", callback_data="pf:add:AAPL"
                    )
                ]
            ]
        )
        new = _mark_added(markup, "NVDA")
        assert new.inline_keyboard[0][0].callback_data == "pf:add:AAPL"


class TestShortLine:
    def test_fx(self) -> None:
        quote = FxQuote(code="USD", name="Доллар", value=88.5, nominal=1)
        assert _short_line("fx", "USD", quote) == "USD — 88.50 ₽"

    def test_fx_nominal(self) -> None:
        quote = FxQuote(code="JPY", name="Иена", value=0.5234, nominal=100)
        assert _short_line("fx", "JPY", quote) == "JPY — 0.5234 ₽"

    def test_stock(self) -> None:
        quote = StockQuote(symbol="AAPL", price=100.5, change_percent=1.23)
        assert _short_line("stock", "AAPL", quote) == "AAPL — $100.50 (+1.23%)"

    def test_stock_negative(self) -> None:
        quote = StockQuote(symbol="NVDA", price=140.1, change_percent=-2.5)
        assert _short_line("stock", "NVDA", quote) == "NVDA — $140.10 (-2.50%)"

    def test_with_quantity_int(self) -> None:
        quote = StockQuote(symbol="AAPL", price=100.5, change_percent=1.23)
        assert _short_line("stock", "AAPL", quote, 5.0) == "AAPL — $100.50 (+1.23%) ×5"

    def test_with_quantity_float(self) -> None:
        quote = StockQuote(symbol="BTC", price=100.5, change_percent=1.23)
        assert _short_line("crypto", "BTC", quote, 0.5) == "BTC — $100.50 (+1.23%) ×0.5"


class TestQuoteTextQuantity:
    def test_stock_cost(self) -> None:
        quote = StockQuote(symbol="AAPL", price=302.25, change_percent=0.5)
        text = _quote_text("stock", "AAPL", quote, 5)
        assert "Количество: 5" in text
        assert "Стоимость: <b>1,511.25 $</b>" in text

    def test_no_quantity(self) -> None:
        quote = StockQuote(symbol="AAPL", price=302.25, change_percent=0.5)
        text = _quote_text("stock", "AAPL", quote)
        assert "Количество" not in text


class TestFmtQty:
    def test_integer(self) -> None:
        assert _fmt_qty(5.0) == "5"

    def test_float(self) -> None:
        assert _fmt_qty(0.5) == "0.5"


class TestTrendChange:
    def test_up(self) -> None:
        prices = [100.0, 105.0]
        assert _trend_change(prices, 7) == 5.0

    def test_down(self) -> None:
        prices = [100.0, 90.0]
        assert _trend_change(prices, 7) == -10.0

    def test_too_short(self) -> None:
        assert _trend_change([100.0], 7) is None

    def test_empty(self) -> None:
        assert _trend_change([], 7) is None


class TestQuoteTextTrend:
    def test_trend_appended(self) -> None:
        quote = StockQuote(symbol="BTC", price=63588.5, change_percent=2.0)
        text = _quote_text("crypto", "BTC", quote, trend="Тренд: 7д +5.00%, 30д -3.00%")
        assert "Тренд: 7д +5.00%" in text

    def test_no_trend_by_default(self) -> None:
        quote = StockQuote(symbol="BTC", price=63588.5, change_percent=2.0)
        text = _quote_text("crypto", "BTC", quote)
        assert "Тренд" not in text


class TestCacheKey:
    def test_fx(self) -> None:
        assert _cache_key("fx", "USD") == "fx:USD"

    def test_crypto(self) -> None:
        assert _cache_key("crypto", "BTC") == "crypto:BTC"

    def test_stock_resolves_index(self) -> None:
        assert _cache_key("stock", "SPX") == "stock:SPY"


class TestAlertLine:
    def test_above(self) -> None:
        from src.database.models import Alert

        alert = Alert(
            id=1,
            telegram_id=1,
            asset_type="crypto",
            symbol="BTC",
            target_price=70000,
            direction="above",
        )
        text = _alert_line(alert)
        assert "BTC" in text and "выше" in text and "$70,000.00" in text

    def test_below(self) -> None:
        from src.database.models import Alert

        alert = Alert(
            id=2,
            telegram_id=1,
            asset_type="stock",
            symbol="AAPL",
            target_price=200,
            direction="below",
        )
        text = _alert_line(alert)
        assert "AAPL" in text and "ниже" in text
