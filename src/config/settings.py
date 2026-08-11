"""Настройки приложения, загрузка из .env (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все переменные окружения проекта. Секреты не логируются."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str

    # База данных (SQLAlchemy async)
    database_url: str = "sqlite+aiosqlite:///./ai_parser.db"

    # Внешние API
    fcs_api_key: str | None = None
    finnhub_api_key: str | None = None
    coingecko_api_key: str | None = None
    openrouter_api_key: str | None = None
    groq_api_key: str | None = None

    # Админ (Telegram user_id) для критических уведомлений
    admin_id: int | None = None

    # Поведение бота
    rate_limit_per_minute: int = 10
    cache_ttl_fx_seconds: int = 3600  # валюты: 1 час
    cache_ttl_stock_seconds: int = 600  # акции/крипта: 10 минут
    cache_ttl_fundamental_seconds: int = 1800  # профиль/новости: 30 минут

    # AI-агент
    openrouter_model: str = "nvidia/nemotron-3-nano-30b-a3b:free"
    openrouter_max_tokens: int = 1200


@lru_cache
def get_settings() -> Settings:
    """Загружает настройки один раз и кэширует результат."""
    return Settings()
