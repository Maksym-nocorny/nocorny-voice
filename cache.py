"""Two-layer transcription cache.

L1 (in-memory, file_unique_id) — instant, but resets on restart.
L2 (Neon Postgres, content SHA-256) — survives restarts, catches identical-content
re-uploads with different file_unique_id (e.g. forwarded between users).

Both layers degrade gracefully: missing DATABASE_URL or Postgres errors return None
from get_by_hash() and silently swallow store_by_hash() — bot keeps working.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from cachetools import TTLCache

from analytics import pool as analytics_pool
from config import CACHE_MAX_SIZE, CACHE_TTL_SEC

logger = logging.getLogger(__name__)

# L1: file_unique_id -> transcribed text
transcription_cache: TTLCache = TTLCache(maxsize=CACHE_MAX_SIZE, ttl=CACHE_TTL_SEC)


# --- L1 (in-memory, by file_unique_id) ---

def store_transcription(file_unique_id: str, text: str) -> None:
    transcription_cache[file_unique_id] = text


def get_transcription(file_unique_id: str) -> Optional[str]:
    return transcription_cache.get(file_unique_id)


def remove_transcription(file_unique_id: str) -> None:
    transcription_cache.pop(file_unique_id, None)


def clear_all() -> None:
    transcription_cache.clear()


# --- L2 (Neon Postgres, by content SHA-256) ---

def hash_file(path: str) -> str:
    """Stream SHA-256 of a file; ~150-300ms for 50 MB."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


_GET_BY_HASH_SQL = """
UPDATE nocorny_voice.transcription_cache
SET last_hit_at = now(), hit_count = hit_count + 1
WHERE content_hash = $1
RETURNING text
"""

_STORE_BY_HASH_SQL = """
INSERT INTO nocorny_voice.transcription_cache (content_hash, text)
VALUES ($1, $2)
ON CONFLICT (content_hash) DO UPDATE
SET last_hit_at = now(), hit_count = nocorny_voice.transcription_cache.hit_count + 1
"""


async def get_by_hash(content_hash: str) -> Optional[str]:
    """L2 lookup. Returns None on miss OR if Postgres unavailable."""
    p = analytics_pool.get()
    if p is None:
        return None
    try:
        return await p.fetchval(_GET_BY_HASH_SQL, content_hash)
    except Exception:  # noqa: BLE001 — graceful degradation
        logger.warning("cache_l2_get_failed hash=%s", content_hash[:12], exc_info=True)
        return None


async def store_by_hash(content_hash: str, text: str) -> None:
    """L2 store. Silently swallows errors — bot keeps working without L2."""
    p = analytics_pool.get()
    if p is None:
        return
    try:
        await p.execute(_STORE_BY_HASH_SQL, content_hash, text)
    except Exception:  # noqa: BLE001 — graceful degradation
        logger.warning("cache_l2_store_failed hash=%s", content_hash[:12], exc_info=True)
