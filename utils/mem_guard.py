"""Controlled self-restart before Render's OOM killer strikes.

The bot leaks ~0.7 MB of RSS per transcription (dispatcher incident
2026-08-04-oom-restart.md: nine OOM kills in two months, every memory cycle
ending at the 512 MiB Free-tier ceiling). Until the leak's root cause is
fixed, this guard replaces the unpredictable kernel OOM kill — which silently
destroys any transcription in flight — with a clean exit at a provably quiet
moment: every MEM_GUARD_CHECK_INTERVAL_SEC it reads the process's own RSS;
when RSS exceeds MEM_GUARD_THRESHOLD_MB and NO update is being processed or
queued, it logs `mem_guard_restart` and raises SystemExit(0).
python-telegram-bot catches SystemExit escaping the event loop and runs its
full graceful-shutdown chain (post_shutdown included, so analytics flushes),
then Render restarts the process (~40 s, back to the ~120 MB baseline).

Safety properties:
  - "quiet" is a real check, not a guess: handlers.transcribe.handle_message
    is wrapped with track_inflight(), and queued-but-not-started updates are
    counted via `busy_fn` (Telegram already got a 200 for those, so exiting
    would lose them too). When in doubt — RSS unreadable, busy_fn failing —
    the guard skips the cycle and re-checks later; a missed restart costs
    nothing, a restart during someone's transcription kills it.
  - There is no `await` between the busy check and the exit, so on the
    single-threaded event loop no transcription can start in the gap.
  - Restart-loop brake: the guard never fires during the first
    MEM_GUARD_MIN_UPTIME_SEC of process life.
  - MEM_GUARD_THRESHOLD_MB=0 (or an empty value) disables the guard entirely;
    start() then never schedules the task.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import resource
import sys
import time
from typing import Callable, Optional

from config import (
    MEM_GUARD_CHECK_INTERVAL_SEC,
    MEM_GUARD_MIN_UPTIME_SEC,
    MEM_GUARD_THRESHOLD_MB,
)

logger = logging.getLogger(__name__)

_MB = 1024 * 1024
_PAGE_SIZE = resource.getpagesize()

_task: Optional[asyncio.Task] = None
_started_at: float = 0.0
_inflight: int = 0


def track_inflight(func):
    """Decorator: count the wrapped async handler as work in flight.

    While the counter is non-zero the guard will not restart the process.
    The event loop is single-threaded, so a plain int is race-free.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        global _inflight
        _inflight += 1
        try:
            return await func(*args, **kwargs)
        finally:
            _inflight -= 1

    wrapper._mem_guard_tracked = True  # introspection hook for tests
    return wrapper


def inflight_count() -> int:
    return _inflight


def _read_rss_mb() -> Optional[float]:
    """Current RSS in MB, or None when unreadable.

    Linux (Render): /proc/self/statm field 2 — CURRENT resident pages, not the
    peak, so page-cache reclaim between checks is respected and a transient
    spike doesn't force a restart. Fallback (macOS dev, /proc absent):
    getrusage peak RSS — good enough there, since in production the guard only
    runs in webhook mode on Render where /proc always exists.
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            rss_pages = int(f.read().split()[1])
        return rss_pages * _PAGE_SIZE / _MB
    except (OSError, ValueError, IndexError):
        pass
    try:
        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    return max_rss / _MB if sys.platform == "darwin" else max_rss / 1024


def _check_once(threshold_mb: float, min_uptime_sec: float,
                busy_fn: Optional[Callable[[], int]]) -> None:
    """One guard cycle. Synchronous on purpose: no `await` may separate the
    busy check from the exit, or a transcription could start in the gap."""
    rss_mb = _read_rss_mb()
    if rss_mb is None:
        logger.warning("mem_guard_rss_unavailable")
        return
    if rss_mb < threshold_mb:
        logger.debug("mem_guard_ok rss_mb=%.0f threshold_mb=%.0f",
                     rss_mb, threshold_mb)
        return

    uptime_sec = time.monotonic() - _started_at
    if uptime_sec < min_uptime_sec:
        logger.info(
            "mem_guard_wait_uptime rss_mb=%.0f uptime_sec=%.0f "
            "min_uptime_sec=%.0f", rss_mb, uptime_sec, min_uptime_sec,
        )
        return

    pending = 0
    if busy_fn is not None:
        try:
            pending = int(busy_fn() or 0)
        except Exception:  # noqa: BLE001 — in doubt, don't kill anyone's work
            logger.warning("mem_guard_busy_check_failed", exc_info=True)
            return
    if _inflight > 0 or pending > 0:
        logger.info(
            "mem_guard_wait_busy rss_mb=%.0f inflight=%d pending=%d",
            rss_mb, _inflight, pending,
        )
        return

    logger.warning(
        "mem_guard_restart rss_mb=%.0f threshold_mb=%.0f uptime_sec=%.0f "
        "inflight=0 pending=0 — clean exit before the OOM killer; "
        "Render restarts the service",
        rss_mb, threshold_mb, uptime_sec,
    )
    sys.exit(0)


async def _loop(threshold_mb: float, interval_sec: float,
                min_uptime_sec: float,
                busy_fn: Optional[Callable[[], int]]) -> None:
    while True:
        await asyncio.sleep(interval_sec)
        try:
            _check_once(threshold_mb, min_uptime_sec, busy_fn)
        except SystemExit:
            # Propagate through the task: asyncio re-raises SystemExit into
            # the event loop, PTB catches it and shuts down gracefully.
            raise
        except Exception:  # noqa: BLE001 — the guard must never die
            logger.warning("mem_guard_check_failed", exc_info=True)


def start(*, threshold_mb: float = MEM_GUARD_THRESHOLD_MB,
          interval_sec: float = MEM_GUARD_CHECK_INTERVAL_SEC,
          min_uptime_sec: float = MEM_GUARD_MIN_UPTIME_SEC,
          busy_fn: Optional[Callable[[], int]] = None) -> None:
    """Start the background guard. Idempotent; threshold <= 0 disables it.

    `busy_fn` returns the number of updates accepted but not yet handled
    (e.g. `lambda: app.update_queue.qsize()`); those count as work in flight.
    """
    global _task, _started_at
    if threshold_mb <= 0:
        logger.info("mem_guard_disabled threshold_mb=%s", threshold_mb)
        return
    if _task is not None and not _task.done():
        return
    _started_at = time.monotonic()
    _task = asyncio.create_task(
        _loop(threshold_mb, interval_sec, min_uptime_sec, busy_fn),
        name="mem_guard",
    )
    logger.info(
        "mem_guard_started threshold_mb=%.0f interval=%.0fs min_uptime=%.0fs",
        threshold_mb, interval_sec, min_uptime_sec,
    )


async def stop() -> None:
    """Cancel the background guard."""
    global _task
    task = _task
    _task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except BaseException:  # noqa: BLE001 — CancelledError, or the guard's own
        # SystemExit: when the guard just triggered the restart, the task
        # holds SystemExit and awaiting it re-raises; swallowing it here keeps
        # PTB's post_shutdown chain (analytics flush) intact.
        pass
