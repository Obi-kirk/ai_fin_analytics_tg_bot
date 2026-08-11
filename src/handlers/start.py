"""Обработчик команды /start — приветствие и меню."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Показывает приветствие и список доступных команд."""
    text = (
        "Привет! Я финансовый аналитик 🤖\n\n"
        "Доступные команды:\n"
        "• /rate <b>USD</b> — курс валюты (USD, EUR, CNY...)\n"
        "• /stock <b>AAPL</b> — цена акции\n"
        "• /crypto <b>BTC</b> — цена криптовалюты\n"
        "• /analyze <b>AAPL</b> — AI-анализ акции\n"
        "• /help — справка\n\n"
        "Данные: ЦБ РФ, FCS API. Кэшируются для скорости."
    )
    await message.answer(text)
