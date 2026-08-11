"""Rate limiting: не более N событий в минуту с одного пользователя.

Ограничивает сообщения и callback-запросы (защита от спама, AGENTS.md п.7).
"""

import logging
import time
from collections import defaultdict, deque
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

log = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0


class ThrottlingMiddleware(BaseMiddleware):
    """Скользящее окно: хранит метки времени событий каждого пользователя."""

    def __init__(self, rate_limit_per_minute: int) -> None:
        self._limit = rate_limit_per_minute
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def _allow(self, user_id: int, now: float) -> bool:
        history = self._events[user_id]
        while history and now - history[0] > WINDOW_SECONDS:
            history.popleft()
        if len(history) >= self._limit:
            return False
        history.append(now)
        return True

    async def __call__(
        self,
        handler: Any,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            user_id = event.from_user.id

        if user_id is not None and not self._allow(user_id, time.monotonic()):
            if isinstance(event, Message):
                await event.answer(
                    "⏳ Слишком часто! Подожди немного и попробуй снова."
                )
            else:
                await event.answer("⏳ Слишком часто! Подожди немного.")
            return None
        return await handler(event, data)
