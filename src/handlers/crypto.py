"""Обработчик команды /crypto — цена криптовалюты (CoinGecko)."""

import logging
import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config.settings import get_settings
from src.services.cache import TTLCache
from src.services.financial_api import CoinGeckoClient, StockQuote, make_session

log = logging.getLogger(__name__)
router = Router()

# Символ монеты -> id CoinGecko
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
}

COIN_RE = re.compile(r"^[A-Z0-9]{2,10}$")


@router.message(Command("crypto"))
async def cmd_crypto(message: Message, cache: TTLCache) -> None:
    """Показывает цену криптовалюты, например: /crypto BTC."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажи монету, например: /crypto BTC или /crypto SOL")
        return
    raw = args[1].strip().upper()
    if not COIN_RE.match(raw):
        await message.answer("Некорректное название монеты.")
        return

    settings = get_settings()
    key = f"crypto:{raw}"
    try:
        quote: StockQuote = await cache.get_or_set(
            key, lambda: fetch_crypto(raw), settings.cache_ttl_stock_seconds
        )
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить цену. Попробуй позже.")
        return
    await message.answer(format_crypto(raw, quote))


async def fetch_crypto(symbol: str) -> StockQuote:
    """Цена монеты через CoinGecko (с демо-ключом, без него — keyless)."""
    gecko_id = COINS.get(symbol, symbol.lower())
    async with make_session() as session:
        gecko = CoinGeckoClient(get_settings().coingecko_api_key)
        try:
            return await gecko.get_quote(gecko_id, session)
        except Exception:
            log.exception("Не удалось получить цену %s от CoinGecko", gecko_id)
            raise


def format_crypto(symbol: str, quote: StockQuote) -> str:
    """Форматирует цену криптовалюты для Telegram (HTML)."""
    sign = "+" if quote.change_percent >= 0 else ""
    change = (
        f"\nИзменение: {sign}{quote.change_percent:.2f}%"
        if quote.change_percent
        else ""
    )
    return f"🪙 <b>{symbol}</b>\nЦена: <b>${quote.price:,.2f}</b>{change}"
