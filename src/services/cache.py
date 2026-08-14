"""Simple thread-safe in-memory cache with TTL.

No external service is used — fits a single bot process.
For scaling (multiple instances) replace with Redis.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class TTLCache:
    """Cache with per-entry TTL and an optional garbage collector."""

    def __init__(self, gc_interval_seconds: int = 300) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._gc_interval = gc_interval_seconds
        self._gc_task: asyncio.Task[None] | None = None
        self._started = False
        self.hits = 0
        self.misses = 0

    def start_gc(self) -> None:
        """Starts background cleanup of expired entries (for the polling loop)."""
        if not self._started:
            self._started = True
            self._gc_task = asyncio.create_task(self._gc_loop())

    async def stop_gc(self) -> None:
        """Stops the background cleanup."""
        if self._gc_task:
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass

    async def get(self, key: str) -> Any | None:
        """Returns the value or None if the key is missing or expired."""
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if time.monotonic() > expires_at:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return value

    async def stats(self) -> dict[str, int]:
        """Cache statistics: entries, hits, misses."""
        async with self._lock:
            return {
                "entries": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
            }

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Stores a value with a lifetime in seconds."""
        async with self._lock:
            self._data[key] = (time.monotonic() + ttl_seconds, value)

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl_seconds: int,
    ) -> T:
        """Returns the cached value or computes and stores a new one."""
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await factory()
        await self.set(key, value, ttl_seconds)
        return value

    async def delete(self, key: str) -> None:
        """Forcefully removes an entry (for the "refresh" button)."""
        async with self._lock:
            self._data.pop(key, None)

    async def clear(self) -> None:
        """Clears the whole cache."""
        async with self._lock:
            self._data.clear()

    async def _gc_loop(self) -> None:
        """Periodically removes expired entries."""
        while True:
            await asyncio.sleep(self._gc_interval)
            now = time.monotonic()
            async with self._lock:
                expired = [k for k, (exp, _) in self._data.items() if now > exp]
                for k in expired:
                    self._data.pop(k, None)
