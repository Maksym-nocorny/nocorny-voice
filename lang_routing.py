"""Which engine should hear this user: local Whisper or Gemini?

Whisper is weak at a few languages (Persian above all — ~19% of our users).
The worker reports the language it detected and the handler re-routes to
Gemini when it is one of STT_CLOUD_LANGS, but that costs one wasted Whisper
pass per message. To skip the pass next time, remember the last detected
language per user; the user's Telegram UI language is a first guess before
anything was detected. Memory only (TTL 30 days, bounded) — a restart just
means one more Whisper pass per Persian speaker.

Group chats are keyed by user as well: the speaker is what matters, not the
room.
"""
from __future__ import annotations

from typing import Optional

from cachetools import TTLCache

from config import STT_CLOUD_LANGS

_last_lang: TTLCache = TTLCache(maxsize=20_000, ttl=30 * 24 * 3600)


def _norm(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    code = code.strip().lower().replace("_", "-")
    return code or None


def is_cloud_language(code: Optional[str]) -> bool:
    code = _norm(code)
    if not code:
        return False
    return code in STT_CLOUD_LANGS or code.split("-", 1)[0] in STT_CLOUD_LANGS


def remember(user_id: Optional[int], detected: Optional[str]) -> None:
    code = _norm(detected)
    if user_id is None or not code:
        return
    _last_lang[user_id] = code


def last_language(user_id: Optional[int]) -> Optional[str]:
    if user_id is None:
        return None
    return _last_lang.get(user_id)


def prefers_cloud(user_id: Optional[int], ui_language: Optional[str]) -> bool:
    """True when this user should skip the local worker and go to Gemini."""
    remembered = last_language(user_id)
    if remembered is not None:
        return is_cloud_language(remembered)
    return is_cloud_language(ui_language)


def reset() -> None:  # tests
    _last_lang.clear()
