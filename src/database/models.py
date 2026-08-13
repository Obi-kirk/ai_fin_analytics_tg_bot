"""Модели БД: пользователи, история запросов, портфель, алерты, дайджест."""

from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """Зарегистрированный пользователь бота (минимальный набор PII)."""

    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_banned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    role: Mapped[str] = mapped_column(
        String(16), default="user", server_default="user", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )


class PortfolioItem(Base):
    """Актив из портфеля (watchlist) пользователя: валюты, акции, крипта."""

    __tablename__ = "portfolio_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    asset_type: Mapped[str] = mapped_column(String(8))  # fx | stock | crypto
    symbol: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )


class Alert(Base):
    """Алерт на цену: сработает, когда цена пересечёт порог (active)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    asset_type: Mapped[str] = mapped_column(String(8))  # fx | stock | crypto
    symbol: Mapped[str] = mapped_column(String(16))
    target_price: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(8))  # above | below
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )


class QueryLog(Base):
    """Запись запроса пользователя: команда/колбэк, текст, время.

    Хранится только минимум: telegram_id (псевдоним), тип события
    и обрезанный текст — без имён и контактов (AGENTS.md п.9).
    """

    __tablename__ = "query_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    event_type: Mapped[str] = mapped_column(String(16))  # "message" | "callback"
    command: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )


class DigestSubscription(Base):
    """Подписка на ежедневный дайджест.

    last_sent — дата последней отправки (для пропуска повторной рассылки
    в один день и корректного восстановления после рестарта).
    """

    __tablename__ = "digest_subscriptions"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_sent: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )
