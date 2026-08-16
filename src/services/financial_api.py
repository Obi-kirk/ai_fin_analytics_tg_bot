"""Clients for financial APIs: CBR (currencies), Finnhub (stocks), CoinGecko (crypto).

All outgoing requests are checked against the domain white-list (AGENTS.md, item 6).
Clients never log or store API keys.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

# White-list of domains for outgoing requests (AGENTS.md, item 6)
ALLOWED_API_DOMAINS = (
    "www.cbr.ru",
    "iss.moex.com",  # MOEX ISS — Russian stocks (free, no key)
    "api.coingecko.com",
    "finnhub.io",
    "openrouter.ai",  # and api.openrouter.ai
    "api.telegram.org",
)


class ApiRateLimitError(RuntimeError):
    """Rate limit of the free API exceeded (HTTP 429)."""


# Currencies supported by CBR (ISO codes)
CBR_CURRENCIES = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "CNY",
        "JPY",
        "AED",
        "TRY",
        "VND",
        "THB",
        "CHF",
        "KZT",
        "CZK",
    }
)

BASE_HEADERS = {"User-Agent": "ai-parser-bot/0.1 (finance telegram bot)"}
HTTP_TIMEOUT_SECONDS = 10


def make_session() -> aiohttp.ClientSession:
    """HTTP session with a timeout so the bot never hangs on external APIs."""
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
        headers=BASE_HEADERS,
    )


def _check_domain(url: str) -> None:
    """Ensures the URL belongs to an allowed domain."""
    allowed = any(domain in url for domain in ALLOWED_API_DOMAINS)
    if not allowed:
        raise ValueError(f"URL is not in the white-list: {url}")


@dataclass
class FxQuote:
    """Currency rate from CBR."""

    code: str  # ISO code, e.g. USD
    name: str  # currency name
    value: float  # rate per one unit (RUB per 1 USD)
    nominal: int  # nominal (usually 100 for JPY/CNY)


@dataclass
class StockQuote:
    """Stock/crypto quote."""

    symbol: str
    price: float
    change_percent: float


class CBRClient:
    """CBR currency rates (free, no key required). HTML/XML daily."""

    BASE_URL = "https://www.cbr.ru/scripts/XML_daily.asp"

    async def get_quote(self, code: str, session: aiohttp.ClientSession) -> FxQuote:
        """Returns the currency rate by ISO code (USD, EUR, ...)."""
        iso = code.upper()
        if iso not in CBR_CURRENCIES:
            raise ValueError(f"Currency {iso} is not supported")
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
        raise LookupError(f"Currency {iso} not found in the CBR response")


class MoexClient:
    """Russian stock quotes via the official MOEX ISS API (free, no key).

    Endpoint: https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR
    Returns prices in RUB and the daily change in percent (CHANGE).
    """

    BASE_URL = "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"

    @staticmethod
    async def get_quote(symbol: str, session: aiohttp.ClientSession) -> StockQuote:
        """Current quote of a Russian stock by ticker (SBER, GAZP, ...)."""
        params = {
            "iss.meta": "off",
            "iss.only": "securities,marketdata",
            "securities.columns": "SECID,SHORTNAME",
            "marketdata.columns": "SECID,LAST,CHANGE",
            "securities": symbol,
        }
        _check_domain(MoexClient.BASE_URL)
        async with session.get(MoexClient.BASE_URL, params=params) as resp:
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()
        rows = (payload.get("marketdata") or {}).get("data") or []
        if not rows:
            raise LookupError(f"MOEX does not know ticker {symbol}")
        row = rows[0]
        price = row[1]
        change = row[2] if len(row) > 2 else 0.0
        if not price:
            raise LookupError(f"MOEX returned no price for {symbol}")
        return StockQuote(
            symbol=symbol,
            price=float(price),
            change_percent=float(change or 0),
        )


class FinnhubClient:
    """Stock quotes via Finnhub (free tier: 60 requests/min)."""

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str | None) -> None:
        self._api_key = api_key

    async def get_quote(
        self, symbol: str, session: aiohttp.ClientSession
    ) -> StockQuote:
        """Current stock/index quote by ticker (AAPL, ^GSPC, SPY)."""
        if not self._api_key:
            raise RuntimeError(
                f"Finnhub API key is not configured (FINNHUB_API_KEY) — cannot request {symbol}"
            )
        url = f"{self.BASE_URL}/quote"
        params = {"symbol": symbol, "token": self._api_key}
        _check_domain(url)
        async with session.get(url, params=params, headers=BASE_HEADERS) as resp:
            if resp.status == 429:
                raise ApiRateLimitError("Finnhub: request rate limit exceeded")
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()
        if not payload.get("c"):
            raise LookupError(
                f"Finnhub does not know ticker {symbol} (or rate limit exceeded)"
            )
        return StockQuote(
            symbol=symbol,
            price=float(payload["c"]),
            change_percent=float(payload.get("dp") or 0),
        )

    async def get_company_profile(
        self, symbol: str, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        """Company profile: name, sector, description (may be empty)."""
        if not self._api_key:
            raise RuntimeError(
                f"Finnhub API key is not configured (FINNHUB_API_KEY) — cannot request profile for {symbol}"
            )
        url = f"{self.BASE_URL}/stock/profile2"
        params = {"symbol": symbol, "token": self._api_key}
        _check_domain(url)
        async with session.get(url, params=params, headers=BASE_HEADERS) as resp:
            if resp.status == 429:
                raise ApiRateLimitError("Finnhub: request rate limit exceeded")
            resp.raise_for_status()
            payload: dict[str, Any] = await resp.json()
        return payload or {}

    async def get_news(
        self, symbol: str, session: aiohttp.ClientSession, days: int = 10
    ) -> list[dict[str, Any]]:
        """Recent news for the ticker (free tier returns headlines)."""
        if not self._api_key:
            raise RuntimeError(
                f"Finnhub API key is not configured (FINNHUB_API_KEY) — cannot request news for {symbol}"
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
                raise ApiRateLimitError("Finnhub: request rate limit exceeded")
            resp.raise_for_status()
            payload: list[dict[str, Any]] = await resp.json()
        return [n for n in payload if n.get("headline")][:10]


class CoinGeckoClient:
    """CoinGecko crypto prices. Demo key: 100 req/min, 10k/month.

    Also works without a key, but the limit is much lower and unstable.
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
                raise ApiRateLimitError("CoinGecko: request rate limit exceeded")
            resp.raise_for_status()
            return await resp.json()

    async def get_quote(
        self, coin_id: str, session: aiohttp.ClientSession
    ) -> StockQuote:
        """Current price and 24h change (in %) in USD."""
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
            raise LookupError(f"CoinGecko does not know coin {coin_id}") from exc

    async def get_prices_batch(
        self, coin_ids: list[str], session: aiohttp.ClientSession
    ) -> dict[str, float]:
        """Prices of several coins in one request: {id: price in USD}.

        Saves the free CoinGecko quota for background alerts.
        """
        if not coin_ids:
            return {}
        url = f"{self.BASE_URL}/simple/price"
        payload = await self._get(
            url, {"ids": ",".join(coin_ids), "vs_currencies": "usd"}, session
        )
        prices: dict[str, float] = {}
        for coin_id, data in payload.items():
            try:
                prices[coin_id] = float(data["usd"])
            except (KeyError, TypeError, ValueError):
                continue
        return prices

    async def get_market_data(
        self, coin_id: str, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        """Fundamental coin data: market cap, volume, rank, ATH."""
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
            raise LookupError(f"CoinGecko does not know coin {coin_id}")
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
        """Price history (USD) for N days: values only."""
        url = f"{self.BASE_URL}/coins/{coin_id}/market_chart"
        payload = await self._get(
            url, {"vs_currency": "usd", "days": str(days)}, session
        )
        prices = payload.get("prices") or []
        try:
            return [float(p[1]) for p in prices]
        except (TypeError, ValueError) as exc:
            raise LookupError(
                f"CoinGecko returned malformed history for {coin_id}"
            ) from exc

    async def get_trending(
        self, session: aiohttp.ClientSession
    ) -> list[dict[str, Any]]:
        """Top-15 trending coins on CoinGecko."""
        url = f"{self.BASE_URL}/search/trending"
        payload = await self._get(url, {}, session)
        coins = []
        for entry in payload.get("coins") or []:
            item = entry.get("item") or {}
            coins.append(
                {
                    "name": item.get("name"),
                    "symbol": item.get("symbol"),
                    "rank": item.get("market_cap_rank"),
                    "price_btc": item.get("price_btc"),
                }
            )
        return coins

    async def get_top_market_cap(
        self, session: aiohttp.ClientSession, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Top coins by market cap (ordered by market_cap_desc)."""
        url = f"{self.BASE_URL}/coins/markets"
        payload = await self._get(
            url,
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": str(limit),
                "page": "1",
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
            session,
        )
        if not isinstance(payload, list):
            raise TypeError("CoinGecko returned a malformed market cap top list")
        coins = []
        for item in payload:
            coins.append(
                {
                    "symbol": (item.get("symbol") or "").upper(),
                    "name": item.get("name"),
                    "rank": item.get("market_cap_rank"),
                    "price": item.get("current_price"),
                    "market_cap": item.get("market_cap"),
                    "change_percent": item.get("price_change_percentage_24h"),
                }
            )
        return coins
