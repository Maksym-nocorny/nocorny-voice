"""asyncpg connection pool lifecycle for the analytics subsystem."""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def create(dsn: str, *, min_size: int = 2, max_size: int = 10,
                 command_timeout: int = 10,
                 max_inactive_connection_lifetime: float = 200.0) -> asyncpg.Pool:
    """Create the global pool. Caller must ensure dsn is valid; raises on failure.

    `max_inactive_connection_lifetime` is set under Neon's ~300s idle timeout so
    asyncpg recycles before the server kills the socket.
    """
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
        max_inactive_connection_lifetime=max_inactive_connection_lifetime,
        server_settings={"application_name": "nocorny_voice_bot"},
    )
    logger.info("analytics_pool_initialized min=%d max=%d", min_size, max_size)
    return _pool


def get() -> Optional[asyncpg.Pool]:
    return _pool


async def close() -> None:
    global _pool
    if _pool is None:
        return
    try:
        await _pool.close()
    finally:
        _pool = None
        logger.info("analytics_pool_closed")
