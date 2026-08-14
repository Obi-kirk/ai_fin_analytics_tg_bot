"""Handlers for the /stock and /news commands — stock quotes and news (Finnhub)."""

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.config.settings import get_settings
from src.i18n import t
from src.services.cache import TTLCache
from src.services.financial_api import (
    ApiRateLimitError,
    FinnhubClient,
    StockQuote,
    make_session,
)

log = logging.getLogger(__name__)
router = Router()

# Indexes and their tickers: the user types SPX, the bot fetches an ETF equivalent.
# Finnhub /quote returns only stocks and ETFs — indexes (^GSPC etc.) are not supported.
INDEX_ALIASES = {
    "SPX": "SPY",
    "S&P500": "SPY",
    "S&P": "SPY",
    "DJI": "DIA",
    "DOW": "DIA",
    "IXIC": "QQQ",
    "NASDAQ": "QQQ",
    "VIX": "VIXY",
}

TICKER_RE = re.compile(r"^[A-Z0-9.\-^]{1,15}$")


def resolve_stock_symbol(raw: str) -> str:
    """Returns the real ticker for the request: indexes -> ETF equivalents.

    Finnhub /quote returns only stocks and ETFs; ^GSPC, ^DJI etc. are not supported.
    """
    return INDEX_ALIASES.get(raw.upper(), raw.upper())


@router.message(Command("stock"))
async def cmd_stock(message: Message, cache: TTLCache) -> None:
    """Shows a stock or index price, e.g.: /stock AAPL."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(t("stock.usage"))
        return
    raw = args[1].strip().upper()
    symbol = resolve_stock_symbol(raw)
    if not TICKER_RE.match(symbol):
        await message.answer(t("stock.bad_ticker", raw=raw))
        return

    settings = get_settings()
    key = f"stock:{symbol}"
    try:
        quote: StockQuote = await cache.get_or_set(
            key, lambda: fetch_stock(symbol), settings.cache_ttl_stock_seconds
        )
    except ApiRateLimitError:
        await message.answer(t("stock.rate_limit"))
        return
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await message.answer(t("stock.fetch_failed"))
        return
    await message.answer(format_stock(quote, display=raw))


async def fetch_stock(symbol: str) -> StockQuote:
    """Fetches a Finnhub quote via a separate HTTP session."""
    async with make_session() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        try:
            return await client.get_quote(symbol, session)
        except Exception:
            log.exception("Failed to fetch quote %s from Finnhub", symbol)
            raise


async def fetch_news(symbol: str) -> list[dict]:
    """Fresh Finnhub news for the ticker."""
    async with make_session() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        try:
            return await client.get_news(symbol, session)
        except Exception:
            log.exception("Failed to fetch news %s from Finnhub", symbol)
            raise


def _format_news_date(ts: int | None) -> str:
    """News date in DD.MM format (UTC)."""
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m")


def format_news(symbol: str, news: list[dict], limit: int = 5) -> str:
    """Formats news for the ticker for Telegram (HTML)."""
    lines = [t("stock.news_title", symbol=symbol) + "\n"]
    shown = 0
    for item in news:
        headline = (item.get("headline") or "").strip()
        url = item.get("url")
        if not headline or not url:
            continue
        date = _format_news_date(item.get("datetime"))
        link = f'<a href="{url}">{t("stock.news.read")}</a>' if url else ""
        lines.append(f"• {date} — {headline} {link}")
        shown += 1
        if shown >= limit:
            break
    if not shown:
        lines.append(t("stock.news.empty"))
    return "\n".join(lines)


@router.message(Command("news"))
async def cmd_news(message: Message, cache: TTLCache) -> None:
    """Latest news for the ticker, e.g.: /news AAPL."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(t("stock.news.usage"))
        return
    symbol = args[1].strip().upper()
    if not TICKER_RE.match(symbol):
        await message.answer(t("stock.bad_ticker_short"))
        return

    await _send_news(message.answer, symbol, cache)


def news_kb(symbol: str) -> InlineKeyboardMarkup:
    """Buttons under the news: back to the quote and back to the stocks submenu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("stock.btn.back_stock"), callback_data=f"stock:{symbol}"
                ),
                InlineKeyboardButton(
                    text=t("stock.btn.back_menu"), callback_data="submenu:stock"
                ),
            ]
        ]
    )


async def _send_news(
    send: Callable[..., Awaitable[Any]], symbol: str, cache: TTLCache
) -> None:
    """Fetches news from the cache/API and sends it (text, buttons)."""
    settings = get_settings()
    try:
        news = await cache.get_or_set(
            f"stock:news:{symbol}",
            lambda: fetch_news(symbol),
            settings.cache_ttl_fundamental_seconds,
        )
    except ApiRateLimitError:
        await send(t("stock.news_rate_limit"))
        return
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await send(t("stock.news_failed"))
        return
    await send(format_news(symbol, news), reply_markup=news_kb(symbol))


@router.callback_query(F.data.regexp(r"^news:[A-Z0-9.\-^]+$"))
async def on_news_cb(callback: CallbackQuery, cache: TTLCache) -> None:
    """Opens stock news right from the submenu (the "News" button)."""
    symbol = callback.data.split(":", 1)[1]
    await _send_news(callback.message.edit_text, symbol, cache)
    await callback.answer()


def format_stock(quote: StockQuote, display: str | None = None) -> str:
    """Formats a stock quote for Telegram (HTML).

    ``display`` — how to name the asset in the title (for indexes this is
    the user's original alias, e.g. SPX, while quote.symbol == SPY).
    """
    label = display or quote.symbol
    sign = "+" if quote.change_percent >= 0 else ""
    return t(
        "stock.format",
        label=label,
        price=f"{quote.price:,.2f}",
        sign=sign,
        change=f"{quote.change_percent:.2f}",
    )
