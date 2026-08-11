"""Клиенты финансовых API: ЦБ РФ (валюты), FCS API (акции/крипта), CoinGecko (крипта).

Все исходящие запросы проходят проверку white-листа доменов (AGENTS.md, п.6).
Клиенты не логируют и не сохраняют ключи доступа.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

# White-list доменов для исходящих запросов (AGENTS.md, п.6)
ALLOWED_API_DOMAINS = (
    "www.cbr.ru",
    "api-v4.fcsapi.com",
    "api.coingecko.com",
    "finnhub.io",
    "openrouter.ai",  # и api.openrouter.ai
    "api.telegram.org",
)


class ApiRateLimitError(RuntimeError):
    """Превышен лимит запросов бесплатного API (HTTP 429)."""


# Поддерживаемые валюты ЦБ РФ (коды ISO)
CBR_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CNY", "JPY"})

BASE_HEADERS = {"User-Agent": "ai-parser-bot/0.1 (finance telegram bot)"}
HTTP_TIMEOUT_SECONDS = 10


def make_session() -> aiohttp.ClientSession:
    """HTTP-сессия с таймаутом, чтобы бот не зависал на внешних API."""
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
        headers=BASE_HEADERS,
    )


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
            if (valute.findtext("CharCode") or "").strip() != iso:
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
        if not payload.get("status", False):
            raise RuntimeError(f"FCS API: {payload.get('msg', 'неизвестная ошибка')}")
        response = payload.get("response") or []
        if not response or "active" not in response[0]:
            raise LookupError(f"FCS API не вернул данных по {symbol}")
        quote: dict[str, Any] = response[0]["active"]
        try:
            return StockQuote(
                symbol=symbol,
                price=float(quote["c"]),
                change_percent=float(quote.get("chp") or 0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LookupError(f"Некорректный ответ FCS API по {symbol}") from exc


class FinnhubClient:
    """Котировки акций через Finnhub (бесплатно: 60 запросов/мин)."""

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    async def get_quote(
        self, symbol: str, session: aiohttp.ClientSession
    ) -> StockQuote:
        """Текущая котировка акции/индекса по тикеру (AAPL, ^GSPC, SPY)."""
        if not self._api_key:
            raise RuntimeError(
                f"Finnhub API ключ не настроен (FINNHUB_API_KEY) — нельзя запросить {symbol}"
            )
        url = f"{self.BASE_URL}/quote"
        params = {"symbol": symbol, "token": self._api_key}
        _check_domain(url)
        async with session.get(url, params=params, headers=BASE_HEADERS) as resp:
            if resp.status == 429:
                raise ApiRateLimitError("Finnhub: превышен лимит запросов")
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()
        if not payload.get("c"):
            raise LookupError(f"Finnhub не знает тикер {symbol} (или превышен лимит)")
        return StockQuote(
            symbol=symbol,
            price=float(payload["c"]),
            change_percent=float(payload.get("dp") or 0),
        )

    async def get_company_profile(
        self, symbol: str, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        """Справка о компании: название, сектор, описание (может быть пустой)."""
        if not self._api_key:
            raise RuntimeError(
                f"Finnhub API ключ не настроен (FINNHUB_API_KEY) — нельзя запросить профиль {symbol}"
            )
        url = f"{self.BASE_URL}/stock/profile2"
        params = {"symbol": symbol, "token": self._api_key}
        _check_domain(url)
        async with session.get(url, params=params, headers=BASE_HEADERS) as resp:
            if resp.status == 429:
                raise ApiRateLimitError("Finnhub: превышен лимит запросов")
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()
        return payload or {}

    async def get_news(
        self, symbol: str, session: aiohttp.ClientSession, days: int = 10
    ) -> list[dict[str, Any]]:
        """Свежие новости по тикеру (free-тариф отдаёт заголовки)."""
        if not self._api_key:
            raise RuntimeError(
                f"Finnhub API ключ не настроен (FINNHUB_API_KEY) — нельзя запросить новости {symbol}"
            )
        today = datetime.now(timezone.utc).date()
        since = today - timedelta(days=days)
        url = f"{self.BASE_URL}/company-news"
        params = {
            "symbol": symbol,
            "from": since.isoformat(),
            "to": today.isoformat(),
            "token": self._api_key,
        }
        _check_domain(url)
        async with session.get(url, params=params, headers=BASE_HEADERS) as resp:
            if resp.status == 429:
                raise ApiRateLimitError("Finnhub: превышен лимит запросов")
            resp.raise_for_status()
            payload: list[dict[str, Any]] = await resp.json()
        return [n for n in payload if n.get("headline")][:10]


class CoinGeckoClient:
    """Цены криптовалют CoinGecko. Демо-ключ: 100 req/мин, 10k/мес.

    Работает и без ключа (keyless), но лимит значительно ниже и нестабилен.
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def _get(
        self, url: str, params: dict[str, str], session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        _check_domain(url)
        headers: dict[str, str] = dict(BASE_HEADERS)
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 429:
                raise ApiRateLimitError("CoinGecko: превышен лимит запросов")
            resp.raise_for_status()
            return await resp.json()

    async def get_quote(
        self, coin_id: str, session: aiohttp.ClientSession
    ) -> StockQuote:
        """Текущая цена и изменение за 24 часа (в %) в USD."""
        url = f"{self.BASE_URL}/simple/price"
        payload = await self._get(
            url,
            {
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            session,
        )
        try:
            data = payload[coin_id]
            return StockQuote(
                symbol=coin_id,
                price=float(data["usd"]),
                change_percent=float(data.get("usd_24h_change") or 0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LookupError(f"CoinGecko не знает монету {coin_id}") from exc

    async def get_market_data(
        self, coin_id: str, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        """Фундаментальные данные монеты: капитализация, объём, ранг, ATH."""
        url = f"{self.BASE_URL}/coins/{coin_id}"
        payload = await self._get(
            url,
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "false",
                "developer_data": "false",
            },
            session,
        )
        if "market_data" not in payload:
            raise LookupError(f"CoinGecko не знает монету {coin_id}")
        md = payload.get("market_data", {}) or {}
        return {
            "name": payload.get("name"),
            "rank": payload.get("market_cap_rank"),
            "market_cap": (md.get("market_cap") or {}).get("usd"),
            "volume": (md.get("total_volume") or {}).get("usd"),
            "ath": (md.get("ath") or {}).get("usd"),
            "ath_date": (md.get("ath_date") or {}).get("usd"),
            "description": (payload.get("description") or {}).get("en", ""),
        }

    async def get_price_history(
        self, coin_id: str, session: aiohttp.ClientSession, days: int = 30
    ) -> list[float]:
        """История цен (US-доллары) за N дней: только значения."""
        url = f"{self.BASE_URL}/coins/{coin_id}/market_chart"
        payload = await self._get(
            url, {"vs_currency": "usd", "days": str(days)}, session
        )
        prices = payload.get("prices") or []
        try:
            return [float(p[1]) for p in prices]
        except (TypeError, ValueError) as exc:
            raise LookupError(
                f"CoinGecko вернул некорректную историю для {coin_id}"
            ) from exc
