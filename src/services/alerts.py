"""Фоновый мониторинг алертов цен.

Каждые ``alert_interval_seconds`` (по умолчанию 30 минут) проверяет активные
алерты: для крипты — одним батч-запросом CoinGecko (экономия бесплатного
лимита), для валют/акций — по одному запросу (лимиты ЦБ/Finnhub позволяют).
При пересечении порога отправляет уведомление и деактивирует алерт.
"""

import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from src.database.db import get_session
from src.database.models import Alert
from src.handlers.crypto import COINS
from src.handlers.rate import fetch_fx
from src.handlers.stock import fetch_stock
from src.services.financial_api import CoinGeckoClient, make_session

log = logging.getLogger(__name__)

ALERT_FORMAT = (
    "🔔 <b>Алерт сработал</b>\n"
    "{symbol}: <b>${price:,.2f}</b> — {direction} порога "
    "<b>${target:,.2f}</b>\n\nУправление: /alerts"
)


def _gecko_id(symbol: str) -> str:
    """Символ монеты -> id CoinGecko (из COINS или lower-case)."""
    return COINS.get(symbol, symbol.lower())


def alert_triggered(price: float, target: float, direction: str) -> bool:
    """Правило срабатывания алерта (above: цена >= порог, below: <=)."""
    if direction == "above":
        return price >= target
    if direction == "below":
        return price <= target
    return False


async def _fetch_prices(asset_type: str, symbols: list[str]) -> dict[str, float]:
    """Цены для алертов одного типа: {symbol: цена}. Крипта — батчем."""
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
        except Exception:  # noqa: BLE001 — один тикер не роняет весь цикл
            log.warning(
                "Не удалось получить цену %s для алерта (%s)", symbol, asset_type
            )
    return prices


async def check_alerts(bot: Bot) -> int:
    """Проверяет все активные алерты; возвращает число сработавших."""
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
                if not alert_triggered(price, alert.target_price, alert.direction):
                    continue
                try:
                    await bot.send_message(
                        alert.telegram_id,
                        ALERT_FORMAT.format(
                            symbol=alert.symbol,
                            price=price,
                            direction=alert.direction,
                            target=alert.target_price,
                        ),
                    )
                except Exception:  # noqa: BLE001 — один недоставленный алерт не фатален
                    log.warning(
                        "Не удалось отправить алерт %s пользователю %s",
                        alert.symbol,
                        alert.telegram_id,
                    )
                    continue
                alert.is_active = False
                fired += 1
        await session.commit()
    if fired:
        log.info("Сработало алертов: %s", fired)
    return fired


async def run_alert_loop(bot: Bot, interval_seconds: int) -> None:
    """Бесконечный цикл проверки алертов (фоновая задача бота)."""
    log.info("Алерт-цикл запущен (интервал %s с)", interval_seconds)
    while True:
        try:
            await check_alerts(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в цикле проверки алертов")
        await asyncio.sleep(interval_seconds)
