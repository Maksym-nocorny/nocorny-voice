"""stt_remote in local HTTP mode (bot on the same box as the worker)."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import stt_remote


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(stt_remote, "STT_SERVER_TOKEN", "tok")
    monkeypatch.setattr(stt_remote, "STT_LOCAL_URL", "http://worker.test")
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_BASE_SEC", 5.0)
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_PER_AUDIO_SEC", 0.0)
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_MAX_SEC", 5.0)
    monkeypatch.setattr(stt_remote, "STT_CLOUD_LANGS", frozenset({"fa"}))
    stt_remote._registry.reset()
    stt_remote._local_health.update({"ok": False, "at": -1e9})
    stt_remote._local_client = None
    yield
    stt_remote._local_client = None
    stt_remote._registry.reset()


def _install(monkeypatch, handler):
    client = httpx.AsyncClient(base_url="http://worker.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(stt_remote, "_local_client", client)


@pytest.mark.asyncio
async def test_local_ok_result(monkeypatch, local):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = req.content
        seen["dur"] = req.headers.get("x-duration")
        return httpx.Response(200, json={"status": "ok", "language": "RU", "text": "привіт",
                                         "speech_sec": 3.0, "whisper_s": 0.9})
    _install(monkeypatch, handler)
    res = await stt_remote.transcribe(b"OGG", "audio/ogg", duration_sec=7, lang_hint="ru")
    assert res.status == "ok" and res.text == "привіт" and res.language == "ru"
    assert seen["auth"] == "Bearer tok" and seen["body"] == b"OGG" and seen["dur"] == "7"


@pytest.mark.asyncio
async def test_local_503_and_connection_errors_fall_back(monkeypatch, local):
    _install(monkeypatch, lambda req: httpx.Response(503, json={"error": "power_low"}))
    with pytest.raises(stt_remote.WorkerUnavailable):
        await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)
    assert stt_remote._local_health["ok"] is False

    def boom(req):
        raise httpx.ConnectError("refused")
    _install(monkeypatch, boom)
    with pytest.raises(stt_remote.WorkerUnavailable):
        await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)


@pytest.mark.asyncio
async def test_local_timeout_counts_towards_breaker(monkeypatch, local):
    def slow(req):
        raise httpx.ReadTimeout("slow")
    _install(monkeypatch, slow)
    with pytest.raises(stt_remote.WorkerUnavailable):
        await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)
    assert stt_remote._registry.timeouts_in_a_row == 1


@pytest.mark.asyncio
async def test_local_availability_follows_healthz(monkeypatch, local):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"ok": True})
    _install(monkeypatch, handler)
    assert stt_remote.is_available() is False      # first call only schedules the probe
    await asyncio.sleep(0.05)
    assert stt_remote.is_available() is True
    assert calls == ["/healthz"]

    def down(req):
        return httpx.Response(503, json={"ok": False, "reason": "memory_low"})
    _install(monkeypatch, down)
    stt_remote._local_health["at"] = -1e9
    stt_remote.is_available()
    await asyncio.sleep(0.05)
    assert stt_remote.is_available() is False


@pytest.mark.asyncio
async def test_local_mode_ignores_socket_workers(monkeypatch, local):
    _install(monkeypatch, lambda req: httpx.Response(200, json={"status": "empty", "speech_sec": 0}))
    res = await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)
    assert res.status == "empty"
