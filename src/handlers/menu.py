"""Bot menu: reply keyboard + inline submenus with tickers and a refresh button."""

import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
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
from src.handlers.portfolio import open_portfolio
from src.handlers.rate import fetch_fx, format_fx
from src.handlers.stock import fetch_stock, format_stock, resolve_stock_symbol
from src.i18n import t
from src.services.cache import TTLCache
from src.services.financial_api import ApiRateLimitError

log = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------- reply menu

# Reply button texts in all languages (the in-chat keyboard is not updated automatically)
_MENU_BUTTONS = {
    "fx": ("💱 Курсы", "💱 Rates"),
    "stock": ("📈 Акции", "📈 Stocks"),
    "crypto": ("🪙 Крипта", "🪙 Crypto"),
    "analyse": ("🤖 AI-анализ", "🤖 AI Analysis"),
    "portfolio": ("📁 Портфель", "📁 Portfolio"),
    "help": ("❓ Помощь", "❓ Help"),
}

# Each button text -> kind (for all languages, so old keyboards keep working)
_MENU_TEXT_TO_KIND = {
    text: kind for kind, texts in _MENU_BUTTONS.items() for text in texts
}


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Reply keyboard of the main menu in the current language."""
    t_fx = t("menu.btn.fx")
    t_stock = t("menu.btn.stock")
    t_crypto = t("menu.btn.crypto")
    t_analyse = t("menu.btn.analyse")
    t_portfolio = t("menu.btn.portfolio")
    t_help = t("menu.btn.help")
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t_fx), KeyboardButton(text=t_stock)],
            [KeyboardButton(text=t_crypto), KeyboardButton(text=t_analyse)],
            [KeyboardButton(text=t_portfolio), KeyboardButton(text=t_help)],
        ],
        resize_keyboard=True,
    )


MAIN_MENU = main_menu_kb()

# ------------------------------------------------------- submenu sets

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
    "AVGO",
    "ORCL",
    "CRM",
    "INTC",
    "DIS",
    "KO",
    "PEP",
    "WMT",
    "BA",
    "PYPL",
    "V",
    "MA",
    "UNH",
    "JNJ",
    "PG",
    "XOM",
    "CVX",
    "BAC",
    "COST",
    "UBER",
)
INDEXES = ("SPX", "DJI", "VIX")
CRYPTO_TOP10 = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BNB", "AVAX", "DOT")

# Stock markets: "world" (Finnhub, USD) and "ru" (MOEX, RUB)
STOCK_MARKETS = ("world", "ru")
STOCKS_PER_PAGE = 10


# Russian stocks for menus/AI analysis (lazy import to avoid a cycle)
def _ru_stocks() -> tuple[str, ...]:
    from src.handlers.stock import RU_STOCKS

    return RU_STOCKS


RU_STOCKS_FOR_MENU = _ru_stocks()


def stock_market_title(market: str) -> str:
    """Market name in the current language: «🌍 Мир» / «🇷🇺 РФ»."""
    return t(f"stock.market.{market}")


def stock_page_kb(market: str, page: int) -> InlineKeyboardMarkup:
    """Stock submenu for a market: ticker buttons + market switch + pagination.

    World market: STOCKS_TOP10 split into pages + an index row.
    RU market: RU_STOCKS (30 tickers) split into pages.
    """
    from src.handlers.stock import RU_STOCKS

    symbols = list(STOCKS_TOP10) if market == "world" else list(RU_STOCKS)
    pages = (len(symbols) + STOCKS_PER_PAGE - 1) // STOCKS_PER_PAGE
    page = max(0, min(page, pages - 1))
    builder = InlineKeyboardBuilder()

    chunk = symbols[page * STOCKS_PER_PAGE : (page + 1) * STOCKS_PER_PAGE]
    for i in range(0, len(chunk), 2):
        builder.row(
            *[
                InlineKeyboardButton(text=n, callback_data=f"stock:{n}")
                for n in chunk[i : i + 2]
            ]
        )
    if market == "world" and page == 0:
        builder.row(
            *[InlineKeyboardButton(text=n, callback_data=f"stock:{n}") for n in INDEXES]
        )
    if pages > 1:
        row = []
        if page > 0:
            row.append(
                InlineKeyboardButton(
                    text="◀️", callback_data=f"stock_page:{market}:{page - 1}"
                )
            )
        row.append(
            InlineKeyboardButton(
                text=t("stock.page", page=page + 1, total=pages),
                callback_data=f"stock_page:{market}:{page}",
            )
        )
        if page < pages - 1:
            row.append(
                InlineKeyboardButton(
                    text="▶️", callback_data=f"stock_page:{market}:{page + 1}"
                )
            )
        builder.row(*row)
    builder.row(
        *[
            InlineKeyboardButton(
                text=stock_market_title(m),
                callback_data=f"stock_market:{m}",
            )
            for m in STOCK_MARKETS
        ]
    )
    return builder.as_markup()


def stock_menu_text(market: str) -> str:
    """Submenu title for the stock market."""
    return t("menu.title.stock") + " · " + stock_market_title(market)


def stock_choice_kb() -> InlineKeyboardMarkup:
    """Market selection screen: two buttons (world / Russian stocks)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        *[
            InlineKeyboardButton(
                text=stock_market_title(m), callback_data=f"stock_market:{m}"
            )
            for m in STOCK_MARKETS
        ]
    )
    return builder.as_markup()


def stock_choice_text() -> str:
    """Title of the market selection screen."""
    return t("stock.market.choose")


ANALYSE_GROUPS = {
    "stock_world": tuple(STOCKS_TOP10),
    "stock_ru": tuple(RU_STOCKS_FOR_MENU),
    "index": ("SPX", "DJI", "VIX"),
    "crypto": ("BTC", "ETH", "SOL", "XRP"),
}
ANALYSE_GROUP_TITLES = {
    "stock_world": "📈 Мир",
    "stock_ru": "📈 РФ",
    "index": "📊 Индексы",
    "crypto": "🪙 Крипта",
}


def analyse_group_title(group: str) -> str:
    """AI analysis category name in the current language."""
    return t(f"menu.group.{group}")


def menu_title(kind: str) -> str:
    """Submenu title in the current language."""
    return t(f"menu.title.{kind}")


def submenu_kb(kind: str) -> InlineKeyboardMarkup:
    """Inline submenu keyboard: tickers + an index row for stocks."""
    builder = InlineKeyboardBuilder()
    if kind == "fx":
        builder.row(
            InlineKeyboardButton(text=t("menu.btn.convert"), callback_data="conv:start")
        )
        for i in range(0, len(CURRENCIES_TOP10), 4):
            builder.row(
                *[
                    InlineKeyboardButton(text=n, callback_data=f"fx:{n}")
                    for n in CURRENCIES_TOP10[i : i + 4]
                ]
            )
    elif kind == "stock":
        return stock_page_kb("world", 0)
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
                InlineKeyboardButton(
                    text=analyse_group_title(g), callback_data=f"analyse_cat:{g}"
                )
                for g in ANALYSE_GROUPS
            ]
        )
    else:
        raise ValueError(f"Unknown submenu: {kind}")
    return builder.as_markup()


def refresh_kb(cache_key: str) -> InlineKeyboardMarkup:
    """Buttons under the price card: refresh, for stocks — news,
    add to portfolio, back to the submenu.
    """
    kind = cache_key.split(":", 1)[0]
    symbol = cache_key.split(":", 1)[1]
    builder = InlineKeyboardBuilder()
    row1 = [
        InlineKeyboardButton(
            text=t("menu.btn.refresh"), callback_data=f"refresh:{cache_key}"
        )
    ]
    if kind == "stock":
        row1.append(
            InlineKeyboardButton(
                text=t("menu.btn.news"), callback_data=f"news:{symbol}"
            )
        )
    elif kind == "crypto":
        row1.append(
            InlineKeyboardButton(
                text=t("menu.btn.chart"), callback_data=f"chart:{symbol}"
            )
        )
    elif kind == "fx":
        row1.append(
            InlineKeyboardButton(
                text=t("menu.btn.pairs"), callback_data=f"fxpair:{symbol}"
            )
        )
    builder.row(*row1)
    builder.row(
        InlineKeyboardButton(
            text=t("menu.btn.add_portfolio"), callback_data=f"pf:add:{symbol}"
        ),
        InlineKeyboardButton(
            text=t("menu.btn.back_menu"), callback_data=f"submenu:{kind}"
        ),
    )
    return builder.as_markup()


# ------------------------------------------------------------ reply handlers


@router.message(
    F.text.in_(
        {
            "💱 Курсы",
            "📈 Акции",
            "🪙 Крипта",
            "🤖 AI-анализ",
            "📁 Портфель",
            "❓ Помощь",
            "💱 Rates",
            "📈 Stocks",
            "🪙 Crypto",
            "🤖 AI Analysis",
            "📁 Portfolio",
            "❓ Help",
        }
    )
)
async def on_menu_button(message: Message, cache: TTLCache) -> None:
    """Handles the main menu buttons."""
    text = message.text or ""
    kind = _MENU_TEXT_TO_KIND[text]
    if kind == "help":
        await message.answer(t("start.help_text"), reply_markup=main_menu_kb())
        return
    if kind == "portfolio":
        await open_portfolio(message, cache)
        return
    if kind == "stock":
        await message.answer(stock_choice_text(), reply_markup=stock_choice_kb())
        return
    await message.answer(menu_title(kind), reply_markup=submenu_kb(kind))


# ------------------------------------------------------------ callback handlers


async def _quote_and_edit(
    callback: CallbackQuery,
    cache: TTLCache,
    cache_key: str,
    ttl: int,
    fetch: Callable[[], Awaitable[object]],
    render: Callable[[object], str],
    render_arg: str | None = None,
) -> None:
    """Fetches data from the cache and edits the message with a refresh button."""
    try:
        quote = await cache.get_or_set(cache_key, fetch, ttl)
    except ApiRateLimitError:
        await callback.answer(t("menu.api_limit"), show_alert=True)
        return
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await callback.answer(t("menu.fetch_failed"), show_alert=True)
        return
    text = render(quote) if render_arg is None else render(render_arg, quote)
    try:
        await callback.message.edit_text(
            text, reply_markup=refresh_kb(cache_key), disable_web_page_preview=True
        )
    except TelegramBadRequest:
        # the quote did not change since the last refresh — nothing to update
        pass
    await callback.answer()


@router.callback_query(F.data.regexp(r"^fx:[A-Z]+$"))
async def on_fx(callback: CallbackQuery, cache: TTLCache) -> None:
    """Handles the currency selection from the submenu."""
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
    """Handles the stock/index selection from the submenu."""
    raw = callback.data.split(":", 1)[1]
    symbol = resolve_stock_symbol(raw)
    settings = get_settings()
    await _quote_and_edit(
        callback,
        cache,
        f"stock:{symbol}",
        settings.cache_ttl_stock_seconds,
        lambda: fetch_stock(symbol),
        lambda q: format_stock(q, display=raw, name=q.name),
        render_arg=None,
    )


@router.callback_query(F.data.regexp(r"^stock_market:(world|ru)$"))
async def on_stock_market(callback: CallbackQuery) -> None:
    """Switches the stock submenu between world and Russian markets."""
    market = callback.data.split(":", 1)[1]
    text = stock_menu_text(market)
    kb = stock_page_kb(market, 0)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        # pressed the already-active market — nothing to change
        pass
    await callback.answer()


@router.callback_query(F.data.regexp(r"^stock_page:(world|ru):\d+$"))
async def on_stock_page(callback: CallbackQuery) -> None:
    """Turns the stock submenu page (◀️ / ▶️)."""
    _, market, raw_page = callback.data.split(":")
    page = int(raw_page)
    try:
        await callback.message.edit_text(
            stock_menu_text(market), reply_markup=stock_page_kb(market, page)
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.regexp(r"^crypto:[A-Z]+$"))
async def on_crypto(callback: CallbackQuery, cache: TTLCache) -> None:
    """Handles the coin selection from the submenu."""
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
    """Returns the message to the selection submenu (the "Back" button)."""
    kind = callback.data.split(":", 1)[1]
    if kind == "stock":
        text, kb = stock_choice_text(), stock_choice_kb()
    else:
        text, kb = menu_title(kind), submenu_kb(kind)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(
    F.data.regexp(r"^analyse_cat:(stock_world|stock_ru|index|crypto)$")
)
async def on_analyse_cat(callback: CallbackQuery) -> None:
    """Asset list of the AI analysis category (paginated)."""
    group = callback.data.split(":", 1)[1]
    text, kb = analyse_cat_page(group, 0)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(
    F.data.regexp(r"^analyse_page:(stock_world|stock_ru|index|crypto):\d+$")
)
async def on_analyse_page(callback: CallbackQuery) -> None:
    """Turns the AI analysis category page."""
    _, group, raw_page = callback.data.split(":")
    text, kb = analyse_cat_page(group, int(raw_page))
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await callback.answer()


def analyse_cat_page(group: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Text and keyboard of one AI analysis category page (10 items each)."""
    symbols = list(ANALYSE_GROUPS[group])
    pages = (len(symbols) + STOCKS_PER_PAGE - 1) // STOCKS_PER_PAGE
    page = max(0, min(page, pages - 1))
    builder = InlineKeyboardBuilder()
    chunk = symbols[page * STOCKS_PER_PAGE : (page + 1) * STOCKS_PER_PAGE]
    for i in range(0, len(chunk), 2):
        builder.row(
            *[
                InlineKeyboardButton(text=n, callback_data=f"analyse:{n}")
                for n in chunk[i : i + 2]
            ]
        )
    if pages > 1:
        row = []
        if page > 0:
            row.append(
                InlineKeyboardButton(
                    text="◀️", callback_data=f"analyse_page:{group}:{page - 1}"
                )
            )
        row.append(
            InlineKeyboardButton(
                text=t("stock.page", page=page + 1, total=pages),
                callback_data=f"analyse_page:{group}:{page}",
            )
        )
        if page < pages - 1:
            row.append(
                InlineKeyboardButton(
                    text="▶️", callback_data=f"analyse_page:{group}:{page + 1}"
                )
            )
        builder.row(*row)
    builder.row(
        InlineKeyboardButton(
            text=t("menu.btn.back_categories"), callback_data="submenu:analyse"
        )
    )
    text = t("menu.analyse_choose", title=analyse_group_title(group))
    return text, builder.as_markup()


@router.callback_query(F.data.startswith("refresh:"))
async def on_refresh(callback: CallbackQuery, cache: TTLCache) -> None:
    """Clears the cache entry and re-fetches the data."""
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
        await callback.answer(t("menu.unknown_refresh"))
