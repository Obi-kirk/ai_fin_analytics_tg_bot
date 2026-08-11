"""Обработчик команды /crypto — цена криптовалюты (CoinGecko)."""

import logging
import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config.settings import get_settings
from src.services.cache import TTLCache
from src.services.financial_api import (
    ApiRateLimitError,
    CoinGeckoClient,
    StockQuote,
    make_session,
)

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
    except ApiRateLimitError:
        await message.answer(
            "⚠️ Превышен лимит запросов к API крипты. Попробуй через минуту."
        )
        return
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


async def fetch_trending() -> list[dict]:
    """Топ трендовых монет CoinGecko (кэшируется на 10 минут)."""
    async with make_session() as session:
        gecko = CoinGeckoClient(get_settings().coingecko_api_key)
        try:
            return await gecko.get_trending(session)
        except Exception:
            log.exception("Не удалось получить тренды от CoinGecko")
            raise


def format_trending(coins: list[dict]) -> str:
    """Форматирует топ трендовых монет для Telegram (HTML)."""
    lines = ["🔥 <b>Тренды CoinGecko</b>\n"]
    for i, coin in enumerate(coins[:10], start=1):
        rank = f"#{coin['rank']}" if coin.get("rank") else "—"
        lines.append(f"{i}. {coin['name']} <b>({coin['symbol']})</b> — ранг {rank}")
    lines.append("\nПроверь цену: /crypto SYMBOL или AI-анализ в меню.")
    return "\n".join(lines)


@router.message(Command("trending"))
async def cmd_trending(message: Message, cache: TTLCache) -> None:
    """Показывает топ трендовых криптовалют."""
    settings = get_settings()
    try:
        coins = await cache.get_or_set(
            "trending", fetch_trending, settings.cache_ttl_stock_seconds
        )
    except ApiRateLimitError:
        await message.answer(
            "⚠️ Превышен лимит запросов к API крипты. Попробуй через минуту."
        )
        return
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить тренды. Попробуй позже.")
        return
    await message.answer(format_trending(coins))


def _format_cap(value: float | None) -> str:
    """Компактное представление капитализации: $1.29T, $320B, $45M."""
    if not value:
        return "—"
    for suffix, divisor in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if value >= divisor:
            return f"${value / divisor:,.2f}{suffix}"
    return f"${value:,.0f}"


async def fetch_top() -> list[dict]:
    """Топ монет по капитализации (кэшируется на 10 минут)."""
    async with make_session() as session:
        gecko = CoinGeckoClient(get_settings().coingecko_api_key)
        try:
            return await gecko.get_top_market_cap(session)
        except Exception:
            log.exception("Не удалось получить топ капитализации от CoinGecko")
            raise


def format_top(coins: list[dict]) -> str:
    """Форматирует топ монет по капитализации для Telegram (HTML)."""
    lines = ["🏆 <b>Топ криптовалют по капитализации</b>\n"]
    for i, coin in enumerate(coins[:10], start=1):
        change = coin.get("change_percent")
        sign = "+" if change is not None and change >= 0 else ""
        change_str = (
            f" — {sign}{change:.2f}%" if isinstance(change, (int, float)) else ""
        )
        lines.append(
            f"{i}. {coin['name']} <b>({coin['symbol']})</b>"
            f"\n   💵 ${coin['price']:,.2f}{change_str}"
            f"\n   💰 Капитализация: {_format_cap(coin['market_cap'])}"
        )
    lines.append("\nПодробнее: /crypto SYMBOL или AI-анализ в меню.")
    return "\n".join(lines)


@router.message(Command("top"))
async def cmd_top(message: Message, cache: TTLCache) -> None:
    """Показывает топ монет по капитализации."""
    settings = get_settings()
    try:
        coins = await cache.get_or_set(
            "top", fetch_top, settings.cache_ttl_stock_seconds
        )
    except ApiRateLimitError:
        await message.answer(
            "⚠️ Превышен лимит запросов к API крипты. Попробуй через минуту."
        )
        return
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить топ. Попробуй позже.")
        return
    await message.answer(format_top(coins))


def format_crypto(symbol: str, quote: StockQuote) -> str:
    """Форматирует цену криптовалюты для Telegram (HTML)."""
    sign = "+" if quote.change_percent >= 0 else ""
    change = (
        f"\nИзменение: {sign}{quote.change_percent:.2f}%"
        if quote.change_percent
        else ""
    )
    return f"🪙 <b>{symbol}</b>\nЦена: <b>${quote.price:,.2f}</b>{change}"
