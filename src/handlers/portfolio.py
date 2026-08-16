"""Portfolio (watchlist) and price alerts: inline interface + commands.

Commands: /portfolio, /add, /remove, /alert, /alerts, /remove_alert.
Inline callbacks:
  pf:menu                        — main portfolio menu
  pf:cat:{type}                  — category asset list with prices
  pf:view:{type}:{symbol}        — detailed asset card from the portfolio
  pf:refresh:{type}:{symbol}     — refresh card from the portfolio
  pf:add:{symbol}                — add an asset (button on the menu card)
  pf:remove                      — asset list for removal
  pf:del:{type}:{symbol}         — ask for deletion confirmation
  pf:confirm_del:{type}:{symbol} — confirm deletion
  pf:cancel_del                  — cancel deletion
  pf:alerts                      — alert list
  pf:alert_del:{id}              — ask for alert deletion confirmation
  pf:confirm_alert_del:{id}      — confirm alert deletion
  pf:cancel_alert_del            — cancel alert deletion
  pf:alert:{symbol}              — start the alert creation FSM (from card)
  pf:alert_dir:above|below       — alert direction (FSM)
  pf:alert_cancel                — cancel the alert FSM

The asset type is detected automatically: CBR currency -> fx, coin from the
CoinGecko list -> crypto, otherwise a stock ticker -> stock.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, select, update

from src.config.settings import get_settings
from src.database.db import get_session
from src.database.models import Alert, PortfolioItem
from src.handlers.crypto import COINS, fetch_crypto, format_crypto
from src.handlers.rate import fetch_fx, format_fx
from src.handlers.stock import (
    TICKER_RE,
    fetch_stock,
    format_stock,
    is_ru_stock,
    resolve_stock_symbol,
)
from src.i18n import t
from src.services.cache import TTLCache
from src.services.financial_api import (
    CBR_CURRENCIES,
    ApiRateLimitError,
    CoinGeckoClient,
    make_session,
)

log = logging.getLogger(__name__)
router = Router()

TYPE_ICONS = {"fx": "💱", "stock": "📈", "crypto": "🪙"}


def _type_title(asset_type: str) -> str:
    """Category name in the current language."""
    return t(f"portfolio.type.{asset_type}")


class AlertState(StatesGroup):
    """FSM for alert creation: type -> value (price or %) -> direction."""

    mode = State()
    value = State()
    direction = State()


class AddState(StatesGroup):
    """FSM for adding an arbitrary asset to the portfolio: symbol -> quantity."""

    symbol = State()
    quantity = State()


class QtyState(StatesGroup):
    """FSM for changing the asset quantity in the portfolio."""

    quantity = State()


def _fmt_qty(q: float) -> str:
    """Formats quantity without trailing zeros (5.0 -> 5, 0.5 -> 0.5)."""
    return f"{q:g}"


def resolve_asset_type(symbol: str) -> str | None:
    """Determines the asset type: fx | stock | crypto (by symbol)."""
    if symbol in CBR_CURRENCIES:
        return "fx"
    if symbol in COINS:
        return "crypto"
    if TICKER_RE.match(symbol):
        return "stock"
    return None


def parse_alert_args(rest: str) -> tuple[str, str, float] | None:
    """Parses /alert arguments: <symbol> [above|below] <price>."""
    parts = rest.strip().split()
    if len(parts) < 2 or len(parts) > 3:
        return None
    symbol = parts[0].upper()
    if len(parts) == 2:
        direction, raw_price = "above", parts[1]
    else:
        direction, raw_price = parts[1].lower(), parts[2]
        if direction not in ("above", "below"):
            return None
    if not resolve_asset_type(symbol):
        return None
    try:
        target = float(raw_price.replace(",", "."))
    except ValueError:
        return None
    if target <= 0:
        return None
    return symbol, direction, target


async def _pf_counts(telegram_id: int) -> dict[str, int]:
    """Counts portfolio assets by category."""
    counts = {"fx": 0, "stock": 0, "crypto": 0}
    async for session in get_session():
        rows = (
            (
                await session.execute(
                    select(PortfolioItem.asset_type).where(
                        PortfolioItem.telegram_id == telegram_id
                    )
                )
            )
            .scalars()
            .all()
        )
    for asset_type in rows:
        if asset_type in counts:
            counts[asset_type] += 1
    return counts


def _pf_menu_kb(counts: dict[str, int]) -> InlineKeyboardMarkup:
    """Main portfolio keyboard: non-empty categories only + actions."""
    builder = InlineKeyboardBuilder()
    cat_rows = [
        InlineKeyboardButton(
            text=f"{TYPE_ICONS[t]} {_type_title(t)} ({counts[t]})",
            callback_data=f"pf:cat:{t}",
        )
        for t in ("fx", "stock", "crypto")
        if counts[t] > 0
    ]
    if cat_rows:
        builder.row(*cat_rows)
    add_btn = InlineKeyboardButton(
        text=t("portfolio.btn.add"), callback_data="pf:add_menu"
    )
    if cat_rows:
        builder.row(
            add_btn,
            InlineKeyboardButton(
                text=t("portfolio.btn.remove"), callback_data="pf:remove"
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text=t("portfolio.btn.alerts"), callback_data="pf:alerts"
            )
        )
    else:
        builder.row(add_btn)
        builder.row(
            InlineKeyboardButton(
                text=t("portfolio.btn.alerts"), callback_data="pf:alerts"
            )
        )
    return builder.as_markup()


async def _portfolio_value(telegram_id: int, cache: TTLCache) -> str:
    """Total portfolio value line (USD/RUB); '' — no quantity data."""
    async for session in get_session():
        items = (
            (
                await session.execute(
                    select(PortfolioItem).where(
                        PortfolioItem.telegram_id == telegram_id,
                        PortfolioItem.quantity.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    if not items:
        return ""
    usd_rate: float | None = None
    try:
        usd = await _fetch_quote("fx", "USD", cache)
        usd_rate = usd.value / usd.nominal
    except Exception:  # noqa: BLE001 - RUB total unavailable without USD rate
        log.warning("Failed to fetch the USD rate for the portfolio total")
    rub_total = 0.0
    usd_total = 0.0
    for item in items:
        try:
            quote = await _fetch_quote(item.asset_type, item.symbol, cache)
        except Exception:  # noqa: BLE001 — one asset must not break the total
            log.warning("Failed to fetch price %s for the portfolio total", item.symbol)
            continue
        qty = item.quantity or 0.0
        if item.asset_type == "fx" or (
            item.asset_type == "stock" and is_ru_stock(item.symbol)
        ):
            # price in RUB (CBR currencies and MOEX stocks)
            rub = _per_unit(item.asset_type, quote) * qty
            rub_total += rub
            if usd_rate:
                usd_total += rub / usd_rate
        else:
            # price in USD (world stocks and crypto)
            usd = quote.price * qty
            usd_total += usd
            if usd_rate:
                rub_total += usd * usd_rate
    if rub_total <= 0 and usd_total <= 0:
        return ""
    if usd_rate:
        return t(
            "portfolio.value",
            usd=f"{usd_total:,.2f}",
            rub=f"{rub_total:,.0f}",
        )
    if rub_total > 0:
        return t("portfolio.value.rub_only", rub=f"{rub_total:,.0f}")
    return t("portfolio.value.usd_only", usd=f"{usd_total:,.2f}")


async def _render_pf_menu(
    telegram_id: int, cache: TTLCache | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    """Text and keyboard of the main portfolio menu."""
    counts = await _pf_counts(telegram_id)
    if sum(counts.values()) == 0:
        text = t("portfolio.empty")
    else:
        text = t("portfolio.choose")
        if cache is not None:
            value_str = await cache.get_or_set(
                f"pf:value:{telegram_id}",
                lambda: _portfolio_value(telegram_id, cache),
                get_settings().cache_ttl_stock_seconds,
            )
            if value_str:
                text += value_str
    return text, _pf_menu_kb(counts)


async def open_portfolio(message: Message, cache: TTLCache | None = None) -> None:
    """Opens the inline portfolio menu (the "Portfolio" reply button and /portfolio)."""
    text, kb = await _render_pf_menu(message.from_user.id, cache)
    await message.answer(text, reply_markup=kb)


async def _fetch_quote(asset_type: str, symbol: str, cache: TTLCache) -> object:
    """Fetches an asset quote through the cache (by type)."""
    settings = get_settings()
    if asset_type == "fx":
        return await cache.get_or_set(
            f"fx:{symbol}",
            lambda: fetch_fx(symbol),
            settings.cache_ttl_fx_seconds,
        )
    if asset_type == "stock":
        resolved = resolve_stock_symbol(symbol)
        return await cache.get_or_set(
            f"stock:{resolved}",
            lambda: fetch_stock(resolved),
            settings.cache_ttl_stock_seconds,
        )
    if asset_type == "crypto":
        return await cache.get_or_set(
            f"crypto:{symbol}",
            lambda: fetch_crypto(symbol),
            settings.cache_ttl_stock_seconds,
        )
    raise ValueError(f"Unknown asset type: {asset_type}")


def _short_line(
    asset_type: str, symbol: str, quote: object, quantity: float | None = None
) -> str:
    """One-line quote description for the category list."""
    if asset_type == "fx":
        rate = f"{quote.value:.2f}" if quote.value >= 1 else f"{quote.value:.4f}"
        base = f"{quote.code} — {rate} ₽"
    elif asset_type == "stock" and is_ru_stock(symbol):
        sign = "+" if quote.change_percent >= 0 else ""
        base = f"{symbol} — {quote.price:,.2f} ₽ ({sign}{quote.change_percent:.2f}%)"
    else:
        sign = "+" if quote.change_percent >= 0 else ""
        base = f"{symbol} — ${quote.price:,.2f} ({sign}{quote.change_percent:.2f}%)"
    if quantity is not None:
        base += f" ×{_fmt_qty(quantity)}"
    return base


def _per_unit(asset_type: str, quote: object) -> float:
    """Price per asset unit (for fx — taking the CBR nominal into account)."""
    if asset_type == "fx":
        return quote.value / quote.nominal
    return quote.price


def _quote_text(
    asset_type: str,
    symbol: str,
    quote: object,
    quantity: float | None = None,
    trend: str = "",
) -> str:
    """Full asset card text (as in the main menu)."""
    if asset_type == "fx":
        text = format_fx(quote)
    elif asset_type == "stock":
        text = format_stock(quote, display=symbol)
    else:
        text = format_crypto(symbol, quote)
    if trend:
        text += f"\n{trend}"
    if quantity is not None:
        if asset_type == "fx" or (asset_type == "stock" and is_ru_stock(symbol)):
            currency = "₽"
        else:
            currency = "$"
        value = _per_unit(asset_type, quote) * quantity
        text += t(
            "portfolio.qty_line",
            qty=_fmt_qty(quantity),
            value=f"{value:,.2f}",
            currency=currency,
        )
    return text


def _pf_quote_kb(asset_type: str, symbol: str) -> InlineKeyboardMarkup:
    """Portfolio card keyboard: refresh/news/alert/qty/remove."""
    builder = InlineKeyboardBuilder()
    row1 = [
        InlineKeyboardButton(
            text=t("menu.btn.refresh"),
            callback_data=f"pf:refresh:{asset_type}:{symbol}",
        )
    ]
    if asset_type == "stock":
        row1.append(
            InlineKeyboardButton(
                text=t("menu.btn.news"), callback_data=f"news:{symbol}"
            )
        )
    elif asset_type == "crypto":
        row1.append(
            InlineKeyboardButton(
                text=t("menu.btn.chart"), callback_data=f"chart:{symbol}"
            )
        )
    builder.row(*row1)
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.btn.alert"), callback_data=f"pf:alert:{symbol}"
        ),
        InlineKeyboardButton(
            text=t("portfolio.btn.qty"), callback_data=f"pf:qty:{symbol}"
        ),
        InlineKeyboardButton(
            text=t("portfolio.btn.remove_item"),
            callback_data=f"pf:del:{asset_type}:{symbol}",
        ),
    )
    builder.row(
        InlineKeyboardButton(text=t("portfolio.btn.back"), callback_data="pf:menu")
    )
    return builder.as_markup()


async def _get_quantity(telegram_id: int, symbol: str) -> float | None:
    """Asset quantity in the user's portfolio (None — not set)."""
    async for session in get_session():
        quantity = (
            await session.execute(
                select(PortfolioItem.quantity).where(
                    PortfolioItem.telegram_id == telegram_id,
                    PortfolioItem.symbol == symbol,
                )
            )
        ).scalar_one_or_none()
    return quantity


def _trend_change(prices: list[float], days: int) -> float | None:
    """Price change (%) from the price N days ago to the last one."""
    if len(prices) < 2:
        return None
    step = max(1, len(prices) // days)
    first = prices[-1 - step]
    last = prices[-1]
    if not first:
        return None
    return (last - first) / first * 100


async def _fetch_price_history(coin_id: str) -> list[float]:
    """30-day coin price history (CoinGecko, for the 7d/30d trend)."""
    async with make_session() as session:
        client = CoinGeckoClient(get_settings().coingecko_api_key)
        return await client.get_price_history(coin_id, session)


async def _trend_hint(asset_type: str, symbol: str, cache: TTLCache) -> str:
    """7d/30d trend line for crypto (no data for stocks/currencies)."""
    if asset_type != "crypto":
        return ""
    settings = get_settings()
    coin_id = COINS.get(symbol, symbol.lower())
    try:
        history = await cache.get_or_set(
            f"crypto:chart:{symbol}",
            lambda: _fetch_price_history(coin_id),
            settings.cache_ttl_fundamental_seconds,
        )
    except Exception:  # noqa: BLE001 — the trend is not critical
        log.warning("Failed to fetch price history for %s", symbol)
        return ""
    change_7 = _trend_change(history, 7)
    change_30 = _trend_change(history, 30)
    parts = []
    if change_7 is not None:
        parts.append(f"7d {change_7:+.2f}%")
    if change_30 is not None:
        parts.append(f"30d {change_30:+.2f}%")
    return t("portfolio.trend", parts=", ".join(parts)) if parts else ""


def _cache_key(asset_type: str, symbol: str) -> str:
    """Quote cache key (for stock — by the resolved ticker)."""
    if asset_type == "stock":
        return f"stock:{resolve_stock_symbol(symbol)}"
    return f"{asset_type}:{symbol}"


async def _quote_and_edit_pf(
    callback: CallbackQuery,
    cache: TTLCache,
    asset_type: str,
    symbol: str,
    fetch: Callable[[], Awaitable[object]],
) -> None:
    """Fetches the quote (with cache) and edits the portfolio card."""
    try:
        quote = await fetch()
    except ApiRateLimitError:
        await callback.answer(t("menu.api_limit"), show_alert=True)
        return
    except Exception:  # noqa: BLE001 — external API boundary, error already logged
        await callback.answer(t("menu.fetch_failed"), show_alert=True)
        return
    quantity = await _get_quantity(callback.from_user.id, symbol)
    trend = await _trend_hint(asset_type, symbol, cache)
    await callback.message.edit_text(
        _quote_text(asset_type, symbol, quote, quantity, trend),
        reply_markup=_pf_quote_kb(asset_type, symbol),
        disable_web_page_preview=True,
    )
    await callback.answer()


async def _list_items(telegram_id: int, asset_type: str) -> list[PortfolioItem]:
    """Portfolio assets of one category (sorted by symbol)."""
    async for session in get_session():
        items = (
            (
                await session.execute(
                    select(PortfolioItem)
                    .where(
                        PortfolioItem.telegram_id == telegram_id,
                        PortfolioItem.asset_type == asset_type,
                    )
                    .order_by(PortfolioItem.symbol)
                )
            )
            .scalars()
            .all()
        )
    return list(items)


@router.message(Command("portfolio"))
async def cmd_portfolio(message: Message, cache: TTLCache) -> None:
    """Opens the inline portfolio menu."""
    await open_portfolio(message, cache)


# ---------------------------------------------------------- inline: main menu


@router.callback_query(F.data == "pf:menu")
async def on_pf_menu(callback: CallbackQuery, cache: TTLCache) -> None:
    """Shows the main portfolio menu."""
    text, kb = await _render_pf_menu(callback.from_user.id, cache)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


async def _render_cat(
    callback: CallbackQuery, cache: TTLCache, asset_type: str
) -> None:
    """Category asset list with live prices."""
    items = await _list_items(callback.from_user.id, asset_type)
    if not items:
        await callback.message.edit_text(
            t(
                "portfolio.cat.empty",
                icon=TYPE_ICONS[asset_type],
                title=_type_title(asset_type),
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("portfolio.btn.back"), callback_data="pf:menu"
                        )
                    ]
                ]
            ),
        )
        await callback.answer()
        return
    quotes = await asyncio.gather(
        *[_fetch_quote(asset_type, item.symbol, cache) for item in items],
        return_exceptions=True,
    )
    lines = [
        t(
            "portfolio.cat.title",
            icon=TYPE_ICONS[asset_type],
            title=_type_title(asset_type),
            n=len(items),
        )
    ]
    builder = InlineKeyboardBuilder()
    for item, quote in zip(items, quotes):
        if isinstance(quote, Exception):
            lines.append(t("portfolio.unavailable", symbol=item.symbol))
        else:
            lines.append(_short_line(asset_type, item.symbol, quote, item.quantity))
        builder.row(
            InlineKeyboardButton(
                text=f"{TYPE_ICONS[asset_type]} {item.symbol}",
                callback_data=f"pf:view:{asset_type}:{item.symbol}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.btn.refresh_all"),
            callback_data=f"pf:cat_refresh:{asset_type}",
        ),
        InlineKeyboardButton(text=t("portfolio.btn.back"), callback_data="pf:menu"),
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:cat:(fx|stock|crypto)$"))
async def on_pf_cat(callback: CallbackQuery, cache: TTLCache) -> None:
    """Category asset list with live prices."""
    asset_type = callback.data.split(":", 2)[2]
    await _render_cat(callback, cache, asset_type)


@router.callback_query(F.data.regexp(r"^pf:cat_refresh:(fx|stock|crypto)$"))
async def on_pf_cat_refresh(callback: CallbackQuery, cache: TTLCache) -> None:
    """Clears the cache of all category assets and rebuilds the list."""
    asset_type = callback.data.split(":", 2)[2]
    for item in await _list_items(callback.from_user.id, asset_type):
        await cache.delete(_cache_key(asset_type, item.symbol))
    await _render_cat(callback, cache, asset_type)


@router.callback_query(F.data.regexp(r"^pf:view:(fx|stock|crypto):[A-Z0-9.\-]+$"))
async def on_pf_view(callback: CallbackQuery, cache: TTLCache) -> None:
    """Detailed asset card from the portfolio."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    await _quote_and_edit_pf(
        callback,
        cache,
        asset_type,
        symbol,
        lambda: _fetch_quote(asset_type, symbol, cache),
    )


@router.callback_query(F.data.regexp(r"^pf:refresh:(fx|stock|crypto):[A-Z0-9.\-]+$"))
async def on_pf_refresh(callback: CallbackQuery, cache: TTLCache) -> None:
    """Refreshes the portfolio asset card (cache reset)."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    await cache.delete(_cache_key(asset_type, symbol))
    await _quote_and_edit_pf(
        callback,
        cache,
        asset_type,
        symbol,
        lambda: _fetch_quote(asset_type, symbol, cache),
    )


# ---------------------------------------------------------- inline: adding


def _mark_added(markup: InlineKeyboardMarkup, symbol: str) -> InlineKeyboardMarkup:
    """Replaces the "Add to portfolio" button with an inactive "In portfolio" one."""
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for btn in row:
            if btn.callback_data == f"pf:add:{symbol}":
                new_row.append(
                    InlineKeyboardButton(
                        text=t("portfolio.btn.added"), callback_data="pf:added"
                    )
                )
            else:
                new_row.append(btn)
        rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.regexp(r"^pf:add:[A-Z0-9.\-]+$"))
async def on_pf_add(callback: CallbackQuery) -> None:
    """Adds an asset to the portfolio (button on the main menu card)."""
    symbol = callback.data.split(":", 2)[2]
    asset_type = resolve_asset_type(symbol)
    if asset_type is None:
        await callback.answer(t("portfolio.add.unknown_type"), show_alert=True)
        return
    async for session in get_session():
        exists = (
            await session.execute(
                select(PortfolioItem).where(
                    PortfolioItem.telegram_id == callback.from_user.id,
                    PortfolioItem.symbol == symbol,
                )
            )
        ).scalar()
        if exists is None:
            session.add(
                PortfolioItem(
                    telegram_id=callback.from_user.id,
                    asset_type=asset_type,
                    symbol=symbol,
                )
            )
            await session.commit()
            added = True
        else:
            added = False
    markup = callback.message.reply_markup
    if markup is not None and added:
        await callback.message.edit_reply_markup(
            reply_markup=_mark_added(markup, symbol)
        )
    await callback.answer(
        t("portfolio.add.toast", symbol=symbol)
        if added
        else t("portfolio.add.already", symbol=symbol)
    )


# ---------------------------------------------------------- inline: add FSM


async def _add_item(
    telegram_id: int, symbol: str, asset_type: str, quantity: float | None
) -> bool:
    """Adds an asset to the portfolio; True if it was added (did not exist before)."""
    async for session in get_session():
        exists = (
            await session.execute(
                select(PortfolioItem).where(
                    PortfolioItem.telegram_id == telegram_id,
                    PortfolioItem.symbol == symbol,
                )
            )
        ).scalar()
        if exists is None:
            session.add(
                PortfolioItem(
                    telegram_id=telegram_id,
                    asset_type=asset_type,
                    symbol=symbol,
                    quantity=quantity,
                )
            )
            await session.commit()
            added = True
        else:
            added = False
    return added


async def _parse_qty(raw: str) -> float | None:
    """Parses the quantity; None — invalid value."""
    try:
        qty = float(raw.replace(",", "."))
    except ValueError:
        return None
    if qty <= 0:
        return None
    return qty


async def _added_message(
    added: bool, symbol: str, asset_type: str, quantity: float | None
) -> str:
    """Message about the result of adding an asset to the portfolio."""
    qty_suffix = (
        t("portfolio.add.qty_suffix", qty=_fmt_qty(quantity))
        if quantity is not None
        else ""
    )
    if added:
        return t(
            "portfolio.add.done",
            icon=TYPE_ICONS[asset_type],
            symbol=symbol,
            type=_type_title(asset_type),
            qty=qty_suffix,
        )
    return t(
        "portfolio.add.exists",
        icon=TYPE_ICONS[asset_type],
        symbol=symbol,
    )


@router.callback_query(F.data == "pf:add_menu")
async def on_pf_add_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Starts the FSM for adding an arbitrary asset."""
    await state.set_state(AddState.symbol)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.btn.cancel"), callback_data="pf:add_cancel"
        )
    )
    await callback.message.edit_text(
        t("portfolio.add.prompt"),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.message(AddState.symbol)
async def on_add_symbol(message: Message, state: FSMContext) -> None:
    """Accepts the symbol and asks for the quantity."""
    symbol = (message.text or "").strip().upper().split(" ", 1)[0]
    asset_type = resolve_asset_type(symbol)
    if asset_type is None:
        await message.answer(t("portfolio.add.bad_symbol"))
        return
    await state.update_data(symbol=symbol, asset_type=asset_type)
    await state.set_state(AddState.quantity)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("portfolio.btn.skip"), callback_data="pf:add_skip"),
        InlineKeyboardButton(
            text=t("portfolio.btn.cancel"), callback_data="pf:add_cancel"
        ),
    )
    await message.answer(
        t("portfolio.add.qty_prompt", symbol=symbol),
        reply_markup=builder.as_markup(),
    )


@router.message(AddState.quantity)
async def on_add_quantity(message: Message, state: FSMContext, cache: TTLCache) -> None:
    """Accepts the quantity and adds the asset to the portfolio."""
    qty = await _parse_qty((message.text or "").strip())
    if qty is None:
        await message.answer(t("portfolio.add.bad_qty"))
        return
    data = await state.get_data()
    symbol = data["symbol"]
    asset_type = data["asset_type"]
    added = await _add_item(message.from_user.id, symbol, asset_type, qty)
    await state.clear()
    _, kb = await _render_pf_menu(message.from_user.id, cache)
    await message.answer(
        await _added_message(added, symbol, asset_type, qty), reply_markup=kb
    )


@router.callback_query(F.data == "pf:add_skip")
async def on_pf_add_skip(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Adds the asset without a quantity."""
    data = await state.get_data()
    symbol = data.get("symbol")
    asset_type = data.get("asset_type")
    if not symbol or not asset_type:
        await state.clear()
        await callback.answer(t("portfolio.stale"), show_alert=True)
        return
    added = await _add_item(callback.from_user.id, symbol, asset_type, None)
    await state.clear()
    _, kb = await _render_pf_menu(callback.from_user.id, cache)
    await callback.message.answer(
        await _added_message(added, symbol, asset_type, None), reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "pf:add_cancel")
async def on_pf_add_cancel(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Cancels the asset adding FSM."""
    await state.clear()
    text, kb = await _render_pf_menu(callback.from_user.id, cache)
    await callback.message.edit_text(
        f"{t('portfolio.cancelled')}\n\n{text}", reply_markup=kb
    )
    await callback.answer()


# -------------------------------------------------- inline: quantity FSM


@router.callback_query(F.data.regexp(r"^pf:qty:[A-Z0-9.\-]+$"))
async def on_pf_qty(callback: CallbackQuery, state: FSMContext) -> None:
    """Starts the FSM for changing the asset quantity."""
    symbol = callback.data.split(":", 2)[2]
    await state.set_state(QtyState.quantity)
    await state.update_data(symbol=symbol)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.btn.cancel"), callback_data="pf:qty_cancel"
        )
    )
    await callback.message.edit_text(
        t("portfolio.qty.prompt", symbol=symbol),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.message(QtyState.quantity)
async def on_qty_value(message: Message, state: FSMContext, cache: TTLCache) -> None:
    """Accepts the quantity and saves it."""
    qty = await _parse_qty((message.text or "").strip())
    if qty is None:
        await message.answer(t("portfolio.add.bad_qty"))
        return
    data = await state.get_data()
    symbol = data["symbol"]
    async for session in get_session():
        await session.execute(
            update(PortfolioItem)
            .where(
                PortfolioItem.telegram_id == message.from_user.id,
                PortfolioItem.symbol == symbol,
            )
            .values(quantity=qty)
        )
        await session.commit()
    await state.clear()
    _, kb = await _render_pf_menu(message.from_user.id, cache)
    await message.answer(
        t("portfolio.qty.saved", symbol=symbol, qty=_fmt_qty(qty)),
        reply_markup=kb,
    )


@router.callback_query(F.data == "pf:qty_cancel")
async def on_pf_qty_cancel(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Cancels the quantity change."""
    await state.clear()
    text, kb = await _render_pf_menu(callback.from_user.id, cache)
    await callback.message.edit_text(
        f"{t('portfolio.cancelled')}\n\n{text}", reply_markup=kb
    )
    await callback.answer()


# ---------------------------------------------------------- inline: deletion


async def _remove_kb(telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """List of all portfolio assets with delete buttons."""
    async for session in get_session():
        items = (
            (
                await session.execute(
                    select(PortfolioItem)
                    .where(PortfolioItem.telegram_id == telegram_id)
                    .order_by(PortfolioItem.asset_type, PortfolioItem.symbol)
                )
            )
            .scalars()
            .all()
        )
    if not items:
        return (
            t("portfolio.remove.empty"),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("portfolio.btn.back"), callback_data="pf:menu"
                        )
                    ]
                ]
            ),
        )
    lines = [t("portfolio.remove.title") + "\n"]
    builder = InlineKeyboardBuilder()
    for item in items:
        lines.append(
            f"{TYPE_ICONS.get(item.asset_type, '')} {item.symbol} "
            f"({_type_title(item.asset_type)})"
        )
        builder.row(
            InlineKeyboardButton(
                text=f"🗑 {item.symbol}",
                callback_data=f"pf:del:{item.asset_type}:{item.symbol}",
            )
        )
    builder.row(
        InlineKeyboardButton(text=t("portfolio.btn.back"), callback_data="pf:menu")
    )
    return "\n".join(lines), builder.as_markup()


@router.callback_query(F.data == "pf:remove")
async def on_pf_remove(callback: CallbackQuery) -> None:
    """Shows the asset list for removal."""
    text, kb = await _remove_kb(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:del:(fx|stock|crypto):[A-Z0-9.\-]+$"))
async def on_pf_del(callback: CallbackQuery) -> None:
    """Asks for asset deletion confirmation."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.remove.yes"),
            callback_data=f"pf:confirm_del:{asset_type}:{symbol}",
        ),
        InlineKeyboardButton(
            text=t("portfolio.btn.cancel"), callback_data="pf:cancel_del"
        ),
    )
    await callback.message.edit_text(
        t(
            "portfolio.remove.confirm",
            icon=TYPE_ICONS[asset_type],
            symbol=symbol,
        ),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(
    F.data.regexp(r"^pf:confirm_del:(fx|stock|crypto):[A-Z0-9.\-]+$")
)
async def on_pf_confirm_del(callback: CallbackQuery, cache: TTLCache) -> None:
    """Confirmed asset deletion from the portfolio."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    async for session in get_session():
        await session.execute(
            delete(PortfolioItem).where(
                PortfolioItem.telegram_id == callback.from_user.id,
                PortfolioItem.symbol == symbol,
            )
        )
        await session.commit()
    text, kb = await _render_pf_menu(callback.from_user.id, cache)
    await callback.message.edit_text(
        f"{t('portfolio.remove.done', icon=TYPE_ICONS[asset_type], symbol=symbol)}\n\n{text}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "pf:cancel_del")
async def on_pf_cancel_del(callback: CallbackQuery, cache: TTLCache) -> None:
    """Cancels the deletion and returns to the main portfolio menu."""
    text, kb = await _render_pf_menu(callback.from_user.id, cache)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject) -> None:
    """Adds an asset to the portfolio: /add BTC (or AAPL, USD)."""
    symbol = ((command.args or "").strip().upper()).split(" ", 1)[0]
    asset_type = resolve_asset_type(symbol) if symbol else None
    if asset_type is None:
        await message.answer(t("portfolio.cmd.add.usage"))
        return
    async for session in get_session():
        exists = (
            await session.execute(
                select(PortfolioItem).where(
                    PortfolioItem.telegram_id == message.from_user.id,
                    PortfolioItem.symbol == symbol,
                )
            )
        ).scalar()
        if exists is None:
            session.add(
                PortfolioItem(
                    telegram_id=message.from_user.id,
                    asset_type=asset_type,
                    symbol=symbol,
                )
            )
            await session.commit()
            added = True
        else:
            added = False
    await message.answer(
        t(
            "portfolio.cmd.add.done",
            symbol=symbol,
            icon=TYPE_ICONS[asset_type],
            type=_type_title(asset_type),
        )
        if added
        else t("portfolio.cmd.add.exists", symbol=symbol)
    )


@router.message(Command("remove"))
async def cmd_remove(message: Message, command: CommandObject) -> None:
    """Removes an asset from the portfolio: /remove BTC."""
    symbol = ((command.args or "").strip().upper()).split(" ", 1)[0]
    if not symbol:
        await message.answer(t("portfolio.cmd.remove.usage"))
        return
    async for session in get_session():
        result = await session.execute(
            delete(PortfolioItem).where(
                PortfolioItem.telegram_id == message.from_user.id,
                PortfolioItem.symbol == symbol,
            )
        )
        await session.commit()
        removed = result.rowcount > 0
    await message.answer(
        t("portfolio.cmd.remove.done", symbol=symbol)
        if removed
        else t("portfolio.cmd.remove.missing", symbol=symbol)
    )


@router.message(Command("alert"))
async def cmd_alert(message: Message, command: CommandObject) -> None:
    """Sets a price alert: /alert BTC 70000 or /alert BTC below 50000."""
    parsed = parse_alert_args(command.args or "")
    if parsed is None:
        await message.answer(t("portfolio.alert.usage"))
        return
    symbol, direction, target = parsed
    asset_type = resolve_asset_type(symbol) or "stock"
    async for session in get_session():
        session.add(
            Alert(
                telegram_id=message.from_user.id,
                asset_type=asset_type,
                symbol=symbol,
                target_price=target,
                direction=direction,
            )
        )
        await session.commit()
    arrow = (
        t("portfolio.alert.above")
        if direction == "above"
        else t("portfolio.alert.below")
    )
    await message.answer(
        t(
            "portfolio.alert.set",
            symbol=symbol,
            arrow=arrow,
            target=f"{target:,.2f}",
            currency="₽" if is_ru_stock(symbol) else "$",
        )
    )


@router.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    """Shows the user's active alerts."""
    async for session in get_session():
        alerts = (
            (
                await session.execute(
                    select(Alert)
                    .where(
                        Alert.telegram_id == message.from_user.id,
                        Alert.is_active.is_(True),
                    )
                    .order_by(Alert.id)
                )
            )
            .scalars()
            .all()
        )
    if not alerts:
        await message.answer(t("portfolio.alert.empty"))
        return
    lines = [t("portfolio.alert.title") + "\n"]
    for num, a in enumerate(alerts, 1):
        lines.append(f"• <code>{num}</code>. {_alert_line(a)}")
    lines.append(t("portfolio.alert.hint_remove"))
    await message.answer("\n".join(lines))


# ---------------------------------------------------------- inline: alerts


def _alert_line(a: Alert) -> str:
    """One-line alert description (without a number — numbered in the list)."""
    arrow = (
        t("portfolio.alert.above")
        if a.direction == "above"
        else t("portfolio.alert.below")
    )
    if a.mode == "percent":
        return (
            f"{TYPE_ICONS.get(a.asset_type, '')}"
            f"<b>{a.symbol}</b> {arrow} {a.target_price:g}%"
        )
    currency = "₽" if is_ru_stock(a.symbol) else "$"
    return (
        f"{TYPE_ICONS.get(a.asset_type, '')}"
        f"<b>{a.symbol}</b> {arrow} {currency}{a.target_price:,.2f}"
    )


async def _alerts_text_kb(telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Text and keyboard of the alert list with delete buttons."""
    async for session in get_session():
        alerts = (
            (
                await session.execute(
                    select(Alert)
                    .where(
                        Alert.telegram_id == telegram_id,
                        Alert.is_active.is_(True),
                    )
                    .order_by(Alert.id)
                )
            )
            .scalars()
            .all()
        )
    if not alerts:
        return (
            t("portfolio.alert.empty2"),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("portfolio.btn.back"), callback_data="pf:menu"
                        )
                    ]
                ]
            ),
        )
    lines = [t("portfolio.alert.title") + "\n"]
    builder = InlineKeyboardBuilder()
    for num, a in enumerate(alerts, 1):
        lines.append(f"• <code>{num}</code>. {_alert_line(a)}")
        builder.row(
            InlineKeyboardButton(
                text=t("portfolio.alert.btn_del", num=num),
                callback_data=f"pf:alert_del:{a.id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text=t("portfolio.btn.back"), callback_data="pf:menu")
    )
    return "\n".join(lines), builder.as_markup()


@router.callback_query(F.data == "pf:alerts")
async def on_pf_alerts(callback: CallbackQuery) -> None:
    """Shows the user's alert list."""
    text, kb = await _alerts_text_kb(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:alert_del:\d+$"))
async def on_pf_alert_del(callback: CallbackQuery) -> None:
    """Asks for alert deletion confirmation."""
    alert_id = int(callback.data.split(":", 2)[2])
    async for session in get_session():
        alert = (
            await session.execute(
                select(Alert).where(
                    Alert.id == alert_id,
                    Alert.telegram_id == callback.from_user.id,
                )
            )
        ).scalar_one_or_none()
    if alert is None:
        await callback.answer(t("portfolio.alert.not_found"), show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.alert.yes_del"),
            callback_data=f"pf:confirm_alert_del:{alert_id}",
        ),
        InlineKeyboardButton(
            text=t("portfolio.btn.cancel"), callback_data="pf:cancel_alert_del"
        ),
    )
    await callback.message.edit_text(
        t("portfolio.alert.delete_confirm", line=_alert_line(alert)),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:confirm_alert_del:\d+$"))
async def on_pf_confirm_alert_del(callback: CallbackQuery) -> None:
    """Confirmed alert deletion."""
    alert_id = int(callback.data.split(":", 2)[2])
    async for session in get_session():
        await session.execute(
            delete(Alert).where(
                Alert.id == alert_id,
                Alert.telegram_id == callback.from_user.id,
            )
        )
        await session.commit()
    text, kb = await _alerts_text_kb(callback.from_user.id)
    await callback.message.edit_text(
        f"{t('portfolio.alert.deleted', id=alert_id)}\n\n{text}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "pf:cancel_alert_del")
async def on_pf_cancel_alert_del(callback: CallbackQuery) -> None:
    """Cancels the alert deletion."""
    text, kb = await _alerts_text_kb(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------------------------------------------------------- inline: alert FSM


async def _cached_price_hint(asset_type: str, symbol: str, cache: TTLCache) -> str:
    """Current price from the cache (hint when entering the alert price)."""
    quote = await cache.get(_cache_key(asset_type, symbol))
    if quote is None:
        return ""
    if asset_type == "fx":
        return t("portfolio.alert.hint_fx", price=f"{quote.value:.2f}")
    sign = "+" if quote.change_percent >= 0 else ""
    if asset_type == "stock" and is_ru_stock(symbol):
        return t(
            "portfolio.alert.hint_ru",
            price=f"{quote.price:,.2f}",
            sign=sign,
            pct=f"{quote.change_percent:.2f}",
        )
    return t(
        "portfolio.alert.hint",
        price=f"{quote.price:,.2f}",
        sign=sign,
        pct=f"{quote.change_percent:.2f}",
    )


@router.callback_query(F.data.regexp(r"^pf:alert:[A-Z0-9.\-]+$"))
async def on_pf_alert(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Starts the alert creation FSM: type selection."""
    symbol = callback.data.split(":", 2)[2]
    asset_type = resolve_asset_type(symbol) or "stock"
    await state.set_state(AlertState.mode)
    await state.update_data(symbol=symbol, asset_type=asset_type)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.alert.btn_price"), callback_data="pf:alert_mode:absolute"
        ),
        InlineKeyboardButton(
            text=t("portfolio.alert.btn_percent"), callback_data="pf:alert_mode:percent"
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.btn.cancel"), callback_data="pf:alert_cancel"
        )
    )
    await callback.message.edit_text(
        t("portfolio.alert.type_prompt", symbol=symbol),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:alert_mode:(absolute|percent)$"))
async def on_pf_alert_mode(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Accepts the alert type and asks for the value (price or percent)."""
    mode = callback.data.split(":", 2)[2]
    await state.update_data(mode=mode)
    await state.set_state(AlertState.value)
    data = await state.get_data()
    symbol = data["symbol"]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.btn.cancel"), callback_data="pf:alert_cancel"
        )
    )
    if mode == "percent":
        hint = await _cached_price_hint(data["asset_type"], symbol, cache)
        await callback.message.edit_text(
            t("portfolio.alert.percent_prompt", symbol=symbol, hint=hint),
            reply_markup=builder.as_markup(),
        )
    else:
        hint = await _cached_price_hint(data["asset_type"], symbol, cache)
        await callback.message.edit_text(
            t("portfolio.alert.price_prompt", symbol=symbol, hint=hint),
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.message(AlertState.value)
async def on_alert_value(message: Message, state: FSMContext) -> None:
    """Accepts the value (price or %) and asks for the direction."""
    raw = (message.text or "").strip().replace(",", ".").replace("%", "")
    try:
        value = float(raw)
    except ValueError:
        await message.answer(t("portfolio.alert.bad_number"))
        return
    if value <= 0:
        await message.answer(t("portfolio.alert.bad_value"))
        return
    await state.update_data(value=value)
    await state.set_state(AlertState.direction)
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.alert.btn_above"), callback_data="pf:alert_dir:above"
        ),
        InlineKeyboardButton(
            text=t("portfolio.alert.btn_below"), callback_data="pf:alert_dir:below"
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t("portfolio.btn.cancel"), callback_data="pf:alert_cancel"
        )
    )
    suffix = "%" if data.get("mode") == "percent" else ""
    await message.answer(
        t(
            "portfolio.alert.direction",
            symbol=data["symbol"],
            value=f"{value:g}",
            suffix=suffix,
        ),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.regexp(r"^pf:alert_dir:(above|below)$"))
async def on_alert_dir(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Direction selection: creates the alert and finishes the FSM."""
    direction = callback.data.split(":", 2)[2]
    data = await state.get_data()
    symbol = data.get("symbol")
    value = data.get("value")
    if not symbol or not value:
        await state.clear()
        await callback.answer(t("portfolio.stale"), show_alert=True)
        return
    asset_type = data.get("asset_type") or resolve_asset_type(symbol) or "stock"
    mode = data.get("mode") or "absolute"
    baseline = None
    if mode == "percent":
        quote = await _fetch_quote(asset_type, symbol, cache)
        baseline = quote.value if asset_type == "fx" else quote.price
    async for session in get_session():
        session.add(
            Alert(
                telegram_id=callback.from_user.id,
                asset_type=asset_type,
                symbol=symbol,
                target_price=value,
                direction=direction,
                mode=mode,
                baseline_price=baseline,
            )
        )
        await session.commit()
    await state.clear()
    arrow = (
        t("portfolio.alert.above")
        if direction == "above"
        else t("portfolio.alert.below")
    )
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("portfolio.btn.back"), callback_data="pf:menu")
    )
    unit = (
        "%"
        if mode == "percent"
        else f"{'₽' if is_ru_stock(symbol) else '$'}{value:,.2f}"
    )
    await callback.message.edit_text(
        t(
            "portfolio.alert.set2",
            symbol=symbol,
            arrow=arrow,
            value=f"{value:g}",
            unit=unit,
        ),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "pf:alert_cancel")
async def on_alert_cancel(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Cancels the alert creation FSM."""
    await state.clear()
    text, kb = await _render_pf_menu(callback.from_user.id, cache)
    await callback.message.edit_text(
        f"{t('portfolio.cancelled')}\n\n{text}", reply_markup=kb
    )
    await callback.answer()


@router.message(Command("remove_alert"))
async def cmd_remove_alert(message: Message, command: CommandObject) -> None:
    """Deletes an alert by id: /remove_alert 3."""
    try:
        alert_id = int((command.args or "").strip().split()[0])
    except (ValueError, IndexError):
        await message.answer(t("portfolio.remove_alert.usage"))
        return
    async for session in get_session():
        result = await session.execute(
            delete(Alert).where(
                Alert.id == alert_id,
                Alert.telegram_id == message.from_user.id,
            )
        )
        await session.commit()
        removed = result.rowcount > 0
    await message.answer(
        t("portfolio.alert.deleted", id=alert_id)
        if removed
        else t("portfolio.remove_alert.missing", id=alert_id)
    )
