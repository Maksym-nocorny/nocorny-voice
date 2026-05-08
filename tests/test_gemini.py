"""Tests for pure helpers in gemini_service (no Gemini API calls)."""
from __future__ import annotations

from gemini_service import _is_likely_loop, _split_language_prefix


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


# --- _is_likely_loop -------------------------------------------------------


def test_loop_detected_when_at_max_tokens():
    # 8192 / 8192 = 100% — clearly capped, almost always a runaway.
    assert _is_likely_loop(out_tokens=8192, duration_sec=600, max_tokens=8192)


def test_loop_detected_when_close_to_max_tokens():
    # 7800 / 8192 ≈ 95.2% — also flagged.
    assert _is_likely_loop(out_tokens=7800, duration_sec=600, max_tokens=8192)


def test_loop_detected_when_rate_too_high():
    # 1000 tokens for 50 seconds = 20 tok/s — way above the 8/s threshold.
    assert _is_likely_loop(out_tokens=1000, duration_sec=50, max_tokens=8192)


def test_normal_long_speech_not_flagged():
    # 2000 tokens over 600s = 3.3 tok/s — comfortably normal speech.
    assert not _is_likely_loop(out_tokens=2000, duration_sec=600, max_tokens=8192)


def test_short_normal_speech_not_flagged():
    # 200 tokens over 60s = 3.3 tok/s.
    assert not _is_likely_loop(out_tokens=200, duration_sec=60, max_tokens=8192)


def test_unknown_duration_only_uses_token_cap():
    # Without duration we can only check the cap signal.
    assert not _is_likely_loop(out_tokens=200, duration_sec=None, max_tokens=8192)
    assert _is_likely_loop(out_tokens=8192, duration_sec=None, max_tokens=8192)


def test_short_clip_skips_rate_check():
    # On a 1-2s voice ("Привіт, як справи?") tokens/sec is too noisy — a
    # legitimate short reply easily exceeds the 8 tok/s threshold. Only the
    # max-tokens cap should still apply.
    assert not _is_likely_loop(out_tokens=12, duration_sec=1, max_tokens=8192)
    assert not _is_likely_loop(out_tokens=20, duration_sec=2, max_tokens=8192)
    assert not _is_likely_loop(out_tokens=40, duration_sec=4, max_tokens=8192)
    # Cap signal still fires regardless of duration.
    assert _is_likely_loop(out_tokens=8192, duration_sec=1, max_tokens=8192)
