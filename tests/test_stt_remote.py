"""stt_remote: worker WebSocket protocol, auth, timeouts, disconnect handling."""
from __future__ import annotations

import asyncio
import json
import struct

import pytest
import tornado.web
import tornado.websocket
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets

import stt_remote

TOKEN = "unit-test-token"


@pytest.fixture(autouse=True)
def _patch_token(monkeypatch):
    monkeypatch.setattr(stt_remote, "STT_SERVER_TOKEN", TOKEN)
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_BASE_SEC", 0.5)
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_PER_AUDIO_SEC", 0.0)
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_MAX_SEC", 1.0)
    monkeypatch.setattr(stt_remote, "STT_TRIP_AFTER_TIMEOUTS", 2)
    monkeypatch.setattr(stt_remote, "STT_TRIP_COOLDOWN_SEC", 60.0)
    monkeypatch.setattr(stt_remote, "STT_CLOUD_LANGS", frozenset({"fa"}))
    stt_remote._registry.reset()
    yield
    stt_remote._registry.reset()


class _Server:
    """Tornado app with only the worker route, on a random localhost port."""

    def __init__(self):
        self.app = tornado.web.Application([stt_remote.tornado_handler_spec(TOKEN)],
                                           websocket_ping_interval=0)
        self.server = HTTPServer(self.app)
        socks = bind_sockets(0, "127.0.0.1")
        self.port = socks[0].getsockname()[1]
        self.server.add_sockets(socks)

    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}{stt_remote.WORKER_PATH}"

    def stop(self):
        self.server.stop()


async def _connect(server: _Server, token: str = TOKEN):
    from tornado.httpclient import HTTPRequest
    req = HTTPRequest(server.url(), headers={"Authorization": f"Bearer {token}"})
    return await tornado.websocket.websocket_connect(req)


def _unpack(frame: bytes):
    n = struct.unpack("!I", frame[:4])[0]
    return json.loads(frame[4:4 + n]), frame[4 + n:]


@pytest.mark.asyncio
async def test_rejects_bad_token():
    srv = _Server()
    try:
        with pytest.raises(tornado.httpclient.HTTPClientError) as ei:
            await _connect(srv, token="wrong")
        assert ei.value.code == 401
        assert not stt_remote.is_available()
    finally:
        srv.stop()


@pytest.mark.asyncio
async def test_job_roundtrip_ok_result():
    srv = _Server()
    try:
        ws = await _connect(srv)
        await ws.write_message(json.dumps({"type": "hello", "parallel": 3, "model": "m"}))
        for _ in range(20):
            if stt_remote.is_available():
                break
            await asyncio.sleep(0.01)
        assert stt_remote.is_available() and stt_remote.worker_count() == 1

        async def worker_side():
            frame = await ws.read_message()
            header, audio = _unpack(frame)
            assert audio == b"OGGDATA" and header["duration"] == 7 and header["lang_hint"] == "ru"
            await ws.write_message(json.dumps({
                "type": "result", "id": header["id"], "status": "ok",
                "language": "RU", "text": "привіт", "speech_sec": 2.5, "whisper_s": 0.4,
            }))

        wtask = asyncio.create_task(worker_side())
        res = await stt_remote.transcribe(b"OGGDATA", "audio/ogg", duration_sec=7, lang_hint="ru")
        await wtask
        assert res.status == "ok" and res.text == "привіт" and res.language == "ru"
        assert res.speech_sec == 2.5
        ws.close()
    finally:
        srv.stop()


@pytest.mark.asyncio
async def test_empty_and_cloud_statuses_pass_through():
    srv = _Server()
    try:
        ws = await _connect(srv)
        for _ in range(20):
            if stt_remote.is_available():
                break
            await asyncio.sleep(0.01)

        async def worker_side(status, **extra):
            header, _ = _unpack(await ws.read_message())
            await ws.write_message(json.dumps({"type": "result", "id": header["id"],
                                               "status": status, **extra}))

        t = asyncio.create_task(worker_side("empty", speech_sec=0.0))
        res = await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)
        await t
        assert res.status == "empty" and res.text == ""

        t = asyncio.create_task(worker_side("cloud", language="fa"))
        res = await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)
        await t
        assert res.status == "cloud" and res.language == "fa"
        ws.close()
    finally:
        srv.stop()


@pytest.mark.asyncio
async def test_worker_error_and_ok_without_text_raise():
    srv = _Server()
    try:
        ws = await _connect(srv)
        for _ in range(20):
            if stt_remote.is_available():
                break
            await asyncio.sleep(0.01)

        async def worker_side(payload):
            header, _ = _unpack(await ws.read_message())
            await ws.write_message(json.dumps({"type": "result", "id": header["id"], **payload}))

        t = asyncio.create_task(worker_side({"status": "error", "error": "busy"}))
        with pytest.raises(stt_remote.WorkerUnavailable):
            await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)
        await t

        t = asyncio.create_task(worker_side({"status": "ok", "text": "   "}))
        with pytest.raises(stt_remote.WorkerUnavailable):
            await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)
        await t
        ws.close()
    finally:
        srv.stop()


@pytest.mark.asyncio
async def test_timeout_sends_cancel_and_raises():
    srv = _Server()
    try:
        ws = await _connect(srv)
        for _ in range(20):
            if stt_remote.is_available():
                break
            await asyncio.sleep(0.01)
        with pytest.raises(stt_remote.WorkerUnavailable):
            await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=0)
        await ws.read_message()  # the job frame
        cancel = json.loads(await ws.read_message())
        assert cancel["type"] == "cancel" and cancel["id"]
        # nothing left pending on our side
        assert stt_remote._registry._pending == {}
        ws.close()
    finally:
        srv.stop()


@pytest.mark.asyncio
async def test_worker_disconnect_releases_waiters_immediately(monkeypatch):
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_BASE_SEC", 30.0)
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_MAX_SEC", 30.0)
    srv = _Server()
    try:
        ws = await _connect(srv)
        for _ in range(20):
            if stt_remote.is_available():
                break
            await asyncio.sleep(0.01)
        job = asyncio.create_task(stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3))
        await ws.read_message()
        ws.close()
        with pytest.raises(stt_remote.WorkerUnavailable):
            await asyncio.wait_for(job, timeout=5)
        for _ in range(50):
            if not stt_remote.is_available():
                break
            await asyncio.sleep(0.01)
        assert not stt_remote.is_available()
    finally:
        srv.stop()


@pytest.mark.asyncio
async def test_no_worker_raises_at_once():
    with pytest.raises(stt_remote.WorkerUnavailable):
        await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)


def test_timeout_budget_is_capped(monkeypatch):
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_BASE_SEC", 25.0)
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_PER_AUDIO_SEC", 0.6)
    monkeypatch.setattr(stt_remote, "STT_TIMEOUT_MAX_SEC", 480.0)
    assert stt_remote.timeout_for(None) == 25.0
    assert stt_remote.timeout_for(100) == 85.0
    assert stt_remote.timeout_for(1800) == 480.0


@pytest.mark.asyncio
async def test_two_timeouts_trip_the_breaker_until_a_success():
    srv = _Server()
    try:
        ws = await _connect(srv)
        for _ in range(20):
            if stt_remote.is_available():
                break
            await asyncio.sleep(0.01)
        for _ in range(2):
            with pytest.raises(stt_remote.WorkerUnavailable):
                await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=0)
        assert stt_remote._registry.tripped() and not stt_remote.is_available()
        with pytest.raises(stt_remote.WorkerUnavailable):
            await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=0)
        # drain the stale job/cancel frames the timed-out calls left behind
        while True:
            try:
                await asyncio.wait_for(ws.read_message(), 0.2)
            except asyncio.TimeoutError:
                break
        # cooldown over -> a good answer resets the counter
        stt_remote._registry.skip_until = 0.0
        assert stt_remote.is_available()

        async def worker_side():
            header, _ = _unpack(await ws.read_message())
            await ws.write_message(json.dumps({"type": "result", "id": header["id"],
                                               "status": "ok", "text": "ok"}))
        t = asyncio.create_task(worker_side())
        res = await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=0)
        await t
        assert res.status == "ok" and stt_remote._registry.timeouts_in_a_row == 0
        ws.close()
    finally:
        srv.stop()


@pytest.mark.asyncio
async def test_ok_in_cloud_language_is_downgraded_to_cloud():
    srv = _Server()
    try:
        ws = await _connect(srv)
        for _ in range(20):
            if stt_remote.is_available():
                break
            await asyncio.sleep(0.01)

        async def worker_side():
            header, _ = _unpack(await ws.read_message())
            await ws.write_message(json.dumps({"type": "result", "id": header["id"],
                                               "status": "ok", "language": "fa-IR",
                                               "text": "متن"}))
        t = asyncio.create_task(worker_side())
        res = await stt_remote.transcribe(b"x", "audio/ogg", duration_sec=3)
        await t
        assert res.status == "cloud" and res.language == "fa-ir" and res.text == ""
        ws.close()
    finally:
        srv.stop()
