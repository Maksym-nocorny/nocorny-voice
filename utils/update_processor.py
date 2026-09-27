"""Fair concurrent update processing: one user can't block everybody else.

Before this, PTB ran with its default of one update at a time for the whole
bot. On 2026-09-27 a user forwarded 11 voice messages in a row and every other
user (the owner included) waited ~3.5 minutes behind them.

Rules:
  - Media updates (voice / audio / video / video note) are serialized PER USER
    (per chat in groups) — a user's own files still come back in the order they were sent — and at
    most `max_media` transcriptions run at once across all users, which keeps
    RSS inside Render's 512 MiB.
  - The per-user lock is taken BEFORE the global media slot, so a user with a
    backlog holds at most one slot while their other files wait.
  - Everything else (/start, /stats, callback buttons) is not throttled and
    never waits behind a transcription.

`busy_count()` reports every update accepted but not finished (waiting or
running); mem_guard must not restart while it is non-zero, otherwise updates
Telegram already got a 200 for would be lost.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Dict, Hashable, Optional

from telegram import Update
from telegram.ext import BaseUpdateProcessor

logger = logging.getLogger(__name__)

# Upper bound on updates PTB hands us at once. Deliberately large: the real
# throttle is the media semaphore below; this only bounds bookkeeping.
_MAX_CONCURRENT_UPDATES = 4096


def _media_key(update: object) -> Optional[Hashable]:
    """Serialization key for media updates, None for everything else."""
    if not isinstance(update, Update):
        return None
    msg = update.effective_message
    if msg is None or not (msg.voice or msg.audio or msg.video or msg.video_note):
        return None
    # Groups share one queue: parallel replies there would hit Telegram's
    # ~20 messages/minute per-group limit.
    chat = update.effective_chat
    if chat is not None and chat.type != chat.PRIVATE:
        return ("chat", chat.id)
    user = update.effective_user
    if user is not None:
        return ("user", user.id)
    return ("chat", chat.id) if chat is not None else None


class FairUpdateProcessor(BaseUpdateProcessor):
    __slots__ = ("_max_media", "_media_sem", "_user_locks", "_busy")

    def __init__(self, max_media: int):
        super().__init__(_MAX_CONCURRENT_UPDATES)
        if max_media < 1:
            raise ValueError("max_media must be a positive integer")
        self._max_media = max_media
        # Created on first use, inside the running loop (Python <3.10 binds
        # asyncio primitives to the loop current at construction time).
        self._media_sem: Optional[asyncio.Semaphore] = None
        # key -> [lock, number of updates holding or waiting on it]
        self._user_locks: Dict[Hashable, list] = {}
        self._busy = 0

    def busy_count(self) -> int:
        return self._busy

    async def do_process_update(self, update: object,
                                coroutine: Awaitable[Any]) -> None:
        self._busy += 1
        try:
            key = _media_key(update)
            if key is None:
                await coroutine
                return
            entry = self._user_locks.get(key)
            if entry is None:
                entry = self._user_locks[key] = [asyncio.Lock(), 0]
            if self._media_sem is None:
                self._media_sem = asyncio.Semaphore(self._max_media)
            entry[1] += 1
            started = False
            try:
                async with entry[0]:
                    async with self._media_sem:
                        started = True
                        await coroutine
            finally:
                if not started and hasattr(coroutine, "close"):
                    coroutine.close()  # cancelled while waiting: no warning
                entry[1] -= 1
                if entry[1] == 0:
                    self._user_locks.pop(key, None)
        finally:
            self._busy -= 1

    async def initialize(self) -> None:
        logger.info("update_processor_started max_media=%d", self._max_media)

    async def shutdown(self) -> None:
        pass
