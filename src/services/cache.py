"""Простой потокобезопасный in-memory кэш с TTL.

Не используется ни один внешний сервис — подходит для одного процесса бота.
Для масштабирования (несколько инстансов) заменить на Redis.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class TTLCache:
    """Кэш с временем жизни записей и опциональным сборщиком мусора."""

    def __init__(self, gc_interval_seconds: int = 300) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._gc_interval = gc_interval_seconds
        self._gc_task: asyncio.Task[None] | None = None
        self._started = False

    def start_gc(self) -> None:
        """Запускает фоновую очистку просроченных записей (для polling-цикла)."""
        if not self._started:
            self._started = True
            self._gc_task = asyncio.create_task(self._gc_loop())

    async def stop_gc(self) -> None:
        """Останавливает фоновую очистку."""
        if self._gc_task:
            self._gc_task.cancel()
            try:
                await self._gc_task
            except asyncio.CancelledError:
                pass

    async def get(self, key: str) -> Any | None:
        """Возвращает значение или None, если ключа нет или он просрочен."""
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if time.monotonic() > expires_at:
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Сохраняет значение с временем жизни в секундах."""
        async with self._lock:
            self._data[key] = (time.monotonic() + ttl_seconds, value)

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl_seconds: int,
    ) -> T:
        """Возвращает кэшированное значение или вычисляет и сохраняет новое."""
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await factory()
        await self.set(key, value, ttl_seconds)
        return value

    async def clear(self) -> None:
        """Полная очистка кэша."""
        async with self._lock:
            self._data.clear()

    async def _gc_loop(self) -> None:
        """Периодически удаляет просроченные записи."""
        while True:
            await asyncio.sleep(self._gc_interval)
            now = time.monotonic()
            async with self._lock:
                expired = [k for k, (exp, _) in self._data.items() if now > exp]
                for k in expired:
                    self._data.pop(k, None)
