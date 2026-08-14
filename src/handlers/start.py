"""Handler for the /start command — greeting, menu and disclaimer.

The disclaimer is shown as a separate message on every /start:
the user explicitly acknowledges it before using the bot.
"""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.handlers.menu import main_menu_kb
from src.i18n import t

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Shows the greeting, menu and disclaimer."""
    await message.answer(t("start.help_text"), reply_markup=main_menu_kb())
    await message.answer(t("start.disclaimer"))
