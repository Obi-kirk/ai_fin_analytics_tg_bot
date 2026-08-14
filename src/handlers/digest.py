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
from sqlalchemy import delete, select

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


async def _is_subscribed(telegram_id: int) -> bool:
    """Whether the user is subscribed to the digest."""
    async for session in get_session():
        sub = (
            await session.execute(
                select(DigestSubscription).where(
                    DigestSubscription.telegram_id == telegram_id
                )
            )
        ).scalar()
    return sub is not None


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


def _status_text(subscribed: bool) -> str:
    """Subscription status text."""
    settings = get_settings()
    state = t("digest.status.on") if subscribed else t("digest.status.off")
    return t(
        "digest.status",
        state=state,
        time=f"{settings.digest_hour:02d}:{settings.digest_minute:02d}",
    )


def _status_kb(subscribed: bool) -> InlineKeyboardMarkup:
    """Status buttons: setup, send, subscribe."""
    sub_label = t("digest.btn.unsubscribe") if subscribed else t("digest.btn.subscribe")
    sub_data = "dg:off" if subscribed else "dg:on"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("digest.btn.setup"), callback_data="dg:setup"),
        InlineKeyboardButton(text=t("digest.btn.send"), callback_data="dg:send"),
    )
    builder.row(InlineKeyboardButton(text=sub_label, callback_data=sub_data))
    return builder.as_markup()


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    """Shows the daily digest subscription status."""
    subscribed = await _is_subscribed(message.from_user.id)
    await message.answer(_status_text(subscribed), reply_markup=_status_kb(subscribed))


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
        f"{t('digest.subscribed')}\n\n{_status_text(True)}",
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
        f"{t('digest.unsubscribed')}\n\n{_status_text(False)}",
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
        _status_text(subscribed), reply_markup=_status_kb(subscribed)
    )
    await callback.answer()
