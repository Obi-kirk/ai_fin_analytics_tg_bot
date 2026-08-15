"""Admin function tests (no DB or network).

Pure functions are tested: admin rights check, pagination,
argument parsing, callback-data serialization.
"""

import pytest
from aiogram.filters import CommandObject
from aiogram.types import User as TelegramUser

import src.filters as filters_mod
from src.filters import AdminFilter, SuperAdminFilter
from src.handlers import admin as admin_mod
from src.handlers.admin import (
    BroadcastCD,
    UsersPageCD,
    _is_admin,
    _parse_setrole_args,
    _parse_target,
    _users_page_kb,
)


class _FakeSettings:
    admin_id: int | None = 123456789


@pytest.fixture
def admin_settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    """Replaces settings: ADMIN_ID = 123456789 (one instance per test)."""
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

    def test_no_admin_id_set(self, admin_settings: _FakeSettings) -> None:
        admin_settings.admin_id = None
        assert _is_admin(_user(123456789)) is False


class TestParseSetrole:
    def test_valid(self) -> None:
        assert _parse_setrole_args("123456789 admin") == (123456789, "admin")
        assert _parse_setrole_args(" 123 user ") == (123, "user")

    def test_invalid_role(self) -> None:
        assert _parse_setrole_args("123456789 root") is None

    def test_id_not_number(self) -> None:
        assert _parse_setrole_args("abc admin") is None

    def test_empty(self) -> None:
        assert _parse_setrole_args(None) is None
        assert _parse_setrole_args("") is None


class TestAdminFilter:
    async def test_admin_passes(self) -> None:
        assert await AdminFilter()(None, is_admin=True) is True

    async def test_regular_denied(self) -> None:
        assert await AdminFilter()(None, is_admin=False) is False

    async def test_missing_denied(self) -> None:
        assert await AdminFilter()(None) is False


class TestSuperAdminFilter:
    class _Event:
        def __init__(self, user_id: int) -> None:
            self.from_user = TelegramUser(id=user_id, is_bot=False, first_name="T")

    async def test_owner_passes(self, admin_settings, monkeypatch) -> None:
        monkeypatch.setattr(filters_mod, "get_settings", lambda: admin_settings)
        assert await SuperAdminFilter()(self._Event(123456789)) is True

    async def test_other_denied(self, admin_settings, monkeypatch) -> None:
        monkeypatch.setattr(filters_mod, "get_settings", lambda: admin_settings)
        assert await SuperAdminFilter()(self._Event(42)) is False

    async def test_no_admin_id(self, admin_settings, monkeypatch) -> None:
        admin_settings.admin_id = None
        monkeypatch.setattr(filters_mod, "get_settings", lambda: admin_settings)
        assert await SuperAdminFilter()(self._Event(123456789)) is False


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
