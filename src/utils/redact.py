"""PII masking in logs — AGENTS.md item 9.

The RedactFormatter applies a mask to every log message:
phones, emails and long numeric strings are replaced with [REDACTED].
Telegram IDs (9-10 digits) are not masked: they are needed for /ban and
debugging, being a pseudonym rather than contact data.
"""

import logging
import re

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Phones: with country code (+7 912...), mobile 9XXXXXXXXX or numbers
# with separators (dashes/spaces). Bare 10-digit numbers (Telegram IDs,
# codes) are not masked.
_PHONE_RE = re.compile(
    r"(?<![A-Za-zА-Яа-я0-9])"
    r"(?:"
    r"(?:\+7|\+1|\+44|\+49|8)(?:[\s\-()]*\d){10,11}"
    r"|9(?:[\s\-()]*\d){9}"
    r"|(?:\d+[\s\-()]+){3,}\d{2,4}"
    r")(?!\d)"
)
# Cards/accounts: continuous sequences of 13+ digits
_LONG_NUMBER_RE = re.compile(r"\b\d{13,}\b")

_REDACTED_EMAIL = "[REDACTED_EMAIL]"
_REDACTED_PHONE = "[REDACTED_PHONE]"
_REDACTED_NUMBER = "[REDACTED_NUMBER]"


def redact_pii(text: str) -> str:
    """Replaces contact data in the text with masks."""
    text = _LONG_NUMBER_RE.sub(_REDACTED_NUMBER, text)
    text = _EMAIL_RE.sub(_REDACTED_EMAIL, text)
    text = _PHONE_RE.sub(_REDACTED_PHONE, text)
    return text


class RedactFormatter(logging.Formatter):
    """Log formatter: masks PII before applying the standard format."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        record.msg = redact_pii(message)
        record.args = ()
        return super().format(record)
