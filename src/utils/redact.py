"""Маскировка персональных данных (PII) в логах — AGENTS.md п.9.

Форматтер RedactFormatter применяет маску к любому сообщению лога:
телефоны, email и длинные числовые номера заменяются на [REDACTED].
Telegram ID (9-10 цифр) не маскируется: он нужен для /ban и отладки,
это псевдоним, а не контактные данные.
"""

import logging
import re

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Телефоны: с кодом страны (+7 912...), мобильные 9XXXXXXXXX или номера
# с разделителями (дефисы/пробелы). Голые 10-значные числа (Telegram ID,
# коды) не маскируются.
_PHONE_RE = re.compile(
    r"(?<![A-Za-zА-Яа-я0-9])"
    r"(?:"
    r"(?:\+7|\+1|\+44|\+49|8)(?:[\s\-()]*\d){10,11}"
    r"|9(?:[\s\-()]*\d){9}"
    r"|(?:\d+[\s\-()]+){3,}\d{2,4}"
    r")(?!\d)"
)
# Карты/счета: непрерывные последовательности из 13+ цифр
_LONG_NUMBER_RE = re.compile(r"\b\d{13,}\b")

_REDACTED_EMAIL = "[REDACTED_EMAIL]"
_REDACTED_PHONE = "[REDACTED_PHONE]"
_REDACTED_NUMBER = "[REDACTED_NUMBER]"


def redact_pii(text: str) -> str:
    """Заменяет контактные данные в тексте на маски."""
    text = _LONG_NUMBER_RE.sub(_REDACTED_NUMBER, text)
    text = _EMAIL_RE.sub(_REDACTED_EMAIL, text)
    text = _PHONE_RE.sub(_REDACTED_PHONE, text)
    return text


class RedactFormatter(logging.Formatter):
    """Форматтер логов: маскирует PII до применения стандартного формата."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        record.msg = redact_pii(message)
        record.args = ()
        return super().format(record)
