"""Обработчик команды /start — приветствие, меню и дисклеймер.

Дисклеймер показывается отдельным сообщением при каждом /start:
пользователь явно подтверждает осведомлённость перед использованием.
"""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.handlers.menu import MAIN_MENU
from src.i18n import t

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Показывает приветствие, меню и дисклеймер."""
    await message.answer(t("start.help_text"), reply_markup=MAIN_MENU)
    await message.answer(t("start.disclaimer"))
