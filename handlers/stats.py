"""/stats command handler — admin-only analytics overview."""
from __future__ import annotations

import logging
import time
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import analytics
from config import ADMIN_USER_ID, TELEGRAM_MAX_MESSAGE_LEN
from utils.logging_setup import new_request_id, set_chat, set_user
from utils.markdown import chunk_text

logger = logging.getLogger(__name__)

_overview_cache: tuple[float, str] | None = None
_OVERVIEW_TTL_SEC = 5.0

_SECTIONS = [
    ("overview", "Overview"),
    ("users", "Users"),
    ("content", "Content"),
    ("perf", "Perf"),
    ("cost", "Cost"),
]


def nav_keyboard(
    current: str,
    page: int = 1,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    rows = []
    if total_pages > 1:
        pg_row = []
        if page > 1:
            pg_row.append(InlineKeyboardButton("←", callback_data=f"stats:{current}:{page - 1}"))
        pg_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="stats:noop:0"))
        if page < total_pages:
            pg_row.append(InlineKeyboardButton("→", callback_data=f"stats:{current}:{page + 1}"))
        rows.append(pg_row)
    nav_row = [
        InlineKeyboardButton(
            f"· {label} ·" if key == current else label,
            callback_data=f"stats:{key}:1",
        )
        for key, label in _SECTIONS
    ]
    rows.append(nav_row)
    return InlineKeyboardMarkup(rows)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message:
        return

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
    page = int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else 1
    try:
        text, keyboard = await _render_with_keyboard(sub, page)
    except Exception:
        logger.exception("stats_render_failed sub=%s", sub)
        await update.message.reply_text("Stats unavailable (query error).")
        return

    chunks = list(chunk_text(text, TELEGRAM_MAX_MESSAGE_LEN))
    for i, chunk in enumerate(chunks):
        km = keyboard if i == len(chunks) - 1 else None
        await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=km)


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    user = update.effective_user
    if ADMIN_USER_ID is None or not user or user.id != ADMIN_USER_ID:
        return

    parts = query.data.split(":")
    if len(parts) < 3 or parts[0] != "stats":
        return
    sub = parts[1]
    page = int(parts[2]) if parts[2].isdigit() else 1

    if sub == "noop":
        return

    if not analytics.is_enabled():
        return

    try:
        text, keyboard = await _render_with_keyboard(sub, page)
    except Exception:
        logger.exception("stats_callback_failed sub=%s page=%s", sub, page)
        return

    chunks = list(chunk_text(text, TELEGRAM_MAX_MESSAGE_LEN))
    for i, chunk in enumerate(chunks):
        km = keyboard if i == len(chunks) - 1 else None
        await query.message.reply_text(chunk, parse_mode="HTML", reply_markup=km)


async def _render_with_keyboard(sub: str, page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    global _overview_cache
    if sub in ("overview", "o"):
        now = time.monotonic()
        if _overview_cache and (now - _overview_cache[0]) < _OVERVIEW_TTL_SEC:
            text = _overview_cache[1]
        else:
            text = analytics.render_overview(await analytics.get_overview())
            _overview_cache = (now, text)
        return text, nav_keyboard("overview")
    if sub in ("users", "u"):
        s = await analytics.get_users_section(page=page)
        text = analytics.render_users(s)
        _page = getattr(s, "page", page)
        _total_pages = getattr(s, "total_pages", 1)
        return text, nav_keyboard("users", page=_page, total_pages=_total_pages)
    if sub in ("content", "c"):
        text = analytics.render_content(await analytics.get_content_section())
        return text, nav_keyboard("content")
    if sub in ("perf", "p", "performance"):
        text = analytics.render_perf(await analytics.get_perf_section())
        return text, nav_keyboard("perf")
    if sub in ("cost", "$"):
        text = analytics.render_cost(await analytics.get_cost_section())
        return text, nav_keyboard("cost")
    return (
        "Unknown subcommand. Use one of:\n"
        "  /stats           — overview\n"
        "  /stats users     — cohorts and all users\n"
        "  /stats content   — media types, durations, languages\n"
        "  /stats perf      — latency, errors, cache, rate-limits\n"
        "  /stats cost      — tokens, minutes, RPM/RPD"
    ), nav_keyboard("overview")


def reset_cache() -> None:
    """Test helper."""
    global _overview_cache
    _overview_cache = None
