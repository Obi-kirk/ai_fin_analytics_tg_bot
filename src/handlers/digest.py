"""Подписка и настройка дневного дайджеста: /digest + inline-кнопки.

Колбэки:
  dg:on / dg:off               — подписка / отписка
  dg:send                      — собрать дайджест сейчас (по своему набору)
  dg:setup                     — категории настройки набора
  dg:setup_cat:TYPE            — список активов категории (переключатели)
  dg:toggle:TYPE:SYM           — включить / выключить актив в наборе
  dg:back                      — возврат из настройки к статусу
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, select

from src.config.settings import get_settings
from src.database.db import get_session
from src.database.models import DigestAsset, DigestSubscription
from src.services.cache import TTLCache
from src.services.digest import DIGEST_AVAILABLE, build_digest

router = Router()

TYPE_ICONS = {"fx": "💱", "stock": "📈", "crypto": "🪙"}
TYPE_TITLES = {"fx": "Валюты", "stock": "Акции", "crypto": "Крипта"}


async def _is_subscribed(telegram_id: int) -> bool:
    """Подписан ли пользователь на дайджест."""
    async for session in get_session():
        sub = (
            await session.execute(
                select(DigestSubscription).where(
                    DigestSubscription.telegram_id == telegram_id
                )
            )
        ).scalar()
    return sub is not None


async def _asset_symbols(telegram_id: int, asset_type: str) -> set[str]:
    """Символы выбранного типа в персональном наборе."""
    async for session in get_session():
        rows = (
            (
                await session.execute(
                    select(DigestAsset.symbol).where(
                        DigestAsset.telegram_id == telegram_id,
                        DigestAsset.asset_type == asset_type,
                    )
                )
            )
            .scalars()
            .all()
        )
    return set(rows)


def _status_text(subscribed: bool) -> str:
    """Текст статуса подписки."""
    settings = get_settings()
    state = "🔔 включена" if subscribed else "🔕 выключена"
    return (
        "📰 <b>Дневной дайджест</b>\n\n"
        f"Статус: <b>{state}</b>\n"
        f"Время отправки: каждый день в "
        f"{settings.digest_hour:02d}:{settings.digest_minute:02d}.\n\n"
        "Настрой свой набор активов или собери дайджест прямо сейчас."
    )


def _status_kb(subscribed: bool) -> InlineKeyboardMarkup:
    """Кнопки статуса: настройка, собрать, подписка."""
    sub_label = "🔕 Отписаться" if subscribed else "🔔 Подписаться"
    sub_data = "dg:off" if subscribed else "dg:on"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚙️ Настроить набор", callback_data="dg:setup"),
        InlineKeyboardButton(text="📤 Собрать сейчас", callback_data="dg:send"),
    )
    builder.row(InlineKeyboardButton(text=sub_label, callback_data=sub_data))
    return builder.as_markup()


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    """Показывает статус подписки на дневной дайджест."""
    subscribed = await _is_subscribed(message.from_user.id)
    await message.answer(_status_text(subscribed), reply_markup=_status_kb(subscribed))


@router.callback_query(F.data == "dg:on")
async def on_digest_on(callback: CallbackQuery) -> None:
    """Подписка на дайджест."""
    async for session in get_session():
        exists = (
            await session.execute(
                select(DigestSubscription).where(
                    DigestSubscription.telegram_id == callback.from_user.id
                )
            )
        ).scalar()
        if exists is None:
            session.add(DigestSubscription(telegram_id=callback.from_user.id))
            await session.commit()
    await callback.message.edit_text(
        f"✅ Вы подписаны на дневной дайджест.\n\n{_status_text(True)}",
        reply_markup=_status_kb(True),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:off")
async def on_digest_off(callback: CallbackQuery) -> None:
    """Отписка от дайджеста."""
    async for session in get_session():
        await session.execute(
            delete(DigestSubscription).where(
                DigestSubscription.telegram_id == callback.from_user.id
            )
        )
        await session.commit()
    await callback.message.edit_text(
        f"🔕 Вы отписаны от дайджеста.\n\n{_status_text(False)}",
        reply_markup=_status_kb(False),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:send")
async def on_digest_send(callback: CallbackQuery, cache: TTLCache) -> None:
    """Собирает дайджест по набору пользователя и отправляет сейчас."""
    await callback.answer("Собираю дайджест…")
    try:
        text = await build_digest(callback.from_user.id, cache)
        await callback.message.answer(text)
    except Exception:  # noqa: BLE001 — граница внешнего API
        await callback.message.answer("😔 Не удалось собрать дайджест. Попробуй позже.")


async def _setup_kb(telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура категорий настройки набора."""
    counts = {t: len(await _asset_symbols(telegram_id, t)) for t in TYPE_TITLES}
    builder = InlineKeyboardBuilder()
    builder.row(
        *[
            InlineKeyboardButton(
                text=(
                    f"{TYPE_ICONS[t]} {TYPE_TITLES[t]} "
                    f"({counts[t]}/{len(DIGEST_AVAILABLE[t])})"
                ),
                callback_data=f"dg:setup_cat:{t}",
            )
            for t in ("fx", "stock", "crypto")
        ]
    )
    builder.row(InlineKeyboardButton(text="↩️ Назад", callback_data="dg:back"))
    return builder.as_markup()


async def _toggle_kb(telegram_id: int, asset_type: str) -> InlineKeyboardMarkup:
    """Кнопки-переключатели активов категории."""
    selected = await _asset_symbols(telegram_id, asset_type)
    builder = InlineKeyboardBuilder()
    symbols = DIGEST_AVAILABLE[asset_type]
    for i in range(0, len(symbols), 3):
        builder.row(
            *[
                InlineKeyboardButton(
                    text=f"{'✅' if s in selected else '☑️'} {s}",
                    callback_data=f"dg:toggle:{asset_type}:{s}",
                )
                for s in symbols[i : i + 3]
            ]
        )
    builder.row(InlineKeyboardButton(text="↩️ Категории", callback_data="dg:setup"))
    return builder.as_markup()


@router.callback_query(F.data == "dg:setup")
async def on_digest_setup(callback: CallbackQuery) -> None:
    """Меню настройки набора активов."""
    await callback.message.edit_text(
        "⚙️ <b>Настройка дайджеста</b>\n\n"
        "Выбери категорию, чтобы включить активы в свой набор. "
        "Если набор пуст — присылается дефолтный топ.",
        reply_markup=await _setup_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^dg:setup_cat:(fx|stock|crypto)$"))
async def on_digest_setup_cat(callback: CallbackQuery) -> None:
    """Список активов категории с переключателями."""
    asset_type = callback.data.split(":", 2)[2]
    selected = await _asset_symbols(callback.from_user.id, asset_type)
    await callback.message.edit_text(
        f"{TYPE_ICONS[asset_type]} <b>{TYPE_TITLES[asset_type]}</b> "
        f"({len(selected)}/{len(DIGEST_AVAILABLE[asset_type])})\n\n"
        "Нажимай на актив, чтобы включить или выключить его в дайджесте.",
        reply_markup=await _toggle_kb(callback.from_user.id, asset_type),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^dg:toggle:(fx|stock|crypto):[A-Z0-9.\-]+$"))
async def on_digest_toggle(callback: CallbackQuery) -> None:
    """Включает / выключает актив в персональном наборе."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    async for session in get_session():
        exists = (
            await session.execute(
                select(DigestAsset).where(
                    DigestAsset.telegram_id == callback.from_user.id,
                    DigestAsset.symbol == symbol,
                )
            )
        ).scalar()
        if exists is None:
            session.add(
                DigestAsset(
                    telegram_id=callback.from_user.id,
                    asset_type=asset_type,
                    symbol=symbol,
                )
            )
        else:
            await session.execute(
                delete(DigestAsset).where(
                    DigestAsset.telegram_id == callback.from_user.id,
                    DigestAsset.symbol == symbol,
                )
            )
        await session.commit()
    selected = await _asset_symbols(callback.from_user.id, asset_type)
    await callback.message.edit_text(
        f"{TYPE_ICONS[asset_type]} <b>{TYPE_TITLES[asset_type]}</b> "
        f"({len(selected)}/{len(DIGEST_AVAILABLE[asset_type])})\n\n"
        "Нажимай на актив, чтобы включить или выключить его в дайджесте.",
        reply_markup=await _toggle_kb(callback.from_user.id, asset_type),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:back")
async def on_digest_back(callback: CallbackQuery) -> None:
    """Возврат из настройки к статусу подписки."""
    subscribed = await _is_subscribed(callback.from_user.id)
    await callback.message.edit_text(
        _status_text(subscribed), reply_markup=_status_kb(subscribed)
    )
    await callback.answer()
