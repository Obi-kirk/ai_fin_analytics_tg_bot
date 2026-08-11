"""Тесты административных функций (без БД и сети).

Проверяются чистые функции: проверка прав админа, пагинация,
парсинг аргументов, сериализация callback-данных.
"""

import pytest
from aiogram.filters import CommandObject
from aiogram.types import User as TelegramUser

from src.handlers import admin as admin_mod
from src.handlers.admin import (
    BroadcastCD,
    UsersPageCD,
    _is_admin,
    _parse_target,
    _users_page_kb,
    is_admin_command,
)


class _FakeSettings:
    admin_id: int | None = 123456789


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    """Подменяет настройки: ADMIN_ID = 123456789 (один инстанс на тест)."""
    fake = _FakeSettings()
    monkeypatch.setattr(admin_mod, "get_settings", lambda: fake)
    return fake


def _user(user_id: int) -> TelegramUser:
    return TelegramUser(id=user_id, is_bot=False, first_name="Test")


class TestIsAdmin:
    def test_admin_allowed(self, admin_settings) -> None:
        assert _is_admin(_user(123456789)) is True

    def test_regular_user_denied(self, admin_settings) -> None:
        assert _is_admin(_user(42)) is False

    def test_filter_wrapper(self, admin_settings) -> None:
        assert is_admin_command(_user(123456789)) is True
        assert is_admin_command(_user(42)) is False

    def test_no_admin_id_set(self, admin_settings: _FakeSettings) -> None:
        admin_settings.admin_id = None
        assert _is_admin(_user(123456789)) is False


class TestUsersPageKb:
    async def test_first_page(self) -> None:
        kb = await _users_page_kb(page=1, pages=3)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert texts == ["1/3", "▶️"]

    async def test_middle_page(self) -> None:
        kb = await _users_page_kb(page=2, pages=3)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert texts == ["◀️", "2/3", "▶️"]

    async def test_last_page(self) -> None:
        kb = await _users_page_kb(page=3, pages=3)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert texts == ["◀️", "3/3"]

    async def test_single_page(self) -> None:
        kb = await _users_page_kb(page=1, pages=1)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert texts == ["1/1"]


class TestParseTarget:
    def test_valid_id(self) -> None:
        cmd = CommandObject(command="ban", args=" 123456789 abc ")
        assert _parse_target(cmd) == 123456789

    def test_empty_args(self) -> None:
        cmd = CommandObject(command="ban", args=None)
        assert _parse_target(cmd) is None

    def test_garbage(self) -> None:
        cmd = CommandObject(command="ban", args="hello")
        assert _parse_target(cmd) is None


class TestCallbackData:
    def test_broadcast_roundtrip(self) -> None:
        data = BroadcastCD(action="confirm", msg_id=7)
        packed = data.pack()
        unpacked = BroadcastCD.unpack(packed)
        assert unpacked.action == "confirm"
        assert unpacked.msg_id == 7

    def test_users_page_roundtrip(self) -> None:
        data = UsersPageCD(page=4)
        unpacked = UsersPageCD.unpack(data.pack())
        assert unpacked.page == 4
