"""Daily digest: CBR rates, top stocks/crypto, user portfolio.

Sent once a day at the configured time (digest_hour:digest_minute).
The last-sent date is stored in the DB (last_sent), so after a bot
restart the digest is not sent again on the same day.
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
from src.handlers.stock import fetch_stock, is_ru_stock, resolve_stock_symbol
from src.i18n import t
from src.services.cache import TTLCache
from src.services.financial_api import CBR_CURRENCIES

log = logging.getLogger(__name__)

DIGEST_FX = ("USD", "EUR", "CNY", "JPY")
DIGEST_STOCKS = ("AAPL", "NVDA", "MSFT", "TSLA", "META")
DIGEST_CRYPTO = ("BTC", "ETH", "SOL", "XRP")

# Assets available for a custom digest set (same sources as in the menu)
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
    """Personal digest asset set: {type: [symbols]}."""
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


def _line(
    asset_type: str, symbol: str, quote: object, quantity: float | None = None
) -> str:
    """One-line quote description for the digest."""
    if asset_type == "fx":
        rate = f"{quote.value:.2f}" if quote.value >= 1 else f"{quote.value:.4f}"
        base = f"{quote.code} — {rate} ₽"
    elif asset_type == "stock" and is_ru_stock(symbol):
        sign = "+" if quote.change_percent >= 0 else ""
        base = f"{symbol} — {quote.price:,.2f} ₽ ({sign}{quote.change_percent:.2f}%)"
    else:
        sign = "+" if quote.change_percent >= 0 else ""
        base = f"{symbol} — ${quote.price:,.2f} ({sign}{quote.change_percent:.2f}%)"
    if quantity is not None:
        base += f" ×{quantity:g}"
    return base


async def _fetch_quote(asset_type: str, symbol: str, cache: TTLCache) -> object:
    """Asset quote through the cache (same keys as in the menu)."""
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
    raise ValueError(f"Unknown asset type: {asset_type}")


async def _section(
    title: str, symbols: list[str], asset_type: str, cache: TTLCache
) -> list[str]:
    """Lines of a digest section (one failing ticker does not break it)."""
    lines = [f"{title}\n"]
    for symbol in symbols:
        try:
            quote = await _fetch_quote(asset_type, symbol, cache)
            lines.append(_line(asset_type, symbol, quote))
        except Exception:  # noqa: BLE001 — boundary of an external API
            log.warning("Digest: failed to fetch %s (%s)", symbol, asset_type)
    return lines


async def build_digest(telegram_id: int, cache: TTLCache) -> str:
    """Builds the digest text for the user.

    If a personal set is configured (digest_assets) — only that one is
    used; otherwise the default top list. The portfolio is always added.
    """
    lines = [t("digest.build.title") + "\n"]
    user_assets = await _user_assets(telegram_id)
    sections: list[list[str]] = []
    if user_assets:
        if user_assets.get("fx"):
            sections.append(
                await _section(t("digest.section.fx"), user_assets["fx"], "fx", cache)
            )
        if user_assets.get("stock"):
            sections.append(
                await _section(
                    t("digest.section.stock"), user_assets["stock"], "stock", cache
                )
            )
        if user_assets.get("crypto"):
            sections.append(
                await _section(
                    t("digest.section.crypto"), user_assets["crypto"], "crypto", cache
                )
            )
    else:
        sections.append(
            await _section(t("digest.section.fx_default"), list(DIGEST_FX), "fx", cache)
        )
        sections.append(
            await _section(
                t("digest.section.stock"), list(DIGEST_STOCKS), "stock", cache
            )
        )
        sections.append(
            await _section(
                t("digest.section.crypto"), list(DIGEST_CRYPTO), "crypto", cache
            )
        )
    for i, section in enumerate(sections):
        if i:
            lines.append("")
        lines.extend(section)

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
        lines.append(t("digest.portfolio_title"))
        lines.append("")
        for item in items:
            try:
                quote = await _fetch_quote(item.asset_type, item.symbol, cache)
                lines.append(_line(item.asset_type, item.symbol, quote, item.quantity))
            except Exception:  # noqa: BLE001 — one asset does not break the digest
                lines.append(t("digest.unavailable", symbol=item.symbol))

    lines.append(t("digest.disclaimer"))
    return "\n".join(lines)


async def check_digest(bot: Bot, cache: TTLCache) -> int:
    """Sends the digest to subscribers within the time window; count sent."""
    settings = get_settings()
    now = datetime.now(timezone.utc).astimezone()  # local server time
    window_start = settings.digest_hour * 60 + settings.digest_minute
    window_end = window_start + 3  # 3-minute window so we don't miss it
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
            except Exception:  # noqa: BLE001 — one user is not fatal
                log.warning(
                    "Digest: failed to send to user %s",
                    sub.telegram_id,
                )
        await session.commit()
    if sent:
        log.info("Digest sent to %s subscribers", sent)
    return sent


async def run_digest_loop(bot: Bot, cache: TTLCache, interval_seconds: int) -> None:
    """Infinite loop checking the digest sending time."""
    log.info("Digest loop started (check every %s s)", interval_seconds)
    while True:
        try:
            await check_digest(bot, cache)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error in the digest loop")
        await asyncio.sleep(interval_seconds)
