"""Handlers for the /rate and /convert commands — CBR RF currencies with caching.

/convert (and the "Convert" button) works via an FSM dialog:
amount → currency "from" → currency "to" → result.
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
from src.i18n import t
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

# The ruble is the CBR base currency; its rate is not in the XML
_FX_RUB = FxQuote(code="RUB", name="RUB", value=1.0, nominal=1)

# Currencies for the conversion dialog (button order)
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

# Cryptocurrencies in the converter (rate via USD/CoinGecko)
CONVERT_CRYPTO = ("BTC", "ETH", "SOL", "XRP")

# All assets available for conversion (fiat + crypto)
CONVERT_OPTIONS = CONVERT_CURRENCIES + CONVERT_CRYPTO

# Ready-made amounts for the first dialog step
_AMOUNT_PRESETS = (100, 200, 500, 1000, 5000)

_CONVERT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s+([A-Za-z]{3})\s+([A-Za-z]{3})\s*$")

_AMOUNT_MAX = 1_000_000_000


class ConvertState(StatesGroup):
    """Steps of the currency conversion dialog."""

    amount = State()
    from_code = State()
    to_code = State()


def currency_kb(prefix: str, exclude: str | None = None) -> InlineKeyboardMarkup:
    """Asset selection buttons (step "from"/"to") + a "Cancel" button."""
    builder = InlineKeyboardBuilder()
    codes = [c for c in CONVERT_OPTIONS if c != exclude]
    for i in range(0, len(codes), 3):
        builder.row(
            *[
                InlineKeyboardButton(text=c, callback_data=f"{prefix}:{c}")
                for c in codes[i : i + 3]
            ]
        )
    builder.row(
        InlineKeyboardButton(text=t("convert.btn.cancel"), callback_data="conv:cancel")
    )
    return builder.as_markup()


async def _ask_from(callback: CallbackQuery, state: FSMContext) -> None:
    """Asks for the currency "from" (after entering the amount)."""
    await state.set_state(ConvertState.from_code)
    await callback.message.edit_text(
        t("convert.ask_from"), reply_markup=currency_kb("cvfrom")
    )
    await callback.answer()


def amount_kb() -> InlineKeyboardMarkup:
    """Ready-made amounts + cancel on the first dialog step."""
    builder = InlineKeyboardBuilder()
    builder.row(
        *[
            InlineKeyboardButton(text=str(n), callback_data=f"conv:amount:{n}")
            for n in _AMOUNT_PRESETS
        ]
    )
    builder.row(
        InlineKeyboardButton(text=t("convert.btn.cancel"), callback_data="conv:cancel")
    )
    return builder.as_markup()


async def _start_dialog(callback: CallbackQuery, state: FSMContext) -> None:
    """Dialog start: asks to choose the currency "from"."""
    await state.clear()
    await state.set_state(ConvertState.from_code)
    await callback.message.edit_text(
        t("convert.start"), reply_markup=currency_kb("cvfrom")
    )
    await callback.answer()


@router.callback_query(F.data == "conv:start")
async def on_conv_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Dialog start: asks to enter the amount."""
    await _start_dialog(callback, state)


async def _do_convert(
    from_code: str, to_code: str, amount: float, cache: TTLCache
) -> str | None:
    """Computes the conversion; None on a rate fetch error."""
    try:
        from_rate = await _get_convert_rate(from_code, cache)
        to_rate = await _get_convert_rate(to_code, cache)
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        log.warning("Failed to fetch rates %s/%s", from_code, to_code)
        return None
    return format_convert(amount, from_code, to_code, from_rate, to_rate)


def _retry_kb() -> InlineKeyboardMarkup:
    """A "Retry" button shown on rate fetch errors."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("convert.btn.retry"), callback_data="conv:start"
                )
            ]
        ]
    )


@router.callback_query(F.data.regexp(r"^conv:amount:\d+$"))
async def on_conv_amount_preset(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Accepts a ready amount and shows the conversion result."""
    amount = float(callback.data.split(":", 2)[2])
    data = await state.get_data()
    from_code = data.get("from_code")
    to_code = data.get("to_code")
    await state.clear()
    if not from_code or not to_code:
        await callback.message.edit_text(t("convert.stale"))
        await callback.answer()
        return
    text = await _do_convert(from_code, to_code, amount, cache)
    if text is None:
        await callback.message.edit_text(
            t("convert.fetch_failed"), reply_markup=_retry_kb()
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
    """Accepts the amount and shows the conversion result."""
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer(t("convert.bad_number"))
        return
    if not 0 < amount <= _AMOUNT_MAX:
        await message.answer(t("convert.bad_amount"))
        return
    data = await state.get_data()
    from_code = data.get("from_code")
    to_code = data.get("to_code")
    await state.clear()
    if not from_code or not to_code:
        await message.answer(t("convert.stale_cmd"))
        return
    text = await _do_convert(from_code, to_code, amount, cache)
    if text is None:
        await message.answer(t("convert.fetch_failed"))
        return
    await message.answer(text, reply_markup=convert_kb(amount, from_code, to_code))


@router.callback_query(F.data.regexp(r"^cvfrom:[A-Z]{3}$"))
async def on_conv_from(callback: CallbackQuery, state: FSMContext) -> None:
    """Accepts the currency "from" and asks for the currency "to"."""
    from_code = callback.data.split(":", 1)[1]
    if from_code not in CONVERT_OPTIONS:
        await callback.answer(t("convert.unsupported"))
        return
    await state.update_data(from_code=from_code)
    await state.set_state(ConvertState.to_code)
    await callback.message.edit_text(
        t("convert.from_to", code=from_code),
        reply_markup=currency_kb("cvto", exclude=from_code),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^cvto:[A-Z]{3}$"))
async def on_conv_to(callback: CallbackQuery, state: FSMContext) -> None:
    """Accepts the currency "to" and asks for the amount."""
    to_code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    from_code: str | None = data.get("from_code")
    if not from_code:
        await callback.message.edit_text(t("convert.stale"))
        await callback.answer()
        return
    await state.update_data(to_code=to_code)
    await state.set_state(ConvertState.amount)
    await callback.message.edit_text(
        t("convert.ask_amount", f=from_code, t=to_code), reply_markup=amount_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "conv:cancel")
async def on_conv_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancels the currency conversion dialog."""
    await state.clear()
    await callback.message.edit_text(t("convert.cancelled"))
    await callback.answer()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Cancels the active dialog (/convert)."""
    current = await state.get_state()
    if current is None:
        await message.answer(t("convert.no_dialog"))
        return
    await state.clear()
    await message.answer(t("convert.cancelled_short"))


def parse_convert_args(text: str) -> tuple[float, str, str] | None:
    """Parses /convert arguments: "100 USD RUB" -> (100.0, 'USD', 'RUB')."""
    match = _CONVERT_RE.match(text)
    if not match:
        return None
    amount = float(match.group(1).replace(",", "."))
    return amount, match.group(2).upper(), match.group(3).upper()


def convert_amount(amount: float, from_value: float, to_value: float) -> float:
    """Converts the amount: `amount` units of "from" into units of "to" (rates per unit in RUB)."""
    if to_value <= 0:
        raise ValueError("The target currency rate must not be zero")
    return amount * from_value / to_value


async def fetch_fx(code: str) -> FxQuote:
    """Fetches the rate from the CBR via a separate HTTP session (RUB = 1.0)."""
    code = code.upper()
    if code == "RUB":
        return _FX_RUB
    if code not in CBR_CURRENCIES:
        raise ValueError(f"Currency {code} is not supported")
    async with make_session() as session:
        client = CBRClient()
        try:
            return await client.get_quote(code, session)
        except Exception:
            log.exception("Failed to fetch rate %s from the CBR", code)
            raise


async def _get_fx(code: str, cache: TTLCache) -> FxQuote:
    """Currency rate with cache (RUB is not cached — it is a constant)."""
    if code == "RUB":
        return _FX_RUB
    settings = get_settings()
    return await cache.get_or_set(
        f"fx:{code}", lambda: fetch_fx(code), settings.cache_ttl_fx_seconds
    )


async def _get_crypto_quote(code: str, cache: TTLCache) -> StockQuote:
    """Cryptocurrency quote with cache (USD)."""
    settings = get_settings()
    return await cache.get_or_set(
        f"crypto:{code}",
        lambda: fetch_crypto(code),
        settings.cache_ttl_stock_seconds,
    )


async def _get_convert_rate(code: str, cache: TTLCache) -> float:
    """Rate in rubles per 1 unit of the asset.

    CBR currencies — directly (taking the nominal into account), crypto — via
    the USD price and the CBR dollar rate. All assets are converted through
    a common base — the ruble.
    """
    if code in CONVERT_CRYPTO:
        quote = await _get_crypto_quote(code, cache)
        usd = await _get_fx("USD", cache)
        return quote.price * usd.value / usd.nominal
    quote = await _get_fx(code, cache)
    return quote.value / quote.nominal


def _format_money(value: float) -> str:
    """Money: large values without kopecks, ordinary ones up to 2 digits,
    small (crypto) ones up to 6 significant digits.
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
    """Formats the conversion result for Telegram (HTML)."""
    result = convert_amount(amount, from_value, to_value)
    return t(
        "convert.result",
        amount=_format_money(amount),
        from_code=from_code,
        result=_format_money(result),
        to_code=to_code,
    )


def convert_kb(amount: float, from_code: str, to_code: str) -> InlineKeyboardMarkup:
    """Buttons under the result: "Swap", "Retry" and back to the currency submenu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("convert.btn.swap"),
                    callback_data=f"conv:swap|{amount}|{to_code}|{from_code}",
                ),
                InlineKeyboardButton(
                    text=t("convert.btn.retry"), callback_data="conv:start"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("convert.btn.back"), callback_data="submenu:fx"
                ),
            ],
        ]
    )


@router.callback_query(F.data.regexp(r"^conv:swap\|[\d.]+\|[A-Z]{3}\|[A-Z]{3}$"))
async def on_conv_swap(callback: CallbackQuery, cache: TTLCache) -> None:
    """Recalculates the conversion with the same numbers, swapping the currencies."""
    _, raw_amount, from_code, to_code = callback.data.split("|")
    amount = float(raw_amount)
    try:
        from_rate = await _get_convert_rate(from_code, cache)
        to_rate = await _get_convert_rate(to_code, cache)
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await callback.answer(t("convert.fetch_failed"))
        return
    await callback.message.edit_text(
        format_convert(amount, from_code, to_code, from_rate, to_rate),
        reply_markup=convert_kb(amount, from_code, to_code),
    )
    await callback.answer()


@router.message(Command("convert"))
async def cmd_convert(message: Message, state: FSMContext, cache: TTLCache) -> None:
    """Converts currencies: /convert 100 USD RUB or via a button dialog."""
    args = parse_convert_args(message.text.partition(" ")[2])
    if args is None:
        await state.clear()
        await state.set_state(ConvertState.from_code)
        await message.answer(
            t("convert.start_hint"), reply_markup=currency_kb("cvfrom")
        )
        return
    amount, from_code, to_code = args
    if from_code not in CONVERT_OPTIONS or to_code not in CONVERT_OPTIONS:
        await message.answer(
            t("convert.available", assets=", ".join(sorted(CONVERT_OPTIONS)))
        )
        return
    try:
        from_rate = await _get_convert_rate(from_code, cache)
        to_rate = await _get_convert_rate(to_code, cache)
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await message.answer(t("convert.fetch_failed"))
        return
    await message.answer(format_convert(amount, from_code, to_code, from_rate, to_rate))


@router.message(Command("rate"))
async def cmd_rate(message: Message, cache: TTLCache) -> None:
    """Shows a currency rate: /rate USD; without arguments — all CBR currencies."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await _send_all_rates(message, cache)
        return
    code = args[1].strip().upper()
    if code not in CBR_CURRENCIES:
        await message.answer(
            t(
                "fx.not_supported",
                code=code,
                currencies=", ".join(sorted(CBR_CURRENCIES)),
            )
        )
        return

    settings = get_settings()
    key = f"fx:{code}"
    try:
        quote: FxQuote = await cache.get_or_set(
            key, lambda: fetch_fx(code), settings.cache_ttl_fx_seconds
        )
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await message.answer(t("fx.fetch_failed"))
        return
    await message.answer(format_fx(quote))


def _fx_short_line(quote: FxQuote) -> str:
    """Compact one-line currency rate per unit: "JPY — 0.5209 ₽"."""
    return f"{quote.code} — {_format_rate(quote.value)} ₽"


def _format_rate(value: float) -> str:
    """Rate per unit: >=1 — 2 digits, otherwise 4 (VND, KZT, JPY)."""
    if value >= 1:
        return f"{value:.2f}"
    return f"{value:.4f}"


async def _send_all_rates(message: Message, cache: TTLCache) -> None:
    """Shows the rates of all CBR currencies in one message."""
    quotes = await asyncio.gather(
        *[fetch_fx(code) for code in CBR_CURRENCIES],
        return_exceptions=True,
    )
    lines = [t("fx.all_title") + "\n"]
    for code, quote in zip(sorted(CBR_CURRENCIES), quotes):
        if isinstance(quote, Exception):
            log.warning("Failed to fetch rate %s from the CBR", code)
            continue
        lines.append(_fx_short_line(quote))
    if len(lines) == 1:
        await message.answer(t("fx.all_failed"))
        return
    lines.append(t("fx.more"))
    await message.answer("\n".join(lines))


def format_fx(quote: FxQuote) -> str:
    """Formats a currency rate per unit for Telegram (HTML)."""
    return t(
        "fx.format",
        name=quote.name,
        code=quote.code,
        rate=_format_rate(quote.value),
    )
