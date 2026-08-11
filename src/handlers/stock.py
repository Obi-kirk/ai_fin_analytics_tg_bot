"""Обработчик команды /stock — котировки акций и индексов (Finnhub)."""

import logging
import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

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


def format_stock(quote: StockQuote) -> str:
    """Форматирует котировку акции для Telegram (HTML)."""
    sign = "+" if quote.change_percent >= 0 else ""
    return (
        f"📈 <b>{quote.symbol}</b>\n"
        f"Цена: <b>${quote.price:,.2f}</b>\n"
        f"Изменение: {sign}{quote.change_percent:.2f}%"
    )
