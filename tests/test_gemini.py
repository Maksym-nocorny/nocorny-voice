"""Tests for pure helpers in gemini_service (no Gemini API calls)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import gemini_service
from gemini_service import (
    GeminiResult,
    _is_likely_loop,
    _split_language_prefix,
    _transcribe_one,
)


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
    assert _is_likely_loop(out_tokens=8192, duration_sec=600,
                           finish_reason="MAX_TOKENS", max_tokens=8192)


def test_loop_detected_when_close_to_max_tokens():
    # 7800 / 8192 ≈ 95.2% — also flagged.
    assert _is_likely_loop(out_tokens=7800, duration_sec=600,
                           finish_reason="MAX_TOKENS", max_tokens=8192)


def test_loop_detected_when_rate_above_hard_ceiling():
    # 1000 tokens for 50 seconds = 20 tok/s — exactly at the hard ceiling, not flagged
    # (strict `>` 20). Bump to 1100/50 = 22 tok/s to clearly exceed it.
    assert _is_likely_loop(out_tokens=1100, duration_sec=50,
                           finish_reason="STOP", max_tokens=8192)


def test_loop_detected_when_rate_high_and_not_stopped():
    # 800 tokens / 50s = 16 tok/s, not STOP (e.g. SAFETY/RECITATION/OTHER) — flagged.
    assert _is_likely_loop(out_tokens=800, duration_sec=50,
                           finish_reason="OTHER", max_tokens=8192)


def test_cyrillic_speech_with_stop_not_flagged():
    # Real-world false positive: 34s Ukrainian voice, 356 tokens, finish=STOP.
    # 10.5 tok/s is normal for Cyrillic; STOP means model finished cleanly.
    assert not _is_likely_loop(out_tokens=356, duration_sec=34,
                               finish_reason="STOP", max_tokens=8192)
    # Same shape on a 4-min chunk (8.18 tok/s, STOP).
    assert not _is_likely_loop(out_tokens=1964, duration_sec=240,
                               finish_reason="STOP", max_tokens=8192)
    # And an 18 tok/s chunk that still finished cleanly (under hard ceiling).
    assert not _is_likely_loop(out_tokens=4324, duration_sec=240,
                               finish_reason="STOP", max_tokens=8192)


def test_normal_long_speech_not_flagged():
    # 2000 tokens over 600s = 3.3 tok/s — comfortably normal speech.
    assert not _is_likely_loop(out_tokens=2000, duration_sec=600,
                               finish_reason="STOP", max_tokens=8192)


def test_short_normal_speech_not_flagged():
    # 200 tokens over 60s = 3.3 tok/s.
    assert not _is_likely_loop(out_tokens=200, duration_sec=60,
                               finish_reason="STOP", max_tokens=8192)


def test_unknown_duration_only_uses_token_cap():
    # Without duration we can only check the cap signal.
    assert not _is_likely_loop(out_tokens=200, duration_sec=None,
                               finish_reason="STOP", max_tokens=8192)
    assert _is_likely_loop(out_tokens=8192, duration_sec=None,
                           finish_reason="MAX_TOKENS", max_tokens=8192)


def test_short_clip_skips_rate_check():
    # On a 1-2s voice ("Привіт, як справи?") tokens/sec is too noisy — a
    # legitimate short reply easily exceeds the 8 tok/s threshold. Only the
    # max-tokens cap should still apply.
    assert not _is_likely_loop(out_tokens=12, duration_sec=1,
                               finish_reason="STOP", max_tokens=8192)
    assert not _is_likely_loop(out_tokens=20, duration_sec=2,
                               finish_reason="STOP", max_tokens=8192)
    assert not _is_likely_loop(out_tokens=40, duration_sec=4,
                               finish_reason="STOP", max_tokens=8192)
    # Cap signal still fires regardless of duration.
    assert _is_likely_loop(out_tokens=8192, duration_sec=1,
                           finish_reason="MAX_TOKENS", max_tokens=8192)


# --- _transcribe_one: extra retry on transient 5xx after final attempt ---


@pytest.mark.asyncio
async def test_extra_retry_on_5xx_after_final_attempt(monkeypatch):
    """Repro of production failure: a transient 5xx + a loop hits the temp=1.0
    final attempt, which itself flakes with a Gemini InternalServerError 500.
    Before the fix, the 500 propagated to the handler as error_unknown.
    After: one more attempt at the final temperature, which succeeds — user
    gets the transcription instead of a generic error.

    NOTE: the first attempt is intentionally a transient 5xx, not MAX_TOKENS —
    a double MAX_TOKENS pair now short-circuits before temp=1.0
    (see test_no_final_attempt_when_double_max_tokens)."""
    from google.api_core import exceptions

    success = GeminiResult(
        text="finally got it", prompt_tokens=10, candidates_tokens=20, total_tokens=30,
    )
    side_effects = [
        exceptions.ServiceUnavailable("503"),
        gemini_service.TranscriptionDegradedError("loop", finish_reason="MAX_TOKENS"),
        exceptions.InternalServerError("500"),
        success,
    ]
    attempt_spy = AsyncMock(side_effect=side_effects)
    monkeypatch.setattr(gemini_service, "_attempt_transcribe", attempt_spy)
    # Make _get_transcribe_model a no-op so no Gemini API key is required.
    monkeypatch.setattr(gemini_service, "_get_transcribe_model", lambda: object())
    # _transcribe_one reads bytes from disk synchronously — short-circuit it.
    monkeypatch.setattr(gemini_service, "_read_file_bytes", lambda _p: b"")

    result = await _transcribe_one("/tmp/fake.ogg", "audio/ogg", duration_sec=120)
    assert result is success
    assert attempt_spy.await_count == 4


@pytest.mark.asyncio
async def test_no_final_attempt_when_jittered_retry_recitation(monkeypatch):
    """RECITATION is a content-safety block (copyrighted lyrics/quoted text):
    raising temperature can't unblock it, the model keeps reproducing the
    protected sequence. After the jittered retry (temp=0.30) returns
    RECITATION, skip the final temp=1.0 call — it's a guaranteed wasted
    Gemini call and ~10s of user wait. Loop-style degradations (MAX_TOKENS)
    still get the final attempt — they empirically recover."""
    side_effects = [
        gemini_service.TranscriptionDegradedError("blocked", finish_reason="RECITATION"),
        gemini_service.TranscriptionDegradedError("blocked", finish_reason="RECITATION"),
    ]
    attempt_spy = AsyncMock(side_effect=side_effects)
    monkeypatch.setattr(gemini_service, "_attempt_transcribe", attempt_spy)
    monkeypatch.setattr(gemini_service, "_get_transcribe_model", lambda: object())
    monkeypatch.setattr(gemini_service, "_read_file_bytes", lambda _p: b"")

    with pytest.raises(gemini_service.TranscriptionDegradedError) as exc_info:
        await _transcribe_one("/tmp/fake.ogg", "audio/ogg", duration_sec=240)
    assert exc_info.value.finish_reason == "RECITATION"
    assert attempt_spy.await_count == 2  # no temp=1.0 call


@pytest.mark.asyncio
async def test_no_extra_retry_when_final_attempt_degrades(monkeypatch):
    """The extra-retry path is ONLY for transient server 5xx. If the final
    attempt itself raises TranscriptionDegradedError (a content-side loop the
    model can't recover from), we surface it immediately — another attempt
    won't help and just wastes a billed call.

    First two attempts here are transient 5xx (not MAX_TOKENS), so the
    double-MAX_TOKENS short-circuit doesn't fire and we reach temp=1.0."""
    from google.api_core import exceptions

    side_effects = [
        exceptions.ServiceUnavailable("503"),
        exceptions.InternalServerError("500"),
        gemini_service.TranscriptionDegradedError("loop", finish_reason="MAX_TOKENS"),
    ]
    attempt_spy = AsyncMock(side_effect=side_effects)
    monkeypatch.setattr(gemini_service, "_attempt_transcribe", attempt_spy)
    monkeypatch.setattr(gemini_service, "_get_transcribe_model", lambda: object())
    monkeypatch.setattr(gemini_service, "_read_file_bytes", lambda _p: b"")

    with pytest.raises(gemini_service.TranscriptionDegradedError):
        await _transcribe_one("/tmp/fake.ogg", "audio/ogg", duration_sec=120)
    assert attempt_spy.await_count == 3


@pytest.mark.asyncio
async def test_no_final_attempt_when_double_max_tokens(monkeypatch):
    """Two MAX_TOKENS-finish degradations in a row mean the model is truly
    looping on the audio (almost always copyrighted song lyrics). The temp=1.0
    escape almost never recovers from this — production data over 24h showed
    every double-MAX chunk also hit MAX_TOKENS at temp=1.0. Skip the third
    attempt to save a billed Gemini call."""
    side_effects = [
        gemini_service.TranscriptionDegradedError("loop", finish_reason="MAX_TOKENS"),
        gemini_service.TranscriptionDegradedError("loop", finish_reason="MAX_TOKENS"),
    ]
    attempt_spy = AsyncMock(side_effect=side_effects)
    monkeypatch.setattr(gemini_service, "_attempt_transcribe", attempt_spy)
    monkeypatch.setattr(gemini_service, "_get_transcribe_model", lambda: object())
    monkeypatch.setattr(gemini_service, "_read_file_bytes", lambda _p: b"")

    with pytest.raises(gemini_service.TranscriptionDegradedError) as exc_info:
        await _transcribe_one("/tmp/fake.ogg", "audio/ogg", duration_sec=120)
    assert exc_info.value.finish_reason == "MAX_TOKENS"
    assert attempt_spy.await_count == 2  # no temp=1.0 call


@pytest.mark.asyncio
async def test_two_5xx_in_a_row_surface_as_degraded(monkeypatch):
    """When the temp=1.0 attempt 5xxs AND the +1 retry-after-5xx also 5xxs,
    we convert the second 5xx to a TranscriptionDegradedError so the chunked
    aggregator can mark the chunk and the handler can record cost — instead
    of letting the bare Gemini exception escape as chunk_unexpected_error."""
    from google.api_core import exceptions

    side_effects = [
        exceptions.ServiceUnavailable("503"),
        gemini_service.TranscriptionDegradedError("loop", finish_reason="MAX_TOKENS"),
        exceptions.InternalServerError("500"),
        exceptions.InternalServerError("500"),
    ]
    attempt_spy = AsyncMock(side_effect=side_effects)
    monkeypatch.setattr(gemini_service, "_attempt_transcribe", attempt_spy)
    monkeypatch.setattr(gemini_service, "_get_transcribe_model", lambda: object())
    monkeypatch.setattr(gemini_service, "_read_file_bytes", lambda _p: b"")

    with pytest.raises(gemini_service.TranscriptionDegradedError) as exc_info:
        await _transcribe_one("/tmp/fake.ogg", "audio/ogg", duration_sec=120)
    assert exc_info.value.finish_reason == "gemini_5xx"
    assert attempt_spy.await_count == 4


@pytest.mark.asyncio
async def test_chunked_fallback_on_single_shot_degraded(monkeypatch):
    """Repro of 2026-06-14 production failures: a sub-chunking-threshold clip
    (97-190s) hit MAX_TOKENS/RECITATION on all 3 single-shot retries. The
    chunked fallback kicks in, splits the file into smaller pieces, and
    recovers."""
    degrade = gemini_service.TranscriptionDegradedError(
        "loop", finish_reason="MAX_TOKENS",
        prompt_tokens=100, candidates_tokens=8192, total_tokens=8292,
    )
    recovery = GeminiResult(
        text="recovered via chunks", prompt_tokens=50,
        candidates_tokens=200, total_tokens=250,
    )
    single_shot_spy = AsyncMock(side_effect=degrade)
    chunked_spy = AsyncMock(return_value=recovery)
    monkeypatch.setattr(gemini_service, "_transcribe_one", single_shot_spy)
    monkeypatch.setattr(gemini_service, "_transcribe_chunked", chunked_spy)
    monkeypatch.setattr(gemini_service, "_ffmpeg_binary", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(gemini_service, "_configure_once", lambda: None)

    result = await gemini_service.transcribe(
        "/tmp/fake.ogg", "audio/ogg", duration_sec=140,
    )
    assert result is recovery
    assert single_shot_spy.await_count == 1
    assert chunked_spy.await_count == 1
    # Half-duration chunk size, capped & floored by the fallback constants.
    _, kwargs = chunked_spy.call_args
    assert kwargs["chunk_sec"] == 70


@pytest.mark.asyncio
async def test_no_chunked_fallback_for_short_clip(monkeypatch):
    """Below the min-duration threshold, halving produces useless slivers —
    we propagate the original degraded error instead of wasting a Gemini call."""
    degrade = gemini_service.TranscriptionDegradedError(
        "loop", finish_reason="MAX_TOKENS",
    )
    single_shot_spy = AsyncMock(side_effect=degrade)
    chunked_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "_transcribe_one", single_shot_spy)
    monkeypatch.setattr(gemini_service, "_transcribe_chunked", chunked_spy)
    monkeypatch.setattr(gemini_service, "_ffmpeg_binary", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(gemini_service, "_configure_once", lambda: None)

    with pytest.raises(gemini_service.TranscriptionDegradedError):
        await gemini_service.transcribe(
            "/tmp/fake.ogg", "audio/ogg", duration_sec=20,
        )
    chunked_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_chunked_fallback_propagates_aggregated_usage(monkeypatch):
    """When BOTH paths degrade, the surfaced error must carry combined token
    usage from single-shot + chunked attempts — Gemini bills both."""
    single_err = gemini_service.TranscriptionDegradedError(
        "single", finish_reason="MAX_TOKENS",
        prompt_tokens=100, prompt_audio_tokens=64,
        candidates_tokens=8000, total_tokens=8100,
    )
    chunked_err = gemini_service.TranscriptionDegradedError(
        "chunked", finish_reason="RECITATION",
        prompt_tokens=50, prompt_audio_tokens=32,
        candidates_tokens=200, total_tokens=250,
    )
    monkeypatch.setattr(gemini_service, "_transcribe_one", AsyncMock(side_effect=single_err))
    monkeypatch.setattr(gemini_service, "_transcribe_chunked", AsyncMock(side_effect=chunked_err))
    monkeypatch.setattr(gemini_service, "_ffmpeg_binary", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(gemini_service, "_configure_once", lambda: None)

    with pytest.raises(gemini_service.TranscriptionDegradedError) as exc_info:
        await gemini_service.transcribe(
            "/tmp/fake.ogg", "audio/ogg", duration_sec=140,
        )
    final = exc_info.value
    assert final.prompt_tokens == 150
    assert final.prompt_audio_tokens == 96
    assert final.candidates_tokens == 8200
    assert final.total_tokens == 8350
    assert final.finish_reason == "MAX_TOKENS"  # original surfaces


def test_finish_reason_defaults_to_none():
    # Backwards-compatible default: missing finish_reason behaves like "not STOP",
    # so the soft rate signal still fires (preserves prior behavior on callers
    # that haven't been updated).
    # 1100/50 = 22 tok/s — above hard ceiling, flagged regardless of finish_reason.
    assert _is_likely_loop(out_tokens=1100, duration_sec=50, max_tokens=8192)
    # 800/50 = 16 tok/s with no finish_reason → soft rule fires (default != STOP).
    assert _is_likely_loop(out_tokens=800, duration_sec=50, max_tokens=8192)
    # 200/50 = 4 tok/s — well below soft threshold even with no finish_reason.
    assert not _is_likely_loop(out_tokens=200, duration_sec=50, max_tokens=8192)
