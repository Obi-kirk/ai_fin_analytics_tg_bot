"""aiogram filters for access control (RBAC, AGENTS.md item 4).

The role is taken from the ``data`` context, populated by :class:`UsersMiddleware`:
- ``role`` — "user" | "admin"
- ``is_admin`` — super-admin (from .env) or role admin

``SuperAdminFilter`` — only the owner from .env (for role assignment).
"""

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from src.config.settings import get_settings


class AdminFilter(BaseFilter):
    """Passes messages from admins (from .env or with role admin)."""

    async def __call__(self, event: TelegramObject, data: dict[str, Any]) -> bool:
        return bool(data.get("is_admin"))


class SuperAdminFilter(BaseFilter):
    """Passes only the owner from ADMIN_ID (.env)."""

    async def __call__(self, event: TelegramObject, data: dict[str, Any]) -> bool:
        admin_id = get_settings().admin_id
        from_user = getattr(event, "from_user", None)
        return bool(admin_id) and from_user is not None and from_user.id == admin_id
