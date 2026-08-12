"""Административные команды: панель, рассылка с подтверждением, баны.

Доступ только у ADMIN_ID из .env (AGENTS.md п.4 — проверка прав в фильтре).
Рассылка требует явного подтверждения (Human-in-the-Loop, AGENTS.md п.7).
"""

import logging
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.types import User as TelegramUser
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select, update

from src.config.settings import get_settings
from src.database.db import get_session
from src.database.models import QueryLog, User
from src.filters import AdminFilter, SuperAdminFilter
from src.middleware.users import BotStats
from src.services.cache import TTLCache

log = logging.getLogger(__name__)
router = Router()


class BroadcastCD(CallbackData, prefix="broadcast"):
    """Callback-данные подтверждения рассылки."""

    action: str  # "confirm" | "cancel"
    msg_id: int  # id сообщения-текста рассылки


class UsersPageCD(CallbackData, prefix="users"):
    """Callback-данные пагинации списка пользователей."""

    page: int


USERS_PER_PAGE = 10


# Память ожидающей рассылки: id пользователя (админа) -> текст
_pending_broadcast: dict[int, tuple[int, str]] = {}


def _is_admin(user: TelegramUser) -> bool:
    """Совместимость: проверка по ADMIN_ID из .env (без обращения к БД)."""
    admin_id = get_settings().admin_id
    return bool(admin_id) and user.id == admin_id


@router.message(Command("admin"), AdminFilter())
async def cmd_admin(message: Message, stats: BotStats, cache: TTLCache) -> None:
    """Панель администратора: пользователи, кэш, аптайм, популярные команды."""
    async for session in get_session():
        total_users = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar()
        banned_users = (
            await session.execute(
                select(func.count()).select_from(User).where(User.is_banned.is_(True))
            )
        ).scalar()
    cache_stats = await cache.stats()
    rate = get_settings().rate_limit_per_minute

    top = "\n".join(f"  /{c} — {n}" for c, n in stats.top_commands(5)) or "  пока нет"
    await message.answer(
        "🔐 <b>Панель администратора</b>\n\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"🚫 В бане: <b>{banned_users}</b>\n"
        f"🕐 Аптайм: <b>{stats.uptime_human()}</b>\n"
        f"⚙️ Лимит сообщений: {rate}/мин\n\n"
        f"🗂 Кэш: {cache_stats['entries']} записей, "
        f"hits {cache_stats['hits']}, misses {cache_stats['misses']}\n"
        f"📊 Популярные команды:\n{top}\n\n"
        f"Сообщений: {stats.messages}, колбэков: {stats.callbacks}"
    )


@router.message(Command("cachestats"), AdminFilter())
async def cmd_cachestats(message: Message, cache: TTLCache) -> None:
    """Статистика кэша: попадания, промахи, количество записей."""
    stats = await cache.stats()
    total = stats["hits"] + stats["misses"]
    hit_rate = f"{stats['hits'] / total * 100:.1f}%" if total else "—"
    await message.answer(
        "🗂 <b>Кэш</b>\n"
        f"Записей: {stats['entries']}\n"
        f"Попаданий: {stats['hits']}\n"
        f"Промахов: {stats['misses']}\n"
        f"Эффективность: {hit_rate}"
    )


@router.message(Command("users"), AdminFilter())
async def cmd_users(message: Message) -> None:
    """Список пользователей, первая страница."""
    await _show_users_page(message, page=1)


@router.message(Command("recent"), AdminFilter())
async def cmd_recent(message: Message) -> None:
    """Последние запросы пользователей (история query_log)."""
    async for session in get_session():
        entries = (
            (
                await session.execute(
                    select(QueryLog).order_by(QueryLog.id.desc()).limit(10)
                )
            )
            .scalars()
            .all()
        )
    if not entries:
        await message.answer("История запросов пока пуста.")
        return
    lines = ["🕐 <b>Последние запросы</b>\n"]
    for entry in entries:
        time = entry.created_at.strftime("%d.%m %H:%M") if entry.created_at else "—"
        kind = "🔘" if entry.event_type == "callback" else "💬"
        text = entry.payload or entry.command or "—"
        lines.append(
            f"• {time} {kind} <code>{entry.telegram_id}</code> "
            f"<b>{entry.command or ''}</b> {text[:120]}"
        )
    await message.answer("\n".join(lines))


@router.callback_query(UsersPageCD.filter(), AdminFilter())
async def on_users_page(callback: CallbackQuery) -> None:
    """Листает страницы списка пользователей."""
    await _edit_users_page(callback, page=UsersPageCD.unpack(callback.data).page)


async def _users_page_text(page: int) -> tuple[str, int, bool]:
    """Текст страницы списка пользователей; возвращает (текст, страниц, есть_следующая)."""
    async for session in get_session():
        total = (await session.execute(select(func.count()).select_from(User))).scalar()
        users = (
            (
                await session.execute(
                    select(User)
                    .order_by(User.created_at.desc())
                    .offset((page - 1) * USERS_PER_PAGE)
                    .limit(USERS_PER_PAGE)
                )
            )
            .scalars()
            .all()
        )
    pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    lines = []
    for u in users:
        role = u.role or "user"
        badge = {
            "admin": "👑",
            "user": "🙂",
        }.get(role, role)
        name = u.first_name or u.username or u.telegram_id
        banned = " 🚫" if u.is_banned else ""
        handle = f" @{u.username}" if u.username else ""
        created = u.created_at.strftime("%d.%m %H:%M") if u.created_at else "—"
        lines.append(
            f"• {badge} <code>{u.telegram_id}</code> {name}{handle}{banned} — {created}"
        )
    header = f"👥 <b>Пользователи</b> — всего: {total}\n"
    return header + "\n".join(lines) or header + "пока нет", pages, page < pages


async def _users_page_kb(page: int, pages: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if page > 1:
        kb.button(text="◀️", callback_data=UsersPageCD(page=page - 1))
    kb.button(text=f"{page}/{pages}", callback_data=UsersPageCD(page=page))
    if page < pages:
        kb.button(text="▶️", callback_data=UsersPageCD(page=page + 1))
    return kb.as_markup()


async def _show_users_page(message: Message, page: int) -> None:
    text, pages, _ = await _users_page_text(page)
    await message.answer(text, reply_markup=await _users_page_kb(page, pages))


async def _edit_users_page(callback: CallbackQuery, page: int) -> None:
    text, pages, _ = await _users_page_text(page)
    await callback.message.edit_text(
        text, reply_markup=await _users_page_kb(page, pages)
    )
    await callback.answer()


@router.message(Command("broadcast"), AdminFilter())
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    """Начинает рассылку: запрашивает подтверждение у администратора."""
    text = command.args
    if not text:
        await message.answer("Укажи текст рассылки: /broadcast Привет всем!")
        return

    async for session in get_session():
        recipients = (
            (
                await session.execute(
                    select(User.telegram_id).where(User.is_banned.is_(False))
                )
            )
            .scalars()
            .all()
        )
    if not recipients:
        await message.answer("Нет зарегистрированных пользователей.")
        return

    pending_msg = await message.answer(
        f"⚠️ <b>Рассылка</b> {len(recipients)} пользователям?\n\n"
        f"<blockquote>{text[:500]}</blockquote>\n"
        "Подтверди или отмени:",
        reply_markup=_confirm_kb(),
    )
    _pending_broadcast[message.from_user.id] = (pending_msg.message_id, text)


def _confirm_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Да, рассылать", callback_data=BroadcastCD(action="confirm", msg_id=0)
    )
    kb.button(text="❌ Отмена", callback_data=BroadcastCD(action="cancel", msg_id=0))
    return kb.as_markup()


@router.callback_query(BroadcastCD.filter(), AdminFilter())
async def on_broadcast_confirm(callback: CallbackQuery, bot: Bot) -> None:
    """Обрабатывает подтверждение/отмену рассылки."""
    data = BroadcastCD.unpack(callback.data)
    pending = _pending_broadcast.pop(callback.from_user.id, None)
    if not pending:
        await callback.answer("Рассылка уже отменена или завершена.")
        await callback.message.edit_text("Рассылка не активна.")
        return
    if data.action == "cancel":
        await callback.message.edit_text("❌ Рассылка отменена.")
        await callback.answer()
        return

    text = pending[1]
    await callback.message.edit_text("📤 Отправляю рассылку…")
    await callback.answer()

    sent, failed = await _do_broadcast(bot, text)
    await callback.message.edit_text(
        f"✅ Рассылка завершена: отправлено <b>{sent}</b>, ошибок <b>{failed}</b>."
    )


async def _do_broadcast(bot: Bot, text: str) -> tuple[int, int]:
    """Отправляет текст всем незабаненным пользователям."""
    sent = failed = 0
    async for session in get_session():
        users = (
            (
                await session.execute(
                    select(User.telegram_id).where(User.is_banned.is_(False))
                )
            )
            .scalars()
            .all()
        )
    for user_id in users:
        try:
            await bot.send_message(user_id, text)
            sent += 1
        except Exception:  # noqa: BLE001 — один пользователь не должен ронять рассылку
            failed += 1
            log.warning("Не удалось отправить рассылку пользователю id=%s", user_id)
    return sent, failed


@router.message(Command("ban"), AdminFilter())
async def cmd_ban(message: Message, command: CommandObject) -> None:
    """Банит пользователя: /ban 123456789."""
    target = _parse_target(command)
    if target is None:
        await message.answer("Укажи Telegram ID: /ban 123456789")
        return
    async for session in get_session():
        result = await session.execute(
            update(User).where(User.telegram_id == target).values(is_banned=True)
        )
        await session.commit()
        banned = result.rowcount > 0
    await message.answer(
        f"🚫 Пользователь <code>{target}</code> забанен."
        if banned
        else f"⚠️ Пользователь <code>{target}</code> не найден (все равно блокируется при следующих запросах)."
    )


@router.message(Command("unban"), AdminFilter())
async def cmd_unban(message: Message, command: CommandObject) -> None:
    """Разбанивает пользователя: /unban 123456789."""
    target = _parse_target(command)
    if target is None:
        await message.answer("Укажи Telegram ID: /unban 123456789")
        return
    async for session in get_session():
        await session.execute(
            update(User).where(User.telegram_id == target).values(is_banned=False)
        )
        await session.commit()
    await message.answer(
        f"✅ Пользователь <code>{target}</code> разбанен (если был в базе)."
    )


def _parse_target(command: CommandObject) -> int | None:
    """Извлекает числовой Telegram ID из аргументов команды."""
    if not command.args:
        return None
    try:
        user_id = int(command.args.strip().split()[0])
    except (ValueError, IndexError):
        return None
    return user_id


_ROLES = ("user", "admin")


@router.message(Command("myrole"))
async def cmd_myrole(message: Message, role: str) -> None:
    """Показывает роль текущего пользователя."""
    await message.answer(f"Твоя роль: <b>{role}</b>")


def _parse_setrole_args(args: str | None) -> tuple[int, str] | None:
    """Разбирает аргументы /setrole: <id> <роль>. Возвращает (id, роль) или None."""
    parts = (args or "").split()
    if len(parts) < 2 or parts[1].lower() not in _ROLES:
        return None
    try:
        return int(parts[0]), parts[1].lower()
    except ValueError:
        return None


@router.message(Command("setrole"), SuperAdminFilter())
async def cmd_setrole(
    message: Message, command: CommandObject, invalidate_role: Any
) -> None:
    """Назначает роль пользователю: /setrole 123456789 admin|user.

    Только владелец бота (ADMIN_ID из .env) может назначать роли.
    """
    parsed = _parse_setrole_args(command.args)
    if parsed is None:
        await message.answer(
            "Формат: /setRole <id> <роль>\nРоли: user, admin\n"
            "Пример: /setrole 123456789 admin"
        )
        return
    target, new_role = parsed
    async for session in get_session():
        result = await session.execute(
            update(User).where(User.telegram_id == target).values(role=new_role)
        )
        await session.commit()
        updated = result.rowcount > 0
    if not updated:
        await message.answer(f"⚠️ Пользователь <code>{target}</code> не найден.")
        return
    if invalidate_role is not None:
        invalidate_role(target)
    await message.answer(
        f"✅ Роль пользователя <code>{target}</code> → <b>{new_role}</b>"
    )
