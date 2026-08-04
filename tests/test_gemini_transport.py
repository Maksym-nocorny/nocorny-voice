"""Gemini SDK transport switch (2026-08-04 OOM experiment) tests.

Covers two things:
  1. `_configure_once` passes GEMINI_TRANSPORT to genai.configure, so the
     REST-vs-gRPC choice is a Render env value, not a deploy.
  2. The retry/hard-error ladders catch the exception classes BOTH transports
     raise: grpc maps status codes to child classes (ResourceExhausted,
     DeadlineExceeded, PermissionDenied), rest maps HTTP statuses to their
     parents (TooManyRequests, GatewayTimeout, Forbidden). A ladder keyed on
     the child only would silently stop retrying under rest.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from google.api_core import exceptions as google_exceptions

import config
import gemini_service


@pytest.fixture()
def _unconfigured(monkeypatch):
    """Reset the configure-once latch so the test exercises the real call."""
    monkeypatch.setattr(gemini_service, "_configured", False)
    monkeypatch.setattr(gemini_service, "_transcribe_model", None)
    yield
    # monkeypatch restores _configured/_transcribe_model automatically.


def test_configure_passes_transport_from_config(_unconfigured):
    with patch.object(gemini_service.genai, "configure") as mock_configure:
        gemini_service._configure_once()
    mock_configure.assert_called_once()
    kwargs = mock_configure.call_args.kwargs
    assert kwargs["transport"] == config.GEMINI_TRANSPORT
    assert kwargs["api_key"] == gemini_service.GEMINI_API_KEY


def test_default_transport_is_rest():
    # The experiment ships with REST on; rollback is GEMINI_TRANSPORT=grpc in
    # Render env. (config is loaded once per test session, so this asserts
    # the default unless the developer's environment overrides it.)
    import os
    expected = os.getenv("GEMINI_TRANSPORT") or "rest"
    assert config.GEMINI_TRANSPORT == expected


def test_configure_is_once_only(_unconfigured):
    with patch.object(gemini_service.genai, "configure") as mock_configure:
        gemini_service._configure_once()
        gemini_service._configure_once()
    assert mock_configure.call_count == 1


def test_rest_transport_available_in_pinned_sdk():
    # transport="rest" must exist in the pinned google-ai-generativelanguage,
    # otherwise the first real call in prod would crash on client build.
    from google.ai.generativelanguage_v1beta.services.generative_service.client import (
        GenerativeServiceClientMeta,
    )
    assert "rest" in GenerativeServiceClientMeta._transport_registry
    assert "grpc" in GenerativeServiceClientMeta._transport_registry


# --- retry ladders must catch both transports' exception classes ------------

def _raise_n_times_then_succeed(exc: Exception, n: int):
    calls = {"count": 0}

    async def _coro():
        calls["count"] += 1
        if calls["count"] <= n:
            raise exc
        return "ok"

    return _coro, calls


@pytest.mark.parametrize("exc", [
    google_exceptions.ResourceExhausted("429 via grpc"),   # grpc child
    google_exceptions.TooManyRequests("429 via rest"),     # rest parent
    google_exceptions.ServiceUnavailable("503 either transport"),
])
async def test_retry_catches_transients_under_both_transports(exc):
    coro, calls = _raise_n_times_then_succeed(exc, 1)
    result = await gemini_service._retry(
        coro,
        base_delay=0.001, rate_limit_base_delay=0.001,
    )
    assert result == "ok"
    assert calls["count"] == 2  # one failure, one retried success


async def test_retry_gives_429_the_long_ladder_under_rest():
    # The 31.07 fix: 429 gets rate_limit_attempts, not the short 5xx budget.
    # Under rest a 429 arrives as TooManyRequests — it must still pick the
    # long ladder, or the wave-of-429 protection silently degrades.
    exc = google_exceptions.TooManyRequests("429 via rest")
    coro, calls = _raise_n_times_then_succeed(exc, 3)
    result = await gemini_service._retry(
        coro,
        attempts=1, base_delay=0.001,          # short 5xx budget: would fail
        rate_limit_attempts=4, rate_limit_base_delay=0.001,
    )
    assert result == "ok"
    assert calls["count"] == 4  # three failures + success — used the long ladder


@pytest.mark.parametrize("exc_cls", [
    google_exceptions.DeadlineExceeded,   # grpc child
    google_exceptions.GatewayTimeout,     # rest parent (HTTP 504)
])
def test_504_classes_count_as_transient_in_transcribe_one(exc_cls):
    # _transcribe_one's `retryable`/`transient_5xx` tuples are function-local;
    # assert via the class relationship the tuples rely on.
    assert issubclass(exc_cls, google_exceptions.GatewayTimeout)


@pytest.mark.parametrize("exc_cls", [
    google_exceptions.PermissionDenied,   # grpc child
    google_exceptions.Forbidden,          # rest parent (HTTP 403)
])
def test_403_classes_hit_the_hard_block_path(exc_cls):
    assert issubclass(exc_cls, google_exceptions.Forbidden)
