"""aiogram filters for access control (RBAC, AGENTS.md item 4).

The role is taken from the middleware context by parameter name:
- ``is_admin`` — super-admin (from .env) or role admin

``SuperAdminFilter`` — only the owner from .env (for role assignment).
"""

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from src.config.settings import get_settings


class AdminFilter(BaseFilter):
    """Passes events from admins (from .env or with role admin)."""

    async def __call__(self, event: TelegramObject, is_admin: bool = False) -> bool:
        return bool(is_admin)


class SuperAdminFilter(BaseFilter):
    """Passes only the owner from ADMIN_ID (.env)."""

    async def __call__(self, event: TelegramObject) -> bool:
        admin_id = get_settings().admin_id
        from_user = getattr(event, "from_user", None)
        return bool(admin_id) and from_user is not None and from_user.id == admin_id
