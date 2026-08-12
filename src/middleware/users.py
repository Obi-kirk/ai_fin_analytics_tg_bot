"""Middleware: учёт пользователей в БД + общая статистика событий.

Пользователь upsert'ится в таблицу users при первом контакте.
Бан проверяется здесь же: заблокированным пользователям бот не отвечает.
"""

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from src.config.settings import get_settings
from src.database.db import get_session
from src.database.models import User

log = logging.getLogger(__name__)

BAN_CACHE_SECONDS = 30


@dataclass
class BotStats:
    """Счётчики событий бота (для /admin)."""

    started_at: float = field(default_factory=time.monotonic)
    messages: int = 0
    callbacks: int = 0
    commands: Counter = field(default_factory=Counter)

    @property
    def uptime_seconds(self) -> int:
        return int(time.monotonic() - self.started_at)

    def uptime_human(self) -> str:
        minutes, seconds = divmod(self.uptime_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}ч {minutes}м {seconds}с"

    def top_commands(self, limit: int = 5) -> list[tuple[str, int]]:
        return self.commands.most_common(limit)


class UsersMiddleware(BaseMiddleware):
    """Считает события, записывает новых пользователей, фильтрует баны."""

    def __init__(self, stats: BotStats) -> None:
        self.stats = stats
        self._known: set[int] = set()
        self._roles: dict[int, str] = {}
        self._banned: dict[int, float] = {}

    def invalidate(self, user_id: int) -> None:
        """Сбрасывает кэш пользователя (например, после смены роли)."""
        self._known.discard(user_id)
        self._roles.pop(user_id, None)
        self._banned.pop(user_id, None)

    async def __call__(
        self,
        handler: Any,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
            self.stats.messages += 1
            if event.text and event.text.startswith("/"):
                cmd = event.text.split()[0].lstrip("/").lower()
                self.stats.commands[cmd] += 1
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id
            self.stats.callbacks += 1

        data["stats"] = self.stats
        data["invalidate_role"] = self.invalidate
        if user_id is None:
            return await handler(event, data)

        role, is_admin = await self._track_user(user_id, event)
        data["role"] = role
        data["is_admin"] = is_admin
        if await self._is_banned(user_id):
            log.info("Отклонён запрос забаненного пользователя id=%s", user_id)
            return None
        return await handler(event, data)

    async def _track_user(
        self, user_id: int, event: TelegramObject
    ) -> tuple[str, bool]:
        """Записывает нового пользователя в БД (один раз); возвращает (role, is_admin)."""
        settings = get_settings()
        super_admin = bool(settings.admin_id) and user_id == settings.admin_id
        if user_id in self._known:
            role = self._roles.get(user_id, "user")
            return role, super_admin or role == "admin"
        first_name = (
            event.from_user.first_name
            if isinstance(event, (Message, CallbackQuery)) and event.from_user
            else None
        )
        username = (
            event.from_user.username
            if isinstance(event, (Message, CallbackQuery)) and event.from_user
            else None
        )
        async for session in get_session():
            user = await session.get(User, user_id)
            if user is None:
                session.add(
                    User(
                        telegram_id=user_id,
                        first_name=first_name,
                        username=username,
                    )
                )
                role = "user"
            else:
                user.first_name = first_name or user.first_name
                user.username = username or user.username
                role = user.role or "user"
            await session.commit()
        self._known.add(user_id)
        self._roles[user_id] = role
        return role, super_admin or role == "admin"

    async def _is_banned(self, user_id: int) -> bool:
        """Проверяет бан с коротким in-memory кэшем."""
        now = time.monotonic()
        cached_at = self._banned.get(user_id)
        if cached_at is not None and now - cached_at < BAN_CACHE_SECONDS:
            return True  # пока кэш жив — считаем забаненным
        async for session in get_session():
            result = await session.execute(
                select(User.is_banned).where(User.telegram_id == user_id)
            )
            is_banned = bool(result.scalar())
        if is_banned:
            self._banned[user_id] = now
        else:
            self._banned.pop(user_id, None)
        return is_banned
