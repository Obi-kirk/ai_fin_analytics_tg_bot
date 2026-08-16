"""Background price alert monitoring.

Every ``alert_interval_seconds`` (default 30 minutes) checks active
alerts: for crypto — one CoinGecko batch request (saves the free
quota), for currencies/stocks — one request each (CBR/Finnhub limits
allow that). When a threshold is crossed, sends a notification and
deactivates the alert.
"""

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from src.database.db import get_session
from src.database.models import Alert
from src.handlers.crypto import COINS
from src.handlers.rate import fetch_fx
from src.handlers.stock import fetch_stock, is_ru_stock
from src.i18n import t
from src.services.financial_api import CoinGeckoClient, make_session

log = logging.getLogger(__name__)


def _alert_message(alert: Alert, price: float) -> str:
    """Notification text: for %-alerts — change from the baseline price."""
    arrow = t("alerts.above") if alert.direction == "above" else t("alerts.below")
    currency = "₽" if is_ru_stock(alert.symbol) else "$"
    if alert.mode == "percent" and alert.baseline_price:
        change_pct = (price - alert.baseline_price) / alert.baseline_price * 100
        return t(
            "alerts.fired",
            symbol=alert.symbol,
            price=f"{price:,.2f}",
            pct=f"{change_pct:+.2f}",
            arrow=arrow,
            target=f"{alert.target_price:,.2f}",
            currency=currency,
        )
    return t(
        "alerts.fired_abs",
        symbol=alert.symbol,
        price=f"{price:,.2f}",
        arrow=arrow,
        target=f"{alert.target_price:,.2f}",
        currency=currency,
    )


def _gecko_id(symbol: str) -> str:
    """Coin symbol -> CoinGecko id (from COINS or lower-case)."""
    return COINS.get(symbol, symbol.lower())


def alert_triggered(
    price: float,
    target: float,
    direction: str,
    mode: str = "absolute",
    baseline: float | None = None,
) -> bool:
    """Alert trigger rule.

    absolute: above — price >= threshold, below — price <= threshold.
    percent: price change from baseline (price at setup time) in %;
    above — grew by target%, below — dropped by target%.
    """
    if mode == "percent" and baseline:
        change_pct = (price - baseline) / baseline * 100
        if direction == "above":
            return change_pct >= target
        if direction == "below":
            return change_pct <= -target
        return False
    if direction == "above":
        return price >= target
    if direction == "below":
        return price <= target
    return False


async def _fetch_prices(asset_type: str, symbols: list[str]) -> dict[str, float]:
    """Prices for alerts of one type: {symbol: price}. Crypto — in a batch."""
    prices: dict[str, float] = {}
    if not symbols:
        return prices
    if asset_type == "crypto":
        gecko_ids = [_gecko_id(s) for s in symbols]
        async with make_session() as session:
            gecko = CoinGeckoClient()
            batch = await gecko.get_prices_batch(gecko_ids, session)
        for symbol, gecko_id in zip(symbols, gecko_ids):
            if gecko_id in batch:
                prices[symbol] = batch[gecko_id]
        return prices
    for symbol in symbols:
        try:
            if asset_type == "fx":
                quote = await fetch_fx(symbol)
                prices[symbol] = quote.value
            elif asset_type == "stock":
                quote = await fetch_stock(symbol)
                prices[symbol] = quote.price
        except Exception:  # noqa: BLE001 — one ticker does not break the loop
            log.warning("Failed to fetch price %s for alert (%s)", symbol, asset_type)
    return prices


async def check_alerts(bot: Bot) -> int:
    """Checks all active alerts; returns the number of triggered ones."""
    async for session in get_session():
        alerts = (
            (await session.execute(select(Alert).where(Alert.is_active.is_(True))))
            .scalars()
            .all()
        )
        if not alerts:
            return 0

        by_type: dict[str, list[Alert]] = {}
        for alert in alerts:
            by_type.setdefault(alert.asset_type, []).append(alert)

        fired = 0
        for asset_type, group in by_type.items():
            prices = await _fetch_prices(asset_type, list({a.symbol for a in group}))
            for alert in group:
                price = prices.get(alert.symbol)
                if price is None:
                    continue
                if not alert_triggered(
                    price,
                    alert.target_price,
                    alert.direction,
                    alert.mode,
                    alert.baseline_price,
                ):
                    continue
                try:
                    await bot.send_message(
                        alert.telegram_id,
                        _alert_message(alert, price),
                    )
                except Exception:  # noqa: BLE001 — one undelivered alert is not fatal
                    log.warning(
                        "Failed to send alert %s to user %s",
                        alert.symbol,
                        alert.telegram_id,
                    )
                    continue
                alert.is_active = False
                fired += 1
        await session.commit()
    if fired:
        log.info("Alerts triggered: %s", fired)
    return fired


async def run_alert_loop(bot: Bot, interval_seconds: int) -> None:
    """Infinite alert-checking loop (a background bot task)."""
    log.info("Alert loop started (interval %s s)", interval_seconds)
    while True:
        try:
            await check_alerts(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error in the alert-checking loop")
        await asyncio.sleep(interval_seconds)
