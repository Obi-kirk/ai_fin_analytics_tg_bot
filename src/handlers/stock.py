"""Обработчик /stock и /news — котировки акций и новости (Finnhub)."""

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

# Индексы и их тикеры: пользователь пишет SPX, бот запрашивает ETF-аналог.
# Finnhub /quote отдаёт только акции и ETF — индексы (^GSPC и т.п.) не поддерживает.
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
    """Возвращает реальный тикер для запроса: индексы -> ETF-аналоги.

    Finnhub /quote отдаёт только акции и ETF; ^GSPC, ^DJI и т.п. не поддерживает.
    """
    return INDEX_ALIASES.get(raw.upper(), raw.upper())


@router.message(Command("stock"))
async def cmd_stock(message: Message, cache: TTLCache) -> None:
    """Показывает цену акции или индекса, например: /stock AAPL."""
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
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer(t("stock.fetch_failed"))
        return
    await message.answer(format_stock(quote, display=raw))


async def fetch_stock(symbol: str) -> StockQuote:
    """Получает котировку Finnhub через отдельный HTTP-сеанс."""
    async with make_session() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        try:
            return await client.get_quote(symbol, session)
        except Exception:
            log.exception("Не удалось получить котировку %s от Finnhub", symbol)
            raise


async def fetch_news(symbol: str) -> list[dict]:
    """Свежие новости по тикеру Finnhub."""
    async with make_session() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        try:
            return await client.get_news(symbol, session)
        except Exception:
            log.exception("Не удалось получить новости %s от Finnhub", symbol)
            raise


def _format_news_date(ts: int | None) -> str:
    """Дата новости в формате ДД.ММ (UTC)."""
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m")


def format_news(symbol: str, news: list[dict], limit: int = 5) -> str:
    """Форматирует новости по тикеру для Telegram (HTML)."""
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
    """Последние новости по тикеру, например: /news AAPL."""
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
    """Кнопки под новостями: назад к котировке и возврат в подменю акций."""
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
    """Достаёт новости из кэша/API и отправляет (текст, кнопки)."""
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
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await send(t("stock.news_failed"))
        return
    await send(format_news(symbol, news), reply_markup=news_kb(symbol))


@router.callback_query(F.data.regexp(r"^news:[A-Z0-9.\-^]+$"))
async def on_news_cb(callback: CallbackQuery, cache: TTLCache) -> None:
    """Открывает новости акции прямо из подменю (кнопка «📰 Новости»)."""
    symbol = callback.data.split(":", 1)[1]
    await _send_news(callback.message.edit_text, symbol, cache)
    await callback.answer()


def format_stock(quote: StockQuote, display: str | None = None) -> str:
    """Форматирует котировку акции для Telegram (HTML).

    ``display`` — как называть актив в заголовке (для индексов это исходный
    алиас пользователя, напр. SPX, тогда как quote.symbol == SPY).
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
