"""Обработчик команды /stock — котировки акций и индексов (Finnhub)."""

import logging
import re

import aiohttp
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config.settings import get_settings
from src.services.cache import TTLCache
from src.services.financial_api import FinnhubClient, StockQuote

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
            key,
            lambda: _fetch_quote(symbol),
            settings.cache_ttl_stock_seconds,
        )
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить котировку. Попробуй позже.")
        return

    sign = "+" if quote.change_percent >= 0 else ""
    await message.answer(
        f"📈 <b>{symbol}</b>\n"
        f"Цена: <b>${quote.price:,.2f}</b>\n"
        f"Изменение: {sign}{quote.change_percent:.2f}%"
    )


async def _fetch_quote(symbol: str) -> StockQuote:
    """Получает котировку Finnhub через отдельный HTTP-сеанс."""
    async with aiohttp.ClientSession() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        try:
            return await client.get_quote(symbol, session)
        except Exception:
            log.exception("Не удалось получить котировку %s от Finnhub", symbol)
            raise
