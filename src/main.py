"""Точка входа бота: запуск в polling-режиме.

Запуск: python -m src.main [--reload]
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

from src.config.settings import get_settings
from src.database.db import close_db, create_tables
from src.handlers.admin import router as admin_router
from src.handlers.analyze import router as analyze_router
from src.handlers.crypto import router as crypto_router
from src.handlers.errors import router as errors_router
from src.handlers.help import router as help_router
from src.handlers.menu import router as menu_router
from src.handlers.rate import router as rate_router
from src.handlers.start import router as start_router
from src.handlers.stock import router as stock_router
from src.middleware.query_log import QueryLogMiddleware
from src.middleware.throttling import ThrottlingMiddleware
from src.middleware.users import BotStats, UsersMiddleware
from src.services.cache import TTLCache
from src.utils.redact import RedactFormatter

log = logging.getLogger(__name__)


def setup_logging() -> None:
    """Настраивает логирование: консоль + ротация файлов (AGENTS.md п.8).

    PII (телефоны, email, номера карт) маскируются форматтером (п.9).
    """
    fmt = RedactFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        "logs/bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


async def _setup_bot_commands(bot: Bot, admin_id: int | None) -> None:
    """Список команд в интерфейсе Telegram: общие и админские (отдельным scope)."""
    user_commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="rate", description="Курс валюты: /rate USD"),
        BotCommand(command="convert", description="Конвертер: /convert 100 USD RUB"),
        BotCommand(command="stock", description="Цена акции: /stock AAPL"),
        BotCommand(command="crypto", description="Цена крипты: /crypto BTC"),
        BotCommand(command="trending", description="Топ трендовых монет"),
        BotCommand(command="top", description="Топ по капитализации"),
        BotCommand(command="news", description="Новости по тикеру: /news AAPL"),
        BotCommand(command="analyze", description="AI-анализ: /analyze BTC"),
        BotCommand(command="myrole", description="Моя роль"),
        BotCommand(command="help", description="Справка"),
    ]
    await bot.set_my_commands(user_commands)
    if admin_id:
        await bot.set_my_commands(
            [
                BotCommand(command="admin", description="Панель администратора"),
                BotCommand(command="users", description="Список пользователей"),
                BotCommand(
                    command="broadcast", description="Рассылка: /broadcast текст"
                ),
                BotCommand(command="ban", description="Бан: /ban id"),
                BotCommand(command="unban", description="Разбан: /unban id"),
                BotCommand(command="cachestats", description="Статистика кэша"),
                BotCommand(command="recent", description="Последние запросы"),
                BotCommand(
                    command="setrole", description="Назначить роль: /setRole id role"
                ),
            ],
            scope=BotCommandScopeChat(chat_id=admin_id),
        )


async def main() -> None:
    """Собирает приложение и запускает поллинг."""
    settings = get_settings()

    bot = Bot(
        settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    cache = TTLCache()
    cache.start_gc()
    dp["cache"] = cache  # DI для хендлеров (ключ — имя параметра)

    dp.message.outer_middleware(ThrottlingMiddleware(settings.rate_limit_per_minute))
    dp.callback_query.outer_middleware(
        ThrottlingMiddleware(settings.rate_limit_per_minute)
    )
    stats = BotStats()
    dp.message.outer_middleware(UsersMiddleware(stats))
    dp.callback_query.outer_middleware(UsersMiddleware(stats))
    dp.message.outer_middleware(QueryLogMiddleware())
    dp.callback_query.outer_middleware(QueryLogMiddleware())

    dp.include_router(errors_router)
    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(help_router)
    dp.include_router(menu_router)
    dp.include_router(rate_router)
    dp.include_router(stock_router)
    dp.include_router(crypto_router)
    dp.include_router(analyze_router)

    await create_tables()
    await _setup_bot_commands(bot, settings.admin_id)

    log.info("Бот запущен (polling)")
    try:
        await dp.start_polling(bot)
    finally:
        await cache.stop_gc()
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    setup_logging()
    import asyncio
    import sys

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    except Exception:
        log.exception("Критическая ошибка при запуске")
        sys.exit(1)
