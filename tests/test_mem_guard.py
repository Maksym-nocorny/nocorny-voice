"""Memory-guard tests — threshold, busy-skip, uptime brake, kill switch."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from utils import mem_guard


@pytest.fixture(autouse=True)
async def _reset():
    yield
    await mem_guard.stop()
    # A failing test must not leak a stuck counter into its neighbours.
    mem_guard._inflight = 0


def _force_old_process(monkeypatch):
    """Pretend the process has been alive long enough for the uptime brake."""
    monkeypatch.setattr(mem_guard, "_started_at", time.monotonic() - 10_000)


# --- _check_once: the decision core -----------------------------------------

def test_exits_clean_when_over_threshold_and_idle(monkeypatch):
    _force_old_process(monkeypatch)
    with patch.object(mem_guard, "_read_rss_mb", return_value=450.0):
        with pytest.raises(SystemExit) as exc_info:
            mem_guard._check_once(400, 0, None)
    assert exc_info.value.code == 0  # clean exit — Render restarts, no error


def test_skips_when_under_threshold(monkeypatch):
    _force_old_process(monkeypatch)
    with patch.object(mem_guard, "_read_rss_mb", return_value=399.0):
        mem_guard._check_once(400, 0, None)  # must not raise


def test_skips_while_transcription_in_flight(monkeypatch):
    _force_old_process(monkeypatch)
    monkeypatch.setattr(mem_guard, "_inflight", 1)
    with patch.object(mem_guard, "_read_rss_mb", return_value=450.0):
        mem_guard._check_once(400, 0, None)  # busy — must not raise


def test_skips_when_updates_still_queued(monkeypatch):
    # Telegram already got a 200 for queued updates; exiting would lose them.
    _force_old_process(monkeypatch)
    with patch.object(mem_guard, "_read_rss_mb", return_value=450.0):
        mem_guard._check_once(400, 0, busy_fn=lambda: 3)  # must not raise


def test_skips_when_busy_check_fails(monkeypatch):
    # In doubt — don't kill anyone's work; re-check next cycle.
    _force_old_process(monkeypatch)

    def _broken_busy() -> int:
        raise RuntimeError("queue gone")

    with patch.object(mem_guard, "_read_rss_mb", return_value=450.0):
        mem_guard._check_once(400, 0, busy_fn=_broken_busy)  # must not raise


def test_skips_when_rss_unreadable(monkeypatch):
    _force_old_process(monkeypatch)
    with patch.object(mem_guard, "_read_rss_mb", return_value=None):
        mem_guard._check_once(400, 0, None)  # must not raise


def test_uptime_brake_blocks_early_restart(monkeypatch):
    # Restart-loop protection: a fresh process never guard-exits, even if the
    # threshold is misconfigured below the baseline RSS.
    monkeypatch.setattr(mem_guard, "_started_at", time.monotonic())
    with patch.object(mem_guard, "_read_rss_mb", return_value=450.0):
        mem_guard._check_once(400, 1800, None)  # must not raise


def test_exits_after_uptime_brake_releases(monkeypatch):
    _force_old_process(monkeypatch)
    with patch.object(mem_guard, "_read_rss_mb", return_value=450.0):
        with pytest.raises(SystemExit):
            mem_guard._check_once(400, 1800, lambda: 0)


# --- track_inflight: the "no work in flight" source of truth ----------------

async def test_track_inflight_counts_during_and_restores_after():
    entered = asyncio.Event()
    release = asyncio.Event()

    @mem_guard.track_inflight
    async def fake_handler():
        entered.set()
        await release.wait()

    assert mem_guard.inflight_count() == 0
    task = asyncio.create_task(fake_handler())
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    assert mem_guard.inflight_count() == 1
    release.set()
    await task
    assert mem_guard.inflight_count() == 0


async def test_track_inflight_restores_on_exception():
    @mem_guard.track_inflight
    async def failing_handler():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await failing_handler()
    assert mem_guard.inflight_count() == 0


def test_handle_message_is_tracked():
    # The real handler must carry the guard's wrapper, or "no work in flight"
    # degrades to a guess and the guard may kill live transcriptions.
    from handlers import transcribe
    assert getattr(transcribe.handle_message, "_mem_guard_tracked", False)


# --- start()/stop(): lifecycle and the kill switch --------------------------

async def test_start_disabled_when_threshold_zero():
    mem_guard.start(threshold_mb=0)
    assert mem_guard._task is None  # kill switch: no task scheduled at all


async def test_start_disabled_when_threshold_negative():
    mem_guard.start(threshold_mb=-1)
    assert mem_guard._task is None


async def test_start_is_idempotent():
    mem_guard.start(threshold_mb=400, interval_sec=999)
    first = mem_guard._task
    mem_guard.start(threshold_mb=400, interval_sec=999)
    assert mem_guard._task is first  # second start() is a no-op


async def test_stop_is_safe_when_not_running():
    await mem_guard.stop()  # no task started — must not raise


async def test_loop_invokes_check_periodically():
    checked = asyncio.Event()
    with patch.object(mem_guard, "_check_once",
                      side_effect=lambda *a, **k: checked.set()) as check:
        mem_guard.start(threshold_mb=400, interval_sec=0.01)
        await asyncio.wait_for(checked.wait(), timeout=2.0)
        await mem_guard.stop()
    assert check.call_count >= 1


async def test_loop_survives_check_errors():
    calls = []

    def _flaky(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("transient")

    with patch.object(mem_guard, "_check_once", side_effect=_flaky):
        mem_guard.start(threshold_mb=400, interval_sec=0.01)
        for _ in range(200):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
        await mem_guard.stop()
    # The guard kept checking after the first failure instead of dying.
    assert len(calls) >= 2


# --- config: env parsing of the kill switch ---------------------------------

def test_threshold_env_unset_keeps_default(monkeypatch):
    from config import _env_int_empty_off
    monkeypatch.delenv("MEM_GUARD_THRESHOLD_MB", raising=False)
    assert _env_int_empty_off("MEM_GUARD_THRESHOLD_MB", 400) == 400


def test_threshold_env_zero_disables(monkeypatch):
    from config import _env_int_empty_off
    monkeypatch.setenv("MEM_GUARD_THRESHOLD_MB", "0")
    assert _env_int_empty_off("MEM_GUARD_THRESHOLD_MB", 400) == 0


def test_threshold_env_empty_disables(monkeypatch):
    from config import _env_int_empty_off
    monkeypatch.setenv("MEM_GUARD_THRESHOLD_MB", "")
    assert _env_int_empty_off("MEM_GUARD_THRESHOLD_MB", 400) == 0


def test_threshold_env_explicit_value_wins(monkeypatch):
    from config import _env_int_empty_off
    monkeypatch.setenv("MEM_GUARD_THRESHOLD_MB", "512")
    assert _env_int_empty_off("MEM_GUARD_THRESHOLD_MB", 400) == 512


# --- RSS reader: sanity on this machine -------------------------------------

def test_read_rss_returns_plausible_value():
    rss = mem_guard._read_rss_mb()
    assert rss is not None
    # A running pytest process holds tens of MB; anything in (1, 16384) is sane.
    assert 1.0 < rss < 16384.0
