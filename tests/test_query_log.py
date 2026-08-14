"""Query history middleware tests: writing to DB (SQLite, no network)."""

from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

import src.database.db as db_module
from src.database.models import QueryLog
from src.middleware.query_log import QueryLogMiddleware

DB_URL = "sqlite+aiosqlite:///./test_query_log.db"


class _FakeSettings:
    database_url: str = DB_URL


@pytest.fixture
async def sqlite_db(monkeypatch: pytest.MonkeyPatch):
    """Replaces the engine with a file-based SQLite and creates tables."""
    monkeypatch.setattr(db_module, "get_settings", lambda: _FakeSettings())
    await db_module.close_db()
    await db_module.create_tables()
    async for session in db_module.get_session():
        await session.execute(db_module.Base.metadata.tables["query_log"].delete())
        await session.commit()
    yield
    await db_module.close_db()


def _message(text: str, user_id: int = 111) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="T"),
        text=text,
    )


def _callback(data: str, user_id: int = 222) -> CallbackQuery:
    return CallbackQuery(
        id="c1",
        from_user=User(id=user_id, is_bot=False, first_name="T"),
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
        ),
    )


async def _fetch_logs() -> list[QueryLog]:
    async for session in db_module.get_session():
        from sqlalchemy import select

        result = await session.execute(select(QueryLog).order_by(QueryLog.id))
        return list(result.scalars().all())
    return []


class TestQueryLogMiddleware:
    async def test_message_recorded(self, sqlite_db) -> None:
        await QueryLogMiddleware._write(_message("/rate USD"))
        logs = await _fetch_logs()
        assert len(logs) == 1
        assert logs[0].telegram_id == 111
        assert logs[0].event_type == "message"
        assert logs[0].command == "/rate"
        assert logs[0].payload == "USD"

    async def test_callback_recorded(self, sqlite_db) -> None:
        await QueryLogMiddleware._write(_callback("fx:USD"))
        logs = await _fetch_logs()
        assert len(logs) == 1
        assert logs[0].event_type == "callback"
        assert logs[0].command == "fx:USD"

    async def test_empty_message_skipped(self, sqlite_db) -> None:
        await QueryLogMiddleware._write(_message("   "))
        assert await _fetch_logs() == []

    async def test_long_text_truncated(self, sqlite_db) -> None:
        await QueryLogMiddleware._write(_message("/analyze " + "а" * 1000))
        logs = await _fetch_logs()
        assert len(logs[0].payload) <= 300

    async def test_handler_still_runs_on_db_failure(self, sqlite_db) -> None:
        """A write failure must not crash the handler."""
        called = False

        async def handler(event, data):
            nonlocal called
            called = True
            return "ok"

        # Break the session: close the DB before calling the middleware
        await db_module.close_db()
        result = await QueryLogMiddleware()(handler, _message("/rate USD"), {})
        assert result == "ok"
        assert called is True
