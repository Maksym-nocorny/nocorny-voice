"""Write path: bounded queue + background drainer that batches inserts.

Design contract:
- track() never awaits, never raises. Drop on overflow.
- Drainer batches up to ANALYTICS_BATCH_SIZE rows per round-trip.
- Drainer also runs the daily retention sweep and a periodic SELECT 1 heartbeat
  (so Neon's 5-min auto-suspend doesn't make the next /stats pay a cold start).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from telegram import Chat, User

from utils.logging_setup import get_request_id

from . import pool

logger = logging.getLogger(__name__)

# Two-statement upsert + insert. Using a single CTE keeps it to one round-trip per row.
_TRACK_SQL = """
WITH u AS (
    INSERT INTO nocorny_voice.users
        (user_id, username, first_name, last_name, language_code,
         first_seen_at, last_seen_at, total_events)
    VALUES ($1, $2, $3, $4, $5, now(), now(), 1)
    ON CONFLICT (user_id) DO UPDATE SET
        username      = EXCLUDED.username,
        first_name    = EXCLUDED.first_name,
        last_name     = EXCLUDED.last_name,
        language_code = COALESCE(EXCLUDED.language_code, nocorny_voice.users.language_code),
        last_seen_at  = now(),
        total_events  = nocorny_voice.users.total_events + 1
    RETURNING user_id
)
INSERT INTO nocorny_voice.events
    (ts, user_id, chat_id, chat_type, request_id, event_type,
     media_type, duration_sec, file_size_bytes, mime_type,
     prompt_tokens, candidates_tokens, total_tokens, latency_ms, error_class)
VALUES (now(), $1, $6, $7, $8, $9,
        $10, $11, $12, $13,
        $14, $15, $16, $17, $18)
"""

_RETENTION_SQL = (
    "DELETE FROM nocorny_voice.events WHERE ts < now() - ($1::int || ' days')::interval"
)


@dataclass
class _Event:
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    language_code: Optional[str]
    chat_id: int
    chat_type: str
    request_id: str
    event_type: str
    media_type: Optional[str] = None
    duration_sec: Optional[int] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    prompt_tokens: Optional[int] = None
    candidates_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    error_class: Optional[str] = None

    def as_args(self) -> tuple:
        return (
            self.user_id, self.username, self.first_name, self.last_name, self.language_code,
            self.chat_id, self.chat_type, self.request_id, self.event_type,
            self.media_type, self.duration_sec, self.file_size_bytes, self.mime_type,
            self.prompt_tokens, self.candidates_tokens, self.total_tokens,
            self.latency_ms, self.error_class,
        )


@dataclass
class _State:
    queue: Optional[asyncio.Queue] = None
    drainer_task: Optional[asyncio.Task] = None
    queue_size: int = 500
    batch_size: int = 100
    flush_interval_sec: float = 2.0
    heartbeat_interval_sec: int = 240
    retention_days: int = 90
    last_full_log_ts: float = 0.0
    last_retention_ts: float = field(default_factory=lambda: 0.0)
    last_heartbeat_ts: float = field(default_factory=lambda: 0.0)


_state = _State()


def configure(*, queue_size: int, batch_size: int, flush_interval_sec: float,
              heartbeat_interval_sec: int, retention_days: int) -> None:
    _state.queue_size = queue_size
    _state.batch_size = batch_size
    _state.flush_interval_sec = flush_interval_sec
    _state.heartbeat_interval_sec = heartbeat_interval_sec
    _state.retention_days = retention_days


def start() -> None:
    """Initialise the queue and start the drainer task. Must be called from inside
    a running event loop (i.e. from an async function or via post_init)."""
    if _state.drainer_task is not None and not _state.drainer_task.done():
        return
    _state.queue = asyncio.Queue(maxsize=_state.queue_size)
    _state.drainer_task = asyncio.create_task(_drainer_loop(), name="analytics_drainer")


async def stop() -> None:
    """Cancel the drainer and try to flush whatever is left."""
    task = _state.drainer_task
    _state.drainer_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — best effort
        pass
    # Final flush attempt
    if _state.queue is not None and not _state.queue.empty() and pool.get() is not None:
        try:
            batch = _drain_batch()
            if batch:
                await _flush(batch)
        except Exception:  # noqa: BLE001
            logger.warning("analytics_final_flush_failed", exc_info=True)
    _state.queue = None


def track(event_type: str, *, user: Optional[User], chat: Optional[Chat],
          info: Any = None, result: Any = None,
          latency_ms: Optional[int] = None,
          error_class: Optional[str] = None) -> None:
    """Enqueue an event. Synchronous; never raises.

    `info` accepts a `_MediaInfo`-like object with duration/file_size/mime_type/media_type.
    `result` accepts a `GeminiResult`-like object with token counts.
    """
    queue = _state.queue
    if queue is None or user is None or chat is None or getattr(user, "is_bot", False):
        return

    event = _Event(
        user_id=user.id,
        username=getattr(user, "username", None),
        first_name=getattr(user, "first_name", None),
        last_name=getattr(user, "last_name", None),
        language_code=getattr(user, "language_code", None),
        chat_id=chat.id,
        chat_type=getattr(chat, "type", "unknown"),
        request_id=get_request_id() or "-",
        event_type=event_type,
        latency_ms=latency_ms,
        error_class=error_class,
    )
    if info is not None:
        event.media_type = _media_type_from_info(info)
        event.duration_sec = getattr(info, "duration", None)
        event.file_size_bytes = getattr(info, "file_size", None)
        event.mime_type = getattr(info, "mime_type", None)
    if result is not None:
        event.prompt_tokens = getattr(result, "prompt_tokens", None)
        event.candidates_tokens = getattr(result, "candidates_tokens", None)
        event.total_tokens = getattr(result, "total_tokens", None)

    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        now = time.monotonic()
        if now - _state.last_full_log_ts > 60:
            _state.last_full_log_ts = now
            logger.warning("analytics_queue_full size=%d dropping events", queue.qsize())


def _media_type_from_info(info: Any) -> Optional[str]:
    """Best-effort: recover 'voice'/'video_note'/'audio'/'video' from _MediaInfo."""
    mime = getattr(info, "mime_type", "") or ""
    ext = getattr(info, "file_ext", "") or ""
    duration = getattr(info, "duration", None)
    if mime == "audio/ogg" and ext == ".ogg":
        return "voice"
    if mime == "video/mp4" and ext == ".mp4" and duration is not None and duration <= 60:
        # video_note is short by Telegram contract; still ambiguous so we accept the bias.
        return "video_note"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return None


def _drain_batch() -> list[_Event]:
    queue = _state.queue
    if queue is None:
        return []
    batch: list[_Event] = []
    while len(batch) < _state.batch_size:
        try:
            batch.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return batch


async def _flush(batch: list[_Event]) -> None:
    p = pool.get()
    if p is None or not batch:
        return
    args_list = [event.as_args() for event in batch]
    async with p.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(_TRACK_SQL, args_list)


async def _maybe_retention(now_mono: float) -> None:
    p = pool.get()
    if p is None:
        return
    # Run retention once per ~24h, anchored to the process lifetime.
    if now_mono - _state.last_retention_ts < 24 * 60 * 60:
        return
    _state.last_retention_ts = now_mono
    try:
        async with p.acquire() as conn:
            deleted = await conn.execute(_RETENTION_SQL, _state.retention_days)
        logger.info("analytics_retention_cleanup result=%s", deleted)
    except Exception:  # noqa: BLE001
        logger.warning("analytics_retention_failed", exc_info=True)


async def _maybe_heartbeat(now_mono: float) -> None:
    p = pool.get()
    if p is None:
        return
    if now_mono - _state.last_heartbeat_ts < _state.heartbeat_interval_sec:
        return
    _state.last_heartbeat_ts = now_mono
    try:
        async with p.acquire() as conn:
            await conn.execute("SELECT 1")
    except Exception:  # noqa: BLE001
        logger.warning("analytics_heartbeat_failed", exc_info=True)


async def _drainer_loop() -> None:
    queue = _state.queue
    assert queue is not None
    # Initialise timestamps so first iteration doesn't spam retention/heartbeat.
    _state.last_retention_ts = time.monotonic()
    _state.last_heartbeat_ts = time.monotonic()
    while True:
        try:
            try:
                first = await asyncio.wait_for(queue.get(), timeout=_state.flush_interval_sec)
            except asyncio.TimeoutError:
                first = None

            now_mono = time.monotonic()
            if first is not None:
                batch = [first] + _drain_batch()
                try:
                    await _flush(batch)
                except Exception:  # noqa: BLE001 — silent fail per design
                    logger.warning("analytics_flush_failed n=%d", len(batch), exc_info=True)

            await _maybe_retention(now_mono)
            await _maybe_heartbeat(now_mono)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — drainer must never die
            logger.exception("analytics_drainer_unexpected_error")
            await asyncio.sleep(1.0)
