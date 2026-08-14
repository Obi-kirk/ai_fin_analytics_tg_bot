"""Global error handler: logging without secrets + admin notification."""

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
    """Notifies the admin (AGENTS.md item 8). No PII."""
    admin_id = get_settings().admin_id
    if not admin_id:
        return
    try:
        await bot.send_message(
            admin_id,
            f"⚠️ <b>Bot error</b>\n<code>{error_text[:1500]}</code>",
        )
    except TelegramAPIError:
        log.warning("Failed to notify the admin about the error")


@router.errors()
async def on_error(event: ErrorEvent, **kwargs: Any) -> None:
    """Logs a handler error and notifies the admin."""
    error = event.exception
    log.error("Error in handler %s: %s", event.update.event_type, error)
    log.error("".join(traceback.format_exception(error)))

    bot = kwargs.get("bot")
    if isinstance(bot, Bot):
        await _notify_admin(bot, f"{type(error).__name__}: {error}")
