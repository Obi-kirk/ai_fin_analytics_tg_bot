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
from src.handlers.stock import TICKER_RE, fetch_stock
from src.services.llm_service import (
    LLMClient,
    markdown_to_html,
    sanitize_user_text,
)

log = logging.getLogger(__name__)
router = Router()

# Символы подменю AI-анализа (из menu.py) и их формат
ANALYSE_TYPES = {
    "BTC": "crypto",
    "ETH": "crypto",
    "AAPL": "stock",
    "TSLA": "stock",
    "NVDA": "stock",
}

QUERY_RE = re.compile(r"^[\w\s.,!?()%$€¥£+-]{1,500}$")

AI_DISCLAIMER = "\n\n— <i>Это не инвестиционная рекомендация.</i>"

SendText = Callable[[str], Awaitable[None]]


async def _stock_context(symbol: str) -> str:
    """Контекст акции/индекса из Finnhub для промпта."""
    quote = await fetch_stock(symbol.upper())
    sign = "+" if quote.change_percent >= 0 else ""
    return (
        "Тип: акция/индекс\n"
        f"Символ: {quote.symbol}\n"
        f"Цена: {quote.price:.4f}\n"
        f"Изменение за день: {sign}{quote.change_percent:.2f}%"
    )


async def _crypto_context(symbol: str) -> str:
    """Контекст криптовалюты из CoinGecko для промпта."""
    quote = await fetch_crypto(symbol.upper())
    sign = "+" if quote.change_percent >= 0 else ""
    return (
        "Тип: криптовалюта\n"
        f"Символ: {quote.symbol}\n"
        f"Цена: {quote.price:.4f}\n"
        f"Изменение за день: {sign}{quote.change_percent:.2f}%"
    )


async def _market_context(symbol: str) -> str:
    """Контекст актива по известному символу подменю AI-анализа."""
    if ANALYSE_TYPES.get(symbol.upper()) == "crypto":
        return await _crypto_context(symbol)
    return await _stock_context(symbol)


async def _detect_context(token: str) -> str | None:
    """Распознаёт актив по токену; None — это не актив (текстовый запрос).

    Для неизвестных тикеров пробуем акцию, затем монету; LookupError
    означает «такого актива нет» и не считается ошибкой.
    """
    upper = token.upper()
    if upper in ANALYSE_TYPES:
        return await _market_context(upper)
    if TICKER_RE.match(upper):
        try:
            return await _stock_context(upper)
        except LookupError:
            pass
    if COIN_RE.match(upper):
        try:
            return await _crypto_context(upper)
        except LookupError:
            pass
    return None


@router.message(Command("analyze"))
async def cmd_analyze(message: Message, bot: Bot) -> None:
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
        context = await _detect_context(first)
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
async def on_analyse(callback: CallbackQuery, bot: Bot) -> None:
    """Анализ тикера из подменю AI-анализа."""
    symbol = callback.data.split(":", 1)[1]
    if symbol not in ANALYSE_TYPES:
        await callback.answer("Неизвестный актив. 🙈")
        return
    await callback.answer()
    try:
        context = await _market_context(symbol)
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
