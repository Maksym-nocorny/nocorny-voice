"""lang_routing: who skips the local worker and goes straight to Gemini."""
from __future__ import annotations

import pytest

import lang_routing


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(lang_routing, "STT_CLOUD_LANGS", frozenset({"fa"}))
    lang_routing.reset()
    yield
    lang_routing.reset()


def test_cloud_language_matching_handles_regions_and_case():
    assert lang_routing.is_cloud_language("fa")
    assert lang_routing.is_cloud_language("FA-IR")
    assert lang_routing.is_cloud_language("fa_IR")
    assert not lang_routing.is_cloud_language("en")
    assert not lang_routing.is_cloud_language(None)
    assert not lang_routing.is_cloud_language("")


def test_ui_language_is_only_a_first_guess():
    assert lang_routing.prefers_cloud(1, "fa")
    assert not lang_routing.prefers_cloud(1, "en")
    # Whisper heard Russian from the "Persian UI" user -> local from now on.
    lang_routing.remember(1, "ru")
    assert not lang_routing.prefers_cloud(1, "fa")
    # ...and Persian detected for an English-UI user -> Gemini from now on.
    lang_routing.remember(2, "fa")
    assert lang_routing.prefers_cloud(2, "en")
    assert lang_routing.last_language(2) == "fa"


def test_remember_ignores_blanks():
    lang_routing.remember(3, None)
    lang_routing.remember(3, "  ")
    lang_routing.remember(None, "fa")
    assert lang_routing.last_language(3) is None


def test_recent_languages_keep_a_short_newest_first_list():
    assert lang_routing.recent_languages(9, "EN") == ["en"]
    assert lang_routing.recent_languages(9, None) == []
    for code in ["ru", "uk", "ru", "en", "pl"]:
        lang_routing.remember(9, code)
    assert lang_routing.recent_languages(9, "fa") == ["pl", "en", "ru"]
