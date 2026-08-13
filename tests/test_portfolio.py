"""Тесты портфеля и алертов: определение типа актива, разбор аргументов.

Без БД и сети — только чистые функции.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.handlers.portfolio import (
    _alert_line,
    _cache_key,
    _mark_added,
    _pf_menu_kb,
    _short_line,
    parse_alert_args,
    resolve_asset_type,
)
from src.services.financial_api import FxQuote, StockQuote


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

    def test_menu_callbacks(self) -> None:
        kb = _pf_menu_kb({"fx": 0, "stock": 0, "crypto": 0})
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert data == [
            "pf:cat:fx",
            "pf:cat:stock",
            "pf:cat:crypto",
            "pf:remove",
            "pf:alerts",
        ]

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
        assert _short_line("fx", quote) == "USD — 88.50 ₽"

    def test_stock(self) -> None:
        quote = StockQuote(symbol="AAPL", price=100.5, change_percent=1.23)
        assert _short_line("stock", quote) == "AAPL — $100.50 (+1.23%)"

    def test_stock_negative(self) -> None:
        quote = StockQuote(symbol="NVDA", price=140.1, change_percent=-2.5)
        assert _short_line("stock", quote) == "NVDA — $140.10 (-2.50%)"


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
