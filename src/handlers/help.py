"""Обработчик команды /help и кнопки «Помощь»."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.handlers.menu import MAIN_MENU

router = Router()

HELP_TEXT = (
    "🤖 <b>Как пользоваться ботом</b>\n\n"
    "Нажимай кнопки меню или пиши команды:\n"
    "• /rate <b>USD</b> — курс валюты (USD, EUR, CNY...)\n"
    "• /stock <b>AAPL</b> — цена акции (SPX, DJI — индексы)\n"
    "• /crypto <b>BTC</b> — цена криптовалюты\n"
    "• /trending — топ трендовых монет\n"
    "• /top — топ монет по капитализации\n"
    "• /news <b>AAPL</b> — последние новости по тикеру\n"
    "• /analyze <b>BTC</b> — AI-анализ актива\n"
    "• /help — эта справка\n\n"
    "Источники: ЦБ РФ (валюты), Finnhub (акции), CoinGecko (крипта).\n"
    "Данные кэшируются: валюты — 1 час, акции/крипта — 10 минут.\n"
    "Под котировками из меню (💱 Курсы, 📈 Акции, 🪙 Крипта) есть кнопки:\n"
    "• «🔄 Обновить» — снимает кэш и запрашивает свежие данные;\n"
    "• «↩️ Меню» — возвращает к списку активов.\n"
    "У ответов на команды (/rate, /stock, /crypto) кнопок нет.\n\n"
    "⚠️ Вся информация носит справочный характер и не является "
    "инвестиционной рекомендацией. Подробнее — по команде /start."
)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Показывает справку и меню."""
    await message.answer(HELP_TEXT, reply_markup=MAIN_MENU)
