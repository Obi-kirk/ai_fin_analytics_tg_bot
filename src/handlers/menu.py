"""Меню бота: reply-клавиатура + inline-подменю с тикерами и кнопкой обновления."""

import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config.settings import get_settings
from src.handlers.crypto import fetch_crypto, format_crypto
from src.handlers.rate import fetch_fx, format_fx
from src.handlers.stock import fetch_stock, format_stock, resolve_stock_symbol
from src.services.cache import TTLCache
from src.services.financial_api import ApiRateLimitError

log = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------- reply-меню

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💱 Курсы"), KeyboardButton(text="📈 Акции")],
        [KeyboardButton(text="🪙 Крипта"), KeyboardButton(text="🤖 AI-анализ")],
        [KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
)

# ------------------------------------------------------- наборы для подменю

CURRENCIES_TOP10 = (
    "USD",
    "EUR",
    "GBP",
    "CNY",
    "JPY",
    "AED",
    "TRY",
    "VND",
    "THB",
    "CHF",
    "KZT",
    "CZK",
)
STOCKS_TOP10 = (
    "AAPL",
    "NVDA",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "NFLX",
    "AMD",
    "JPM",
)
INDEXES = ("SPX", "DJI", "VIX")
CRYPTO_TOP10 = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BNB", "AVAX", "DOT")
ANALYSE_SHORT = ("AAPL", "TSLA", "NVDA", "BTC")

MENU_TITLES = {
    "fx": "💱 Выбери валюту",
    "stock": "📈 Выбери акцию или индекс",
    "crypto": "🪙 Выбери монету",
    "analyse": "🤖 Выбери актив для AI-анализа",
}


def submenu_kb(kind: str) -> InlineKeyboardMarkup:
    """Inline-клавиатура подменю: тикеры + строка индексов для акций."""
    builder = InlineKeyboardBuilder()
    if kind == "fx":
        builder.row(
            InlineKeyboardButton(text="💱 Перевод валют", callback_data="conv:start")
        )
        for i in range(0, len(CURRENCIES_TOP10), 4):
            builder.row(
                *[
                    InlineKeyboardButton(text=n, callback_data=f"fx:{n}")
                    for n in CURRENCIES_TOP10[i : i + 4]
                ]
            )
    elif kind == "stock":
        for i in range(0, len(STOCKS_TOP10), 2):
            builder.row(
                *[
                    InlineKeyboardButton(text=n, callback_data=f"stock:{n}")
                    for n in STOCKS_TOP10[i : i + 2]
                ]
            )
        builder.row(
            *[InlineKeyboardButton(text=n, callback_data=f"stock:{n}") for n in INDEXES]
        )
    elif kind == "crypto":
        for i in range(0, len(CRYPTO_TOP10), 2):
            builder.row(
                *[
                    InlineKeyboardButton(text=n, callback_data=f"crypto:{n}")
                    for n in CRYPTO_TOP10[i : i + 2]
                ]
            )
    elif kind == "analyse":
        builder.row(
            *[
                InlineKeyboardButton(text=n, callback_data=f"analyse:{n}")
                for n in ANALYSE_SHORT
            ]
        )
    else:
        raise ValueError(f"Неизвестное подменю: {kind}")
    return builder.as_markup()


def refresh_kb(cache_key: str) -> InlineKeyboardMarkup:
    """Кнопки под ответом: «Обновить», для акций — «Новости», возврат в подменю."""
    kind = cache_key.split(":", 1)[0]
    buttons = [
        InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh:{cache_key}")
    ]
    if kind == "stock":
        symbol = cache_key.split(":", 1)[1]
        buttons.append(
            InlineKeyboardButton(text="📰 Новости", callback_data=f"news:{symbol}")
        )
    buttons.append(
        InlineKeyboardButton(text="↩️ Меню", callback_data=f"submenu:{kind}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


# ------------------------------------------------------------ reply-обработчики


@router.message(
    F.text.in_({"💱 Курсы", "📈 Акции", "🪙 Крипта", "🤖 AI-анализ", "❓ Помощь"})
)
async def on_menu_button(message: Message) -> None:
    """Реагирует на кнопки главного меню."""
    text = message.text or ""
    kind = {
        "💱 Курсы": "fx",
        "📈 Акции": "stock",
        "🪙 Крипта": "crypto",
        "🤖 AI-анализ": "analyse",
        "❓ Помощь": "help",
    }[text]
    if kind == "help":
        from src.handlers.help import HELP_TEXT

        await message.answer(HELP_TEXT, reply_markup=MAIN_MENU)
        return
    await message.answer(MENU_TITLES[kind], reply_markup=submenu_kb(kind))


# ------------------------------------------------------------ callback-обработчики


async def _quote_and_edit(
    callback: CallbackQuery,
    cache: TTLCache,
    cache_key: str,
    ttl: int,
    fetch: Callable[[], Awaitable[object]],
    render: Callable[[object], str],
    render_arg: str | None = None,
) -> None:
    """Берёт данные из кэша и редактирует сообщение с кнопкой обновления."""
    try:
        quote = await cache.get_or_set(cache_key, fetch, ttl)
    except ApiRateLimitError:
        await callback.answer(
            "⚠️ Превышен лимит API. Попробуй через минуту.", show_alert=True
        )
        return
    except Exception:  # noqa: BLE001 — граница внешнего API, ошибка уже залогирована
        await callback.answer(
            "😔 Не удалось получить данные. Попробуй позже.", show_alert=True
        )
        return
    text = render(quote) if render_arg is None else render(render_arg, quote)
    await callback.message.edit_text(
        text, reply_markup=refresh_kb(cache_key), disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^fx:[A-Z]+$"))
async def on_fx(callback: CallbackQuery, cache: TTLCache) -> None:
    """Обрабатывает выбор валюты из подменю."""
    code = callback.data.split(":", 1)[1]
    settings = get_settings()
    await _quote_and_edit(
        callback,
        cache,
        f"fx:{code}",
        settings.cache_ttl_fx_seconds,
        lambda: fetch_fx(code),
        format_fx,
    )


@router.callback_query(F.data.regexp(r"^stock:[A-Z0-9.\-^]+$"))
async def on_stock(callback: CallbackQuery, cache: TTLCache) -> None:
    """Обрабатывает выбор акции/индекса из подменю."""
    raw = callback.data.split(":", 1)[1]
    symbol = resolve_stock_symbol(raw)
    settings = get_settings()
    await _quote_and_edit(
        callback,
        cache,
        f"stock:{symbol}",
        settings.cache_ttl_stock_seconds,
        lambda: fetch_stock(symbol),
        lambda q: format_stock(q, display=raw),
    )


@router.callback_query(F.data.regexp(r"^crypto:[A-Z]+$"))
async def on_crypto(callback: CallbackQuery, cache: TTLCache) -> None:
    """Обрабатывает выбор монеты из подменю."""
    symbol = callback.data.split(":", 1)[1]
    settings = get_settings()
    await _quote_and_edit(
        callback,
        cache,
        f"crypto:{symbol}",
        settings.cache_ttl_stock_seconds,
        lambda: fetch_crypto(symbol),
        format_crypto,
        render_arg=symbol,
    )


@router.callback_query(F.data.regexp(r"^submenu:(fx|stock|crypto|analyse)$"))
async def on_submenu(callback: CallbackQuery) -> None:
    """Возвращает сообщение к подменю выбора (кнопка «↩️ Меню»)."""
    kind = callback.data.split(":", 1)[1]
    await callback.message.edit_text(MENU_TITLES[kind], reply_markup=submenu_kb(kind))
    await callback.answer()


@router.callback_query(F.data.startswith("refresh:"))
async def on_refresh(callback: CallbackQuery, cache: TTLCache) -> None:
    """Сбрасывает кэш записи и перезапрашивает данные."""
    cache_key = callback.data.split(":", 1)[1]
    await cache.delete(cache_key)
    if cache_key.startswith("fx:"):
        code = cache_key.split(":", 1)[1]
        settings = get_settings()
        await _quote_and_edit(
            callback,
            cache,
            cache_key,
            settings.cache_ttl_fx_seconds,
            lambda: fetch_fx(code),
            format_fx,
        )
    elif cache_key.startswith("stock:"):
        symbol = cache_key.split(":", 1)[1]
        settings = get_settings()
        await _quote_and_edit(
            callback,
            cache,
            cache_key,
            settings.cache_ttl_stock_seconds,
            lambda: fetch_stock(symbol),
            format_stock,
        )
    elif cache_key.startswith("crypto:"):
        symbol = cache_key.split(":", 1)[1]
        settings = get_settings()
        await _quote_and_edit(
            callback,
            cache,
            cache_key,
            settings.cache_ttl_stock_seconds,
            lambda: fetch_crypto(symbol),
            format_crypto,
            render_arg=symbol,
        )
    else:
        await callback.answer("Не знаю, как обновить это. 🙈")
