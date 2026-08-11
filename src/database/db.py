"""Подключение к базе данных (SQLAlchemy async).

Без моделей: создаются postgres (или sqlite для отладки) engine и session factory.
Модели (история запросов, портфель) появятся позже.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config.settings import get_settings

engine: AsyncEngine | None = None
session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """Базовый класс моделей SQLAlchemy."""


def init_db_engine() -> AsyncEngine:
    """Создаёт engine и session factory (вызывается один раз при старте бота)."""
    global engine, session_factory
    if engine is None:
        engine = create_async_engine(get_settings().database_url, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def create_tables() -> None:
    """Создаёт таблицы, которых ещё нет (create_all для разработки)."""
    init_db_engine()
    async with engine.begin() as conn:  # type: ignore[union-attr]
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Возвращает async-сессию БД (зависимость для хендлеров)."""
    init_db_engine()
    async with session_factory() as session:  # type: ignore[union-attr]
        yield session


async def close_db() -> None:
    """Закрывает пул соединений при остановке бота."""
    global engine
    if engine is not None:
        await engine.dispose()
        engine = None
