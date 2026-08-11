"""Тесты rate limiting middleware."""

import time
from datetime import datetime, timezone

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from src.middleware.throttling import WINDOW_SECONDS, ThrottlingMiddleware


def test_allows_up_to_limit() -> None:
    throttler = ThrottlingMiddleware(rate_limit_per_minute=3)
    now = time.monotonic()
    assert throttler._allow(1, now)
    assert throttler._allow(1, now + 0.1)
    assert throttler._allow(1, now + 0.2)
    assert not throttler._allow(1, now + 0.3)


def test_window_slides() -> None:
    throttler = ThrottlingMiddleware(rate_limit_per_minute=2)
    now = time.monotonic()
    assert throttler._allow(1, now)
    assert throttler._allow(1, now + 0.1)
    assert not throttler._allow(1, now + 0.2)
    assert throttler._allow(1, now + WINDOW_SECONDS + 0.1)


def test_users_are_isolated() -> None:
    throttler = ThrottlingMiddleware(rate_limit_per_minute=1)
    now = time.monotonic()
    assert throttler._allow(10, now)
    assert not throttler._allow(10, now + 0.1)
    assert throttler._allow(11, now + 0.1)


def _message(user_id: int) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="T"),
        text="/rate USD",
    )


async def _run_through(
    throttler: ThrottlingMiddleware, event, monkeypatch: pytest.MonkeyPatch
) -> tuple[bool, bool]:
    """Прогоняет событие через middleware; (вызван_хендлер, прислано_предупреждение)."""
    handler_called = False
    warned = [False]

    async def fake_answer(*args, **kwargs) -> None:
        warned[0] = True

    if isinstance(event, Message):
        monkeypatch.setattr(Message, "answer", fake_answer)
    else:
        monkeypatch.setattr(CallbackQuery, "answer", fake_answer)

    async def handler(event, data):
        nonlocal handler_called
        handler_called = True

    await throttler(handler, event, {})
    return handler_called, warned[0]


class TestThrottlingCall:
    async def test_handler_runs_within_limit(self, monkeypatch) -> None:
        throttler = ThrottlingMiddleware(rate_limit_per_minute=2)
        called, warned = await _run_through(throttler, _message(1), monkeypatch)
        assert called is True
        assert warned is False

    async def test_message_blocked_over_limit(self, monkeypatch) -> None:
        throttler = ThrottlingMiddleware(rate_limit_per_minute=1)
        await _run_through(throttler, _message(1), monkeypatch)
        called, warned = await _run_through(throttler, _message(1), monkeypatch)
        assert called is False
        assert warned is True

    async def test_callback_blocked_over_limit(self, monkeypatch) -> None:
        throttler = ThrottlingMiddleware(rate_limit_per_minute=1)
        event = CallbackQuery(
            id="c1",
            from_user=User(id=5, is_bot=False, first_name="T"),
            chat_instance="ci",
            data="fx:USD",
            message=None,
        )
        called, warned = await _run_through(throttler, event, monkeypatch)
        assert called is True
        called, warned = await _run_through(throttler, event, monkeypatch)
        assert called is False
        assert warned is True

    async def test_other_users_not_affected(self, monkeypatch) -> None:
        throttler = ThrottlingMiddleware(rate_limit_per_minute=1)
        await _run_through(throttler, _message(1), monkeypatch)
        called, _ = await _run_through(throttler, _message(2), monkeypatch)
        assert called is True
