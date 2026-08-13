"""Портфель (watchlist) и алерты цен: inline-интерфейс + команды.

Команды: /portfolio, /add, /remove, /alert, /alerts, /remove_alert.
Inline-колбэки:
  pf:menu                        — главное меню портфеля
  pf:cat:{type}                  — список активов категории с ценами
  pf:view:{type}:{symbol}        — детальная карточка актива из портфеля
  pf:refresh:{type}:{symbol}     — обновление карточки из портфеля
  pf:add:{symbol}                — добавить актив (кнопка в карточке меню)
  pf:remove                      — список активов для удаления
  pf:del:{type}:{symbol}         — запрос подтверждения удаления
  pf:confirm_del:{type}:{symbol} — подтвердить удаление
  pf:cancel_del                  — отменить удаление
  pf:alerts                      — список алертов
  pf:alert_del:{id}              — запрос подтверждения удаления алерта
  pf:confirm_alert_del:{id}      — подтвердить удаление алерта
  pf:cancel_alert_del            — отменить удаление алерта
  pf:alert:{symbol}              — начало FSM создания алерта (из карточки)
  pf:alert_dir:above|below       — направление алерта (FSM)
  pf:alert_cancel                — отмена FSM алерта

Тип актива определяется автоматически: валюта ЦБ -> fx, монета из списка
CoinGecko -> crypto, иначе тикер акции -> stock.
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
    resolve_stock_symbol,
)
from src.services.cache import TTLCache
from src.services.financial_api import CBR_CURRENCIES, ApiRateLimitError

log = logging.getLogger(__name__)
router = Router()

TYPE_ICONS = {"fx": "💱", "stock": "📈", "crypto": "🪙"}
TYPE_TITLES = {"fx": "Валюты", "stock": "Акции", "crypto": "Крипта"}


class AlertState(StatesGroup):
    """FSM создания алерта из карточки портфеля: цена -> направление."""

    price = State()
    direction = State()


class AddState(StatesGroup):
    """FSM добавления произвольного актива в портфель: символ -> количество."""

    symbol = State()
    quantity = State()


class QtyState(StatesGroup):
    """FSM изменения количества актива в портфеле."""

    quantity = State()


def _fmt_qty(q: float) -> str:
    """Форматирует количество без лишних нулей (5.0 -> 5, 0.5 -> 0.5)."""
    return f"{q:g}"


def resolve_asset_type(symbol: str) -> str | None:
    """Определяет тип актива: fx | stock | crypto (по символу)."""
    if symbol in CBR_CURRENCIES:
        return "fx"
    if symbol in COINS:
        return "crypto"
    if TICKER_RE.match(symbol):
        return "stock"
    return None


def parse_alert_args(rest: str) -> tuple[str, str, float] | None:
    """Разбирает аргументы /alert: <символ> [above|below] <цена>."""
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
    """Считает активы портфеля по категориям."""
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
    for t in rows:
        if t in counts:
            counts[t] += 1
    return counts


def _pf_menu_kb(counts: dict[str, int]) -> InlineKeyboardMarkup:
    """Главная клавиатура портфеля: только непустые категории + действия."""
    builder = InlineKeyboardBuilder()
    cat_rows = [
        InlineKeyboardButton(
            text=f"{TYPE_ICONS[t]} {TYPE_TITLES[t]} ({counts[t]})",
            callback_data=f"pf:cat:{t}",
        )
        for t in ("fx", "stock", "crypto")
        if counts[t] > 0
    ]
    if cat_rows:
        builder.row(*cat_rows)
    add_btn = InlineKeyboardButton(text="➕ Добавить", callback_data="pf:add_menu")
    if cat_rows:
        builder.row(
            add_btn,
            InlineKeyboardButton(text="➖ Удалить", callback_data="pf:remove"),
        )
        builder.row(InlineKeyboardButton(text="🔔 Алерты", callback_data="pf:alerts"))
    else:
        builder.row(add_btn)
        builder.row(InlineKeyboardButton(text="🔔 Алерты", callback_data="pf:alerts"))
    return builder.as_markup()


async def _render_pf_menu(telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и клавиатура главного меню портфеля."""
    counts = await _pf_counts(telegram_id)
    if sum(counts.values()) == 0:
        text = (
            "📁 <b>Мой портфель</b>\n\nПортфель пуст.\n"
            "Добавь актив: нажми «➕ Добавить» или в меню «📈 Акции» / "
            "«🪙 Крипта» / «💱 Курсы» у цены актива — «➕ В портфель»."
        )
    else:
        text = "📁 <b>Мой портфель</b>\n\nВыбери категорию или действие."
    return text, _pf_menu_kb(counts)


async def open_portfolio(message: Message) -> None:
    """Открывает inline-меню портфеля (reply-кнопка «📁 Портфель» и /portfolio)."""
    text, kb = await _render_pf_menu(message.from_user.id)
    await message.answer(text, reply_markup=kb)


async def _fetch_quote(asset_type: str, symbol: str, cache: TTLCache) -> object:
    """Котировка актива через кэш (по типу)."""
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
    raise ValueError(f"Неизвестный тип актива: {asset_type}")


def _short_line(
    asset_type: str, symbol: str, quote: object, quantity: float | None = None
) -> str:
    """Однострочное описание котировки для списка категории."""
    if asset_type == "fx":
        base = f"{quote.code} — {quote.value:.2f} ₽"
    else:
        sign = "+" if quote.change_percent >= 0 else ""
        base = f"{symbol} — ${quote.price:,.2f} ({sign}{quote.change_percent:.2f}%)"
    if quantity is not None:
        base += f" ×{_fmt_qty(quantity)}"
    return base


def _per_unit(asset_type: str, quote: object) -> float:
    """Цена за единицу актива (для fx — с учётом номинала ЦБ)."""
    if asset_type == "fx":
        return quote.value / quote.nominal
    return quote.price


def _quote_text(
    asset_type: str,
    symbol: str,
    quote: object,
    quantity: float | None = None,
) -> str:
    """Полный текст карточки актива (как в основном меню)."""
    if asset_type == "fx":
        text = format_fx(quote)
    elif asset_type == "stock":
        text = format_stock(quote, display=symbol)
    else:
        text = format_crypto(symbol, quote)
    if quantity is not None:
        currency = "₽" if asset_type == "fx" else "$"
        value = _per_unit(asset_type, quote) * quantity
        text += (
            f"\n\nКоличество: {_fmt_qty(quantity)} • "
            f"Стоимость: <b>{value:,.2f} {currency}</b>"
        )
    return text


def _pf_quote_kb(asset_type: str, symbol: str) -> InlineKeyboardMarkup:
    """Клавиатура карточки из портфеля: обновить/новости/алерт/кол-во/убрать."""
    builder = InlineKeyboardBuilder()
    row1 = [
        InlineKeyboardButton(
            text="🔄 Обновить", callback_data=f"pf:refresh:{asset_type}:{symbol}"
        )
    ]
    if asset_type == "stock":
        row1.append(
            InlineKeyboardButton(text="📰 Новости", callback_data=f"news:{symbol}")
        )
    builder.row(*row1)
    builder.row(
        InlineKeyboardButton(text="🔔 Алерт", callback_data=f"pf:alert:{symbol}"),
        InlineKeyboardButton(text="✏️ Кол-во", callback_data=f"pf:qty:{symbol}"),
        InlineKeyboardButton(
            text="➖ Убрать", callback_data=f"pf:del:{asset_type}:{symbol}"
        ),
    )
    builder.row(InlineKeyboardButton(text="↩️ Портфель", callback_data="pf:menu"))
    return builder.as_markup()


async def _get_quantity(telegram_id: int, symbol: str) -> float | None:
    """Количество актива в портфеле пользователя (None — не задано)."""
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


def _cache_key(asset_type: str, symbol: str) -> str:
    """Кэш-ключ котировки (для stock — по resolved-тикеру)."""
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
    """Берёт котировку (с кэшем) и редактирует карточку портфеля."""
    try:
        quote = await fetch()
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
    quantity = await _get_quantity(callback.from_user.id, symbol)
    await callback.message.edit_text(
        _quote_text(asset_type, symbol, quote, quantity),
        reply_markup=_pf_quote_kb(asset_type, symbol),
        disable_web_page_preview=True,
    )
    await callback.answer()


async def _list_items(telegram_id: int, asset_type: str) -> list[PortfolioItem]:
    """Активы портфеля одной категории (сортировка по символу)."""
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
async def cmd_portfolio(message: Message) -> None:
    """Открывает inline-меню портфеля."""
    await open_portfolio(message)


# ---------------------------------------------------------- inline: главное меню


@router.callback_query(F.data == "pf:menu")
async def on_pf_menu(callback: CallbackQuery) -> None:
    """Показывает главное меню портфеля."""
    text, kb = await _render_pf_menu(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


async def _render_cat(
    callback: CallbackQuery, cache: TTLCache, asset_type: str
) -> None:
    """Список активов категории с живыми ценами."""
    items = await _list_items(callback.from_user.id, asset_type)
    if not items:
        await callback.message.edit_text(
            f"{TYPE_ICONS[asset_type]} <b>{TYPE_TITLES[asset_type]}</b>: пусто.\n"
            "Добавить: нажми «➕ Добавить» или открой цену актива в меню "
            "и нажми «➕ В портфель».",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Портфель", callback_data="pf:menu")]
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
        f"{TYPE_ICONS[asset_type]} <b>{TYPE_TITLES[asset_type]}</b> ({len(items)})\n"
    ]
    builder = InlineKeyboardBuilder()
    for item, quote in zip(items, quotes):
        if isinstance(quote, Exception):
            lines.append(f"{item.symbol} — недоступно")
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
            text="🔄 Обновить всё", callback_data=f"pf:cat_refresh:{asset_type}"
        ),
        InlineKeyboardButton(text="↩️ Портфель", callback_data="pf:menu"),
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:cat:(fx|stock|crypto)$"))
async def on_pf_cat(callback: CallbackQuery, cache: TTLCache) -> None:
    """Список активов категории с живыми ценами."""
    asset_type = callback.data.split(":", 2)[2]
    await _render_cat(callback, cache, asset_type)


@router.callback_query(F.data.regexp(r"^pf:cat_refresh:(fx|stock|crypto)$"))
async def on_pf_cat_refresh(callback: CallbackQuery, cache: TTLCache) -> None:
    """Сбрасывает кэш всех активов категории и пересобирает список."""
    asset_type = callback.data.split(":", 2)[2]
    for item in await _list_items(callback.from_user.id, asset_type):
        await cache.delete(_cache_key(asset_type, item.symbol))
    await _render_cat(callback, cache, asset_type)


@router.callback_query(F.data.regexp(r"^pf:view:(fx|stock|crypto):[A-Z0-9.\-]+$"))
async def on_pf_view(callback: CallbackQuery, cache: TTLCache) -> None:
    """Детальная карточка актива из портфеля."""
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
    """Обновляет карточку актива из портфеля (сброс кэша)."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    await cache.delete(_cache_key(asset_type, symbol))
    await _quote_and_edit_pf(
        callback,
        cache,
        asset_type,
        symbol,
        lambda: _fetch_quote(asset_type, symbol, cache),
    )


# ---------------------------------------------------------- inline: добавление


def _mark_added(markup: InlineKeyboardMarkup, symbol: str) -> InlineKeyboardMarkup:
    """Заменяет кнопку «➕ В портфель» на неактивную «✅ В портфеле»."""
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for btn in row:
            if btn.callback_data == f"pf:add:{symbol}":
                new_row.append(
                    InlineKeyboardButton(text="✅ В портфеле", callback_data="pf:added")
                )
            else:
                new_row.append(btn)
        rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.regexp(r"^pf:add:[A-Z0-9.\-]+$"))
async def on_pf_add(callback: CallbackQuery) -> None:
    """Добавляет актив в портфель (кнопка в карточке основного меню)."""
    symbol = callback.data.split(":", 2)[2]
    asset_type = resolve_asset_type(symbol)
    if asset_type is None:
        await callback.answer("Не удалось определить тип актива. 😔", show_alert=True)
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
        f"✅ {symbol} добавлен в портфель" if added else f"{symbol} уже в портфеле"
    )


# ---------------------------------------------------------- inline: FSM добавления


async def _add_item(
    telegram_id: int, symbol: str, asset_type: str, quantity: float | None
) -> bool:
    """Добавляет актив в портфель; True если добавлен (не было до этого)."""
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
    """Парсит количество; None — некорректное значение."""
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
    """Сообщение о результате добавления актива в портфель."""
    qty_suffix = f" ({_fmt_qty(quantity)} шт.)" if quantity is not None else ""
    if added:
        return (
            f"✅ {TYPE_ICONS[asset_type]} <b>{symbol}</b> добавлен в портфель "
            f"({TYPE_TITLES[asset_type]}){qty_suffix}."
        )
    return f"{TYPE_ICONS[asset_type]} <b>{symbol}</b> уже в портфеле."


@router.callback_query(F.data == "pf:add_menu")
async def on_pf_add_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Начинает FSM добавления произвольного актива."""
    await state.set_state(AddState.symbol)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="↩️ Отмена", callback_data="pf:add_cancel"))
    await callback.message.edit_text(
        "➕ Введи символ актива (например <b>BTC</b>, <b>AAPL</b>, "
        "<b>USD</b>, <b>SPX</b>). /cancel — выйти.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.message(AddState.symbol)
async def on_add_symbol(message: Message, state: FSMContext) -> None:
    """Принимает символ и спрашивает количество."""
    symbol = (message.text or "").strip().upper().split(" ", 1)[0]
    asset_type = resolve_asset_type(symbol)
    if asset_type is None:
        await message.answer(
            "Не распознал актив. Введи тикер акции (AAPL), монету (BTC) "
            "или валюту (USD)."
        )
        return
    await state.update_data(symbol=symbol, asset_type=asset_type)
    await state.set_state(AddState.quantity)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏭️ Пропустить", callback_data="pf:add_skip"),
        InlineKeyboardButton(text="↩️ Отмена", callback_data="pf:add_cancel"),
    )
    await message.answer(
        f"Сколько у тебя <b>{symbol}</b>? Напиши число (например 5 или 0.5) "
        "или пропусти.",
        reply_markup=builder.as_markup(),
    )


@router.message(AddState.quantity)
async def on_add_quantity(message: Message, state: FSMContext) -> None:
    """Принимает количество и добавляет актив в портфель."""
    qty = await _parse_qty((message.text or "").strip())
    if qty is None:
        await message.answer("Это не число. Напиши количество цифрами (например 5).")
        return
    data = await state.get_data()
    symbol = data["symbol"]
    asset_type = data["asset_type"]
    added = await _add_item(message.from_user.id, symbol, asset_type, qty)
    await state.clear()
    _, kb = await _render_pf_menu(message.from_user.id)
    await message.answer(
        await _added_message(added, symbol, asset_type, qty), reply_markup=kb
    )


@router.callback_query(F.data == "pf:add_skip")
async def on_pf_add_skip(callback: CallbackQuery, state: FSMContext) -> None:
    """Добавляет актив без количества."""
    data = await state.get_data()
    symbol = data.get("symbol")
    asset_type = data.get("asset_type")
    if not symbol or not asset_type:
        await state.clear()
        await callback.answer("Диалог устарел. Начни заново.", show_alert=True)
        return
    added = await _add_item(callback.from_user.id, symbol, asset_type, None)
    await state.clear()
    _, kb = await _render_pf_menu(callback.from_user.id)
    await callback.message.answer(
        await _added_message(added, symbol, asset_type, None), reply_markup=kb
    )
    await callback.answer()


@router.callback_query(F.data == "pf:add_cancel")
async def on_pf_add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отменяет FSM добавления актива."""
    await state.clear()
    text, kb = await _render_pf_menu(callback.from_user.id)
    await callback.message.edit_text(f"Отменено.\n\n{text}", reply_markup=kb)
    await callback.answer()


# -------------------------------------------------- inline: FSM количества


@router.callback_query(F.data.regexp(r"^pf:qty:[A-Z0-9.\-]+$"))
async def on_pf_qty(callback: CallbackQuery, state: FSMContext) -> None:
    """Начинает FSM изменения количества актива."""
    symbol = callback.data.split(":", 2)[2]
    await state.set_state(QtyState.quantity)
    await state.update_data(symbol=symbol)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="↩️ Отмена", callback_data="pf:qty_cancel"))
    await callback.message.edit_text(
        f"✏️ Сколько у тебя <b>{symbol}</b>? Введи число (например 5 или 0.5).",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.message(QtyState.quantity)
async def on_qty_value(message: Message, state: FSMContext) -> None:
    """Принимает количество и сохраняет его."""
    qty = await _parse_qty((message.text or "").strip())
    if qty is None:
        await message.answer("Это не число. Напиши количество цифрами (например 5).")
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
    _, kb = await _render_pf_menu(message.from_user.id)
    await message.answer(
        f"✅ Для <b>{symbol}</b> задано количество {_fmt_qty(qty)}.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "pf:qty_cancel")
async def on_pf_qty_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отменяет изменение количества."""
    await state.clear()
    text, kb = await _render_pf_menu(callback.from_user.id)
    await callback.message.edit_text(f"Отменено.\n\n{text}", reply_markup=kb)
    await callback.answer()


# ---------------------------------------------------------- inline: удаление


async def _remove_kb(telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Список всех активов портфеля с кнопками удаления."""
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
            "📁 Портфель пуст.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Портфель", callback_data="pf:menu")]
                ]
            ),
        )
    lines = ["➖ <b>Выбери актив для удаления</b>\n"]
    builder = InlineKeyboardBuilder()
    for item in items:
        lines.append(
            f"{TYPE_ICONS.get(item.asset_type, '')} {item.symbol} "
            f"({TYPE_TITLES[item.asset_type]})"
        )
        builder.row(
            InlineKeyboardButton(
                text=f"🗑 {item.symbol}",
                callback_data=f"pf:del:{item.asset_type}:{item.symbol}",
            )
        )
    builder.row(InlineKeyboardButton(text="↩️ Портфель", callback_data="pf:menu"))
    return "\n".join(lines), builder.as_markup()


@router.callback_query(F.data == "pf:remove")
async def on_pf_remove(callback: CallbackQuery) -> None:
    """Показывает список активов для удаления."""
    text, kb = await _remove_kb(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:del:(fx|stock|crypto):[A-Z0-9.\-]+$"))
async def on_pf_del(callback: CallbackQuery) -> None:
    """Запрашивает подтверждение удаления актива."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, убрать",
            callback_data=f"pf:confirm_del:{asset_type}:{symbol}",
        ),
        InlineKeyboardButton(text="↩️ Отмена", callback_data="pf:cancel_del"),
    )
    await callback.message.edit_text(
        f"Удалить {TYPE_ICONS[asset_type]} <b>{symbol}</b> из портфеля?",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(
    F.data.regexp(r"^pf:confirm_del:(fx|stock|crypto):[A-Z0-9.\-]+$")
)
async def on_pf_confirm_del(callback: CallbackQuery) -> None:
    """Подтверждённое удаление актива из портфеля."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    async for session in get_session():
        await session.execute(
            delete(PortfolioItem).where(
                PortfolioItem.telegram_id == callback.from_user.id,
                PortfolioItem.symbol == symbol,
            )
        )
        await session.commit()
    text, kb = await _render_pf_menu(callback.from_user.id)
    await callback.message.edit_text(
        f"✅ {TYPE_ICONS[asset_type]} <b>{symbol}</b> убран из портфеля.\n\n{text}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "pf:cancel_del")
async def on_pf_cancel_del(callback: CallbackQuery) -> None:
    """Отменяет удаление и возвращает главное меню портфеля."""
    text, kb = await _render_pf_menu(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject) -> None:
    """Добавляет актив в портфель: /add BTC (или AAPL, USD)."""
    symbol = ((command.args or "").strip().upper()).split(" ", 1)[0]
    asset_type = resolve_asset_type(symbol) if symbol else None
    if asset_type is None:
        await message.answer(
            "Не понимаю, что добавить. Примеры: /add BTC, /add AAPL, /add USD"
        )
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
        f"📁 <b>{symbol}</b> добавлен в портфель ({TYPE_ICONS[asset_type]}"
        f"{TYPE_TITLES[asset_type]})."
        if added
        else f"📁 <b>{symbol}</b> уже в портфеле."
    )


@router.message(Command("remove"))
async def cmd_remove(message: Message, command: CommandObject) -> None:
    """Убирает актив из портфеля: /remove BTC."""
    symbol = ((command.args or "").strip().upper()).split(" ", 1)[0]
    if not symbol:
        await message.answer("Укажи актив: /remove BTC")
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
        f"📁 <b>{symbol}</b> убран из портфеля."
        if removed
        else f"📁 <b>{symbol}</b> не было в портфеле."
    )


@router.message(Command("alert"))
async def cmd_alert(message: Message, command: CommandObject) -> None:
    """Ставит алерт на цену: /alert BTC 70000 или /alert BTC below 50000."""
    parsed = parse_alert_args(command.args or "")
    if parsed is None:
        await message.answer(
            "Формат: /alert <символ> [выше|below] <цена>\n"
            "Примеры: /alert BTC 70000 (выше 70 000)\n"
            "         /alert ETH below 3500 (ниже 3 500)"
        )
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
    arrow = "выше" if direction == "above" else "ниже"
    await message.answer(
        f"🔔 Алерт установлен: <b>{symbol}</b> {arrow} "
        f"<b>${target:,.2f}</b>\nПроверяется каждые 30 минут. /alerts — список"
    )


@router.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    """Показывает активные алерты пользователя."""
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
        await message.answer("🔕 Активных алертов нет.\nСоздать: /alert BTC 70000")
        return
    lines = ["🔔 <b>Мои алерты</b>\n"]
    for num, a in enumerate(alerts, 1):
        lines.append(f"• <code>{num}</code>. {_alert_line(a)}")
    lines.append("\nУбрать: /portfolio → 🔔 Алерты")
    await message.answer("\n".join(lines))


# ---------------------------------------------------------- inline: алерты


def _alert_line(a: Alert) -> str:
    """Однострочное описание алерта (без номера — нумерация в списке)."""
    arrow = "выше" if a.direction == "above" else "ниже"
    return (
        f"{TYPE_ICONS.get(a.asset_type, '')}"
        f"<b>{a.symbol}</b> {arrow} ${a.target_price:,.2f}"
    )


async def _alerts_text_kb(telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и клавиатура списка алертов с кнопками удаления."""
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
            (
                "🔕 Активных алертов нет.\n\nСоздать можно из карточки актива — "
                "кнопка «🔔 Алерт» — или командой: /alert BTC 70000."
            ),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Портфель", callback_data="pf:menu")]
                ]
            ),
        )
    lines = ["🔔 <b>Мои алерты</b>\n"]
    builder = InlineKeyboardBuilder()
    for num, a in enumerate(alerts, 1):
        lines.append(f"• <code>{num}</code>. {_alert_line(a)}")
        builder.row(
            InlineKeyboardButton(
                text=f"🗑 Убрать #{num}", callback_data=f"pf:alert_del:{a.id}"
            )
        )
    builder.row(InlineKeyboardButton(text="↩️ Портфель", callback_data="pf:menu"))
    return "\n".join(lines), builder.as_markup()


@router.callback_query(F.data == "pf:alerts")
async def on_pf_alerts(callback: CallbackQuery) -> None:
    """Показывает список алертов пользователя."""
    text, kb = await _alerts_text_kb(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:alert_del:\d+$"))
async def on_pf_alert_del(callback: CallbackQuery) -> None:
    """Запрашивает подтверждение удаления алерта."""
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
        await callback.answer("Алерт не найден.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, удалить", callback_data=f"pf:confirm_alert_del:{alert_id}"
        ),
        InlineKeyboardButton(text="↩️ Отмена", callback_data="pf:cancel_alert_del"),
    )
    await callback.message.edit_text(
        f"Удалить алерт?\n\n{_alert_line(alert)}",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^pf:confirm_alert_del:\d+$"))
async def on_pf_confirm_alert_del(callback: CallbackQuery) -> None:
    """Подтверждённое удаление алерта."""
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
        f"✅ Алерт <code>{alert_id}</code> удалён.\n\n{text}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "pf:cancel_alert_del")
async def on_pf_cancel_alert_del(callback: CallbackQuery) -> None:
    """Отменяет удаление алерта."""
    text, kb = await _alerts_text_kb(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------------------------------------------------------- inline: FSM алерта


async def _cached_price_hint(asset_type: str, symbol: str, cache: TTLCache) -> str:
    """Текущая цена из кэша (подсказка при вводе цены алерта)."""
    quote = await cache.get(_cache_key(asset_type, symbol))
    if quote is None:
        return ""
    if asset_type == "fx":
        return f"Текущая цена: <b>{quote.value:.2f} ₽</b>\n"
    sign = "+" if quote.change_percent >= 0 else ""
    return (
        f"Текущая цена: <b>${quote.price:,.2f}</b> "
        f"({sign}{quote.change_percent:.2f}%)\n"
    )


@router.callback_query(F.data.regexp(r"^pf:alert:[A-Z0-9.\-]+$"))
async def on_pf_alert(
    callback: CallbackQuery, state: FSMContext, cache: TTLCache
) -> None:
    """Начинает FSM создания алерта: просит цену."""
    symbol = callback.data.split(":", 2)[2]
    asset_type = resolve_asset_type(symbol) or "stock"
    await state.set_state(AlertState.price)
    await state.update_data(symbol=symbol, asset_type=asset_type)
    hint = await _cached_price_hint(asset_type, symbol, cache)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="↩️ Отмена", callback_data="pf:alert_cancel"))
    await callback.message.edit_text(
        f"🔔 Цена алерта для <b>{symbol}</b>?\n{hint}Напиши число.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.message(AlertState.price)
async def on_alert_price(message: Message, state: FSMContext) -> None:
    """Принимает цену алерта и спрашивает направление."""
    raw = (message.text or "").strip().replace(",", ".")
    try:
        price = float(raw)
    except ValueError:
        await message.answer("Это не число. Напиши цену цифрами.")
        return
    if price <= 0:
        await message.answer("Цена должна быть больше нуля.")
        return
    await state.update_data(price=price)
    await state.set_state(AlertState.direction)
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬆️ Выше", callback_data="pf:alert_dir:above"),
        InlineKeyboardButton(text="⬇️ Ниже", callback_data="pf:alert_dir:below"),
    )
    builder.row(InlineKeyboardButton(text="↩️ Отмена", callback_data="pf:alert_cancel"))
    await message.answer(
        f"🔔 <b>{data['symbol']}</b>: {price:,.2f}. Сработает, когда цена будет "
        "выше или ниже?",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.regexp(r"^pf:alert_dir:(above|below)$"))
async def on_alert_dir(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор направления: создаёт алерт и завершает FSM."""
    direction = callback.data.split(":", 2)[2]
    data = await state.get_data()
    symbol = data.get("symbol")
    price = data.get("price")
    if not symbol or not price:
        await state.clear()
        await callback.answer("Диалог устарел. Начни заново.", show_alert=True)
        return
    asset_type = data.get("asset_type") or resolve_asset_type(symbol) or "stock"
    async for session in get_session():
        session.add(
            Alert(
                telegram_id=callback.from_user.id,
                asset_type=asset_type,
                symbol=symbol,
                target_price=price,
                direction=direction,
            )
        )
        await session.commit()
    await state.clear()
    arrow = "выше" if direction == "above" else "ниже"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="↩️ Портфель", callback_data="pf:menu"))
    await callback.message.edit_text(
        f"🔔 Алерт установлен: <b>{symbol}</b> {arrow} <b>${price:,.2f}</b>",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "pf:alert_cancel")
async def on_alert_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отменяет FSM создания алерта."""
    await state.clear()
    text, kb = await _render_pf_menu(callback.from_user.id)
    await callback.message.edit_text(f"Отменено.\n\n{text}", reply_markup=kb)
    await callback.answer()


@router.message(Command("remove_alert"))
async def cmd_remove_alert(message: Message, command: CommandObject) -> None:
    """Удаляет алерт по id: /remove_alert 3."""
    try:
        alert_id = int((command.args or "").strip().split()[0])
    except (ValueError, IndexError):
        await message.answer("Укажи id алерта: /remove_alert 3 (id видно в /alerts)")
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
        f"✅ Алерт <code>{alert_id}</code> удалён."
        if removed
        else f"⚠️ Алерт <code>{alert_id}</code> не найден (он твой и активен?)."
    )
