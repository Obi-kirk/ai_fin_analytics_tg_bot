"""Обработчик команды /start — приветствие, меню и описание."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.handlers.help import HELP_TEXT
from src.handlers.menu import MAIN_MENU

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Показывает приветствие, меню и список команд."""
    await message.answer(HELP_TEXT, reply_markup=MAIN_MENU)
