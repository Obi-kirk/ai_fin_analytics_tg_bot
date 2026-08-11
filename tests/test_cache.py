"""Тесты in-memory кэша."""

import asyncio

from src.services.cache import TTLCache


async def test_set_and_get() -> None:
    cache = TTLCache()
    await cache.set("k", {"price": 10}, ttl_seconds=60)
    assert await cache.get("k") == {"price": 10}


async def test_get_missing_returns_none() -> None:
    cache = TTLCache()
    assert await cache.get("nope") is None


async def test_expired_entry_returns_none() -> None:
    cache = TTLCache()
    await cache.set("k", 1, ttl_seconds=0.1)
    await asyncio.sleep(0.15)
    assert await cache.get("k") is None


async def test_get_or_set_factory_call_once_on_hit() -> None:
    cache = TTLCache()
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        return calls

    first = await cache.get_or_set("k", factory, ttl_seconds=60)
    second = await cache.get_or_set("k", factory, ttl_seconds=60)
    assert first == second == 1
    assert calls == 1


async def test_delete_removes_key() -> None:
    cache = TTLCache()
    await cache.set("k", 1, ttl_seconds=60)
    await cache.delete("k")
    assert await cache.get("k") is None


async def test_delete_missing_is_safe() -> None:
    cache = TTLCache()
    await cache.delete("нет_такого")


async def test_clear() -> None:
    cache = TTLCache()
    await cache.set("a", 1, ttl_seconds=60)
    await cache.set("b", 2, ttl_seconds=60)
    await cache.clear()
    assert await cache.get("a") is None
    assert await cache.get("b") is None
