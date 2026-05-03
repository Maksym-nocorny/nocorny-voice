"""Analytics subsystem (Neon Postgres).

Public surface:
    init(database_url)                — async, called from post_init
    close()                           — async, called from post_shutdown
    is_enabled()                      — bool
    track(event_type, *, user, chat, ...)  — sync, fire-and-forget
    get_overview() / get_users_section() / ...   — async, called by /stats handler
    render_overview(...) / render_users(...) / ... — sync, format to HTML

When DATABASE_URL is unset, init() is a no-op, is_enabled() returns False, and
track() silently drops events. The bot stays fully functional without analytics.
"""
from __future__ import annotations

import logging
from typing import Optional

from config import (
    ANALYTICS_BATCH_SIZE,
    ANALYTICS_FLUSH_INTERVAL_SEC,
    ANALYTICS_HEARTBEAT_INTERVAL_SEC,
    ANALYTICS_QUEUE_SIZE,
    ANALYTICS_RETENTION_DAYS,
    CACHE_L2_TTL_DAYS,
)

from . import pool, schema, tracker
from .formatter import (
    render_content,
    render_cost,
    render_overview,
    render_perf,
    render_users,
)
from .queries import (
    get_content_section,
    get_cost_section,
    get_overview,
    get_perf_section,
    get_users_section,
    TopGroup,
)
from .tracker import track

logger = logging.getLogger(__name__)

_enabled = False


async def init(database_url: Optional[str]) -> None:
    """Initialise pool, apply schema, start drainer. Safe to call without a URL."""
    global _enabled
    if not database_url:
        logger.info("analytics_disabled DATABASE_URL not set")
        return
    try:
        p = await pool.create(database_url)
        await schema.apply(p)
        tracker.configure(
            queue_size=ANALYTICS_QUEUE_SIZE,
            batch_size=ANALYTICS_BATCH_SIZE,
            flush_interval_sec=ANALYTICS_FLUSH_INTERVAL_SEC,
            heartbeat_interval_sec=ANALYTICS_HEARTBEAT_INTERVAL_SEC,
            retention_days=ANALYTICS_RETENTION_DAYS,
            cache_l2_ttl_days=CACHE_L2_TTL_DAYS,
        )
        tracker.start()
        _enabled = True
        logger.info("analytics_enabled")
    except Exception:  # noqa: BLE001 — init must not crash the bot
        logger.exception("analytics_init_failed bot will run without analytics")
        await pool.close()
        _enabled = False


async def close() -> None:
    global _enabled
    _enabled = False
    await tracker.stop()
    await pool.close()


def is_enabled() -> bool:
    return _enabled


__all__ = [
    "init",
    "close",
    "is_enabled",
    "track",
    "get_overview",
    "get_users_section",
    "get_content_section",
    "get_perf_section",
    "get_cost_section",
    "render_overview",
    "render_users",
    "render_content",
    "render_perf",
    "render_cost",
    "TopGroup",
]
