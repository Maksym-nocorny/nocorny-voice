"""Tracker tests — pure write-path logic, no real DB."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from analytics import tracker
from analytics import pool as analytics_pool


def _user(uid=7, username="alice", lang="en"):
    return SimpleNamespace(
        id=uid, username=username, first_name="Alice", last_name=None,
        language_code=lang, is_bot=False,
    )


def _chat(cid=42, ctype="private"):
    return SimpleNamespace(id=cid, type=ctype)


@pytest.fixture(autouse=True)
async def _reset_state():
    tracker._state.last_full_log_ts = 0.0
    tracker._state.queue = None
    tracker._state.drainer_task = None
    yield
    if tracker._state.drainer_task is not None:
        await tracker.stop()


def test_track_is_noop_when_not_started():
    """track() before start() should silently drop without raising."""
    tracker.track("start_command", user=_user(), chat=_chat())
    # No assertion needed — just verifying no exception.


def test_track_skips_bots():
    tracker._state.queue = asyncio.Queue(maxsize=10)
    bot_user = SimpleNamespace(id=1, username="b", first_name="b", last_name=None,
                               language_code="en", is_bot=True)
    tracker.track("start_command", user=bot_user, chat=_chat())
    assert tracker._state.queue.empty()


def test_track_enqueues_event_with_user_fields():
    tracker._state.queue = asyncio.Queue(maxsize=10)
    tracker.track("start_command", user=_user(), chat=_chat())
    assert tracker._state.queue.qsize() == 1
    event = tracker._state.queue.get_nowait()
    assert event.event_type == "start_command"
    assert event.user_id == 7
    assert event.username == "alice"
    assert event.chat_id == 42
    assert event.chat_type == "private"


def test_track_with_info_and_result_extracts_fields():
    tracker._state.queue = asyncio.Queue(maxsize=10)
    info = SimpleNamespace(file_id="x", file_unique_id="u", file_ext=".ogg",
                           mime_type="audio/ogg", duration=15, file_size=2048)
    result = SimpleNamespace(text="hello", prompt_tokens=10, candidates_tokens=5,
                             total_tokens=15)
    tracker.track("transcribe_success", user=_user(), chat=_chat(),
                  info=info, result=result, latency_ms=1234)
    event = tracker._state.queue.get_nowait()
    assert event.media_type == "voice"
    assert event.duration_sec == 15
    assert event.file_size_bytes == 2048
    assert event.mime_type == "audio/ogg"
    assert event.prompt_tokens == 10
    assert event.total_tokens == 15
    assert event.latency_ms == 1234


def test_queue_full_drops_silently():
    tracker._state.queue = asyncio.Queue(maxsize=2)
    for _ in range(5):
        tracker.track("start_command", user=_user(), chat=_chat())
    # No exception; queue stays at capacity
    assert tracker._state.queue.qsize() == 2


def test_drain_batch_respects_batch_size():
    tracker._state.queue = asyncio.Queue(maxsize=20)
    tracker._state.batch_size = 5
    for _ in range(8):
        tracker.track("start_command", user=_user(), chat=_chat())
    batch = tracker._drain_batch()
    assert len(batch) == 5
    assert tracker._state.queue.qsize() == 3


def test_track_handles_missing_user_or_chat():
    tracker._state.queue = asyncio.Queue(maxsize=10)
    tracker.track("start_command", user=None, chat=_chat())
    tracker.track("start_command", user=_user(), chat=None)
    assert tracker._state.queue.empty()


def test_media_type_inference():
    voice_info = SimpleNamespace(file_ext=".ogg", mime_type="audio/ogg", duration=10)
    assert tracker._media_type_from_info(voice_info) == "voice"

    audio_info = SimpleNamespace(file_ext=".mp3", mime_type="audio/mpeg", duration=120)
    assert tracker._media_type_from_info(audio_info) == "audio"

    video_info = SimpleNamespace(file_ext=".mp4", mime_type="video/mp4", duration=600)
    assert tracker._media_type_from_info(video_info) == "video"

    note_info = SimpleNamespace(file_ext=".mp4", mime_type="video/mp4", duration=10)
    assert tracker._media_type_from_info(note_info) == "video_note"


async def test_drainer_calls_executemany_when_pool_present(monkeypatch):
    """Drainer drains a batch and pushes it to executemany."""
    seen: list = []

    class FakeConn:
        def transaction(self):
            class T:
                async def __aenter__(self_inner): return self_inner
                async def __aexit__(self_inner, *a): return False
            return T()

        async def executemany(self, sql, args_list):
            seen.append((sql, list(args_list)))

        async def execute(self, *args):  # SELECT 1 heartbeat
            return None

    class FakePool:
        def acquire(self):
            class A:
                async def __aenter__(self_inner): return FakeConn()
                async def __aexit__(self_inner, *a): return False
            return A()

    monkeypatch.setattr(analytics_pool, "get", lambda: FakePool())

    tracker.configure(queue_size=50, batch_size=10, flush_interval_sec=0.05,
                      heartbeat_interval_sec=10_000, retention_days=90)
    tracker.start()
    try:
        for _ in range(3):
            tracker.track("start_command", user=_user(), chat=_chat())

        # Wait long enough for drainer to flush
        for _ in range(20):
            await asyncio.sleep(0.05)
            if seen:
                break
    finally:
        await tracker.stop()

    assert seen, "drainer never called executemany"
    sql, args_list = seen[0]
    assert "INSERT INTO nocorny_voice.events" in sql
    assert len(args_list) == 3


async def test_drainer_survives_db_errors(monkeypatch):
    """If executemany raises, drainer logs and keeps running."""
    call_count = {"n": 0}

    class FakeConn:
        def transaction(self):
            class T:
                async def __aenter__(self_inner): return self_inner
                async def __aexit__(self_inner, *a): return False
            return T()

        async def executemany(self, sql, args_list):
            call_count["n"] += 1
            raise RuntimeError("db down")

        async def execute(self, *args):
            return None

    class FakePool:
        def acquire(self):
            class A:
                async def __aenter__(self_inner): return FakeConn()
                async def __aexit__(self_inner, *a): return False
            return A()

    monkeypatch.setattr(analytics_pool, "get", lambda: FakePool())

    tracker.configure(queue_size=50, batch_size=10, flush_interval_sec=0.05,
                      heartbeat_interval_sec=10_000, retention_days=90)
    tracker.start()
    try:
        tracker.track("start_command", user=_user(), chat=_chat())
        for _ in range(20):
            await asyncio.sleep(0.05)
            if call_count["n"] >= 1:
                break
        assert call_count["n"] >= 1
        # Drainer is still alive
        assert tracker._state.drainer_task is not None
        assert not tracker._state.drainer_task.done()
    finally:
        await tracker.stop()
