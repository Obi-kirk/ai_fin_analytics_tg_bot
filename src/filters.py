"""Фильтры aiogram для проверки прав доступа (RBAC, AGENTS.md п.4).

Role берётся из контекста ``data``, который кладёт :class:`UsersMiddleware`:
- ``role`` — «user» | «admin»
- ``is_admin`` — супер-админ (из .env) или роль admin

``SuperAdminFilter`` — только владелец из .env (для назначения ролей).
"""

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from src.config.settings import get_settings


class AdminFilter(BaseFilter):
    """Пропускает сообщения администраторов (из .env или с ролью admin)."""

    async def __call__(self, event: TelegramObject, data: dict[str, Any]) -> bool:
        return bool(data.get("is_admin"))


class SuperAdminFilter(BaseFilter):
    """Пропускает только владельца из ADMIN_ID (.env)."""

    async def __call__(self, event: TelegramObject, data: dict[str, Any]) -> bool:
        admin_id = get_settings().admin_id
        from_user = getattr(event, "from_user", None)
        return bool(admin_id) and from_user is not None and from_user.id == admin_id
