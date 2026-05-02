"""TTL cache for transcriptions, keyed by Telegram's stable `file_unique_id`.

Same file forwarded later or re-sent in another chat hits the cache and skips Gemini.
"""
from __future__ import annotations

from typing import Optional

from cachetools import TTLCache

from config import CACHE_MAX_SIZE, CACHE_TTL_SEC

# file_unique_id -> transcribed text
transcription_cache: TTLCache = TTLCache(maxsize=CACHE_MAX_SIZE, ttl=CACHE_TTL_SEC)


def store_transcription(file_unique_id: str, text: str) -> None:
    transcription_cache[file_unique_id] = text


def get_transcription(file_unique_id: str) -> Optional[str]:
    return transcription_cache.get(file_unique_id)


def remove_transcription(file_unique_id: str) -> None:
    transcription_cache.pop(file_unique_id, None)


def clear_all() -> None:
    transcription_cache.clear()
