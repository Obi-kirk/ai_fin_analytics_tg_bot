"""Handler for the /start command — greeting, menu and disclaimer.

The disclaimer is shown as a separate message on every /start:
the user explicitly acknowledges it before using the bot.
If the user has not chosen a language yet, a language picker is shown.
"""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from src.handlers.lang import lang_kb
from src.handlers.menu import main_menu_kb
from src.i18n import t

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, lang_set: bool = False) -> None:
    """Shows the greeting, menu and disclaimer."""
    await message.answer(t("start.help_text"), reply_markup=main_menu_kb())
    await message.answer(t("start.disclaimer"))
    if not lang_set:
        await message.answer(t("lang.prompt"), reply_markup=lang_kb())
