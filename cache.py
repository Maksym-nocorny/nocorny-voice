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
import re
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


# A blank transcription is never a usable answer: Telegram rejects an empty
# message ("Message text is empty"), so caching one poisons every later retry
# of the same audio — the handler short-circuits on the cache hit and fails
# again without ever re-asking Gemini. Seen live 08.08.2026: one voice message
# left a user with no reply at all, permanently. Both cache layers therefore
# refuse to store blanks AND refuse to return them, so rows already poisoned
# before this guard existed (L2 survives restarts) degrade to a normal miss.
def _is_blank(text: Optional[str]) -> bool:
    return not text or not text.strip()


# A partially-degraded transcription is worth SENDING but never worth CACHING.
# When a chunked transcribe loses fragments (Gemini PROHIBITED_CONTENT,
# RECITATION, loop detection), gemini_service splices in placeholder lines like
# "[фрагмент 2: не вдалося розпізнати]" and the handler still delivers the rest.
# Caching that text makes the loss permanent: L2 keeps it for 14 days keyed by
# content hash, so every re-send of the same audio short-circuits on the hit and
# replays the crippled half — the user cannot fix it by re-sending. Seen live
# 10.08.2026: one chunk refused with PROHIBITED_CONTENT, partial text cached.
# Same treatment as blanks (08.08.2026): both layers refuse to store placeholder
# text AND refuse to return it, so rows poisoned before this guard existed
# degrade to an ordinary miss and the audio gets a fresh Gemini pass.
#
# The pattern must stay in sync with the placeholder strings built in
# gemini_service (search for "не вдалося розпізнати"). `[^\]]*` tolerates
# extra detail between the fragment number and the colon (e.g. a future
# "[фрагмент 1, частина 2: ...]" from split-chunk salvaging).
_DEGRADED_MARKER_RE = re.compile(r"\[фрагмент \d+[^\]]*: не вдалося розпізнати\]")


def _is_degraded(text: Optional[str]) -> bool:
    return bool(text) and _DEGRADED_MARKER_RE.search(text) is not None


# --- L1 (in-memory, by file_unique_id) ---

def store_transcription(
    file_unique_id: str, text: str, detected_language: Optional[str] = None
) -> None:
    if _is_blank(text):
        logger.warning("cache_skip_blank_store file_unique_id=%s", file_unique_id)
        return
    if _is_degraded(text):
        # Backstop: the handler already skips caching on degraded_chunks > 0;
        # this catches any other caller handing us placeholder text.
        logger.warning("cache_skip_degraded_store file_unique_id=%s", file_unique_id)
        return
    transcription_cache[file_unique_id] = CachedTranscription(text, detected_language)


def get_transcription(file_unique_id: str) -> Optional[CachedTranscription]:
    cached = transcription_cache.get(file_unique_id)
    if cached is not None and _is_blank(cached.text):
        # Poisoned before the guard landed — drop it and report a miss.
        logger.warning("cache_evict_blank file_unique_id=%s", file_unique_id)
        transcription_cache.pop(file_unique_id, None)
        return None
    if cached is not None and _is_degraded(cached.text):
        # Partial text stored before the degraded guard landed — same treatment.
        logger.warning("cache_evict_degraded file_unique_id=%s", file_unique_id)
        transcription_cache.pop(file_unique_id, None)
        return None
    return cached


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
# `text` is normally left alone (the stored transcription is the source of
# truth), with two exceptions — rows written by pre-guard bugs that can never be
# served, because get_by_hash treats them as a miss forever: (a) blank rows and
# (b) rows carrying "[фрагмент N: не вдалося розпізнати]" placeholders. Without
# this heal every retry of that audio pays for Gemini again. The write guards
# above ensure EXCLUDED.text is always clean, so the overwrite only upgrades.
# The LIKE pattern mirrors _DEGRADED_MARKER_RE ('%' spans the fragment number
# and any extra detail before the colon).
_STORE_BY_HASH_SQL = """
INSERT INTO nocorny_voice.transcription_cache (content_hash, text, detected_language)
VALUES ($1, $2, $3)
ON CONFLICT (content_hash) DO UPDATE
SET last_hit_at = now(),
    hit_count = nocorny_voice.transcription_cache.hit_count + 1,
    text = CASE
        WHEN btrim(nocorny_voice.transcription_cache.text) = ''
          OR nocorny_voice.transcription_cache.text
             LIKE '%[фрагмент %: не вдалося розпізнати]%'
        THEN EXCLUDED.text
        ELSE nocorny_voice.transcription_cache.text
    END,
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
    if _is_blank(row["text"]):
        # Blank row written before the guard existed. Treated as a miss so the
        # audio gets a fresh Gemini pass instead of replaying the empty answer.
        logger.warning("cache_l2_blank_ignored hash=%s", content_hash[:12])
        return None
    if _is_degraded(row["text"]):
        # Partial row written before the degraded guard existed (L2 survives
        # restarts and lives 14 days). Miss, so a re-send is a fresh attempt.
        logger.warning("cache_l2_degraded_ignored hash=%s", content_hash[:12])
        return None
    return CachedTranscription(row["text"], row["detected_language"])


async def store_by_hash(
    content_hash: str, text: str, detected_language: Optional[str] = None
) -> None:
    """L2 store. No-op if L2 disabled or Postgres unavailable; swallows errors."""
    if CACHE_L2_TTL_DAYS <= 0:
        return
    if _is_blank(text):
        logger.warning("cache_l2_skip_blank_store hash=%s", content_hash[:12])
        return
    if _is_degraded(text):
        # Backstop mirroring store_transcription — see _DEGRADED_MARKER_RE.
        logger.warning("cache_l2_skip_degraded_store hash=%s", content_hash[:12])
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
