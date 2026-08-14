"""Application settings, loaded from .env (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All project environment variables. Secrets are never logged."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str

    # Database (SQLAlchemy async)
    database_url: str = "sqlite+aiosqlite:///./ai_parser.db"

    # External APIs
    fcs_api_key: str | None = None
    finnhub_api_key: str | None = None
    coingecko_api_key: str | None = None
    openrouter_api_key: str | None = None
    groq_api_key: str | None = None

    # Admin (Telegram user_id) for critical notifications
    admin_id: int | None = None

    # Behavior
    rate_limit_per_minute: int = 10
    default_language: str = "en"  # bot language: "ru" | "en"
    cache_ttl_fx_seconds: int = 3600  # currencies: 1 hour
    cache_ttl_stock_seconds: int = 600  # stocks/crypto: 10 minutes
    cache_ttl_fundamental_seconds: int = 1800  # profile/news: 30 minutes

    # Price alerts: check interval (30 minutes by default)
    alert_interval_seconds: int = 1800

    # Daily digest: send time (server local time)
    digest_hour: int = 9
    digest_minute: int = 0
    digest_check_seconds: int = 60

    # AI agent
    openrouter_model: str = "nvidia/nemotron-3-nano-30b-a3b:free"
    openrouter_max_tokens: int = 1200


@lru_cache
def get_settings() -> Settings:
    """Loads settings once and caches the result."""
    return Settings()
