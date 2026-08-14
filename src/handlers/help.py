"""Обработчик команды /help и кнопки «Помощь»."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.handlers.menu import MAIN_MENU
from src.i18n import t

router = Router()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Показывает справку и меню."""
    await message.answer(t("start.help_text"), reply_markup=MAIN_MENU)
