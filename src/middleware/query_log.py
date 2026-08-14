"""Query history middleware: writes every user event to the DB.

Stores minimal data (AGENTS.md item 9): telegram_id, event type and a
truncated text — no names or contacts. A write failure must not break the request.
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
    """Logs messages and callbacks to the query_log table."""

    async def __call__(self, handler, event: TelegramObject, data: dict) -> object:
        try:
            await self._write(event)
        except Exception:  # noqa: BLE001 — logging must not break the request
            log.warning("Failed to write the request to history")
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
