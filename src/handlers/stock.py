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
from src.services.cache import TTLCache
from src.services.financial_api import (
    ApiRateLimitError,
    FinnhubClient,
    StockQuote,
    make_session,
)

log = logging.getLogger(__name__)
router = Router()

# Индексы и их тикеры: пользователь пишет SPX, бот запрашивает ^GSPC
INDEX_ALIASES = {
    "SPX": "^GSPC",
    "S&P500": "^GSPC",
    "S&P": "^GSPC",
    "DJI": "^DJI",
    "DOW": "^DJI",
    "IXIC": "^IXIC",
    "NASDAQ": "^IXIC",
    "VIX": "^VIX",
}

TICKER_RE = re.compile(r"^[A-Z0-9.\-^]{1,15}$")


@router.message(Command("stock"))
async def cmd_stock(message: Message, cache: TTLCache) -> None:
    """Показывает цену акции или индекса, например: /stock AAPL."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Укажи тикер, например: /stock AAPL или /stock SPX\n"
            "Индексы: SPX, DJI, NASDAQ, VIX."
        )
        return
    raw = args[1].strip().upper()
    symbol = INDEX_ALIASES.get(raw, raw)
    if not TICKER_RE.match(symbol):
        await message.answer(
            f"Тикер {raw} некорректный. Допустимы буквы, цифры, точки, дефис (до 15 символов)."
        )
        return

    settings = get_settings()
    key = f"stock:{symbol}"
    try:
        quote: StockQuote = await cache.get_or_set(
            key, lambda: fetch_stock(symbol), settings.cache_ttl_stock_seconds
        )
    except ApiRateLimitError:
        await message.answer(
            "⚠️ Превышен лимит запросов к API акций. Попробуй через минуту."
        )
        return
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить котировку. Попробуй позже.")
        return
    await message.answer(format_stock(quote))


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
    lines = [f"📰 <b>Новости {symbol}</b> (за 10 дней)\n"]
    shown = 0
    for item in news:
        headline = (item.get("headline") or "").strip()
        url = item.get("url")
        if not headline or not url:
            continue
        date = _format_news_date(item.get("datetime"))
        link = f'<a href="{url}">читать</a>' if url else ""
        lines.append(f"• {date} — {headline} {link}")
        shown += 1
        if shown >= limit:
            break
    if not shown:
        lines.append("Новостей за этот период нет.")
    return "\n".join(lines)


@router.message(Command("news"))
async def cmd_news(message: Message, cache: TTLCache) -> None:
    """Последние новости по тикеру, например: /news AAPL."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажи тикер, например: /news AAPL или /news NVDA")
        return
    symbol = args[1].strip().upper()
    if not TICKER_RE.match(symbol):
        await message.answer("Некорректный тикер.")
        return

    await _send_news(message.answer, symbol, cache)


def news_kb(symbol: str) -> InlineKeyboardMarkup:
    """Кнопки под новостями: назад к котировке и возврат в подменю акций."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ К акции", callback_data=f"stock:{symbol}"
                ),
                InlineKeyboardButton(text="↩️ Меню", callback_data="submenu:stock"),
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
        await send("⚠️ Превышен лимит запросов к API новостей. Попробуй через минуту.")
        return
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await send("😔 Не удалось получить новости. Попробуй позже.")
        return
    await send(format_news(symbol, news), reply_markup=news_kb(symbol))


@router.callback_query(F.data.regexp(r"^news:[A-Z0-9.\-^]+$"))
async def on_news_cb(callback: CallbackQuery, cache: TTLCache) -> None:
    """Открывает новости акции прямо из подменю (кнопка «📰 Новости»)."""
    symbol = callback.data.split(":", 1)[1]
    await _send_news(callback.message.edit_text, symbol, cache)
    await callback.answer()


def format_stock(quote: StockQuote) -> str:
    """Форматирует котировку акции для Telegram (HTML)."""
    sign = "+" if quote.change_percent >= 0 else ""
    return (
        f"📈 <b>{quote.symbol}</b>\n"
        f"Цена: <b>${quote.price:,.2f}</b>\n"
        f"Изменение: {sign}{quote.change_percent:.2f}%"
    )
