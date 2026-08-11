"""LLM-агент: генерация финансового анализа через OpenRouter (бесплатные модели).

Безопасность (AGENTS.md п.2): весь пользовательский ввод проходит через
sanitize_user_text() — подозрительные конструкции удаляются до передачи в LLM.
"""

import html
import logging
import re

import aiohttp

from src.config.settings import get_settings
from src.services.financial_api import _check_domain

log = logging.getLogger(__name__)

# Обрезаем ввод, чтобы не раздувать контекст и не тащить мусор (DoS)
MAX_QUERY_LENGTH = 2000
MAX_CONTEXT_LENGTH = 3000

API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Паттерны попыток промпт-инъекции (AGENTS.md п.2)
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

_SYSTEM_PROMPT = """Ты — финансовый аналитик в Telegram-боте. Отвечай по-русски, кратко и по делу (до 200 слов).

Правила:
- Анализируй ТОЛЬКО данные, переданные в контексте сообщения. Ничего не выдумывай.
- Не давай персональных инвестиционных рекомендаций («покупай/продавай»), только факты и возможные сценарии.
- Если данных недостаточно — честно скажи об этом.
- Формат: короткий вывод о текущей ситуации, что влияет на цену, ключевые уровни (если есть данные)."""


def sanitize_user_text(text: str) -> str:
    """Очищает пользовательский ввод от попыток промпт-инъекций.

    Возвращает обрезанный текст: удаляет подозрительные фрагменты и
    ограничивает длину. Пустой результат после очистки не передаётся в LLM.
    """
    cleaned = text.strip()
    if not cleaned:
        return ""
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned[:MAX_QUERY_LENGTH]


def markdown_to_html(text: str) -> str:
    """Конвертирует базовую markdown-разметку ответа LLM в HTML Telegram.

    Сначала экранируется весь текст (html.escape), затем markdown-акценты
    заменяются тегами — произвольный HTML от модели не проходит.
    """
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"^#{1,6}\s+", "", escaped, flags=re.MULTILINE)
    # Списки markdown -> маркеры (Telegram HTML не знает <ul>)
    escaped = re.sub(r"^[-*]\s+", "• ", escaped, flags=re.MULTILINE)
    return escaped


def build_messages(query: str, context: str) -> list[dict[str, str]]:
    """Собирает сообщения для LLM: системный промпт + данные + вопрос.

    context — проверенные данные из API (не пользовательский ввод),
    query — уже очищенный пользовательский запрос.
    """
    context_limited = context[:MAX_CONTEXT_LENGTH]
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Данные о активе:\n{context_limited}\n\n"
                f"Вопрос пользователя: {query}\n"
                "Дай анализ по этим данным."
            ),
        },
    ]


class LLMClient:
    """Клиент OpenRouter (OpenAI-совместимый /chat/completions).

    Использует бесплатную модель по умолчанию (openrouter_model из .env).
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
        """Отправляет запрос и возвращает текст анализа.

        У LLM-провайдеров время ответа больше, чем у котировок,
        поэтому свой таймаут: 60 секунд.
        """
        if not self._api_key:
            raise RuntimeError("OpenRouter API ключ не настроен (OPENROUTER_API_KEY)")

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
                raise RuntimeError("OpenRouter: превышен лимит бесплатных запросов")
            if resp.status != 200:
                body = await resp.text()
                log.error("OpenRouter ответил %s: %.200s", resp.status, body)
                raise RuntimeError(f"OpenRouter ответил {resp.status}")
            data = await resp.json()
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                log.warning(
                    "Ответ модели обрезан по лимиту max_tokens (%s)", self._max_tokens
                )
            return choice["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            log.error("Некорректный ответ OpenRouter: %.200s", str(data)[:200])
            raise RuntimeError("OpenRouter вернул некорректный ответ")
