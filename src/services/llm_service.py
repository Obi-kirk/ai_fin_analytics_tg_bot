"""LLM agent: financial analysis generation via OpenRouter (free models).

Security (AGENTS.md item 2): all user input passes through
sanitize_user_text() — suspicious constructs are removed before the LLM call.
"""

import html
import logging
import re

import aiohttp

from src.config.settings import get_settings
from src.i18n import t
from src.services.financial_api import _check_domain

log = logging.getLogger(__name__)

# Truncate input so we don't bloat the context or drag in junk (DoS)
MAX_QUERY_LENGTH = 2000
MAX_CONTEXT_LENGTH = 3000

API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Patterns of prompt-injection attempts (AGENTS.md item 2)
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(
        r"ignore\s+(all\s+)?prior\s+(instructions|prompts|messages)", re.IGNORECASE
    ),
    re.compile(r"disregard\s+(all\s+)?(previous|prior)", re.IGNORECASE),
    re.compile(r"\b(system|developer|assistant)\s*[:=]", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"\bnew\s+(system|developer)\s+prompt\b", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
)


def _system_prompt() -> str:
    """System prompt for the LLM in the bot's current language."""
    return t("llm.system_prompt")


def sanitize_user_text(text: str) -> str:
    """Cleans user input of prompt-injection attempts.

    Returns truncated text: removes suspicious fragments and
    limits the length. An empty result is not passed to the LLM.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned[:MAX_QUERY_LENGTH]


def markdown_to_html(text: str) -> str:
    """Converts the LLM reply's basic markdown into Telegram HTML.

    First the whole text is escaped (html.escape), then markdown accents
    are replaced with tags — arbitrary HTML from the model never passes.
    """
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"^#{1,6}\s+", "", escaped, flags=re.MULTILINE)
    # Markdown lists -> markers (Telegram HTML doesn't know <ul>)
    escaped = re.sub(r"^[-*]\s+", "• ", escaped, flags=re.MULTILINE)
    return escaped


def build_messages(query: str, context: str) -> list[dict[str, str]]:
    """Builds messages for the LLM: system prompt + data + question.

    context — verified data from the APIs (not user input),
    query — already sanitized user request.
    """
    context_limited = context[:MAX_CONTEXT_LENGTH]
    return [
        {"role": "system", "content": _system_prompt()},
        {
            "role": "user",
            "content": t("llm.user_prompt", context=context_limited, query=query),
        },
    ]


class LLMClient:
    """OpenRouter client (OpenAI-compatible /chat/completions).

    Uses a free model by default (openrouter_model from .env).
    """

    def __init__(
        self,
        api_key: str | None,
        model: str | None = None,
        max_tokens: int = 700,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    async def analyze(self, query: str, context: str) -> str:
        """Sends the request and returns the analysis text.

        LLM providers respond slower than quote APIs,
        so a dedicated timeout is used: 60 seconds.
        """
        if not self._api_key:
            raise RuntimeError(
                "OpenRouter API key is not configured (OPENROUTER_API_KEY)"
            )

        settings = get_settings()
        model = self._model or settings.openrouter_model
        timeout = aiohttp.ClientTimeout(total=60)
        payload = {
            "model": model,
            "messages": build_messages(query, context),
            "max_tokens": self._max_tokens,
            "temperature": 0.3,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        _check_domain(API_URL)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
            API_URL, json=payload, headers=headers
        ) as resp:
            if resp.status == 429:
                raise RuntimeError("OpenRouter: free request rate limit exceeded")
            if resp.status != 200:
                body = await resp.text()
                log.error("OpenRouter responded %s: %.200s", resp.status, body)
                raise RuntimeError(f"OpenRouter responded {resp.status}")
            data = await resp.json()
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                log.warning(
                    "Model reply truncated by the max_tokens limit (%s)",
                    self._max_tokens,
                )
            return choice["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            log.error("Malformed OpenRouter response: %.200s", str(data)[:200])
            raise RuntimeError("OpenRouter returned a malformed response")
