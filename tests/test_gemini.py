"""Tests for pure helpers in gemini_service (no Gemini API calls)."""
from __future__ import annotations

from gemini_service import _split_language_prefix


def test_strips_lang_marker_and_returns_code():
    text, lang = _split_language_prefix("LANG:uk\nПривіт, як справи?")
    assert text == "Привіт, як справи?"
    assert lang == "uk"


def test_handles_regional_codes():
    text, lang = _split_language_prefix("LANG:pt-br\nOlá mundo")
    assert text == "Olá mundo"
    assert lang == "pt-br"


def test_lowercases_uppercase_codes():
    text, lang = _split_language_prefix("LANG:EN\nhi")
    assert text == "hi"
    assert lang == "en"


def test_returns_text_unchanged_when_marker_missing():
    text, lang = _split_language_prefix("Just a transcription with no marker")
    assert text == "Just a transcription with no marker"
    assert lang is None


def test_drops_obviously_invalid_marker():
    # Code too short / contains forbidden characters → treat as no detection.
    text, lang = _split_language_prefix("LANG:!!\nhello")
    assert text == "hello"
    assert lang is None


def test_handles_marker_only_response():
    # Pathological case: Gemini returned only the marker with no body.
    text, lang = _split_language_prefix("LANG:fr")
    assert text == ""
    assert lang == "fr"


def test_strips_leading_whitespace_after_marker():
    text, lang = _split_language_prefix("LANG:de\n   Hallo")
    assert text == "Hallo"
    assert lang == "de"
