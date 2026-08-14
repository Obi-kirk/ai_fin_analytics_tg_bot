"""Database connection (SQLAlchemy async).

Without models: creates the postgres (or sqlite for debugging) engine and session factory.
Models (query history, portfolio) will be added later.
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
    """Base class for SQLAlchemy models."""


def init_db_engine() -> AsyncEngine:
    """Creates the engine and session factory (called once at bot startup)."""
    global engine, session_factory
    if engine is None:
        engine = create_async_engine(get_settings().database_url, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def create_tables() -> None:
    """Creates missing tables (create_all for development)."""
    init_db_engine()
    async with engine.begin() as conn:  # type: ignore[union-attr]
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Returns an async DB session (dependency for handlers)."""
    init_db_engine()
    async with session_factory() as session:  # type: ignore[union-attr]
        yield session


async def close_db() -> None:
    """Closes the connection pool when the bot stops."""
    global engine
    if engine is not None:
        await engine.dispose()
        engine = None
