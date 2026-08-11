"""Клиенты финансовых API: ЦБ РФ (валюты), FCS API (акции/крипта), CoinGecko (крипта).

Все исходящие запросы проходят проверку white-листа доменов (AGENTS.md, п.6).
Клиенты не логируют и не сохраняют ключи доступа.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

# White-list доменов для исходящих запросов (AGENTS.md, п.6)
ALLOWED_API_DOMAINS = (
    "www.cbr.ru",
    "api-v4.fcsapi.com",
    "api.coingecko.com",
    "api.openrouter.ai",
    "api.telegram.org",
)

# Коды валют ЦБ РФ -> ISO (используется в команде /rate)
CBR_CURRENCIES = {
    "USD": "R01235",
    "EUR": "R01239",
    "GBP": "R01035",
    "CNY": "R01375",
    "JPY": "R01820",
}

BASE_HEADERS = {"User-Agent": "ai-parser-bot/0.1 (finance telegram bot)"}


def _check_domain(url: str) -> None:
    """Гарантирует, что URL принадлежит разрешённому домену."""
    allowed = any(domain in url for domain in ALLOWED_API_DOMAINS)
    if not allowed:
        raise ValueError(f"URL не входит в white-list: {url}")


@dataclass
class FxQuote:
    """Курс валюты от ЦБ РФ."""

    code: str  # ISO-код, например USD
    name: str  # название валюты
    value: float  # курс за 1 единицу (руб. за 1 USD)
    nominal: int  # номинал (для JPY/CNY обычно 100)


@dataclass
class StockQuote:
    """Котировка акции/крипты от FCS API."""

    symbol: str
    price: float
    change_percent: float


class CBRClient:
    """Курсы валют ЦБ РФ (бесплатно, без ключа). HTML/XML daily."""

    BASE_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

    async def get_quote(self, code: str, session: aiohttp.ClientSession) -> FxQuote:
        """Возвращает курс валюты по ISO-коду (USD, EUR, ...)."""
        iso = code.upper()
        if iso not in CBR_CURRENCIES:
            raise ValueError(f"Валюта {iso} не поддерживается")
        _check_domain(self.BASE_URL)
        async with session.get(self.BASE_URL) as resp:
            resp.raise_for_status()
            xml = await resp.text(encoding="windows-1251")
        root = ET.fromstring(xml)
        for valute in root.findall("./Valute"):
            if valute.findtext("ID") != CBR_CURRENCIES[iso]:
                continue
            value_text = (valute.findtext("Value") or "0").replace(",", ".")
            nominal = int(valute.findtext("Nominal") or "1")
            return FxQuote(
                code=iso,
                name=valute.findtext("Name") or iso,
                value=float(value_text) / nominal,
                nominal=nominal,
            )
        raise LookupError(f"Валюта {iso} не найдена в ответе ЦБ РФ")


class FCSClient:
    """FCS API: акции и крипта. Нужен ключ (бесплатный план 500 req/мес)."""

    BASE_URL = "https://api-v4.fcsapi.com"

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    async def get_stock_quote(
        self, symbol: str, session: aiohttp.ClientSession
    ) -> StockQuote:
        """Текущая котировка акции, символ вида 'NASDAQ:AAPL'."""
        return await self._quote("stock", symbol, session)

    async def get_crypto_quote(
        self, symbol: str, session: aiohttp.ClientSession
    ) -> StockQuote:
        """Текущая цена криптовалюты, символ вида 'BTCUSD' (тип coin)."""
        return await self._quote("crypto", symbol, session)

    async def _quote(
        self, market: str, symbol: str, session: aiohttp.ClientSession
    ) -> StockQuote:
        if not self._api_key:
            raise RuntimeError(
                f"FCS API ключ не настроен (переменная FCS_API_KEY) — нельзя запросить {symbol}"
            )
        url = f"{self.BASE_URL}/{market}/latest"
        params = {"symbol": symbol, "access_key": self._api_key}
        if market == "crypto":
            params["type"] = "coin"
        _check_domain(url)
        async with session.get(url, params=params, headers=BASE_HEADERS) as resp:
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()
        response = payload.get("response") or payload
        if not response or not response[0]:
            raise LookupError(f"FCS API не вернул данных по {symbol}: {payload}")
        quote = response[0]
        return StockQuote(
            symbol=symbol,
            price=float(quote["c"]),
            change_percent=float(quote.get("chp") or 0),
        )


class CoinGeckoClient:
    """Цена криптовалюты (бесплатно, без ключа). Резерв при недоступном FCS."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    async def get_price(self, coin_id: str, session: aiohttp.ClientSession) -> float:
        """Цена в USD по id монеты (bitcoin, ethereum, solana)."""
        url = f"{self.BASE_URL}/simple/price"
        params = {"ids": coin_id, "vs_currencies": "usd"}
        _check_domain(url)
        async with session.get(url, params=params, headers=BASE_HEADERS) as resp:
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()
        try:
            return float(payload[coin_id]["usd"])
        except (KeyError, TypeError) as exc:
            raise LookupError(f"CoinGecko не знает монету {coin_id}") from exc
