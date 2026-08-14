"""DB models: users, query history, portfolio, alerts, digest."""

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
    """Registered bot user (minimal set of PII)."""

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
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )


class PortfolioItem(Base):
    """An asset in the user's portfolio (watchlist): currencies, stocks, crypto.

    quantity — the amount held by the user (for balance). Sensitive personal
    data: stored only in the DB, never logged or passed to the LLM/external
    services (AGENTS.md item 9).
    """

    __tablename__ = "portfolio_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    asset_type: Mapped[str] = mapped_column(String(8))  # fx | stock | crypto
    symbol: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )


class Alert(Base):
    """Price alert: fires when the price crosses the threshold (active).

    mode: "absolute" — target_price is a price; "percent" — target_price is a
    percentage change from baseline_price (the price at the moment of setup).
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    asset_type: Mapped[str] = mapped_column(String(8))  # fx | stock | crypto
    symbol: Mapped[str] = mapped_column(String(16))
    target_price: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(8))  # above | below
    mode: Mapped[str] = mapped_column(
        String(8), default="absolute", server_default="absolute", nullable=False
    )
    baseline_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )


class QueryLog(Base):
    """User query record: command/callback, text, time.

    Stores only the minimum: telegram_id (pseudonym), event type and a
    truncated text — no names or contacts (AGENTS.md item 9).
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
    """Subscription to the daily digest.

    last_sent — the date of the last send (to skip duplicate sends on the
    same day and recover correctly after a restart).
    """

    __tablename__ = "digest_subscriptions"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_sent: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )


class DigestAsset(Base):
    """Asset from the user's personal digest set (if set — replaces the default)."""

    __tablename__ = "digest_assets"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    asset_type: Mapped[str] = mapped_column(String(8))  # fx | stock | crypto
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default="now()"
    )
