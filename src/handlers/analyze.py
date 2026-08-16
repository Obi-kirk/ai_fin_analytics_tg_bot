"""Handler for /analyze and analyse:* callbacks — AI analysis of an asset (OpenRouter).

Security: user input goes through sanitize_user_text() before being sent
to the LLM (AGENTS.md item 2); length is limited (MAX_QUERY_LENGTH).
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.config.settings import get_settings
from src.handlers.crypto import COIN_RE, fetch_crypto
from src.handlers.stock import (
    TICKER_RE,
    fetch_news,
    fetch_stock,
    is_ru_stock,
    resolve_stock_symbol,
)
from src.i18n import t
from src.services.cache import TTLCache
from src.services.financial_api import (
    CoinGeckoClient,
    FinnhubClient,
    make_session,
)
from src.services.llm_service import (
    LLMClient,
    markdown_to_html,
    sanitize_user_text,
)

log = logging.getLogger(__name__)
router = Router()


# AI analysis symbols: every stock/coin from the menus can be analyzed.
# Built from the menu sets to stay in sync (lazy import to avoid cycles).
def _analyse_types() -> dict[str, str]:
    from src.handlers.menu import ANALYSE_GROUPS
    from src.handlers.stock import RU_STOCKS

    types: dict[str, str] = {}
    for sym in ANALYSE_GROUPS["stock_world"] + ANALYSE_GROUPS["index"]:
        types[sym] = "stock"
    for sym in RU_STOCKS:
        types[sym] = "stock"
    for sym in ANALYSE_GROUPS["crypto"]:
        types[sym] = "crypto"
    return types


ANALYSE_TYPES = _analyse_types()

QUERY_RE = re.compile(r"^[\w\s.,!?()%$€¥£+-]{1,500}$")

SendText = Callable[[str], Awaitable[None]]

# Context limits: keep the prompt compact (AGENTS.md: token economy)
MAX_DESCRIPTION_LENGTH = 300
MAX_NEWS_ITEMS = 3
MAX_NEWS_LENGTH = 120


async def _fetch_company_profile(symbol: str) -> dict:
    """Finnhub company profile (cached separately from the quote)."""
    async with make_session() as session:
        client = FinnhubClient(get_settings().finnhub_api_key)
        return await client.get_company_profile(symbol, session)


async def _fetch_market_data(coin_id: str) -> dict:
    """CoinGecko fundamental coin data."""
    async with make_session() as session:
        client = CoinGeckoClient(get_settings().coingecko_api_key)
        return await client.get_market_data(coin_id, session)


async def _fetch_price_history(coin_id: str) -> list[float]:
    """30-day coin price history (for the 7d/30d trend)."""
    async with make_session() as session:
        client = CoinGeckoClient(get_settings().coingecko_api_key)
        return await client.get_price_history(coin_id, session)


def _trend_change(prices: list[float], days: int) -> float | None:
    """Price change (%) from the price N days ago to the last one."""
    if len(prices) < 2:
        return None
    step = max(1, len(prices) // days)
    first = prices[-1 - step]
    last = prices[-1]
    if not first:
        return None
    return (last / first - 1) * 100


def _format_money(value: float | None) -> str:
    """Formats large amounts: 1.29T, 21.3B, 900M, 126.1K."""
    if not value:
        return "—"
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    if value >= 1e3:
        return f"${value / 1e3:.1f}K"
    return f"${value:,.0f}"


def _clean_text(text: str, limit: int) -> str:
    """Removes extra spaces/line breaks and truncates the text."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned[:limit]


async def _stock_context(symbol: str, cache: TTLCache) -> str:
    """Stock/index context: quote + company profile + fresh news.

    For indexes (SPX/DJI/VIX) Finnhub does not provide ^-tickers — ETF
    equivalents are used via resolve_stock_symbol (SPX→SPY, etc.).
    Extra data does not break the analysis: the quote remains on failure.
    """
    settings = get_settings()
    resolved = resolve_stock_symbol(symbol)
    quote = await fetch_stock(resolved)
    sign = "+" if quote.change_percent >= 0 else ""
    lines = [
        t("analyze.ctx.type_stock"),
        t("analyze.ctx.symbol", symbol=symbol),
        t("analyze.ctx.price", price=f"{quote.price:.4f}"),
        t("analyze.ctx.day_change", sign=sign, pct=f"{quote.change_percent:.2f}"),
    ]

    profile: dict = {}
    news: list[dict] = []
    try:
        news = await cache.get_or_set(
            f"stock:news:{resolved}",
            lambda: fetch_news(resolved),
            settings.cache_ttl_fundamental_seconds,
        )
        # Russian (MOEX) stocks: no Finnhub profile — the company name
        # comes with the quote (SHORTNAME). World stocks: Finnhub profile.
        if not is_ru_stock(resolved):
            profile = await cache.get_or_set(
                f"stock:profile:{resolved}",
                lambda: _fetch_company_profile(resolved),
                settings.cache_ttl_fundamental_seconds,
            )
    except Exception:  # noqa: BLE001 — extra data is not critical
        log.warning("Failed to fetch profile/news for %s", symbol)

    company_name = quote.name or profile.get("name")
    if company_name or profile.get("finnhubIndustry"):
        lines.append(
            t(
                "analyze.ctx.company",
                name=company_name or "—",
                industry=profile.get("finnhubIndustry") or "—",
            )
        )
    if profile.get("description"):
        lines.append(
            t(
                "analyze.ctx.desc",
                desc=_clean_text(profile["description"], MAX_DESCRIPTION_LENGTH),
            )
        )
    headlines = [_clean_text(n.get("headline", ""), MAX_NEWS_LENGTH) for n in news]
    headlines = [h for h in headlines if h][:MAX_NEWS_ITEMS]
    if headlines:
        lines.append(t("analyze.ctx.news"))
        lines.extend(f"- {h}" for h in headlines)
    return "\n".join(lines)


async def _crypto_context(symbol: str, cache: TTLCache) -> str:
    """Cryptocurrency context: quote + market cap + 7d/30d trend."""
    settings = get_settings()
    quote = await fetch_crypto(symbol)
    sign = "+" if quote.change_percent >= 0 else ""
    lines = [
        t("analyze.ctx.type_crypto"),
        t("analyze.ctx.symbol", symbol=quote.symbol),
        t("analyze.ctx.price", price=f"{quote.price:.4f}"),
        t("analyze.ctx.change_24h", sign=sign, pct=f"{quote.change_percent:.2f}"),
    ]

    market: dict = {}
    history: list[float] = []
    try:
        coin_id = _gecko_id(symbol)
        market = await cache.get_or_set(
            f"crypto:market:{symbol}",
            lambda: _fetch_market_data(coin_id),
            settings.cache_ttl_fundamental_seconds,
        )
        history = await cache.get_or_set(
            f"crypto:chart:{symbol}",
            lambda: _fetch_price_history(coin_id),
            settings.cache_ttl_fundamental_seconds,
        )
    except Exception:  # noqa: BLE001 — extra data is not critical
        log.warning("Failed to fetch market data for %s", symbol)

    if market.get("name") or market.get("rank"):
        lines.append(
            t(
                "analyze.ctx.coin",
                name=market.get("name") or "—",
                rank=market.get("rank") or "—",
            )
        )
    cap = _format_money(market.get("market_cap"))
    volume = _format_money(market.get("volume"))
    ath = _format_money(market.get("ath"))
    lines.append(t("analyze.ctx.fund", cap=cap, vol=volume, ath=ath))
    change_7d = _trend_change(history, 7)
    change_30d = _trend_change(history, 30)
    if change_7d is not None:
        lines.append(
            t(
                "analyze.ctx.trend",
                c7=f"{change_7d:+.2f}",
                c30=f"{change_30d:+.2f}",
            )
        )
    if market.get("description"):
        lines.append(
            t(
                "analyze.ctx.desc",
                desc=_clean_text(market["description"], MAX_DESCRIPTION_LENGTH),
            )
        )
    return "\n".join(lines)


def _gecko_id(symbol: str) -> str:
    """CoinGecko coin id by symbol (aligned with COINS in crypto.py)."""
    from src.handlers.crypto import COINS

    return COINS.get(symbol.upper(), symbol.lower())


async def _market_context(symbol: str, cache: TTLCache) -> str:
    """Context of an asset by a known AI analysis submenu symbol."""
    if ANALYSE_TYPES.get(symbol.upper()) == "crypto":
        return await _crypto_context(symbol, cache)
    return await _stock_context(symbol, cache)


async def _detect_context(token: str, cache: TTLCache) -> str | None:
    """Detects an asset by token; None — it is not an asset (text query).

    For unknown tickers a stock is tried first, then a coin; LookupError
    means "no such asset" and is not treated as an error.
    """
    upper = token.upper()
    if upper in ANALYSE_TYPES:
        return await _market_context(upper, cache)
    if TICKER_RE.match(upper):
        try:
            return await _stock_context(upper, cache)
        except LookupError:
            pass
    if COIN_RE.match(upper):
        try:
            return await _crypto_context(upper, cache)
        except LookupError:
            pass
    return None


@router.message(Command("analyze"))
async def cmd_analyze(message: Message, bot: Bot, cache: TTLCache) -> None:
    """AI analysis: /analyze BTC, /analyze BTC should I buy, /analyze question."""
    raw = message.text.split(maxsplit=1)
    if len(raw) < 2:
        await message.answer(t("analyze.usage"))
        return

    parts = raw[1].strip().split(maxsplit=1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    context = None
    try:
        context = await _detect_context(first, cache)
    except Exception:  # noqa: BLE001 — external API, error already logged
        await message.answer(t("analyze.fetch_failed"))
        return
    if context is not None:
        query = sanitize_user_text(rest) or t(
            "analyze.auto_query", symbol=first.upper()
        )
    else:
        query = sanitize_user_text(raw[1])
    if not query or not QUERY_RE.match(query):
        await message.answer(t("analyze.bad_query"))
        return
    await _run_analysis(bot, message.chat.id, message.answer, query, context)


@router.callback_query(F.data.regexp(r"^analyse:[A-Z]+$"))
async def on_analyse(callback: CallbackQuery, bot: Bot, cache: TTLCache) -> None:
    """Analysis of a ticker from the AI analysis submenu."""
    symbol = callback.data.split(":", 1)[1]
    if symbol not in ANALYSE_TYPES:
        await callback.answer(t("analyze.unknown_asset"))
        return
    await callback.answer()
    try:
        context = await _market_context(symbol, cache)
    except Exception:  # noqa: BLE001 — external API, error already logged
        await callback.message.answer(t("analyze.fetch_failed"))
        return
    query = t("analyze.auto_query", symbol=symbol)
    await _run_analysis(
        bot, callback.message.chat.id, callback.message.answer, query, context
    )


async def _run_analysis(
    bot: Bot,
    chat_id: int,
    send_text: SendText,
    query: str,
    context: str | None,
) -> None:
    """Sends the request to the LLM with a "typing…" indicator."""
    settings = get_settings()
    if not settings.openrouter_api_key:
        await send_text(t("analyze.not_configured"))
        return

    typing_task = asyncio.create_task(_typing_loop(bot, chat_id))
    try:
        if context is None:
            context = t("analyze.context_prompt", query=query[:300])
        client = LLMClient(
            settings.openrouter_api_key,
            max_tokens=settings.openrouter_max_tokens,
        )
        result = await client.analyze(query, context)
    except Exception:
        log.exception("AI analysis failed")
        await send_text(t("analyze.failed"))
        return
    finally:
        typing_task.cancel()
    await send_text(
        f"{t('analyze.title')}\n\n{markdown_to_html(result)}{t('analyze.disclaimer')}"
    )


async def _typing_loop(bot: Bot, chat_id: int) -> None:
    """Shows "typing…" while the LLM is thinking (5 s — Telegram limit)."""
    try:
        while True:
            await bot.send_chat_action(chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        pass
