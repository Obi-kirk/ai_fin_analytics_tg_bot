"""Daily digest subscription and setup: /digest + inline buttons.

Callbacks:
  dg:on / dg:off               — subscribe / unsubscribe
  dg:send                      — build the digest now (for the user's set)
  dg:setup                     — set setup categories
  dg:setup_cat:TYPE            — category asset list (toggles)
  dg:toggle:TYPE:SYM           — enable / disable an asset in the set
  dg:back                      — back from setup to the status
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, select, update

from src.config.settings import get_settings
from src.database.db import get_session
from src.database.models import DigestAsset, DigestSubscription
from src.i18n import t
from src.services.cache import TTLCache
from src.services.digest import DIGEST_AVAILABLE, build_digest

router = Router()

TYPE_ICONS = {"fx": "💱", "stock": "📈", "crypto": "🪙"}


def _type_title(asset_type: str) -> str:
    """Category name in the current language."""
    return t(f"digest.type.{asset_type}")


async def _get_subscription(telegram_id: int) -> DigestSubscription | None:
    """The user's digest subscription row (None — not subscribed)."""
    async for session in get_session():
        return (
            await session.execute(
                select(DigestSubscription).where(
                    DigestSubscription.telegram_id == telegram_id
                )
            )
        ).scalar()
    return None


async def _is_subscribed(telegram_id: int) -> bool:
    """Whether the user is subscribed to the digest."""
    return await _get_subscription(telegram_id) is not None


async def _asset_symbols(telegram_id: int, asset_type: str) -> set[str]:
    """Symbols of the selected type in the personal set."""
    async for session in get_session():
        rows = (
            (
                await session.execute(
                    select(DigestAsset.symbol).where(
                        DigestAsset.telegram_id == telegram_id,
                        DigestAsset.asset_type == asset_type,
                    )
                )
            )
            .scalars()
            .all()
        )
    return set(rows)


async def _status_text(subscribed: bool, telegram_id: int) -> str:
    """Subscription status text."""
    settings = get_settings()
    state = t("digest.status.on") if subscribed else t("digest.status.off")
    sub = await _get_subscription(telegram_id)
    if sub is not None and sub.digest_hour is not None:
        hour = sub.digest_hour
        minute = sub.digest_minute if sub.digest_minute is not None else 0
    else:
        hour = settings.digest_hour
        minute = settings.digest_minute
    return t(
        "digest.status",
        state=state,
        time=f"{hour:02d}:{minute:02d}",
    )


def _status_kb(subscribed: bool) -> InlineKeyboardMarkup:
    """Status buttons: time, setup, send, subscribe."""
    sub_label = t("digest.btn.unsubscribe") if subscribed else t("digest.btn.subscribe")
    sub_data = "dg:off" if subscribed else "dg:on"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("digest.btn.time"), callback_data="dg:time"),
        InlineKeyboardButton(text=t("digest.btn.setup"), callback_data="dg:setup"),
        InlineKeyboardButton(text=t("digest.btn.send"), callback_data="dg:send"),
    )
    builder.row(InlineKeyboardButton(text=sub_label, callback_data=sub_data))
    return builder.as_markup()


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    """Shows the daily digest subscription status."""
    subscribed = await _is_subscribed(message.from_user.id)
    await message.answer(
        await _status_text(subscribed, message.from_user.id),
        reply_markup=_status_kb(subscribed),
    )


@router.callback_query(F.data == "digest:open")
async def on_digest_open(callback: CallbackQuery) -> None:
    """Opens the digest status from the portfolio menu."""
    subscribed = await _is_subscribed(callback.from_user.id)
    await callback.message.edit_text(
        await _status_text(subscribed, callback.from_user.id),
        reply_markup=_status_kb(subscribed),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:on")
async def on_digest_on(callback: CallbackQuery) -> None:
    """Subscribes to the digest."""
    async for session in get_session():
        exists = (
            await session.execute(
                select(DigestSubscription).where(
                    DigestSubscription.telegram_id == callback.from_user.id
                )
            )
        ).scalar()
        if exists is None:
            session.add(DigestSubscription(telegram_id=callback.from_user.id))
            await session.commit()
    await callback.message.edit_text(
        f"{t('digest.subscribed')}\n\n{await _status_text(True, callback.from_user.id)}",
        reply_markup=_status_kb(True),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:off")
async def on_digest_off(callback: CallbackQuery) -> None:
    """Unsubscribes from the digest."""
    async for session in get_session():
        await session.execute(
            delete(DigestSubscription).where(
                DigestSubscription.telegram_id == callback.from_user.id
            )
        )
        await session.commit()
    await callback.message.edit_text(
        f"{t('digest.unsubscribed')}\n\n{await _status_text(False, callback.from_user.id)}",
        reply_markup=_status_kb(False),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:send")
async def on_digest_send(callback: CallbackQuery, cache: TTLCache) -> None:
    """Builds the digest for the user's set and sends it now."""
    await callback.answer(t("digest.sending"))
    try:
        text = await build_digest(callback.from_user.id, cache)
        await callback.message.answer(text)
    except Exception:  # noqa: BLE001 — external API boundary
        await callback.message.answer(t("digest.failed"))


async def _setup_kb(telegram_id: int) -> InlineKeyboardMarkup:
    """Keyboard of the set setup categories."""
    counts = {
        t: len(await _asset_symbols(telegram_id, t)) for t in ("fx", "stock", "crypto")
    }
    builder = InlineKeyboardBuilder()
    builder.row(
        *[
            InlineKeyboardButton(
                text=(
                    f"{TYPE_ICONS[t]} {_type_title(t)} "
                    f"({counts[t]}/{len(DIGEST_AVAILABLE[t])})"
                ),
                callback_data=f"dg:setup_cat:{t}",
            )
            for t in ("fx", "stock", "crypto")
        ]
    )
    builder.row(
        InlineKeyboardButton(text=t("digest.btn.back"), callback_data="dg:back")
    )
    return builder.as_markup()


async def _toggle_kb(telegram_id: int, asset_type: str) -> InlineKeyboardMarkup:
    """Toggle buttons of the category assets."""
    selected = await _asset_symbols(telegram_id, asset_type)
    builder = InlineKeyboardBuilder()
    symbols = DIGEST_AVAILABLE[asset_type]
    for i in range(0, len(symbols), 3):
        builder.row(
            *[
                InlineKeyboardButton(
                    text=f"{'✅' if s in selected else '☑️'} {s}",
                    callback_data=f"dg:toggle:{asset_type}:{s}",
                )
                for s in symbols[i : i + 3]
            ]
        )
    builder.row(
        InlineKeyboardButton(text=t("digest.btn.categories"), callback_data="dg:setup")
    )
    return builder.as_markup()


@router.callback_query(F.data == "dg:setup")
async def on_digest_setup(callback: CallbackQuery) -> None:
    """Asset set setup menu."""
    await callback.message.edit_text(
        t("digest.setup.title"),
        reply_markup=await _setup_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^dg:setup_cat:(fx|stock|crypto)$"))
async def on_digest_setup_cat(callback: CallbackQuery) -> None:
    """Category asset list with toggles."""
    asset_type = callback.data.split(":", 2)[2]
    selected = await _asset_symbols(callback.from_user.id, asset_type)
    await callback.message.edit_text(
        t(
            "digest.setup_cat",
            icon=TYPE_ICONS[asset_type],
            title=_type_title(asset_type),
            sel=len(selected),
            total=len(DIGEST_AVAILABLE[asset_type]),
        ),
        reply_markup=await _toggle_kb(callback.from_user.id, asset_type),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^dg:toggle:(fx|stock|crypto):[A-Z0-9.\-]+$"))
async def on_digest_toggle(callback: CallbackQuery) -> None:
    """Enables / disables an asset in the personal set."""
    _, _, asset_type, symbol = callback.data.split(":", 3)
    async for session in get_session():
        exists = (
            await session.execute(
                select(DigestAsset).where(
                    DigestAsset.telegram_id == callback.from_user.id,
                    DigestAsset.symbol == symbol,
                )
            )
        ).scalar()
        if exists is None:
            session.add(
                DigestAsset(
                    telegram_id=callback.from_user.id,
                    asset_type=asset_type,
                    symbol=symbol,
                )
            )
        else:
            await session.execute(
                delete(DigestAsset).where(
                    DigestAsset.telegram_id == callback.from_user.id,
                    DigestAsset.symbol == symbol,
                )
            )
        await session.commit()
    selected = await _asset_symbols(callback.from_user.id, asset_type)
    await callback.message.edit_text(
        t(
            "digest.setup_cat",
            icon=TYPE_ICONS[asset_type],
            title=_type_title(asset_type),
            sel=len(selected),
            total=len(DIGEST_AVAILABLE[asset_type]),
        ),
        reply_markup=await _toggle_kb(callback.from_user.id, asset_type),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:back")
async def on_digest_back(callback: CallbackQuery) -> None:
    """Back from setup to the subscription status."""
    subscribed = await _is_subscribed(callback.from_user.id)
    await callback.message.edit_text(
        await _status_text(subscribed, callback.from_user.id),
        reply_markup=_status_kb(subscribed),
    )
    await callback.answer()


# ---------------------------------------------------------- digest time

_HOURS = (
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "20",
    "21",
    "22",
    "23",
)
_MINUTES = ("00", "15", "30", "45")


def _time_kb(step: str, hour: int | None = None) -> InlineKeyboardMarkup:
    """Hour or minute chooser for the personal digest time."""
    builder = InlineKeyboardBuilder()
    if step == "hour":
        for i in range(0, len(_HOURS), 4):
            builder.row(
                *[
                    InlineKeyboardButton(text=h, callback_data=f"dg:time_hour:{h}")
                    for h in _HOURS[i : i + 4]
                ]
            )
    else:
        builder.row(
            *[
                InlineKeyboardButton(
                    text=m, callback_data=f"dg:time_set:{hour:02d}:{m}"
                )
                for m in _MINUTES
            ]
        )
    builder.row(
        InlineKeyboardButton(
            text=t("digest.btn.reset_time"), callback_data="dg:time_reset"
        ),
        InlineKeyboardButton(text=t("digest.btn.back"), callback_data="dg:status"),
    )
    return builder.as_markup()


@router.callback_query(F.data == "dg:time")
async def on_digest_time(callback: CallbackQuery) -> None:
    """Starts the personal digest time setup: choose the hour."""
    await callback.message.edit_text(
        t("digest.time_hour"), reply_markup=_time_kb("hour")
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^dg:time_hour:(\d{2})$"))
async def on_digest_time_hour(callback: CallbackQuery) -> None:
    """Accepts the hour and asks for the minutes."""
    hour = int(callback.data.split(":")[2])
    await callback.message.edit_text(
        t("digest.time_minute", hour=f"{hour:02d}"),
        reply_markup=_time_kb("minute", hour),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^dg:time_set:(\d{2}):(\d{2})$"))
async def on_digest_time_set(callback: CallbackQuery) -> None:
    """Saves the personal digest time."""
    _, _, hour_raw, minute_raw = callback.data.split(":")
    hour = int(hour_raw)
    minute = int(minute_raw)
    async for session in get_session():
        sub = (
            await session.execute(
                select(DigestSubscription).where(
                    DigestSubscription.telegram_id == callback.from_user.id
                )
            )
        ).scalar()
        if sub is None:
            sub = DigestSubscription(telegram_id=callback.from_user.id)
            session.add(sub)
        sub.digest_hour = hour
        sub.digest_minute = minute
        await session.commit()
    subscribed = await _is_subscribed(callback.from_user.id)
    await callback.message.edit_text(
        t("digest.time_saved", time=f"{hour:02d}:{minute:02d}")
        + "\n\n"
        + await _status_text(subscribed, callback.from_user.id),
        reply_markup=_status_kb(subscribed),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:time_reset")
async def on_digest_time_reset(callback: CallbackQuery) -> None:
    """Resets the personal digest time to the default (from settings)."""
    async for session in get_session():
        await session.execute(
            update(DigestSubscription)
            .where(DigestSubscription.telegram_id == callback.from_user.id)
            .values(digest_hour=None, digest_minute=None)
        )
        await session.commit()
    subscribed = await _is_subscribed(callback.from_user.id)
    await callback.message.edit_text(
        await _status_text(subscribed, callback.from_user.id),
        reply_markup=_status_kb(subscribed),
    )
    await callback.answer()


@router.callback_query(F.data == "dg:status")
async def on_digest_status(callback: CallbackQuery) -> None:
    """Back to the subscription status."""
    subscribed = await _is_subscribed(callback.from_user.id)
    await callback.message.edit_text(
        await _status_text(subscribed, callback.from_user.id),
        reply_markup=_status_kb(subscribed),
    )
    await callback.answer()
