"""Keep-alive self-ping tests — task lifecycle only, no real network."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from utils import keep_alive


class _FakeClient:
    """Stand-in for httpx.AsyncClient: records GETs, usable as async CM."""

    last_url: str | None = None
    pinged: asyncio.Event | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        _FakeClient.last_url = url
        if _FakeClient.pinged is not None:
            _FakeClient.pinged.set()
        return type("Resp", (), {"status_code": 200})()


@pytest.fixture(autouse=True)
async def _reset():
    _FakeClient.last_url = None
    _FakeClient.pinged = None
    yield
    await keep_alive.stop()


async def test_pings_own_url_after_interval():
    # Create the Event inside the test so it binds to the running loop.
    _FakeClient.pinged = asyncio.Event()
    with patch("utils.keep_alive.httpx.AsyncClient", _FakeClient):
        keep_alive.start("https://nocorny-voice-oregon.onrender.com/", interval_sec=0.01)
        await asyncio.wait_for(_FakeClient.pinged.wait(), timeout=2.0)
    assert _FakeClient.last_url == "https://nocorny-voice-oregon.onrender.com/"


async def test_start_is_idempotent():
    with patch("utils.keep_alive.httpx.AsyncClient", _FakeClient):
        keep_alive.start("https://example.test/", interval_sec=999)
        first = keep_alive._task
        keep_alive.start("https://example.test/", interval_sec=999)
        assert keep_alive._task is first  # second start() is a no-op


async def test_stop_is_safe_when_not_running():
    # No task started — stop() must not raise.
    await keep_alive.stop()
