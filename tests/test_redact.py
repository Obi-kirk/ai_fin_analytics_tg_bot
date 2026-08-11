"""Тесты маскировки PII в логах (AGENTS.md п.9)."""

import logging

from src.utils.redact import RedactFormatter, redact_pii


class TestRedactPii:
    def test_email(self) -> None:
        assert redact_pii("почта user@example.com осталась") == (
            "почта [REDACTED_EMAIL] осталась"
        )

    def test_phone_russian(self) -> None:
        assert redact_pii("+7 912 345-67-89") == "[REDACTED_PHONE]"

    def test_phone_plain(self) -> None:
        assert redact_pii("телефон 89123456789 рядом") == (
            "телефон [REDACTED_PHONE] рядом"
        )

    def test_long_number_card(self) -> None:
        assert redact_pii("карта 4111111111111111 ок") == ("карта [REDACTED_NUMBER] ок")

    def test_telegram_id_kept(self) -> None:
        assert redact_pii("пользователь id=123456789 забанен") == (
            "пользователь id=123456789 забанен"
        )

    def test_bot_id_kept(self) -> None:
        assert redact_pii("Run polling for bot id=8980569456") == (
            "Run polling for bot id=8980569456"
        )

    def test_phone_with_separators_kept(self) -> None:
        assert redact_pii("телефон 495 123-45-67 рядом") == (
            "телефон [REDACTED_PHONE] рядом"
        )

    def test_username_kept(self) -> None:
        assert redact_pii("@some_user") == "@some_user"

    def test_plain_text_unchanged(self) -> None:
        text = "Ошибка в хендлере callback_query"
        assert redact_pii(text) == text


class TestRedactFormatter:
    def test_formatter_masks_message(self) -> None:
        formatter = RedactFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Ошибка: user@site.com",
            args=(),
            exc_info=None,
        )
        assert formatter.format(record) == "Ошибка: [REDACTED_EMAIL]"

    def test_formatter_masks_args(self) -> None:
        formatter = RedactFormatter("%(message)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Пользователь %s позвонил",
            args=("+79991234567",),
            exc_info=None,
        )
        assert formatter.format(record) == "Пользователь [REDACTED_PHONE] позвонил"
