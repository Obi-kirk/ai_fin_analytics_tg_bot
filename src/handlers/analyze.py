"""Обработчик /analyze и колбэков analyse:* — AI-анализ актива (OpenRouter).

Безопасность: ввод пользователя проходит sanitize_user_text() до отправки
в LLM (AGENTS.md п.2), длина ограничена (MAX_QUERY_LENGTH).
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.config.settings import get_settings
from src.handlers.crypto import COIN_RE, fetch_crypto
from src.handlers.stock import TICKER_RE, fetch_stock, resolve_stock_symbol
from src.services.cache import TTLCache
from src.services.financial_api import (
    CoinGeckoClient,
    FinnhubClient,
    make_session,
)
from src.services.llm_service import (
    LLMClient,
    markdown_to_html,
    sanitize_user_text,
)

log = logging.getLogger(__name__)
router = Router()

# Символы подменю AI-анализа (из menu.py) и их формат
ANALYSE_TYPES = {
    "AAPL": "stock",
    "TSLA": "stock",
    "NVDA": "stock",
    "MSFT": "stock",
    "GOOGL": "stock",
    "AMZN": "stock",
    "META": "stock",
    "AMD": "stock",
    "SPX": "stock",
    "DJI": "stock",
    "BTC": "crypto",
    "ETH": "crypto",
    "SOL": "crypto",
    "XRP": "crypto",
}

QUERY_RE = re.compile(r"^[\w\s.,!?()%$€¥£+-]{1,500}$")

AI_DISCLAIMER = "\n\n— <i>Это не инвестиционная рекомендация.</i>"

SendText = Callable[[str], Awaitable[None]]

# Ограничения для контекста: чтобы не раздувать промпт (AGENTS.md: экономия токенов)
MAX_DESCRIPTION_LENGTH = 300
MAX_NEWS_ITEMS = 3
MAX_NEWS_LENGTH = 120


async def _fetch_company_profile(symbol: str) -> dict:
    """Справка о компании Finnhub (кэшируется отдельно от котировки)."""
    async with make_session() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        return await client.get_company_profile(symbol, session)


async def _fetch_news(symbol: str) -> list[dict]:
    """Свежие новости по тикеру Finnhub."""
    async with make_session() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        return await client.get_news(symbol, session)


async def _fetch_market_data(coin_id: str) -> dict:
    """Фундаментальные данные монеты CoinGecko."""
    async with make_session() as session:
        client = CoinGeckoClient(get_settings().coingecko_api_key)
        return await client.get_market_data(coin_id, session)


async def _fetch_price_history(coin_id: str) -> list[float]:
    """История цен монеты за 30 дней (для тренда 7д/30д)."""
    async with make_session() as session:
        client = CoinGeckoClient(get_settings().coingecko_api_key)
        return await client.get_price_history(coin_id, session)


def _trend_change(prices: list[float], days: int) -> float | None:
    """Изменение цены (%) от первой цены N дней назад до последней."""
    if len(prices) < 2:
        return None
    step = max(1, len(prices) // days)
    first = prices[-1 - step]
    last = prices[-1]
    if not first:
        return None
    return (last / first - 1) * 100


def _format_money(value: float | None) -> str:
    """Форматирует крупные суммы: 1.29T, 21.3B, 900M, 126.1K."""
    if not value:
        return "—"
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    if value >= 1e3:
        return f"${value / 1e3:.1f}K"
    return f"${value:,.0f}"


def _clean_text(text: str, limit: int) -> str:
    """Убирает лишние пробелы/переносы и обрезает текст."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned[:limit]


async def _stock_context(symbol: str, cache: TTLCache) -> str:
    """Контекст акции/индекса: котировка + профиль компании + свежие новости.

    Для индексов (SPX/DJI/VIX) Finnhub не отдаёт ^-тикеры — используются
    ETF-аналоги через resolve_stock_symbol (SPX→SPY и т.д.).
    Дополнительные данные не роняют анализ: при сбое остаётся котировка.
    """
    settings = get_settings()
    resolved = resolve_stock_symbol(symbol)
    quote = await fetch_stock(resolved)
    sign = "+" if quote.change_percent >= 0 else ""
    lines = [
        "Тип: акция/индекс",
        f"Символ: {symbol}",
        f"Цена: {quote.price:.4f}",
        f"Изменение за день: {sign}{quote.change_percent:.2f}%",
    ]

    profile: dict = {}
    news: list[dict] = []
    try:
        profile = await cache.get_or_set(
            f"stock:profile:{resolved}",
            lambda: _fetch_company_profile(resolved),
            settings.cache_ttl_fundamental_seconds,
        )
        news = await cache.get_or_set(
            f"stock:news:{resolved}",
            lambda: _fetch_news(resolved),
            settings.cache_ttl_fundamental_seconds,
        )
    except Exception:  # noqa: BLE001 — доп. данные не критичны
        log.warning("Не удалось получить профиль/новости для %s", symbol)

    if profile.get("name") or profile.get("finnhubIndustry"):
        lines.append(
            f"Компания: {profile.get('name') or '—'} "
            f"({profile.get('finnhubIndustry') or '—'})"
        )
    if profile.get("description"):
        lines.append(
            "Описание: " + _clean_text(profile["description"], MAX_DESCRIPTION_LENGTH)
        )
    headlines = [_clean_text(n.get("headline", ""), MAX_NEWS_LENGTH) for n in news]
    headlines = [h for h in headlines if h][:MAX_NEWS_ITEMS]
    if headlines:
        lines.append("Последние новости:")
        lines.extend(f"- {h}" for h in headlines)
    return "\n".join(lines)


async def _crypto_context(symbol: str, cache: TTLCache) -> str:
    """Контекст криптовалюты: котировка + капитализация + тренд 7д/30д."""
    settings = get_settings()
    quote = await fetch_crypto(symbol)
    sign = "+" if quote.change_percent >= 0 else ""
    lines = [
        "Тип: криптовалюта",
        f"Символ: {quote.symbol}",
        f"Цена: {quote.price:.4f}",
        f"Изменение за 24ч: {sign}{quote.change_percent:.2f}%",
    ]

    market: dict = {}
    history: list[float] = []
    try:
        coin_id = _gecko_id(symbol)
        market = await cache.get_or_set(
            f"crypto:market:{symbol}",
            lambda: _fetch_market_data(coin_id),
            settings.cache_ttl_fundamental_seconds,
        )
        history = await cache.get_or_set(
            f"crypto:chart:{symbol}",
            lambda: _fetch_price_history(coin_id),
            settings.cache_ttl_fundamental_seconds,
        )
    except Exception:  # noqa: BLE001 — доп. данные не критичны
        log.warning("Не удалось получить рыночные данные для %s", symbol)

    if market.get("name") or market.get("rank"):
        lines.append(
            f"Монета: {market.get('name') or '—'} (rank #{market.get('rank') or '—'})"
        )
    cap = _format_money(market.get("market_cap"))
    volume = _format_money(market.get("volume"))
    ath = _format_money(market.get("ath"))
    lines.append(f"Капитализация: {cap}, объём за 24ч: {volume}, ATH: {ath}")
    change_7d = _trend_change(history, 7)
    change_30d = _trend_change(history, 30)
    if change_7d is not None:
        lines.append(f"Тренд: 7д {change_7d:+.2f}%, 30д {change_30d:+.2f}%")
    if market.get("description"):
        lines.append(
            "Описание: " + _clean_text(market["description"], MAX_DESCRIPTION_LENGTH)
        )
    return "\n".join(lines)


def _gecko_id(symbol: str) -> str:
    """id монеты в CoinGecko по символу (согласовано с COINS в crypto.py)."""
    from src.handlers.crypto import COINS

    return COINS.get(symbol.upper(), symbol.lower())


async def _market_context(symbol: str, cache: TTLCache) -> str:
    """Контекст актива по известному символу подменю AI-анализа."""
    if ANALYSE_TYPES.get(symbol.upper()) == "crypto":
        return await _crypto_context(symbol, cache)
    return await _stock_context(symbol, cache)


async def _detect_context(token: str, cache: TTLCache) -> str | None:
    """Распознаёт актив по токену; None — это не актив (текстовый запрос).

    Для неизвестных тикеров пробуем акцию, затем монету; LookupError
    означает «такого актива нет» и не считается ошибкой.
    """
    upper = token.upper()
    if upper in ANALYSE_TYPES:
        return await _market_context(upper, cache)
    if TICKER_RE.match(upper):
        try:
            return await _stock_context(upper, cache)
        except LookupError:
            pass
    if COIN_RE.match(upper):
        try:
            return await _crypto_context(upper, cache)
        except LookupError:
            pass
    return None


@router.message(Command("analyze"))
async def cmd_analyze(message: Message, bot: Bot, cache: TTLCache) -> None:
    """AI-анализ: /analyze BTC, /analyze BTC стоит ли покупать, /analyze вопрос."""
    raw = message.text.split(maxsplit=1)
    if len(raw) < 2:
        await message.answer(
            "🤖 Напиши запрос, например:\n"
            "/analyze BTC — анализ монеты\n"
            "/analyze стоит ли покупать BTC — вопрос про рынок\n"
            "или выбери актив в меню AI-анализ"
        )
        return

    parts = raw[1].strip().split(maxsplit=1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    context = None
    try:
        context = await _detect_context(first, cache)
    except Exception:  # noqa: BLE001 — внешний API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить данные о активе.")
        return
    if context is not None:
        query = sanitize_user_text(rest) or f"Проанализируй актив {first.upper()}."
    else:
        query = sanitize_user_text(raw[1])
    if not query or not QUERY_RE.match(query):
        await message.answer("Некорректный запрос. Опиши вопрос проще.")
        return
    await _run_analysis(bot, message.chat.id, message.answer, query, context)


@router.callback_query(F.data.regexp(r"^analyse:[A-Z]+$"))
async def on_analyse(callback: CallbackQuery, bot: Bot, cache: TTLCache) -> None:
    """Анализ тикера из подменю AI-анализа."""
    symbol = callback.data.split(":", 1)[1]
    if symbol not in ANALYSE_TYPES:
        await callback.answer("Неизвестный актив. 🙈")
        return
    await callback.answer()
    try:
        context = await _market_context(symbol, cache)
    except Exception:  # noqa: BLE001 — внешний API, ошибка уже залогирована
        await callback.message.answer("😔 Не удалось получить данные о активе.")
        return
    query = f"Проанализируй актив {symbol}."
    await _run_analysis(
        bot, callback.message.chat.id, callback.message.answer, query, context
    )


async def _run_analysis(
    bot: Bot,
    chat_id: int,
    send_text: SendText,
    query: str,
    context: str | None,
) -> None:
    """Отправляет запрос в LLM с индикатором «печатает…»."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        await send_text(
            "🤖 AI-агент ещё не настроен: добавь OPENROUTER_API_KEY в .env."
        )
        return

    typing_task = asyncio.create_task(_typing_loop(bot, chat_id))
    try:
        if context is None:
            context = "Запрос пользователя: " + query[:300]
        client = LLMClient(
            settings.openrouter_api_key,
            max_tokens=settings.openrouter_max_tokens,
        )
        result = await client.analyze(query, context)
    except Exception:
        log.exception("AI-анализ не удался")
        await send_text("😔 AI не ответил. Попробуй позже или напиши проще.")
        return
    finally:
        typing_task.cancel()
    await send_text(f"🤖 <b>Анализ</b>\n\n{markdown_to_html(result)}{AI_DISCLAIMER}")


async def _typing_loop(bot: Bot, chat_id: int) -> None:
    """Показывает «печатает…», пока LLM думает (5 сек — лимит Telegram)."""
    try:
        while True:
            await bot.send_chat_action(chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass
