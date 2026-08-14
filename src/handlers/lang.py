"""The /lang command — bot language selection (ru/en), saved to users.language."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import update

from src.database.db import get_session
from src.database.models import User
from src.i18n import SUPPORTED_LANGUAGES, set_lang, t

router = Router()

_LANG_FLAGS = {"ru": "🇷🇺", "en": "🇬🇧"}


def lang_kb() -> InlineKeyboardMarkup:
    """Language selection buttons."""
    builder = InlineKeyboardBuilder()
    builder.row(
        *[
            InlineKeyboardButton(
                text=f"{_LANG_FLAGS.get(lang, '')} {t(f'lang.name.{lang}')}",
                callback_data=f"lang:{lang}",
            )
            for lang in SUPPORTED_LANGUAGES
        ]
    )
    return builder.as_markup()


@router.message(Command("lang"))
async def cmd_lang(message: Message) -> None:
    """Shows the language selection."""
    await message.answer(t("lang.prompt"), reply_markup=lang_kb())


@router.callback_query(F.data.regexp(r"^lang:(ru|en)$"))
async def on_lang_choose(callback: CallbackQuery) -> None:
    """Saves the selected language and confirms it."""
    lang = callback.data.split(":", 1)[1]
    set_lang(lang)
    async for session in get_session():
        await session.execute(
            update(User)
            .where(User.telegram_id == callback.from_user.id)
            .values(language=lang)
        )
        await session.commit()
    name = t("lang.name." + lang)
    await callback.message.edit_text(t("lang.set", name=name))
    await callback.answer()
