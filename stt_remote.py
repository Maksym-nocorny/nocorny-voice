"""Self-hosted Whisper worker over WebSocket — the worker dials US.

Why this direction: the worker runs on private hardware with no public
address. An OUTBOUND WebSocket to this already-public service needs nothing
exposed on the worker side. The worker connects to
`wss://<WEBHOOK_URL>/stt/worker` with `Authorization: Bearer STT_SERVER_TOKEN`,
says hello, and from then on this module pushes jobs down the socket and
awaits results.

Wire format (one frame per message so concurrent sends can't interleave):
  us -> worker   binary: 4-byte big-endian header length + JSON header
                 {"id","duration","lang_hint","mime"} + raw audio bytes
                 text:   {"type":"cancel","id"}   (we stopped waiting)
  worker -> us   text:   {"type":"hello",parallel,model,cloud_langs,vad}
                         {"type":"result","id",status:"ok"|"empty"|"cloud"|"error",
                          language,text,speech_sec,whisper_s,elapsed_s,error}

`transcribe()` raises WorkerUnavailable the moment there is no worker, on
timeout, on socket loss or on a worker-side error — the handler then falls
back to Gemini, which is the exact pre-worker behaviour.

Local mode (STT_LOCAL_URL set): the bot runs on the same box as the worker,
so instead of waiting for a socket it POSTs the audio to the worker's local
HTTP side (`/v1/transcribe`, same bearer token) and reads `/healthz` for
availability. Same statuses, same fallback.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Set

import httpx
import tornado.web
import tornado.websocket

from config import (
    STT_CLOUD_LANGS,
    STT_LOCAL_URL,
    STT_SERVER_TOKEN,
    STT_TIMEOUT_BASE_SEC,
    STT_TIMEOUT_MAX_SEC,
    STT_TIMEOUT_PER_AUDIO_SEC,
    STT_TRIP_AFTER_TIMEOUTS,
    STT_TRIP_COOLDOWN_SEC,
)

logger = logging.getLogger(__name__)

WORKER_PATH = "/stt/worker"


class WorkerUnavailable(Exception):
    """No worker, or the worker could not deliver a result in time."""


@dataclass
class RemoteResult:
    status: str                       # "ok" | "empty" | "cloud"
    language: Optional[str] = None
    text: str = ""
    speech_sec: float = 0.0
    whisper_sec: float = 0.0
    elapsed_sec: float = 0.0


class WorkerSocket(tornado.websocket.WebSocketHandler):
    """One connected worker. Registered in the tornado app by bot.py."""

    def initialize(self, token: str) -> None:
        self._token = token
        self.info: dict = {}
        self.outstanding: Set[str] = set()
        self.connected_at = 0.0

    def check_origin(self, origin: str) -> bool:  # not a browser client
        return True

    async def prepare(self) -> None:
        auth = self.request.headers.get("Authorization", "")
        expected = ("Bearer " + self._token).encode("utf-8")
        if not self._token or not hmac.compare_digest(auth.encode("utf-8", "replace"), expected):
            logger.warning("stt_worker_rejected ip=%s", self.request.remote_ip)
            raise tornado.web.HTTPError(401)

    def open(self, *args, **kwargs) -> None:
        self.connected_at = time.monotonic()
        _registry.add(self)
        logger.info("stt_worker_connected ip=%s workers=%d",
                    self.request.remote_ip, len(_registry.workers))

    def on_message(self, message) -> None:
        if isinstance(message, bytes):
            return
        try:
            msg = json.loads(message)
        except (TypeError, ValueError):
            return
        kind = msg.get("type")
        if kind == "hello":
            self.info = {k: msg.get(k) for k in ("parallel", "model", "cloud_langs", "vad")}
            logger.info("stt_worker_hello %s", self.info)
        elif kind == "result":
            _registry.deliver(str(msg.get("id")), msg)
            self.outstanding.discard(str(msg.get("id")))

    def on_close(self) -> None:
        _registry.remove(self)
        logger.info("stt_worker_disconnected code=%s reason=%s outstanding=%d workers=%d",
                    self.close_code, self.close_reason, len(self.outstanding),
                    len(_registry.workers))


class _Registry:
    def __init__(self) -> None:
        self.workers: Set[WorkerSocket] = set()
        self._pending: Dict[str, asyncio.Future] = {}
        # Circuit breaker: a worker whose socket is alive but whose decoder is
        # stuck would otherwise cost every request the full wait budget before
        # Gemini takes over, and hold a global slot the whole time. After
        # STT_TRIP_AFTER_TIMEOUTS timeouts in a row the worker is skipped for
        # STT_TRIP_COOLDOWN_SEC; the first successful result resets it.
        self.timeouts_in_a_row = 0
        self.skip_until = 0.0

    def tripped(self) -> bool:
        return time.monotonic() < self.skip_until

    def note_timeout(self) -> None:
        self.timeouts_in_a_row += 1
        if self.timeouts_in_a_row >= STT_TRIP_AFTER_TIMEOUTS:
            self.skip_until = time.monotonic() + STT_TRIP_COOLDOWN_SEC
            logger.warning("stt_remote_tripped timeouts=%d cooldown=%.0fs",
                           self.timeouts_in_a_row, STT_TRIP_COOLDOWN_SEC)

    def note_success(self) -> None:
        self.timeouts_in_a_row = 0
        self.skip_until = 0.0

    # -- workers ----------------------------------------------------------
    def add(self, ws: WorkerSocket) -> None:
        self.workers.add(ws)

    def remove(self, ws: WorkerSocket) -> None:
        self.workers.discard(ws)
        # Everything that worker was doing is lost — release the waiters now
        # instead of letting them sit out the full timeout.
        for rid in list(ws.outstanding):
            fut = self._pending.get(rid)
            if fut is not None and not fut.done():
                fut.set_exception(WorkerUnavailable("worker disconnected"))
        ws.outstanding.clear()

    def pick(self) -> Optional[WorkerSocket]:
        if not self.workers:
            return None
        return min(self.workers, key=lambda w: len(w.outstanding))

    # -- results ----------------------------------------------------------
    def deliver(self, rid: str, msg: dict) -> None:
        fut = self._pending.get(rid)
        if fut is not None and not fut.done():
            fut.set_result(msg)

    def register(self, rid: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        return fut

    def forget(self, rid: str) -> None:
        self._pending.pop(rid, None)

    def reset(self) -> None:  # tests
        self.workers.clear()
        self._pending.clear()
        self.timeouts_in_a_row = 0
        self.skip_until = 0.0


_registry = _Registry()


def is_available() -> bool:
    if not STT_SERVER_TOKEN or _registry.tripped():
        return False
    if STT_LOCAL_URL:
        return _local_healthy()
    return bool(_registry.workers)


# ----------------------------------------------------------------- local HTTP mode

_LOCAL_HEALTH_TTL_SEC = 15.0
_local_health: dict = {"ok": False, "at": -1e9}
_local_client: Optional[httpx.AsyncClient] = None
_probe_tasks: Set[asyncio.Task] = set()
_ASCII_LANG = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")


async def prime() -> None:
    """One synchronous worker probe at start-up (local mode only)."""
    if STT_LOCAL_URL and STT_SERVER_TOKEN:
        _local_health["at"] = time.monotonic()
        await _refresh_local_health()


async def close() -> None:
    global _local_client
    for t in list(_probe_tasks):
        t.cancel()
    if _local_client is not None:
        await _local_client.aclose()
        _local_client = None


def _ascii_hint(lang_hint: Optional[str]) -> str:
    """HTTP headers are latin-1; anything outside plain language codes is dropped."""
    h = (lang_hint or "").strip().lower()
    return h if _ASCII_LANG.match(h) else ""


def _client() -> httpx.AsyncClient:
    global _local_client
    if _local_client is None:
        _local_client = httpx.AsyncClient(base_url=STT_LOCAL_URL, timeout=httpx.Timeout(10.0))
    return _local_client


def _local_healthy() -> bool:
    """Cached verdict from the last /healthz probe (refreshed in the background)."""
    now = time.monotonic()
    if now - _local_health["at"] > _LOCAL_HEALTH_TTL_SEC:
        _local_health["at"] = now
        try:
            t = asyncio.get_running_loop().create_task(_refresh_local_health())
            _probe_tasks.add(t)
            t.add_done_callback(_probe_tasks.discard)
        except RuntimeError:
            pass
    return bool(_local_health["ok"])


async def _refresh_local_health() -> None:
    ok = False
    try:
        r = await _client().get("/healthz")
        ok = r.status_code == 200 and bool(r.json().get("ok"))
        if not ok:
            logger.info("stt_local_unhealthy status=%s body=%s", r.status_code, r.text[:120])
    except Exception as e:  # noqa: BLE001
        logger.info("stt_local_health_failed exc=%s", e)
    was = _local_health["ok"]
    _local_health["ok"] = ok
    if was != ok:
        logger.warning("stt_local_available=%s url=%s", ok, STT_LOCAL_URL)


async def _transcribe_local(audio: bytes, mime_type: str, *,
                            duration_sec: Optional[int], lang_hint: Optional[str],
                            rid: str) -> dict:
    headers = {"Authorization": "Bearer " + STT_SERVER_TOKEN, "Content-Type": mime_type,
               "X-Request-Id": rid, "X-Duration": str(duration_sec or ""),
               "X-Lang-Hint": _ascii_hint(lang_hint)}
    try:
        r = await _client().post("/v1/transcribe", content=audio, headers=headers,
                                 timeout=httpx.Timeout(timeout_for(duration_sec), connect=5.0))
    except httpx.TimeoutException as e:
        _registry.note_timeout()
        logger.warning("stt_local_timeout rid=%s duration=%s budget=%.0fs",
                       rid, duration_sec, timeout_for(duration_sec))
        raise WorkerUnavailable("local worker timeout") from e
    except httpx.HTTPError as e:
        _local_health["ok"] = False
        raise WorkerUnavailable(f"local worker unreachable: {e}") from e
    if r.status_code != 200:
        if r.status_code in (503, 504):
            _local_health["ok"] = False
        raise WorkerUnavailable(f"local worker http {r.status_code}: {r.text[:120]}")
    try:
        return r.json()
    except ValueError as e:
        raise WorkerUnavailable("local worker bad json") from e


def worker_count() -> int:
    return len(_registry.workers)


def timeout_for(duration_sec: Optional[int]) -> float:
    d = float(duration_sec or 0)
    return min(STT_TIMEOUT_MAX_SEC, STT_TIMEOUT_BASE_SEC + STT_TIMEOUT_PER_AUDIO_SEC * d)


def _frame(header: dict, audio: bytes) -> bytes:
    h = json.dumps(header, ensure_ascii=False).encode("utf-8")
    return struct.pack("!I", len(h)) + h + audio


async def transcribe(audio: bytes, mime_type: str, *,
                     duration_sec: Optional[int] = None,
                     lang_hint: Optional[str] = None) -> RemoteResult:
    """Send one audio blob to the worker (local HTTP or connected socket) and wait."""
    rid = uuid.uuid4().hex
    t0 = time.monotonic()
    if STT_LOCAL_URL:
        msg = await _transcribe_local(audio, mime_type, duration_sec=duration_sec,
                                      lang_hint=lang_hint, rid=rid)
        return _result_from(msg, rid, duration_sec, time.monotonic() - t0)
    ws = _registry.pick()
    if ws is None:
        raise WorkerUnavailable("no worker connected")
    fut = _registry.register(rid)
    ws.outstanding.add(rid)
    try:
        try:
            await ws.write_message(
                _frame({"id": rid, "duration": duration_sec, "lang_hint": lang_hint,
                        "mime": mime_type}, audio),
                binary=True,
            )
        except tornado.websocket.WebSocketClosedError as e:
            raise WorkerUnavailable("worker socket closed on send") from e
        try:
            msg = await asyncio.wait_for(fut, timeout=timeout_for(duration_sec))
        except asyncio.TimeoutError as e:
            try:
                await ws.write_message(json.dumps({"type": "cancel", "id": rid}))
            except tornado.websocket.WebSocketClosedError:
                pass
            _registry.note_timeout()
            logger.warning("stt_remote_timeout rid=%s duration=%s budget=%.0fs",
                           rid, duration_sec, timeout_for(duration_sec))
            raise WorkerUnavailable("worker timeout") from e
    finally:
        _registry.forget(rid)
        ws.outstanding.discard(rid)
        # A future failed by remove() after we already left (send error) would
        # log "exception was never retrieved" — consume it.
        if fut.done() and not fut.cancelled():
            fut.exception()
        elif not fut.done():
            fut.cancel()
    return _result_from(msg, rid, duration_sec, time.monotonic() - t0)


def _result_from(msg: dict, rid: str, duration_sec: Optional[int], elapsed: float) -> RemoteResult:
    status = str(msg.get("status") or "error")
    if status == "error":
        logger.warning("stt_remote_error rid=%s error=%s", rid, msg.get("error"))
        raise WorkerUnavailable(f"worker error: {msg.get('error')}")
    if status not in ("ok", "empty", "cloud"):
        raise WorkerUnavailable(f"worker bad status: {status}")
    _registry.note_success()
    language = str(msg.get("language")).lower() if msg.get("language") else None
    if status == "ok" and language and (
            language in STT_CLOUD_LANGS or language.split("-", 1)[0] in STT_CLOUD_LANGS):
        # The worker's cloud list drifted from ours: never hand out Whisper
        # text for a language we route to the cloud.
        status = "cloud"
    res = RemoteResult(
        status=status,
        language=language,
        text=str(msg.get("text") or "") if status == "ok" else "",
        speech_sec=float(msg.get("speech_sec") or 0.0),
        whisper_sec=float(msg.get("whisper_s") or 0.0),
        elapsed_sec=round(elapsed, 2),
    )
    logger.info("stt_remote_result rid=%s status=%s lang=%s speech=%.1f whisper=%.2fs "
                "elapsed=%.2fs duration=%s chars=%d", rid, res.status, res.language,
                res.speech_sec, res.whisper_sec, res.elapsed_sec, duration_sec,
                len(res.text))
    if status == "ok" and not res.text.strip():
        raise WorkerUnavailable("worker returned ok without text")
    return res


def tornado_handler_spec(token: str):
    """(path, handler, kwargs) for tornado Application.add_handlers."""
    return (WORKER_PATH, WorkerSocket, {"token": token})
