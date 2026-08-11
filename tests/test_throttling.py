"""Тесты rate limiting middleware."""

import time

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
