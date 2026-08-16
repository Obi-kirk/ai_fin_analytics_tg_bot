"""Handlers for the /stock and /news commands — stock quotes and news (Finnhub)."""

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
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
    GoogleNewsClient,
    MoexClient,
    StockQuote,
    YahooClient,
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

# Russian stocks (MOEX tickers, prices in RUB). Everything else goes to Finnhub.
RU_STOCKS = (
    "SBER",
    "GAZP",
    "LKOH",
    "ROSN",
    "NVTK",
    "PLZL",
    "TATN",
    "MGNT",
    "MOEX",
    "SNGS",
    "SBERP",
    "VTBR",
    "AFLT",
    "GMKN",
    "CHMF",
    "NLMK",
    "MAGN",
    "PHOR",
    "ALRS",
    "IRAO",
    "FEES",
    "RTKM",
    "RSTI",
    "TRNFP",
    "HYDR",
    "ENPG",
    "MTLR",
    "PIKK",
    "CBOM",
    "SFIN",
    "SMLT",  # Самолёт — девелопер
    "SIBN",  # Газпром нефть
    "TATNP",  # Татнефть прив.
    "SNGSP",  # Сургутнефтегаз прив.
    "OZON",
    "VKCO",  # VK
    "POSI",  # Positive Technologies
    "LENT",  # Лента
    "RASP",  # Распадская
    "GCHE",  # Черкизово
    "BELU",  # НоваБев (Белуга)
    "TRMK",  # ТМК
    "VSMO",  # ВСМПО-Ависма
    "UPRO",  # Юнипро
    "BSPB",  # Банк Санкт-Петербург
    "SVAV",  # Соллерс
)


def is_ru_stock(symbol: str) -> bool:
    """True if the ticker belongs to the Russian (MOEX) market."""
    return symbol.upper() in RU_STOCKS


# Russian company names for the Google News search (ticker -> company name)
RU_STOCK_NAMES = {
    "SBER": "Сбербанк",
    "GAZP": "Газпром",
    "LKOH": "Лукойл",
    "ROSN": "Роснефть",
    "NVTK": "Новатэк",
    "PLZL": "Полюс",
    "TATN": "Татнефть",
    "MGNT": "Магнит",
    "MOEX": "Московская биржа",
    "SNGS": "Сургутнефтегаз",
    "SBERP": "Сбербанк",
    "VTBR": "ВТБ",
    "AFLT": "Аэрофлот",
    "GMKN": "ГМК Норильский никель",
    "CHMF": "Северсталь",
    "NLMK": "НЛМК",
    "MAGN": "ММК",
    "PHOR": "ФосАгро",
    "ALRS": "АЛРОСА",
    "IRAO": "Интер РАО",
    "FEES": "Россети",
    "RTKM": "Ростелеком",
    "RSTI": "Россети Урал",
    "TRNFP": "Транснефть",
    "HYDR": "РусГидро",
    "ENPG": "Эн+ Груп",
    "MTLR": "Мечел",
    "PIKK": "ПИК",
    "CBOM": "МКБ",
    "SFIN": "ЭсЭфАй",
    "SMLT": "Группа Самолёт",
    "SIBN": "Газпром нефть",
    "TATNP": "Татнефть",
    "SNGSP": "Сургутнефтегаз",
    "OZON": "Ozon",
    "VKCO": "VK",
    "POSI": "Positive Technologies",
    "LENT": "Лента",
    "RASP": "Распадская",
    "GCHE": "Черкизово",
    "BELU": "НоваБев",
    "TRMK": "ТМК",
    "VSMO": "ВСМПО-Ависма",
    "UPRO": "Юнипро",
    "BSPB": "Банк Санкт-Петербург",
    "SVAV": "Соллерс",
}


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
    """Fetches a stock quote: MOEX for Russian tickers, Finnhub otherwise.

    For world stocks the company name is fetched from the Finnhub profile
    (best-effort: a profile failure does not fail the quote).
    """
    if is_ru_stock(symbol):
        async with make_session() as session:
            try:
                return await MoexClient.get_quote(symbol, session)
            except Exception:
                log.exception("Failed to fetch quote %s from MOEX", symbol)
                raise
    async with make_session() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        try:
            quote = await client.get_quote(symbol, session)
        except Exception:
            log.exception("Failed to fetch quote %s from Finnhub", symbol)
            raise
        try:
            profile = await client.get_company_profile(symbol, session)
            if profile.get("name"):
                quote.name = profile["name"]
        except Exception:  # noqa: BLE001 — profile is best-effort, quote still works
            log.warning("Failed to fetch company profile for %s", symbol)
        return quote


async def fetch_news(symbol: str) -> list[dict]:
    """Fresh news for the ticker: Google News RSS for Russian stocks,
    Finnhub otherwise (Finnhub does not cover Russian tickers)."""
    if is_ru_stock(symbol):
        company = RU_STOCK_NAMES.get(symbol.upper(), symbol)
        async with make_session() as session:
            try:
                return await GoogleNewsClient.get_news(company, session)
            except Exception:
                log.exception("Failed to fetch news %s from Google News", symbol)
                raise
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


def format_stock(
    quote: StockQuote, display: str | None = None, name: str | None = None
) -> str:
    """Formats a stock quote for Telegram (HTML).

    ``display`` — how to name the asset in the title (for indexes this is
    the user's original alias, e.g. SPX, while quote.symbol == SPY).
    ``name`` — company name (from MOEX SHORTNAME or the Finnhub profile);
    falls back to the quote's own name field.
    Russian (MOEX) stocks are shown in rubles.
    """
    label = display or quote.symbol
    company = name or quote.name or ""
    sign = "+" if quote.change_percent >= 0 else ""
    if is_ru_stock(label):
        return t(
            "stock.format_ru",
            label=label,
            name=company,
            price=f"{quote.price:,.2f}",
            sign=sign,
            change=f"{quote.change_percent:.2f}",
        )
    return t(
        "stock.format",
        label=label,
        name=company,
        price=f"{quote.price:,.2f}",
        sign=sign,
        change=f"{quote.change_percent:.2f}",
    )


async def fetch_stock_history(symbol: str) -> list[float]:
    """30-day stock price history: MOEX candles for RU, Yahoo for the world."""
    resolved = resolve_stock_symbol(symbol)
    async with make_session() as session:
        if is_ru_stock(symbol):
            try:
                return await MoexClient.get_price_history(resolved, session)
            except Exception:
                log.exception("Failed to fetch history %s from MOEX", symbol)
                raise
        try:
            return await YahooClient.get_price_history(resolved, session)
        except Exception:
            log.exception("Failed to fetch history %s from Yahoo", symbol)
            raise


async def _send_stock_chart(message: Message, symbol: str, cache: TTLCache) -> None:
    """Generates and sends the 30-day stock price chart (PNG)."""
    from src.handlers.crypto import build_chart_png

    raw = symbol.upper()
    if not TICKER_RE.match(raw):
        await message.answer(t("stock.bad_ticker_short"))
        return
    settings = get_settings()
    try:
        history = await cache.get_or_set(
            f"stock:chart:{raw}",
            lambda: fetch_stock_history(raw),
            settings.cache_ttl_fundamental_seconds,
        )
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await message.answer(t("stock.chart.failed"))
        return
    if len(history) < 2:
        await message.answer(t("stock.chart.insufficient"))
        return
    try:
        currency = "RUB" if is_ru_stock(raw) else "USD"
        png = build_chart_png(raw, history, currency=currency)
    except Exception:
        log.exception("Failed to build the chart for %s", raw)
        await message.answer(t("stock.chart.build_failed"))
        return
    await message.answer_photo(
        BufferedInputFile(png, filename=f"{raw}.png"),
        caption=t("stock.chart.caption", symbol=raw),
    )


@router.message(Command("chart"))
async def cmd_chart(message: Message, cache: TTLCache) -> None:
    """Shows a 30-day price chart for a stock or index: /chart SBER."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(t("stock.chart.usage"))
        return
    await _send_stock_chart(message, args[1].strip().upper(), cache)


@router.callback_query(F.data.regexp(r"^stock_chart:[A-Z0-9.\-^]+$"))
async def on_stock_chart_cb(callback: CallbackQuery, cache: TTLCache) -> None:
    """Sends the stock chart from the card button."""
    symbol = callback.data.split(":", 1)[1]
    await callback.answer()
    await _send_stock_chart(callback.message, symbol, cache)
