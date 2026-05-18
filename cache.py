"""Two-layer transcription cache.

L1 (in-memory, file_unique_id) — instant, but resets on restart.
L2 (Neon Postgres, content SHA-256) — survives restarts, catches identical-content
re-uploads with different file_unique_id (e.g. forwarded between users).

Both layers degrade gracefully: missing DATABASE_URL or Postgres errors return None
from get_by_hash() and silently swallow store_by_hash() — bot keeps working.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

from cachetools import TTLCache

from analytics import pool as analytics_pool
from config import CACHE_L2_TTL_DAYS, CACHE_MAX_SIZE, CACHE_TTL_SEC

logger = logging.getLogger(__name__)


@dataclass
class CachedTranscription:
    text: str
    detected_language: Optional[str] = None


# L1: file_unique_id -> CachedTranscription
transcription_cache: TTLCache = TTLCache(maxsize=CACHE_MAX_SIZE, ttl=CACHE_TTL_SEC)


# --- L1 (in-memory, by file_unique_id) ---

def store_transcription(
    file_unique_id: str, text: str, detected_language: Optional[str] = None
) -> None:
    transcription_cache[file_unique_id] = CachedTranscription(text, detected_language)


def get_transcription(file_unique_id: str) -> Optional[CachedTranscription]:
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
RETURNING text, detected_language
"""

# Backfill detected_language on second-and-later inserts where the first insert
# happened before language detection existed.
_STORE_BY_HASH_SQL = """
INSERT INTO nocorny_voice.transcription_cache (content_hash, text, detected_language)
VALUES ($1, $2, $3)
ON CONFLICT (content_hash) DO UPDATE
SET last_hit_at = now(),
    hit_count = nocorny_voice.transcription_cache.hit_count + 1,
    detected_language = COALESCE(
        nocorny_voice.transcription_cache.detected_language, EXCLUDED.detected_language
    )
"""


async def get_by_hash(content_hash: str) -> Optional[CachedTranscription]:
    """L2 lookup. Returns None on miss, if Postgres unavailable, OR if L2 disabled."""
    if CACHE_L2_TTL_DAYS <= 0:
        return None
    p = analytics_pool.get()
    if p is None:
        return None
    try:
        row = await p.fetchrow(_GET_BY_HASH_SQL, content_hash)
    except Exception:  # noqa: BLE001 — graceful degradation
        logger.warning("cache_l2_get_failed hash=%s", content_hash[:12], exc_info=True)
        return None
    if row is None:
        return None
    return CachedTranscription(row["text"], row["detected_language"])


async def store_by_hash(
    content_hash: str, text: str, detected_language: Optional[str] = None
) -> None:
    """L2 store. No-op if L2 disabled or Postgres unavailable; swallows errors."""
    if CACHE_L2_TTL_DAYS <= 0:
        return
    p = analytics_pool.get()
    if p is None:
        return
    try:
        await p.execute(_STORE_BY_HASH_SQL, content_hash, text, detected_language)
    except Exception:  # noqa: BLE001 — graceful degradation
        logger.warning("cache_l2_store_failed hash=%s", content_hash[:12], exc_info=True)


_pending_l2_writes: set[asyncio.Task] = set()


def fire_and_forget_store_by_hash(
    content_hash: str, text: str, detected_language: Optional[str] = None
) -> None:
    """Schedule an L2 store without blocking the caller.

    Holds a strong reference to the task so Python's GC doesn't drop it mid-flight
    (asyncio.create_task alone is not enough — see Python asyncio docs).
    """
    task = asyncio.create_task(store_by_hash(content_hash, text, detected_language))
    _pending_l2_writes.add(task)
    task.add_done_callback(_pending_l2_writes.discard)
