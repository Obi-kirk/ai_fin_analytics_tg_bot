"""Handlers for the /crypto and /chart commands — cryptocurrency (CoinGecko)."""

import io
import logging
import re

import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")  # no GUI — save to file only

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from src.config.settings import get_settings
from src.i18n import t
from src.services.cache import TTLCache
from src.services.financial_api import (
    ApiRateLimitError,
    CoinGeckoClient,
    StockQuote,
    make_session,
)

log = logging.getLogger(__name__)
router = Router()

# Coin symbol -> CoinGecko id
COINS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "LTC": "litecoin",
    "BNB": "binancecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "TON": "the-open-network",
    "TRX": "tron",
    "SHIB": "shiba-inu",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "NEAR": "near",
    "POL": "polygon-ecosystem-token",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "FIL": "filecoin",
    "ICP": "internet-computer",
    "ETC": "ethereum-classic",
    "XLM": "stellar",
    "XMR": "monero",
    "VET": "vechain",
    "HBAR": "hedera-hashgraph",
    "SUI": "sui",
    "PEPE": "pepe",
    "AAVE": "aave",
    "MKR": "maker",
    "ZEC": "zcash",
    "BCH": "bitcoin-cash",
    "WLD": "worldcoin-wld",
    "BONK": "bonk",
}

COIN_RE = re.compile(r"^[A-Z0-9]{2,10}$")


@router.message(Command("crypto"))
async def cmd_crypto(message: Message, cache: TTLCache) -> None:
    """Shows a cryptocurrency price, e.g.: /crypto BTC."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(t("crypto.usage"))
        return
    raw = args[1].strip().upper()
    if not COIN_RE.match(raw):
        await message.answer(t("crypto.bad_coin"))
        return

    settings = get_settings()
    key = f"crypto:{raw}"
    try:
        quote: StockQuote = await cache.get_or_set(
            key, lambda: fetch_crypto(raw), settings.cache_ttl_stock_seconds
        )
    except ApiRateLimitError:
        await message.answer(t("crypto.rate_limit"))
        return
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await message.answer(t("crypto.fetch_failed"))
        return
    await message.answer(format_crypto(raw, quote))


async def fetch_crypto(symbol: str) -> StockQuote:
    """Coin price via CoinGecko (with a demo key, without it — keyless)."""
    gecko_id = COINS.get(symbol, symbol.lower())
    async with make_session() as session:
        gecko = CoinGeckoClient(get_settings().coingecko_api_key)
        try:
            return await gecko.get_quote(gecko_id, session)
        except Exception:
            log.exception("Failed to fetch price %s from CoinGecko", gecko_id)
            raise


async def fetch_trending() -> list[dict]:
    """Top trending coins from CoinGecko (cached for 10 minutes)."""
    async with make_session() as session:
        gecko = CoinGeckoClient(get_settings().coingecko_api_key)
        try:
            return await gecko.get_trending(session)
        except Exception:
            log.exception("Failed to fetch trending from CoinGecko")
            raise


async def _fetch_price_history(coin_id: str) -> list[float]:
    """30-day coin price history (USD)."""
    async with make_session() as session:
        gecko = CoinGeckoClient(get_settings().coingecko_api_key)
        return await gecko.get_price_history(coin_id, session)


def build_chart_png(symbol: str, prices: list[float], currency: str = "USD") -> bytes:
    """PNG line chart of the price over the period (matplotlib, Agg backend)."""
    if not prices:
        raise ValueError("No data for the chart")
    fig, ax = plt.subplots(figsize=(6, 3), dpi=110)
    ax.plot(prices, color="#1f77b4", linewidth=1.6)
    ax.set_title(f"{symbol} — 30 days", fontsize=12)
    ax.set_xlabel("days")
    ax.set_ylabel(currency)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


async def _send_chart(message: Message, symbol: str, cache: TTLCache) -> None:
    """Generates and sends the 30-day coin price chart."""
    if not COIN_RE.match(symbol):
        await message.answer(t("crypto.bad_coin"))
        return
    gecko_id = COINS.get(symbol, symbol.lower())
    settings = get_settings()
    try:
        history = await cache.get_or_set(
            f"crypto:chart:{symbol}",
            lambda: _fetch_price_history(gecko_id),
            settings.cache_ttl_fundamental_seconds,
        )
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await message.answer(t("crypto.chart.failed"))
        return
    if len(history) < 2:
        await message.answer(t("crypto.chart.insufficient"))
        return
    try:
        png = build_chart_png(symbol, history)
    except Exception:
        log.exception("Failed to build the chart for %s", symbol)
        await message.answer(t("crypto.chart.build_failed"))
        return
    await message.answer_photo(
        BufferedInputFile(png, filename=f"{symbol}.png"),
        caption=t("crypto.chart.caption", symbol=symbol),
    )


@router.message(Command("chart"))
async def cmd_chart(message: Message, cache: TTLCache) -> None:
    """Shows the 30-day coin price chart, e.g.: /chart BTC."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(t("crypto.chart.usage"))
        return
    await _send_chart(message, args[1].strip().upper(), cache)


@router.callback_query(F.data.regexp(r"^chart:[A-Z0-9]+$"))
async def on_chart_callback(callback: CallbackQuery, cache: TTLCache) -> None:
    """Sends the coin chart from the button under the card."""
    symbol = callback.data.split(":", 1)[1]
    await callback.answer()
    await _send_chart(callback.message, symbol, cache)


def format_trending(coins: list[dict]) -> str:
    """Formats the top trending coins for Telegram (HTML)."""
    lines = [t("crypto.trending.title") + "\n"]
    for i, coin in enumerate(coins[:10], start=1):
        rank = f"#{coin['rank']}" if coin.get("rank") else "—"
        lines.append(
            f"{i}. {coin['name']} <b>({coin['symbol']})</b> — {t('crypto.trending.rank')} {rank}"
        )
    lines.append(t("crypto.trending.hint"))
    return "\n".join(lines)


@router.message(Command("trending"))
async def cmd_trending(message: Message, cache: TTLCache) -> None:
    """Shows the top trending cryptocurrencies."""
    settings = get_settings()
    try:
        coins = await cache.get_or_set(
            "trending", fetch_trending, settings.cache_ttl_stock_seconds
        )
    except ApiRateLimitError:
        await message.answer(t("crypto.rate_limit"))
        return
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await message.answer(t("crypto.trending.failed"))
        return
    await message.answer(format_trending(coins))


def _format_cap(value: float | None) -> str:
    """Compact market cap representation: $1.29T, $320B, $45M."""
    if not value:
        return "—"
    for suffix, divisor in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if value >= divisor:
            return f"${value / divisor:,.2f}{suffix}"
    return f"${value:,.0f}"


async def fetch_top() -> list[dict]:
    """Top coins by market cap (cached for 10 minutes)."""
    async with make_session() as session:
        gecko = CoinGeckoClient(get_settings().coingecko_api_key)
        try:
            return await gecko.get_top_market_cap(session)
        except Exception:
            log.exception("Failed to fetch the top market cap from CoinGecko")
            raise


def format_top(coins: list[dict]) -> str:
    """Formats the top coins by market cap for Telegram (HTML)."""
    lines = [t("crypto.top.title") + "\n"]
    for i, coin in enumerate(coins[:10], start=1):
        change = coin.get("change_percent")
        sign = "+" if change is not None and change >= 0 else ""
        change_str = (
            f" — {sign}{change:.2f}%" if isinstance(change, (int, float)) else ""
        )
        lines.append(
            f"{i}. {coin['name']} <b>({coin['symbol']})</b>"
            f"\n   💵 ${coin['price']:,.2f}{change_str}"
            f"\n   {t('crypto.top.cap', cap=_format_cap(coin['market_cap']))}"
        )
    lines.append(t("crypto.top.hint"))
    return "\n".join(lines)


@router.message(Command("top"))
async def cmd_top(message: Message, cache: TTLCache) -> None:
    """Shows the top coins by market cap."""
    settings = get_settings()
    try:
        coins = await cache.get_or_set(
            "top", fetch_top, settings.cache_ttl_stock_seconds
        )
    except ApiRateLimitError:
        await message.answer(t("crypto.rate_limit"))
        return
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await message.answer(t("crypto.top.failed"))
        return
    await message.answer(format_top(coins))


def format_crypto(symbol: str, quote: StockQuote) -> str:
    """Formats a cryptocurrency price for Telegram (HTML)."""
    sign = "+" if quote.change_percent >= 0 else ""
    change = (
        t("crypto.change", sign=sign, pct=f"{quote.change_percent:.2f}")
        if quote.change_percent
        else ""
    )
    return t("crypto.format", symbol=symbol, price=f"{quote.price:,.2f}", change=change)
