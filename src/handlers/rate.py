"""Обработчик команд /rate и /convert — валюты ЦБ РФ с кэшированием."""

import logging
import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config.settings import get_settings
from src.services.cache import TTLCache
from src.services.financial_api import (
    CBR_CURRENCIES,
    CBRClient,
    FxQuote,
    make_session,
)

log = logging.getLogger(__name__)
router = Router()

# Рубль — базовая валюта ЦБ РФ, курса в XML нет
_FX_RUB = FxQuote(code="RUB", name="Российский рубль", value=1.0, nominal=1)

_CONVERT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s+([A-Za-z]{3})\s+([A-Za-z]{3})\s*$")


def parse_convert_args(text: str) -> tuple[float, str, str] | None:
    """Разбирает аргументы /convert: «100 USD RUB» -> (100.0, 'USD', 'RUB')."""
    match = _CONVERT_RE.match(text)
    if not match:
        return None
    amount = float(match.group(1).replace(",", "."))
    return amount, match.group(2).upper(), match.group(3).upper()


def convert_amount(amount: float, from_value: float, to_value: float) -> float:
    """Конвертирует сумму: amount единиц «из» в единицы «в» (курсы за 1 шт к руб.)."""
    if to_value <= 0:
        raise ValueError("Курс целевой валюты не может быть нулевым")
    return amount * from_value / to_value


async def fetch_fx(code: str) -> FxQuote:
    """Получает курс из ЦБ РФ через отдельный HTTP-сеанс (RUB = 1.0)."""
    code = code.upper()
    if code == "RUB":
        return _FX_RUB
    if code not in CBR_CURRENCIES:
        raise ValueError(f"Валюта {code} не поддерживается")
    async with make_session() as session:
        client = CBRClient()
        try:
            return await client.get_quote(code, session)
        except Exception:
            log.exception("Не удалось получить курс %s от ЦБ РФ", code)
            raise


async def _get_fx(code: str, cache: TTLCache) -> FxQuote:
    """Курс валюты с кэшем (RUB не кэшируется — константа)."""
    if code == "RUB":
        return _FX_RUB
    settings = get_settings()
    return await cache.get_or_set(
        f"fx:{code}", lambda: fetch_fx(code), settings.cache_ttl_fx_seconds
    )


def _format_money(value: float) -> str:
    """Деньги: без нулей после запятой, иначе до 2 знаков."""
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:,.2f}"


@router.message(Command("convert"))
async def cmd_convert(message: Message, cache: TTLCache) -> None:
    """Конвертирует валюты: /convert 100 USD RUB."""
    args = parse_convert_args(message.text.partition(" ")[2])
    if args is None:
        await message.answer(
            "Формат: /convert <сумма> <из> <в>\n"
            "Пример: /convert 100 USD RUB\n"
            f"Доступны: RUB, {', '.join(sorted(CBR_CURRENCIES))}"
        )
        return
    amount, from_code, to_code = args

    try:
        from_quote = await _get_fx(from_code, cache)
        to_quote = await _get_fx(to_code, cache)
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить курс от ЦБ РФ. Попробуй позже.")
        return

    result = convert_amount(amount, from_quote.value, to_quote.value)
    await message.answer(
        f"💱 <b>{_format_money(amount)} {from_code}</b> = "
        f"<b>{_format_money(result)} {to_code}</b>\n\nИсточник: ЦБ РФ"
    )


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
            key, lambda: fetch_fx(code), settings.cache_ttl_fx_seconds
        )
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить курс от ЦБ РФ. Попробуй позже.")
        return
    await message.answer(format_fx(quote))


def format_fx(quote: FxQuote) -> str:
    """Форматирует курс валюты для Telegram (HTML)."""
    suffix = f" за {quote.nominal}" if quote.nominal != 1 else ""
    return (
        f"💱 <b>{quote.name}</b> ({quote.code})\n"
        f"Курс: <b>{quote.value:.2f} ₽</b>{suffix}\n\nИсточник: ЦБ РФ"
    )
