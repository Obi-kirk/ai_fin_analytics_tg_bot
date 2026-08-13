"""Подписка на ежедневный дайджест: /digest + кнопки вкл/выкл."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, select

from src.config.settings import get_settings
from src.database.db import get_session
from src.database.models import DigestSubscription

router = Router()


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


def _status_text(subscribed: bool) -> str:
    """Текст статуса подписки."""
    settings = get_settings()
    state = "🔔 включена" if subscribed else "🔕 выключена"
    return (
        "📰 <b>Дневной дайджест</b>\n\n"
        f"Статус: <b>{state}</b>\n"
        f"Время отправки: каждый день в "
        f"{settings.digest_hour:02d}:{settings.digest_minute:02d}.\n\n"
        "В дайджесте: курсы ЦБ, топ акций и крипты, ваш портфель."
    )


def _status_kb(subscribed: bool) -> InlineKeyboardMarkup:
    """Кнопка подписки/отписки."""
    label = "🔕 Отписаться" if subscribed else "🔔 Подписаться"
    data = "dg:off" if subscribed else "dg:on"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data)]]
    )


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    """Показывает статус подписки на дневной дайджест."""
    subscribed = await _is_subscribed(message.from_user.id)
    await message.answer(
        _status_text(subscribed), reply_markup=_status_kb(subscribed)
    )


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
            session.add(
                DigestSubscription(telegram_id=callback.from_user.id)
            )
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
