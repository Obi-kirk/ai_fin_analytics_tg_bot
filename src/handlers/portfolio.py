"""Портфель (watchlist) и алерты цен: команды /portfolio, /add, /remove,
/alert, /alerts, /remove_alert.

Тип актива определяется автоматически: валюта ЦБ -> fx, монета из списка
CoinGecko -> crypto, иначе тикер акции -> stock.
"""

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import delete, select

from src.database.db import get_session
from src.database.models import Alert, PortfolioItem
from src.handlers.crypto import COINS
from src.handlers.stock import TICKER_RE
from src.services.financial_api import CBR_CURRENCIES

log = logging.getLogger(__name__)
router = Router()

TYPE_ICONS = {"fx": "💱", "stock": "📈", "crypto": "🪙"}
TYPE_TITLES = {"fx": "Валюты", "stock": "Акции", "crypto": "Крипта"}


def resolve_asset_type(symbol: str) -> str | None:
    """Определяет тип актива: fx | stock | crypto (по символу)."""
    if symbol in CBR_CURRENCIES:
        return "fx"
    if symbol in COINS:
        return "crypto"
    if TICKER_RE.match(symbol):
        return "stock"
    return None


def parse_alert_args(rest: str) -> tuple[str, str, float] | None:
    """Разбирает аргументы /alert: <символ> [above|below] <цена>."""
    parts = rest.strip().split()
    if len(parts) < 2 or len(parts) > 3:
        return None
    symbol = parts[0].upper()
    if len(parts) == 2:
        direction, raw_price = "above", parts[1]
    else:
        direction, raw_price = parts[1].lower(), parts[2]
        if direction not in ("above", "below"):
            return None
    if not resolve_asset_type(symbol):
        return None
    try:
        target = float(raw_price.replace(",", "."))
    except ValueError:
        return None
    if target <= 0:
        return None
    return symbol, direction, target


@router.message(Command("portfolio"))
async def cmd_portfolio(message: Message) -> None:
    """Показывает активы в портфеле (watchlist)."""
    async for session in get_session():
        items = (
            (
                await session.execute(
                    select(PortfolioItem)
                    .where(PortfolioItem.telegram_id == message.from_user.id)
                    .order_by(PortfolioItem.asset_type, PortfolioItem.symbol)
                )
            )
            .scalars()
            .all()
        )
    if not items:
        await message.answer(
            "📁 Портфель пуст.\nДобавь актив: /add BTC или /add AAPL или /add USD"
        )
        return
    groups: dict[str, list[str]] = {}
    for item in items:
        groups.setdefault(item.asset_type, []).append(item.symbol)
    lines = ["📁 <b>Мой портфель</b>\n"]
    for asset_type in ("fx", "stock", "crypto"):
        symbols = groups.get(asset_type)
        if symbols:
            lines.append(
                f"{TYPE_ICONS[asset_type]} {TYPE_TITLES[asset_type]}: "
                + ", ".join(symbols)
            )
    lines.append("\nУбрать: /remove BTC")
    await message.answer("\n".join(lines))


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject) -> None:
    """Добавляет актив в портфель: /add BTC (или AAPL, USD)."""
    symbol = ((command.args or "").strip().upper()).split(" ", 1)[0]
    asset_type = resolve_asset_type(symbol) if symbol else None
    if asset_type is None:
        await message.answer(
            "Не понимаю, что добавить. Примеры: /add BTC, /add AAPL, /add USD"
        )
        return
    async for session in get_session():
        exists = (
            await session.execute(
                select(PortfolioItem).where(
                    PortfolioItem.telegram_id == message.from_user.id,
                    PortfolioItem.symbol == symbol,
                )
            )
        ).scalar()
        if exists is None:
            session.add(
                PortfolioItem(
                    telegram_id=message.from_user.id,
                    asset_type=asset_type,
                    symbol=symbol,
                )
            )
            await session.commit()
            added = True
        else:
            added = False
    await message.answer(
        f"📁 <b>{symbol}</b> добавлен в портфель ({TYPE_ICONS[asset_type]}"
        f"{TYPE_TITLES[asset_type]})."
        if added
        else f"📁 <b>{symbol}</b> уже в портфеле."
    )


@router.message(Command("remove"))
async def cmd_remove(message: Message, command: CommandObject) -> None:
    """Убирает актив из портфеля: /remove BTC."""
    symbol = ((command.args or "").strip().upper()).split(" ", 1)[0]
    if not symbol:
        await message.answer("Укажи актив: /remove BTC")
        return
    async for session in get_session():
        result = await session.execute(
            delete(PortfolioItem).where(
                PortfolioItem.telegram_id == message.from_user.id,
                PortfolioItem.symbol == symbol,
            )
        )
        await session.commit()
        removed = result.rowcount > 0
    await message.answer(
        f"📁 <b>{symbol}</b> убран из портфеля."
        if removed
        else f"📁 <b>{symbol}</b> не было в портфеле."
    )


@router.message(Command("alert"))
async def cmd_alert(message: Message, command: CommandObject) -> None:
    """Ставит алерт на цену: /alert BTC 70000 или /alert BTC below 50000."""
    parsed = parse_alert_args(command.args or "")
    if parsed is None:
        await message.answer(
            "Формат: /alert <символ> [выше|below] <цена>\n"
            "Примеры: /alert BTC 70000 (выше 70 000)\n"
            "         /alert ETH below 3500 (ниже 3 500)"
        )
        return
    symbol, direction, target = parsed
    asset_type = resolve_asset_type(symbol) or "stock"
    async for session in get_session():
        session.add(
            Alert(
                telegram_id=message.from_user.id,
                asset_type=asset_type,
                symbol=symbol,
                target_price=target,
                direction=direction,
            )
        )
        await session.commit()
    arrow = "выше" if direction == "above" else "ниже"
    await message.answer(
        f"🔔 Алерт установлен: <b>{symbol}</b> {arrow} "
        f"<b>${target:,.2f}</b>\nПроверяется каждые 30 минут. /alerts — список"
    )


@router.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    """Показывает активные алерты пользователя."""
    async for session in get_session():
        alerts = (
            (
                await session.execute(
                    select(Alert)
                    .where(
                        Alert.telegram_id == message.from_user.id,
                        Alert.is_active.is_(True),
                    )
                    .order_by(Alert.id)
                )
            )
            .scalars()
            .all()
        )
    if not alerts:
        await message.answer("🔕 Активных алертов нет.\nСоздать: /alert BTC 70000")
        return
    lines = ["🔔 <b>Мои алерты</b>\n"]
    for a in alerts:
        arrow = "выше" if a.direction == "above" else "ниже"
        lines.append(
            f"• <code>{a.id}</code>. {TYPE_ICONS.get(a.asset_type, '')}"
            f"<b>{a.symbol}</b> {arrow} ${a.target_price:,.2f}"
        )
    lines.append("\nУбрать: /remove_alert <id>")
    await message.answer("\n".join(lines))


@router.message(Command("remove_alert"))
async def cmd_remove_alert(message: Message, command: CommandObject) -> None:
    """Удаляет алерт по id: /remove_alert 3."""
    try:
        alert_id = int((command.args or "").strip().split()[0])
    except (ValueError, IndexError):
        await message.answer("Укажи id алерта: /remove_alert 3 (id видно в /alerts)")
        return
    async for session in get_session():
        result = await session.execute(
            delete(Alert).where(
                Alert.id == alert_id,
                Alert.telegram_id == message.from_user.id,
            )
        )
        await session.commit()
        removed = result.rowcount > 0
    await message.answer(
        f"✅ Алерт <code>{alert_id}</code> удалён."
        if removed
        else f"⚠️ Алерт <code>{alert_id}</code> не найден (он твой и активен?)."
    )
