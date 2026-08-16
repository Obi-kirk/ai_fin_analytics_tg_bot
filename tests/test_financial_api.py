"""Financial API client tests using fixtures (no real network).

HTTP requests are mocked with fake responses: CBR XML, Finnhub and CoinGecko JSON.
"""

from types import SimpleNamespace
from typing import Self

import pytest

from src.services.financial_api import (
    ALLOWED_API_DOMAINS,
    CBRClient,
    CoinGeckoClient,
    FinnhubClient,
    MoexClient,
    StockQuote,
    _check_domain,
)

CBR_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="11.08.2026" name="Foreign Currency Market">
  <Valute ID="R01235">
    <NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal>
    <Name>Доллар США</Name><Value>82,6145</Value><VunitRate>82,6145</VunitRate>
  </Valute>
  <Valute ID="R01820">
    <NumCode>392</NumCode><CharCode>JPY</CharCode><Nominal>100</Nominal>
    <Name>Японская иена</Name><Value>52,3456</Value><VunitRate>0,523456</VunitRate>
  </Valute>
</ValCurs>
"""

FINNHUB_JSON = '{"c": 308.26, "dp": -1.6181, "pc": 313.33}'
COINGECKO_JSON = '{"bitcoin": {"usd": 63942.0, "usd_24h_change": -1.84}}'

# MOEX: CHANGE is the absolute RUB change; percent must be derived.
MOEX_JSON = (
    '{"marketdata": {"columns": ["SECID", "LAST", "CHANGE"],'
    ' "data": [["SBER", 273.4, -4.11]]},'
    ' "securities": {"columns": ["SECID", "SHORTNAME"],'
    ' "data": [["SBER", "Сбербанк"]]}}'
)


def make_session(status: int = 200, body: str = "") -> SimpleNamespace:
    """Creates a fake aiohttp session returning the given response."""

    class FakeResponse:
        def __init__(self) -> None:
            self.status = status

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")

        async def text(self, encoding: str = "utf-8") -> str:
            return body

        async def json(self) -> object:
            import json as _json

            return _json.loads(body)

    def _get(*args, **kwargs) -> FakeResponse:
        return FakeResponse()

    return SimpleNamespace(get=_get)


async def test_cbr_usd_parse() -> None:
    quote = await CBRClient().get_quote("USD", make_session(body=CBR_XML))
    assert quote.code == "USD"
    assert quote.name == "Доллар США"
    assert quote.value == pytest.approx(82.6145)
    assert quote.nominal == 1


async def test_cbr_jpy_nominal() -> None:
    quote = await CBRClient().get_quote("JPY", make_session(body=CBR_XML))
    assert quote.nominal == 100
    assert quote.value == pytest.approx(0.523456)


async def test_cbr_unknown_currency() -> None:
    with pytest.raises(ValueError, match="not supported"):
        await CBRClient().get_quote("XXX", make_session(body=CBR_XML))


async def test_finnhub_quote() -> None:
    quote: StockQuote = await FinnhubClient("secret").get_quote(
        "AAPL", make_session(body=FINNHUB_JSON)
    )
    assert quote.price == pytest.approx(308.26)
    assert quote.change_percent == pytest.approx(-1.6181)


async def test_moex_quote_with_name() -> None:
    quote: StockQuote = await MoexClient.get_quote("SBER", make_session(body=MOEX_JSON))
    assert quote.price == pytest.approx(273.4)
    assert quote.name == "Сбербанк"
    # CHANGE is RUB (-4.11) on previous 277.51 -> -1.48%
    assert quote.change_percent == pytest.approx(-1.481, abs=0.01)


async def test_moex_unknown_ticker() -> None:
    with pytest.raises(LookupError):
        await MoexClient.get_quote(
            "XXX", make_session(body='{"marketdata": {"data": []}}')
        )


async def test_finnhub_requires_key() -> None:
    with pytest.raises(RuntimeError, match="FINNHUB_API_KEY"):
        await FinnhubClient(None).get_quote("AAPL", make_session(body=FINNHUB_JSON))


async def test_coingecko_quote() -> None:
    quote: StockQuote = await CoinGeckoClient("key").get_quote(
        "bitcoin", make_session(body=COINGECKO_JSON)
    )
    assert quote.price == pytest.approx(63942.0)
    assert quote.change_percent == pytest.approx(-1.84)


async def test_coingecko_unknown_coin() -> None:
    with pytest.raises(LookupError):
        await CoinGeckoClient("key").get_quote(
            "нет-такой", make_session(body='{"нет-такой": {}}')
        )


def test_whitelist_allows_known_domains() -> None:
    for domain in ALLOWED_API_DOMAINS:
        _check_domain(f"https://{domain}/some/path")


def test_whitelist_blocks_foreign_domain() -> None:
    with pytest.raises(ValueError, match="white-list"):
        _check_domain("https://evil.example.com/api")
