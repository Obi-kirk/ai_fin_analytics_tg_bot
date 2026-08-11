"""Обработчик команды /rate — курс валюты от ЦБ РФ с кэшированием."""

import logging

import aiohttp
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config.settings import get_settings
from src.services.cache import TTLCache
from src.services.financial_api import CBR_CURRENCIES, CBRClient, FxQuote

log = logging.getLogger(__name__)
router = Router()


@router.message(Command("rate"))
async def cmd_rate(message: Message, cache: TTLCache) -> None:
    """Показывает курс валюты, например: /rate USD."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Укажи валюту, например: /rate USD\n"
            f"Поддерживаются: {', '.join(sorted(CBR_CURRENCIES))}"
        )
        return
    code = args[1].strip().upper()
    if code not in CBR_CURRENCIES:
        await message.answer(
            f"Валюта {code} не поддерживается. "
            f"Доступны: {', '.join(sorted(CBR_CURRENCIES))}"
        )
        return

    settings = get_settings()
    key = f"fx:{code}"
    try:
        quote: FxQuote = await cache.get_or_set(
            key,
            lambda: _fetch_quote(code),
            settings.cache_ttl_fx_seconds,
        )
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить курс от ЦБ РФ. Попробуй позже.")
        return
    await message.answer(
        f"💱 <b>{quote.name}</b> ({quote.code})\n"
        f"Курс: <b>{quote.value:.2f} ₽</b>"
        + (f" за {quote.nominal}" if quote.nominal != 1 else "")
        + "\n\nИсточник: ЦБ РФ"
    )


async def _fetch_quote(code: str) -> FxQuote:
    """Получает курс из ЦБ РФ через отдельный HTTP-сеанс."""
    async with aiohttp.ClientSession() as session:
        client = CBRClient()
        try:
            return await client.get_quote(code, session)
        except Exception:
            log.exception("Не удалось получить курс %s от ЦБ РФ", code)
            raise
