"""HTML escape and message-chunking helpers for Telegram parse_mode='HTML'."""
from __future__ import annotations

import html
from typing import List


def escape_html(text: str) -> str:
    """Escape user-controlled text for Telegram HTML mode (escapes &, <, >)."""
    return html.escape(text or "", quote=False)


def chunk_text(text: str, max_length: int) -> List[str]:
    """Split `text` into chunks at most `max_length` chars long.

    Prefers to split on the last newline within the window. Falls back to a hard
    split when no newline is available in the second half of the window.
    """
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    if len(text) <= max_length:
        return [text] if text else []

    chunks: List[str] = []
    remaining = text
    while len(remaining) > max_length:
        split_at = remaining.rfind("\n", 0, max_length)
        if split_at < max_length // 2:
            split_at = max_length
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks
