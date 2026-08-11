"""Глобальный обработчик ошибок: логирование без секретов + уведомление админа."""

import logging
import traceback
from typing import Any

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

from src.config.settings import get_settings

log = logging.getLogger(__name__)
router = Router()


async def _notify_admin(bot: Bot, error_text: str) -> None:
    """Отправляет уведомление администратору (AGENTS.md п.8). Без PII."""
    admin_id = get_settings().admin_id
    if not admin_id:
        return
    try:
        await bot.send_message(
            admin_id,
            f"⚠️ <b>Ошибка в боте</b>\n<code>{error_text[:1500]}</code>",
        )
    except TelegramAPIError:
        log.warning("Не удалось уведомить администратора об ошибке")


@router.errors()
async def on_error(event: ErrorEvent, **kwargs: Any) -> None:
    """Логирует ошибку хендлера и уведомляет администратора."""
    error = event.exception
    log.error("Ошибка в хендлере %s: %s", event.update.event_type, error)
    log.error("".join(traceback.format_exception(error)))

    bot = kwargs.get("bot")
    if isinstance(bot, Bot):
        await _notify_admin(bot, f"{type(error).__name__}: {error}")
