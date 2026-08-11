"""Middleware истории запросов: пишет каждое событие пользователя в БД.

Хранится минимум данных (AGENTS.md п.9): telegram_id, тип события,
обрезанный текст — без имён и контактов. Сбой записи не роняет запрос.
"""

import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.database.db import get_session
from src.database.models import QueryLog

log = logging.getLogger(__name__)

MAX_PAYLOAD_LENGTH = 300
MAX_COMMAND_LENGTH = 64


class QueryLogMiddleware(BaseMiddleware):
    """Логирует сообщения и колбэки в таблицу query_log."""

    async def __call__(self, handler, event: TelegramObject, data: dict) -> object:
        try:
            await self._write(event)
        except Exception:  # noqa: BLE001 — лог не должен ронять запрос
            log.warning("Не удалось записать запрос в историю")
        return await handler(event, data)

    @staticmethod
    async def _write(event: TelegramObject) -> None:
        if isinstance(event, Message):
            if event.from_user is None:
                return
            text = (event.text or "").strip()
            if not text:
                return
            command, _, rest = text.partition(" ")
            record = QueryLog(
                telegram_id=event.from_user.id,
                event_type="message",
                command=command[:MAX_COMMAND_LENGTH],
                payload=(rest or text)[:MAX_PAYLOAD_LENGTH],
            )
        elif isinstance(event, CallbackQuery):
            record = QueryLog(
                telegram_id=event.from_user.id,
                event_type="callback",
                command=(event.data or "")[:MAX_COMMAND_LENGTH],
                payload=None,
            )
        else:
            return
        async for session in get_session():
            session.add(record)
            await session.commit()
