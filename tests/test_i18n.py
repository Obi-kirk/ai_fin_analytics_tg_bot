"""Тесты i18n: словари, подстановка параметров, смена языка."""

import pytest

from src.i18n import SUPPORTED_LANGUAGES, get_lang, reset_lang, set_lang, t


@pytest.fixture(autouse=True)
def _reset_lang():
    reset_lang()
    yield
    reset_lang()


class TestI18n:
    def test_supported_languages(self) -> None:
        assert SUPPORTED_LANGUAGES == ("ru", "en")

    def test_default_language_is_ru(self) -> None:
        assert get_lang() == "ru"

    def test_t_returns_ru_by_default(self) -> None:
        assert "Мой портфель" in t("portfolio.empty")

    def test_switch_to_en(self) -> None:
        set_lang("en")
        assert "My portfolio" in t("portfolio.empty")

    def test_format_params(self) -> None:
        text = t("fx.not_supported", code="ZZZ", currencies="USD, EUR")
        assert "ZZZ" in text
        assert "USD, EUR" in text

    def test_unknown_key_returns_key(self) -> None:
        assert t("no.such.key") == "no.such.key"

    def test_all_ru_keys_have_en_translation(self) -> None:
        from src.i18n import _STRINGS

        ru_keys = set(_STRINGS["ru"])
        en_keys = set(_STRINGS["en"])
        missing = ru_keys - en_keys
        assert not missing, f"Нет EN-перевода для ключей: {missing}"
        extra = en_keys - ru_keys
        assert not extra, f"Лишние EN-ключи: {extra}"

    def test_all_strings_resolve_without_errors(self) -> None:
        from src.i18n import _STRINGS

        for lang in SUPPORTED_LANGUAGES:
            set_lang(lang)
            for key in _STRINGS[lang]:
                assert t(key) is not None
