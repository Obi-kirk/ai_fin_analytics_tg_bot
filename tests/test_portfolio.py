"""Тесты портфеля и алертов: определение типа актива, разбор аргументов.

Без БД и сети — только чистые функции.
"""

from src.handlers.portfolio import parse_alert_args, resolve_asset_type


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
