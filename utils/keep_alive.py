"""Self-ping keep-alive so Render Free doesn't spin the web service down.

Render Free suspends a web service after ~15 min with no inbound HTTP traffic.
Waking it is a full cold start (~25-40s) that outlives Telegram's ~15s
callback-query TTL — which is why /stats buttons came back "Query is too old".

A GitHub Actions cron used to ping the public URL, but GitHub silently drops the
large majority of scheduled runs, so the service still slept for hours. An
in-process asyncio timer fires reliably.

The ping is an HTTP GET to the bot's OWN public URL (through Render's ingress) —
it does NOT touch the database, so it costs zero Neon compute hours.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_task: Optional[asyncio.Task] = None


async def _loop(url: str, interval_sec: float) -> None:
    # One client for the whole loop; closed when the task is cancelled.
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            await asyncio.sleep(interval_sec)
            try:
                resp = await client.get(url)
                logger.debug("keep_alive_ping url=%s status=%s", url, resp.status_code)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — keep-alive must never die
                logger.debug("keep_alive_ping_failed url=%s", url, exc_info=True)


def start(url: str, *, interval_sec: float = 300.0) -> None:
    """Start the background self-ping. Idempotent; no-op if already running."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(url, interval_sec), name="keep_alive")
    logger.info("keep_alive_started url=%s interval=%.0fs", url, interval_sec)


async def stop() -> None:
    """Cancel the background self-ping."""
    global _task
    task = _task
    _task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — best effort
        pass
