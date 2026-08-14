"""UsersMiddleware tests: stats, user tracking, bans (SQLite, no network)."""

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

import src.database.db as db_module
from src.database.models import User as UserModel
from src.middleware.users import BotStats, UsersMiddleware

DB_URL = "sqlite+aiosqlite:///./test_users_mw.db"


class _FakeSettings:
    database_url: str = DB_URL


@pytest.fixture
async def sqlite_db(monkeypatch: pytest.MonkeyPatch):
    """Replaces the engine with a clean file-based SQLite and creates tables."""
    import os

    monkeypatch.setattr(db_module, "get_settings", lambda: _FakeSettings())
    await db_module.close_db()
    # create_all does not change existing tables — remove the DB file from previous runs
    db_path = _FakeSettings().database_url.replace("sqlite+aiosqlite:///", "")
    if db_path != ":memory:" and os.path.exists(db_path):
        os.remove(db_path)
    await db_module.create_tables()
    yield
    await db_module.close_db()


def _message(
    user_id: int, text: str = "/rate USD", username: str | None = None
) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test", username=username),
        text=text,
    )


def _callback(user_id: int, data: str = "fx:USD") -> CallbackQuery:
    return CallbackQuery(
        id="c1",
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        chat_instance="ci",
        data=data,
        message=None,
    )


async def _user_in_db(user_id: int) -> UserModel | None:
    async for session in db_module.get_session():
        return await session.get(UserModel, user_id)
    return None


async def _set_banned(user_id: int, banned: bool) -> None:
    async for session in db_module.get_session():
        session.add(UserModel(telegram_id=user_id, is_banned=banned))
        await session.commit()


class TestBotStats:
    def test_counters(self) -> None:
        stats = BotStats()
        stats.messages += 2
        stats.callbacks += 1
        assert stats.messages == 2
        assert stats.callbacks == 1

    def test_top_commands(self) -> None:
        stats = BotStats()
        stats.commands["rate"] += 3
        stats.commands["stock"] += 1
        assert stats.top_commands(2) == [("rate", 3), ("stock", 1)]

    def test_uptime_human(self) -> None:
        stats = BotStats()
        stats.started_at -= 3661.0  # an hour + minute + second ago
        assert stats.uptime_human() == "1h 1m 1s"


class TestUsersMiddleware:
    async def test_message_counts_and_passes(self, sqlite_db) -> None:
        stats = BotStats()
        mw = UsersMiddleware(stats)
        called = False

        async def handler(event, data):
            nonlocal called
            assert data["stats"] is stats
            called = True

        await mw(handler, _message(111, "/rate USD"), {})
        assert called is True
        assert stats.messages == 1
        assert stats.commands["rate"] == 1
        assert stats.callbacks == 0

    async def test_callback_counts(self, sqlite_db) -> None:
        stats = BotStats()
        mw = UsersMiddleware(stats)

        async def handler(event, data):
            pass

        await mw(handler, _callback(222), {})
        assert stats.callbacks == 1

    async def test_new_user_recorded(self, sqlite_db) -> None:
        stats = BotStats()
        mw = UsersMiddleware(stats)

        async def handler(event, data):
            pass

        await mw(handler, _message(777, username="alice"), {})
        user = await _user_in_db(777)
        assert user is not None
        assert user.username == "alice"
        assert user.is_banned is False

    async def test_known_user_not_duplicated(self, sqlite_db) -> None:
        stats = BotStats()
        mw = UsersMiddleware(stats)

        async def handler(event, data):
            pass

        await mw(handler, _message(777), {})
        await mw(handler, _message(777), {})
        count = 0
        async for session in db_module.get_session():
            from sqlalchemy import func, select

            count = (
                await session.execute(
                    select(func.count())
                    .select_from(UserModel)
                    .where(UserModel.telegram_id == 777)
                )
            ).scalar()
        assert count == 1

    async def test_banned_user_blocked(self, sqlite_db) -> None:
        await _set_banned(333, banned=True)
        stats = BotStats()
        mw = UsersMiddleware(stats)
        called = False

        async def handler(event, data):
            nonlocal called
            called = True

        result = await mw(handler, _message(333), {})
        assert called is False
        assert result is None

    async def test_unbanned_user_passes(self, sqlite_db) -> None:
        stats = BotStats()
        mw = UsersMiddleware(stats)
        called = False

        async def handler(event, data):
            nonlocal called
            called = True

        await mw(handler, _message(444), {})
        assert called is True


class TestUsersMiddlewareLanguage:
    async def _run(self, mw, event, data=None):
        async def handler(event, data):
            return data

        return await mw(handler, event, data or {})

    async def test_default_language_when_not_set(self, sqlite_db) -> None:
        mw = UsersMiddleware(BotStats())
        data = await self._run(mw, _message(901))
        assert data["lang"] == "en"
        assert data["lang_set"] is False

    async def test_language_loaded_from_db(self, sqlite_db) -> None:
        async for session in db_module.get_session():
            session.add(UserModel(telegram_id=902, language="ru"))
            await session.commit()
        mw = UsersMiddleware(BotStats())
        data = await self._run(mw, _message(902))
        assert data["lang"] == "ru"
        assert data["lang_set"] is True

    async def test_language_change_applies_after_invalidate(self, sqlite_db) -> None:
        async for session in db_module.get_session():
            session.add(UserModel(telegram_id=903, language="en"))
            await session.commit()
        mw = UsersMiddleware(BotStats())

        data1 = await self._run(mw, _message(903))
        assert data1["lang"] == "en"

        # user switches to Russian: update DB and invalidate the cache
        async for session in db_module.get_session():
            from sqlalchemy import update

            await session.execute(
                update(UserModel)
                .where(UserModel.telegram_id == 903)
                .values(language="ru")
            )
            await session.commit()
        mw.invalidate(903)

        data2 = await self._run(mw, _message(903))
        assert data2["lang"] == "ru"
        assert data2["lang_set"] is True

    async def test_same_middleware_instance_shares_cache(self, sqlite_db) -> None:
        """Regression: /lang callback and next message must see the same language."""
        mw = UsersMiddleware(BotStats())
        data_msg = await self._run(mw, _message(904))
        assert data_msg["lang"] == "en"

        # callback sets language to ru (like on_lang_choose) and invalidates
        from src.i18n import set_lang

        set_lang("ru")
        async for session in db_module.get_session():
            from sqlalchemy import update

            await session.execute(
                update(UserModel)
                .where(UserModel.telegram_id == 904)
                .values(language="ru")
            )
            await session.commit()
        mw.invalidate(904)

        data_cb = await self._run(mw, _callback(904, "lang:ru"))
        assert data_cb["lang"] == "ru"

        # next message must also be in Russian
        data_msg2 = await self._run(mw, _message(904, "/help"))
        assert data_msg2["lang"] == "ru"
