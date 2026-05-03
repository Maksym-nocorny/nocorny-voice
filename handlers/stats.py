"""/stats command handler — admin-only analytics overview."""
from __future__ import annotations

import logging
import time
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

import analytics
from config import ADMIN_USER_ID, TELEGRAM_MAX_MESSAGE_LEN
from utils.logging_setup import new_request_id, set_chat, set_user
from utils.markdown import chunk_text

logger = logging.getLogger(__name__)

# Cache rendered overview HTML for 5s to absorb rapid /stats spam.
_overview_cache: tuple[float, str] | None = None
_OVERVIEW_TTL_SEC = 5.0


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message:
        return

    # Silent rejection — don't reveal the command to non-admins.
    if ADMIN_USER_ID is None or user.id != ADMIN_USER_ID:
        return

    new_request_id()
    set_user(user.id)
    set_chat(chat.id)

    if not analytics.is_enabled():
        await update.message.reply_text(
            "Analytics not available (DATABASE_URL not configured).",
            parse_mode="HTML",
        )
        return

    sub = (context.args[0].lower() if context.args else "overview")
    try:
        text = await _render(sub)
    except Exception:
        logger.exception("stats_render_failed sub=%s", sub)
        await update.message.reply_text("Stats unavailable (query error).")
        return

    for chunk in chunk_text(text, TELEGRAM_MAX_MESSAGE_LEN):
        await update.message.reply_text(chunk, parse_mode="HTML")


async def _render(sub: str) -> str:
    global _overview_cache
    if sub in ("overview", "o"):
        now = time.monotonic()
        if _overview_cache and (now - _overview_cache[0]) < _OVERVIEW_TTL_SEC:
            return _overview_cache[1]
        text = analytics.render_overview(await analytics.get_overview())
        _overview_cache = (now, text)
        return text
    if sub in ("users", "u"):
        return analytics.render_users(await analytics.get_users_section())
    if sub in ("content", "c"):
        return analytics.render_content(await analytics.get_content_section())
    if sub in ("perf", "p", "performance"):
        return analytics.render_perf(await analytics.get_perf_section())
    if sub in ("cost", "$"):
        return analytics.render_cost(await analytics.get_cost_section())
    return (
        "Unknown subcommand. Use one of:\n"
        "  /stats           — overview\n"
        "  /stats users     — cohorts and top spenders\n"
        "  /stats content   — media types, durations, languages\n"
        "  /stats perf      — latency, errors, cache, rate-limits\n"
        "  /stats cost      — tokens, minutes, RPM/RPD"
    )


def reset_cache() -> None:
    """Test helper."""
    global _overview_cache
    _overview_cache = None
