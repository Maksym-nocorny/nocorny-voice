"""Render query results to Telegram HTML for /stats."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from utils.markdown import escape_html

from .queries import (
    ContentSection,
    CostSection,
    OverviewSection,
    PerfSection,
    TopUser,
    TopUserByTokens,
    UsersSection,
)


def _user_label(u) -> str:
    if u.username:
        return f"@{escape_html(u.username)}"
    # No public username — render as a tg://user?id deep-link so the admin can
    # tap through to the profile. Falls back to "user_<id>" if no first_name.
    name = (u.first_name or "").strip() or f"user_{u.user_id}"
    return f'<a href="tg://user?id={u.user_id}">{escape_html(name)}</a>'


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _fmt_int(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def _fmt_tokens(x: int) -> str:
    if x >= 1_000_000:
        return f"{x / 1_000_000:.2f}M"
    if x >= 1_000:
        return f"{x / 1_000:.1f}K"
    return str(x)


def _fmt_minutes(m: float) -> str:
    if m >= 60:
        return f"{m / 60:.1f}h"
    return f"{m:.1f}m"


def _fmt_top_users(users: list[TopUser]) -> str:
    if not users:
        return "  (none)"
    lines = []
    for i, u in enumerate(users, 1):
        lines.append(f"  {i}. {_user_label(u)} — {_fmt_int(u.total_events)}")
    return "\n".join(lines)


def _fmt_top_users_tokens(users: list[TopUserByTokens]) -> str:
    if not users:
        return "  (none)"
    lines = []
    for i, u in enumerate(users, 1):
        lines.append(f"  {i}. {_user_label(u)} — {_fmt_tokens(u.tokens)}")
    return "\n".join(lines)


def _fmt_grouped(rows, *, label: str = "") -> str:
    if not rows:
        return "  (none)"
    return "\n".join(f"  • {escape_html(r.label)}: {_fmt_int(r.count)}" for r in rows)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _empty(message: str) -> str:
    return f"<b>Nocorny.voice — stats</b>\n\n{escape_html(message)}"


# --------------------------------------------------------------------------- overview


def render_overview(s: Optional[OverviewSection]) -> str:
    if s is None:
        return _empty("Analytics not available (DATABASE_URL not configured).")
    return (
        f"<b>Nocorny.voice — overview</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Volume</b>\n"
        f"  • Today: {_fmt_int(s.today)}  (24h: {_fmt_int(s.last_24h)}  •  7d: {_fmt_int(s.last_7d)})\n"
        f"  • This hour: {_fmt_int(s.this_hour)}\n"
        f"  • DAU {_fmt_int(s.dau)}  •  WAU {_fmt_int(s.wau)}  •  MAU {_fmt_int(s.mau)}\n"
        f"  • Users: {_fmt_int(s.total_users)} total, {_fmt_int(s.new_users_today)} new today\n\n"
        f"<b>Top users (24h)</b>\n{_fmt_top_users(s.top_users_24h)}\n\n"
        f"<b>Performance (24h)</b>\n"
        f"  • Cache hit rate: {_fmt_pct(s.cache_hit_rate_24h)}\n"
        f"  • Latency p50/p95: {s.latency_p50_ms}ms / {s.latency_p95_ms}ms\n"
        f"  • Error rate: {_fmt_pct(s.error_rate_24h)}\n"
        f"  • Rate-limited: {_fmt_int(s.rate_limited_24h)}\n\n"
        f"<b>Cost (24h)</b>\n"
        f"  • Tokens: {_fmt_tokens(s.total_tokens_24h)}\n"
        f"  • Minutes processed: {_fmt_minutes(s.minutes_24h)}\n"
        f"  • RPM now: {_fmt_int(s.rpm_now)}  •  RPD today: {_fmt_int(s.rpd_today)}\n\n"
        f"<i>Use /stats users | content | perf | cost for detail.</i>"
    )


# --------------------------------------------------------------------------- users


def render_users(s: Optional[UsersSection]) -> str:
    if s is None:
        return _empty("Analytics not available.")
    return (
        f"<b>Nocorny.voice — users</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Cohorts</b>\n"
        f"  • Total users: {_fmt_int(s.total_users)}\n"
        f"  • DAU {_fmt_int(s.dau)}  •  WAU {_fmt_int(s.wau)}  •  MAU {_fmt_int(s.mau)}\n"
        f"  • New today: {_fmt_int(s.new_users_today)}  •  new 7d: {_fmt_int(s.new_users_7d)}\n\n"
        f"<b>Top users (all-time, by events)</b>\n{_fmt_top_users(s.top_users_all)}\n\n"
        f"<b>Top users (30d, by events)</b>\n{_fmt_top_users(s.top_users_30d)}\n\n"
        f"<b>Top users (30d, by tokens)</b>\n{_fmt_top_users_tokens(s.top_users_by_tokens_30d)}\n\n"
        f"<b>Languages</b>\n{_fmt_grouped(s.languages)}"
    )


# --------------------------------------------------------------------------- content


def render_content(s: Optional[ContentSection]) -> str:
    if s is None:
        return _empty("Analytics not available.")
    return (
        f"<b>Nocorny.voice — content</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Media types</b>\n{_fmt_grouped(s.media_types)}\n\n"
        f"<b>Chat types</b>\n{_fmt_grouped(s.chat_types)}\n\n"
        f"<b>Duration buckets</b>\n{_fmt_grouped(s.duration_buckets)}\n\n"
        f"<b>Totals</b>\n"
        f"  • Lifetime: {_fmt_minutes(s.total_minutes_lifetime)} processed\n"
        f"  • 30d: {_fmt_minutes(s.total_minutes_30d)} processed\n"
        f"  • Avg duration: {s.avg_duration_sec:.0f}s  •  median: {s.median_duration_sec}s"
    )


# --------------------------------------------------------------------------- perf


def render_perf(s: Optional[PerfSection]) -> str:
    if s is None:
        return _empty("Analytics not available.")
    return (
        f"<b>Nocorny.voice — performance</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Cache</b>\n"
        f"  • Hit rate 24h: {_fmt_pct(s.cache_hit_rate_24h)}\n"
        f"  • Hit rate 7d:  {_fmt_pct(s.cache_hit_rate_7d)}\n\n"
        f"<b>Latency 24h (Gemini end-to-end)</b>\n"
        f"  • p50: {s.latency_p50_ms}ms\n"
        f"  • p95: {s.latency_p95_ms}ms\n"
        f"  • p99: {s.latency_p99_ms}ms\n\n"
        f"<b>Errors 24h</b>\n"
        f"  • Rate: {_fmt_pct(s.error_rate_24h)}\n"
        f"  • Rate-limited (per-user): {_fmt_int(s.rate_limited_24h)}\n\n"
        f"<b>Errors by class (7d)</b>\n{_fmt_grouped(s.errors_by_class)}\n\n"
        f"<b>Rejected files (24h)</b>\n{_fmt_grouped(s.rejected_24h)}"
    )


# --------------------------------------------------------------------------- cost


def render_cost(s: Optional[CostSection]) -> str:
    if s is None:
        return _empty("Analytics not available.")
    return (
        f"<b>Nocorny.voice — cost</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Tokens</b>\n"
        f"  • Lifetime: {_fmt_tokens(s.total_tokens_lifetime)}\n"
        f"  • 30d:      {_fmt_tokens(s.total_tokens_30d)}\n"
        f"  • 24h:      {_fmt_tokens(s.total_tokens_24h)}\n"
        f"  • Avg/req (30d): {s.avg_tokens_per_request_30d:.0f}\n\n"
        f"<b>Audio processed</b>\n"
        f"  • Lifetime: {_fmt_minutes(s.minutes_lifetime)}\n"
        f"  • 30d:      {_fmt_minutes(s.minutes_30d)}\n\n"
        f"<b>Throughput</b>\n"
        f"  • RPM now: {_fmt_int(s.rpm_now)}\n"
        f"  • RPD today: {_fmt_int(s.rpd_today)}"
    )
