"""Middleware: tracks users in the DB + overall event statistics.

A user is upserted into the users table on first contact.
Bans are checked here as well: banned users get no response from the bot.
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
from src.i18n import set_lang

log = logging.getLogger(__name__)

BAN_CACHE_SECONDS = 30


@dataclass
class BotStats:
    """Bot event counters (for /admin)."""

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
        return f"{hours}h {minutes}m {seconds}s"

    def top_commands(self, limit: int = 5) -> list[tuple[str, int]]:
        return self.commands.most_common(limit)


class UsersMiddleware(BaseMiddleware):
    """Counts events, records new users, filters bans."""

    def __init__(self, stats: BotStats) -> None:
        self.stats = stats
        self._known: set[int] = set()
        self._roles: dict[int, str] = {}
        self._langs: dict[int, str] = {}
        self._banned: dict[int, float] = {}

    def invalidate(self, user_id: int) -> None:
        """Resets the user cache (e.g. after a role change)."""
        self._known.discard(user_id)
        self._roles.pop(user_id, None)
        self._langs.pop(user_id, None)
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
        data["lang"] = self._langs.get(user_id, get_settings().default_language)
        data["lang_set"] = user_id in self._langs
        set_lang(data["lang"])
        if await self._is_banned(user_id):
            log.info("Rejected request from banned user id=%s", user_id)
            return None
        return await handler(event, data)

    async def _track_user(
        self, user_id: int, event: TelegramObject
    ) -> tuple[str, bool]:
        """Records a new user in the DB (once); returns (role, is_admin)."""
        settings = get_settings()
        super_admin = bool(settings.admin_id) and user_id == settings.admin_id
        if user_id in self._known:
            role = self._roles.get(user_id, "user")
            if user_id not in self._langs:
                await self._load_lang(user_id)
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
                        language=None,
                    )
                )
                role = "user"
            else:
                user.first_name = first_name or user.first_name
                user.username = username or user.username
                role = user.role or "user"
                if user.language:
                    self._langs[user_id] = user.language
            await session.commit()
        self._known.add(user_id)
        self._roles[user_id] = role
        return role, super_admin or role == "admin"

    async def _load_lang(self, user_id: int) -> None:
        """Loads the user's language from the DB into the cache (once)."""
        async for session in get_session():
            user = await session.get(User, user_id)
            if user is not None and user.language:
                self._langs[user_id] = user.language

    async def _is_banned(self, user_id: int) -> bool:
        """Checks the ban with a short in-memory cache."""
        now = time.monotonic()
        cached_at = self._banned.get(user_id)
        if cached_at is not None and now - cached_at < BAN_CACHE_SECONDS:
            return True  # while the cache is alive — treat as banned
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
