"""Per-user request budget using a sliding-window approach."""
from __future__ import annotations

import time
from typing import List

from cachetools import TTLCache

from config import RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SEC


class RateLimiter:
    """Sliding-window rate limiter. Tracks request timestamps per user."""

    def __init__(self, max_requests: int, window_sec: int, max_users: int = 10000) -> None:
        self.max_requests = max_requests
        self.window_sec = window_sec
        # TTL is 2x window so entries don't disappear mid-evaluation; cleanup is automatic
        self._history: TTLCache = TTLCache(maxsize=max_users, ttl=window_sec * 2)

    def is_allowed(self, user_id: int, *, now: float | None = None) -> bool:
        """Return True if user has budget; consumes one request slot if allowed."""
        if now is None:
            now = time.monotonic()
        cutoff = now - self.window_sec
        history: List[float] = [t for t in self._history.get(user_id, []) if t > cutoff]
        if len(history) >= self.max_requests:
            self._history[user_id] = history
            return False
        history.append(now)
        self._history[user_id] = history
        return True

    def reset(self) -> None:
        self._history.clear()


_default = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SEC)


def is_allowed(user_id: int) -> bool:
    return _default.is_allowed(user_id)


def reset() -> None:
    _default.reset()
