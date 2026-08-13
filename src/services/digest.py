"""Ежедневный дайджест: курсы ЦБ, топ акций/крипты, портфель пользователя.

Отправляется один раз в день в настроенное время (digest_hour:digest_minute).
Дата последней отправки хранится в БД (last_sent), поэтому после рестарта
бота повторная рассылка в тот же день не происходит.
"""

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy import select

from src.config.settings import get_settings
from src.database.db import get_session
from src.database.models import DigestAsset, DigestSubscription, PortfolioItem
from src.handlers.crypto import fetch_crypto
from src.handlers.rate import fetch_fx
from src.handlers.stock import fetch_stock, resolve_stock_symbol
from src.services.cache import TTLCache
from src.services.financial_api import CBR_CURRENCIES

log = logging.getLogger(__name__)

DIGEST_FX = ("USD", "EUR", "CNY", "JPY")
DIGEST_STOCKS = ("AAPL", "NVDA", "MSFT", "TSLA", "META")
DIGEST_CRYPTO = ("BTC", "ETH", "SOL", "XRP")

DIGEST_DISCLAIMER = "\n\n— <i>Это не инвестиционная рекомендация.</i>"

# Доступные для настройки своего набора (те же источники, что в меню)
DIGEST_AVAILABLE = {
    "fx": tuple(sorted(CBR_CURRENCIES)),
    "stock": (
        "AAPL",
        "NVDA",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "TSLA",
        "AMD",
        "SPX",
        "DJI",
        "VIX",
    ),
    "crypto": ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BNB"),
}


async def _user_assets(telegram_id: int) -> dict[str, list[str]]:
    """Персональный набор активов дайджеста: {тип: [символы]}."""
    async for session in get_session():
        rows = (
            (
                await session.execute(
                    select(DigestAsset)
                    .where(DigestAsset.telegram_id == telegram_id)
                    .order_by(DigestAsset.asset_type, DigestAsset.symbol)
                )
            )
            .scalars()
            .all()
        )
    groups: dict[str, list[str]] = {}
    for row in rows:
        groups.setdefault(row.asset_type, []).append(row.symbol)
    return groups


def _line(asset_type: str, symbol: str, quote: object) -> str:
    """Однострочное описание котировки для дайджеста."""
    if asset_type == "fx":
        return f"{quote.code} — {quote.value:.2f} ₽"
    sign = "+" if quote.change_percent >= 0 else ""
    return f"{symbol} — ${quote.price:,.2f} ({sign}{quote.change_percent:.2f}%)"


async def _fetch_quote(asset_type: str, symbol: str, cache: TTLCache) -> object:
    """Котировка актива через кэш (те же ключи, что в меню)."""
    settings = get_settings()
    if asset_type == "fx":
        return await cache.get_or_set(
            f"fx:{symbol}",
            lambda: fetch_fx(symbol),
            settings.cache_ttl_fx_seconds,
        )
    if asset_type == "stock":
        resolved = resolve_stock_symbol(symbol)
        return await cache.get_or_set(
            f"stock:{resolved}",
            lambda: fetch_stock(resolved),
            settings.cache_ttl_stock_seconds,
        )
    if asset_type == "crypto":
        return await cache.get_or_set(
            f"crypto:{symbol}",
            lambda: fetch_crypto(symbol),
            settings.cache_ttl_stock_seconds,
        )
    raise ValueError(f"Неизвестный тип актива: {asset_type}")


async def _section(
    title: str, symbols: list[str], asset_type: str, cache: TTLCache
) -> list[str]:
    """Строки секции дайджеста (ошибка одного тикера не роняет всё)."""
    lines = [f"{title}\n"]
    for symbol in symbols:
        try:
            quote = await _fetch_quote(asset_type, symbol, cache)
            lines.append(_line(asset_type, symbol, quote))
        except Exception:  # noqa: BLE001 — граница внешнего API
            log.warning("Дайджест: не удалось получить %s (%s)", symbol, asset_type)
    return lines


async def build_digest(telegram_id: int, cache: TTLCache) -> str:
    """Собирает текст дайджеста для пользователя.

    Если настроен персональный набор (digest_assets) — используется только
    он; иначе дефолтный топ. Портфель добавляется всегда.
    """
    lines = ["🌅 <b>Доброе утро! Дневной дайджест</b>\n"]
    user_assets = await _user_assets(telegram_id)
    if user_assets:
        if user_assets.get("fx"):
            lines += await _section("💱 <b>Валюты</b>", user_assets["fx"], "fx", cache)
        if user_assets.get("stock"):
            lines += await _section(
                "📈 <b>Акции</b>", user_assets["stock"], "stock", cache
            )
        if user_assets.get("crypto"):
            lines += await _section(
                "🪙 <b>Крипта</b>", user_assets["crypto"], "crypto", cache
            )
    else:
        lines += await _section("💱 <b>Курсы ЦБ</b>", list(DIGEST_FX), "fx", cache)
        lines.append("")
        lines += await _section("📈 <b>Акции</b>", list(DIGEST_STOCKS), "stock", cache)
        lines.append("")
        lines += await _section(
            "🪙 <b>Крипта</b>", list(DIGEST_CRYPTO), "crypto", cache
        )

    async for session in get_session():
        items = (
            (
                await session.execute(
                    select(PortfolioItem)
                    .where(PortfolioItem.telegram_id == telegram_id)
                    .order_by(PortfolioItem.asset_type, PortfolioItem.symbol)
                )
            )
            .scalars()
            .all()
        )
    if items:
        lines.append("")
        lines.append("📁 <b>Ваш портфель</b>")
        for item in items:
            try:
                quote = await _fetch_quote(item.asset_type, item.symbol, cache)
                lines.append(_line(item.asset_type, item.symbol, quote))
            except Exception:  # noqa: BLE001 — один актив не роняет дайджест
                lines.append(f"{item.symbol} — недоступно")

    lines.append(DIGEST_DISCLAIMER)
    return "\n".join(lines)


async def check_digest(bot: Bot, cache: TTLCache) -> int:
    """Отправляет дайджест подписчикам в окне времени; число отправленных."""
    settings = get_settings()
    now = datetime.now(timezone.utc).astimezone()  # локальное время сервера
    window_start = settings.digest_hour * 60 + settings.digest_minute
    window_end = window_start + 3  # окно 3 минуты, чтобы не пропустить
    if not (window_start <= now.hour * 60 + now.minute < window_end):
        return 0

    sent = 0
    async for session in get_session():
        subs = (await session.execute(select(DigestSubscription))).scalars().all()
        for sub in subs:
            if sub.last_sent == now.date():
                continue
            try:
                text = await build_digest(sub.telegram_id, cache)
                await bot.send_message(sub.telegram_id, text)
                sub.last_sent = now.date()
                sent += 1
            except Exception:  # noqa: BLE001 — один пользователь не фатален
                log.warning(
                    "Дайджест: не удалось отправить пользователю %s",
                    sub.telegram_id,
                )
        await session.commit()
    if sent:
        log.info("Дайджест отправлен %s подписчикам", sent)
    return sent


async def run_digest_loop(bot: Bot, cache: TTLCache, interval_seconds: int) -> None:
    """Бесконечный цикл проверки времени отправки дайджеста."""
    log.info("Дайджест-цикл запущен (проверка каждые %s с)", interval_seconds)
    while True:
        try:
            await check_digest(bot, cache)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в цикле дайджеста")
        await asyncio.sleep(interval_seconds)
