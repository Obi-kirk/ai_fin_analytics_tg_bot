"""Shared pytest fixtures: reset i18n language after each test."""

import pytest

from src.i18n import reset_lang


@pytest.fixture(autouse=True)
def _reset_i18n_lang():
    """Resets the bot language to the configured default after each test."""
    yield
    reset_lang()
