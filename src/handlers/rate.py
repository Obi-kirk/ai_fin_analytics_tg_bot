"""Обработчик команд /rate и /convert — валюты ЦБ РФ с кэшированием.

/convert (и кнопка «💱 Перевод валют») работает через FSM-диалог:
сумма → валюта «из» → валюта «в» → результат.
"""

import asyncio
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config.settings import get_settings
from src.handlers.crypto import fetch_crypto
from src.services.cache import TTLCache
from src.services.financial_api import (
    CBR_CURRENCIES,
    CBRClient,
    FxQuote,
    StockQuote,
    make_session,
)

log = logging.getLogger(__name__)
router = Router()

# Рубль — базовая валюта ЦБ РФ, курса в XML нет
_FX_RUB = FxQuote(code="RUB", name="Российский рубль", value=1.0, nominal=1)

# Валюты для выбора в диалоге конвертации (порядок кнопок)
CONVERT_CURRENCIES = (
    "RUB",
    "USD",
    "EUR",
    "CNY",
    "AED",
    "VND",
    "THB",
    "TRY",
    "GBP",
    "JPY",
)

# Криптовалюты в конвертере (курс через USD/CoinGecko)
CONVERT_CRYPTO = ("BTC", "ETH", "SOL", "XRP")

# Все доступные для конвертации активы (фиат + крипта)
CONVERT_OPTIONS = CONVERT_CURRENCIES + CONVERT_CRYPTO

# Готовые суммы на первом шаге диалога
_AMOUNT_PRESETS = (100, 200, 500, 1000, 5000)

_CONVERT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s+([A-Za-z]{3})\s+([A-Za-z]{3})\s*$")

_AMOUNT_MAX = 1_000_000_000


class ConvertState(StatesGroup):
    """Шаги диалога конвертации валют."""

    amount = State()
    from_code = State()
    to_code = State()


def currency_kb(prefix: str, exclude: str | None = None) -> InlineKeyboardMarkup:
    """Кнопки выбора актива (шаг «из»/«в») + кнопка «Отмена»."""
    builder = InlineKeyboardBuilder()
    codes = [c for c in CONVERT_OPTIONS if c != exclude]
    for i in range(0, len(codes), 3):
        builder.row(
            *[
                InlineKeyboardButton(text=c, callback_data=f"{prefix}:{c}")
                for c in codes[i : i + 3]
            ]
        )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="conv:cancel"))
    return builder.as_markup()


async def _ask_from(callback: CallbackQuery, state: FSMContext) -> None:
    """Спрашивает валюту «из» (после ввода суммы)."""
    await state.set_state(ConvertState.from_code)
    await callback.message.edit_text(
        "Из какой валюты переводим?", reply_markup=currency_kb("cvfrom")
    )
    await callback.answer()


def amount_kb() -> InlineKeyboardMarkup:
    """Готовые суммы + отмена на первом шаге диалога."""
    builder = InlineKeyboardBuilder()
    builder.row(
        *[
            InlineKeyboardButton(text=str(n), callback_data=f"conv:amount:{n}")
            for n in _AMOUNT_PRESETS
        ]
    )
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="conv:cancel"))
    return builder.as_markup()


async def _start_dialog(callback: CallbackQuery, state: FSMContext) -> None:
    """Начало диалога: просим выбрать актив «из»."""
    await state.clear()
    await state.set_state(ConvertState.from_code)
    await callback.message.edit_text(
        "💱 <b>Конвертация</b>\n\nИз какого актива переводим?",
        reply_markup=currency_kb("cvfrom"),
    )
    await callback.answer()


@router.callback_query(F.data == "conv:start")
async def on_conv_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Начало диалога: просим ввести сумму."""
    await _start_dialog(callback, state)


async def _do_convert(
    from_code: str, to_code: str, amount: float, cache: TTLCache
) -> str | None:
    """Считает конвертацию; None при ошибке получения курсов."""
    try:
        from_rate = await _get_convert_rate(from_code, cache)
        to_rate = await _get_convert_rate(to_code, cache)
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        log.warning("Не удалось получить курсы %s/%s", from_code, to_code)
        return None
    return format_convert(amount, from_code, to_code, from_rate, to_rate)


def _retry_kb() -> InlineKeyboardMarkup:
    """Кнопка «Ещё раз» при ошибке получения курсов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💱 Ещё раз", callback_data="conv:start")]
        ]
    )


@router.callback_query(F.data.regexp(r"^conv:amount:\d+$"))
async def on_conv_amount_preset(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Принимает готовую сумму и показывает результат конвертации."""
    amount = float(callback.data.split(":", 2)[2])
    data = await state.get_data()
    from_code = data.get("from_code")
    to_code = data.get("to_code")
    await state.clear()
    if not from_code or not to_code:
        await callback.message.edit_text("Диалог устарел, начни заново.")
        await callback.answer()
        return
    text = await _do_convert(from_code, to_code, amount, cache)
    if text is None:
        await callback.message.edit_text(
            "😔 Не удалось получить курсы. Попробуй позже.", reply_markup=_retry_kb()
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        text, reply_markup=convert_kb(amount, from_code, to_code)
    )
    await callback.answer()


@router.message(ConvertState.amount)
async def on_convert_amount(
    message: Message, state: FSMContext, cache: TTLCache
) -> None:
    """Принимает сумму и показывает результат конвертации."""
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Это не похоже на число. Попробуй ещё раз.")
        return
    if not 0 < amount <= _AMOUNT_MAX:
        await message.answer("Сумма должна быть больше нуля и не слишком большой.")
        return
    data = await state.get_data()
    from_code = data.get("from_code")
    to_code = data.get("to_code")
    await state.clear()
    if not from_code or not to_code:
        await message.answer("Диалог устарел, начни заново: /convert")
        return
    text = await _do_convert(from_code, to_code, amount, cache)
    if text is None:
        await message.answer("😔 Не удалось получить курсы. Попробуй позже.")
        return
    await message.answer(text, reply_markup=convert_kb(amount, from_code, to_code))


@router.callback_query(F.data.regexp(r"^cvfrom:[A-Z]{3}$"))
async def on_conv_from(callback: CallbackQuery, state: FSMContext) -> None:
    """Принимает валюту «из» и спрашивает валюту «в»."""
    from_code = callback.data.split(":", 1)[1]
    if from_code not in CONVERT_OPTIONS:
        await callback.answer("Актив не поддерживается.")
        return
    await state.update_data(from_code=from_code)
    await state.set_state(ConvertState.to_code)
    await callback.message.edit_text(
        f"Из <b>{from_code}</b>. В какую валюту переводим?",
        reply_markup=currency_kb("cvto", exclude=from_code),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^cvto:[A-Z]{3}$"))
async def on_conv_to(callback: CallbackQuery, state: FSMContext) -> None:
    """Принимает валюту «в» и просит сумму."""
    to_code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    from_code: str | None = data.get("from_code")
    if not from_code:
        await callback.message.edit_text("Диалог устарел, начни заново.")
        await callback.answer()
        return
    await state.update_data(to_code=to_code)
    await state.set_state(ConvertState.amount)
    await callback.message.edit_text(
        f"<b>{from_code} → {to_code}</b>. Введи сумму или выбери готовую:",
        reply_markup=amount_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "conv:cancel")
async def on_conv_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отменяет диалог перевода валют."""
    await state.clear()
    await callback.message.edit_text("❌ Перевод отменён.")
    await callback.answer()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отменяет активный диалог (/convert)."""
    current = await state.get_state()
    if current is None:
        await message.answer("Нет активного диалога.")
        return
    await state.clear()
    await message.answer("❌ Отменено.")


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


async def _get_crypto_quote(code: str, cache: TTLCache) -> StockQuote:
    """Котировка криптовалюты с кэшем (USD)."""
    settings = get_settings()
    return await cache.get_or_set(
        f"crypto:{code}",
        lambda: fetch_crypto(code),
        settings.cache_ttl_stock_seconds,
    )


async def _get_convert_rate(code: str, cache: TTLCache) -> float:
    """Курс в рублях за 1 единицу актива.

    Валюты ЦБ — напрямую (с учётом номинала), крипта — через цену в USD
    и курс доллара ЦБ. Все активы конвертируются через общую базу — рубль.
    """
    if code in CONVERT_CRYPTO:
        quote = await _get_crypto_quote(code, cache)
        usd = await _get_fx("USD", cache)
        return quote.price * usd.value / usd.nominal
    quote = await _get_fx(code, cache)
    return quote.value / quote.nominal


def _format_money(value: float) -> str:
    """Деньги: крупные — без копеек, обычные — до 2 знаков,
    мелкие (крипта) — до 6 значащих.
    """
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def format_convert(
    amount: float, from_code: str, to_code: str, from_value: float, to_value: float
) -> str:
    """Форматирует результат конвертации для Telegram (HTML)."""
    result = convert_amount(amount, from_value, to_value)
    return (
        f"💱 <b>{_format_money(amount)} {from_code}</b> = "
        f"<b>{_format_money(result)} {to_code}</b>\n\nИсточник: ЦБ РФ, CoinGecko"
    )


def convert_kb(amount: float, from_code: str, to_code: str) -> InlineKeyboardMarkup:
    """Кнопки под результатом: «Поменять», «Ещё раз» и возврат в подменю валют."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Поменять",
                    callback_data=f"conv:swap|{amount}|{to_code}|{from_code}",
                ),
                InlineKeyboardButton(text="💱 Ещё раз", callback_data="conv:start"),
            ],
            [
                InlineKeyboardButton(text="↩️ Меню", callback_data="submenu:fx"),
            ],
        ]
    )


@router.callback_query(F.data.regexp(r"^conv:swap\|[\d.]+\|[A-Z]{3}\|[A-Z]{3}$"))
async def on_conv_swap(callback: CallbackQuery, cache: TTLCache) -> None:
    """Пересчитывает конвертацию с теми же числами, поменяв валюты местами."""
    _, raw_amount, from_code, to_code = callback.data.split("|")
    amount = float(raw_amount)
    try:
        from_rate = await _get_convert_rate(from_code, cache)
        to_rate = await _get_convert_rate(to_code, cache)
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await callback.answer("😔 Не удалось получить курсы. Попробуй позже.")
        return
    await callback.message.edit_text(
        format_convert(amount, from_code, to_code, from_rate, to_rate),
        reply_markup=convert_kb(amount, from_code, to_code),
    )
    await callback.answer()


@router.message(Command("convert"))
async def cmd_convert(message: Message, state: FSMContext, cache: TTLCache) -> None:
    """Конвертирует валюты: /convert 100 USD RUB или диалогом через кнопку."""
    args = parse_convert_args(message.text.partition(" ")[2])
    if args is None:
        await state.clear()
        await state.set_state(ConvertState.from_code)
        await message.answer(
            "💱 <b>Конвертация</b>\n\nИз какого актива переводим?\n"
            "Или одной командой: /convert 100 USD RUB",
            reply_markup=currency_kb("cvfrom"),
        )
        return
    amount, from_code, to_code = args
    if from_code not in CONVERT_OPTIONS or to_code not in CONVERT_OPTIONS:
        await message.answer(f"Доступны: {', '.join(sorted(CONVERT_OPTIONS))}")
        return
    try:
        from_rate = await _get_convert_rate(from_code, cache)
        to_rate = await _get_convert_rate(to_code, cache)
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await message.answer("😔 Не удалось получить курсы. Попробуй позже.")
        return
    await message.answer(format_convert(amount, from_code, to_code, from_rate, to_rate))


@router.message(Command("rate"))
async def cmd_rate(message: Message, cache: TTLCache) -> None:
    """Показывает курс валюты: /rate USD; без аргумента — все валюты ЦБ."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await _send_all_rates(message, cache)
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


def _fx_short_line(quote: FxQuote) -> str:
    """Компактная строка курса валюты: «USD — 88.50 ₽» (за номинал ЦБ)."""
    rate = quote.value * quote.nominal
    suffix = f" за {quote.nominal}" if quote.nominal != 1 else ""
    return f"{quote.code} — {rate:.2f} ₽{suffix}"


async def _send_all_rates(message: Message, cache: TTLCache) -> None:
    """Показывает курсы всех валют ЦБ одним сообщением."""
    quotes = await asyncio.gather(
        *[fetch_fx(code) for code in CBR_CURRENCIES],
        return_exceptions=True,
    )
    lines = ["💱 <b>Курсы ЦБ РФ</b>\n"]
    for code, quote in zip(sorted(CBR_CURRENCIES), quotes):
        if isinstance(quote, Exception):
            log.warning("Не удалось получить курс %s от ЦБ РФ", code)
            continue
        lines.append(_fx_short_line(quote))
    if len(lines) == 1:
        await message.answer("😔 Не удалось получить курсы. Попробуй позже.")
        return
    lines.append("\nПодробнее: /rate USD")
    await message.answer("\n".join(lines))


def format_fx(quote: FxQuote) -> str:
    """Форматирует курс валюты для Telegram (HTML).

    ЦБ отдаёт value за 1 единицу; показываем курс за номинал
    (для JPY — «52.08 ₽ за 100», как принято в банковских таблицах).
    """
    rate = quote.value * quote.nominal
    suffix = f" за {quote.nominal}" if quote.nominal != 1 else ""
    return (
        f"💱 <b>{quote.name}</b> ({quote.code})\n"
        f"Курс: <b>{rate:.2f} ₽</b>{suffix}\n\nИсточник: ЦБ РФ"
    )
