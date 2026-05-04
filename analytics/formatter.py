"""Render query results to Telegram HTML for /stats."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from utils.markdown import escape_html

from .queries import (
    AllUsersSection,
    ContentSection,
    CostSection,
    OverviewSection,
    PerfSection,
    TopGroup,
    TopUser,
    TopUserUsage,
    UsersSection,
)


def _user_label(u) -> str:
    if u.username:
        return f"@{escape_html(u.username)}"
    # No public username. Telegram's tg://user?id deep-link is only clickable
    # when both users share a common chat — for cross-user admin views it
    # usually isn't, so always append a copyable <code>id</code> fallback.
    name = (u.first_name or "").strip() or f"user_{u.user_id}"
    return (f'<a href="tg://user?id={u.user_id}">{escape_html(name)}</a>'
            f' · <code>{u.user_id}</code>')


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


def _fmt_usd(x: float) -> str:
    if x >= 1:
        return f"${x:.2f}"
    return f"${x:.4f}"


def _fmt_top_users(users: list[TopUser], *, offset: int = 0) -> str:
    if not users:
        return "  (none)"
    lines = []
    for i, u in enumerate(users, 1 + offset):
        lines.append(f"  {i}. {_user_label(u)} — {_fmt_int(u.total_events)}")
    return "\n".join(lines)


def _fmt_top_users_usage(users: list[TopUserUsage], *,
                         show_cost: bool, offset: int = 0) -> str:
    if not users:
        return "  (none)"
    lines = []
    for i, u in enumerate(users, 1 + offset):
        ev = _fmt_int(u.total_events)
        tk = _fmt_tokens(u.tokens)
        if show_cost:
            metrics = f"{ev} ev · {tk} · {_fmt_usd(u.cost_usd)}"
        else:
            metrics = f"{ev} ev · {tk}"
        lines.append(f"  {i}. {_user_label(u)} — {metrics}")
    return "\n".join(lines)


_GROUP_TYPE_TAG = {
    "group": "group",
    "supergroup": "supergroup",
    "channel": "channel",
}


def _group_label(g: TopGroup) -> str:
    name = (g.title or "").strip() or f"chat_{g.chat_id}"
    tag = _GROUP_TYPE_TAG.get(g.chat_type, g.chat_type)
    return (f"{escape_html(name)} <i>({escape_html(tag)})</i>"
            f" · <code>{g.chat_id}</code>")


def _fmt_top_groups(groups: list[TopGroup], *, offset: int = 0) -> str:
    if not groups:
        return "  (none)"
    lines = []
    for i, g in enumerate(groups, 1 + offset):
        lines.append(f"  {i}. {_group_label(g)} — {_fmt_int(g.total_events)}")
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
    pricing_active = s.price_per_1m_input > 0 or s.price_per_1m_output > 0
    cost_lines = (
        f"  • USD 24h: {_fmt_usd(s.cost_usd_24h)}  •  30d: {_fmt_usd(s.cost_usd_30d)}\n"
        if pricing_active else ""
    )
    return (
        f"<b>Nocorny.voice — overview</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Volume</b>\n"
        f"  • Today: {_fmt_int(s.today)}  (24h: {_fmt_int(s.last_24h)}  •  7d: {_fmt_int(s.last_7d)})\n"
        f"  • This hour: {_fmt_int(s.this_hour)}\n"
        f"  • DAU {_fmt_int(s.dau)}  •  WAU {_fmt_int(s.wau)}  •  MAU {_fmt_int(s.mau)}\n"
        f"  • Users: {_fmt_int(s.total_users)} total, {_fmt_int(s.new_users_today)} new today\n\n"
        f"<b>Top users (24h)</b>\n{_fmt_top_users(s.top_users_24h)}\n\n"
        f"<b>Cost</b>\n"
        f"  • Tokens 24h: {_fmt_tokens(s.total_tokens_24h)}\n"
        f"  • Minutes 24h: {_fmt_minutes(s.minutes_24h)}\n"
        f"{cost_lines}"
        f"  • RPM now: {_fmt_int(s.rpm_now)}  •  RPD today: {_fmt_int(s.rpd_today)}\n\n"
        f"<i>Error rate (24h): {_fmt_pct(s.error_rate_24h)}</i>"
    )


# --------------------------------------------------------------------------- users


def render_users(s: Optional[UsersSection]) -> str:
    if s is None:
        return _empty("Analytics not available.")
    show_cost = s.price_per_1m_input > 0 or s.price_per_1m_output > 0
    header = "ev · tokens · USD" if show_cost else "ev · tokens"
    groups_header = f"<b>Groups using the bot ({_fmt_int(s.total_groups)} total) — events</b>"
    return (
        f"<b>Nocorny.voice — users</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Cohorts</b>\n"
        f"  • Total users: {_fmt_int(s.total_users)}\n"
        f"  • DAU {_fmt_int(s.dau)}  •  WAU {_fmt_int(s.wau)}  •  MAU {_fmt_int(s.mau)}\n"
        f"  • New today: {_fmt_int(s.new_users_today)}  •  new 7d: {_fmt_int(s.new_users_7d)}\n\n"
        f"<b>Top users (30d) — {header}</b>\n"
        f"{_fmt_top_users_usage(s.top_users_30d, show_cost=show_cost)}\n\n"
        f"{groups_header}\n"
        f"{_fmt_top_groups(s.top_groups)}\n\n"
        f"<b>Languages</b>\n{_fmt_grouped(s.languages)}"
    )


# --------------------------------------------------------------------------- all users


def render_all_users(s: Optional[AllUsersSection]) -> str:
    if s is None:
        return _empty("Analytics not available.")
    show_cost = s.price_per_1m_input > 0 or s.price_per_1m_output > 0
    page_info = f" — page {s.page}/{s.total_pages}" if s.total_pages > 1 else ""
    offset = (s.page - 1) * s.page_size
    end = offset + len(s.top_users_all)
    range_info = f", #{offset + 1}–#{end}" if s.total_users > 0 else ""
    header = "ev · tokens · USD" if show_cost else "ev · tokens"
    return (
        f"<b>Nocorny.voice — all users{page_info}</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Total users:</b> {_fmt_int(s.total_users)}\n\n"
        f"<b>By all-time events{range_info} — {header}</b>\n"
        f"{_fmt_top_users_usage(s.top_users_all, show_cost=show_cost, offset=offset)}"
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
    cost_block = ""
    if s.price_per_1m_input > 0 or s.price_per_1m_output > 0:
        cost_block = (
            f"<b>Cost (USD)</b>\n"
            f"  • Lifetime: {_fmt_usd(s.cost_usd_lifetime)}\n"
            f"  • 30d:      {_fmt_usd(s.cost_usd_30d)}\n"
            f"  • 7d:       {_fmt_usd(s.cost_usd_7d)}\n"
            f"  • 24h:      {_fmt_usd(s.cost_usd_24h)}\n"
            f"  • Avg/req (30d): {_fmt_usd(s.avg_cost_usd_per_request_30d)}\n"
            f"  <i>at ${s.price_per_1m_input:.2f}/1M in · "
            f"${s.price_per_1m_output:.2f}/1M out</i>\n\n"
        )
    return (
        f"<b>Nocorny.voice — cost</b>\n"
        f"<i>{_now_utc()}</i>\n\n"
        f"<b>Tokens</b>\n"
        f"  • Lifetime: {_fmt_tokens(s.total_tokens_lifetime)}\n"
        f"  • 30d:      {_fmt_tokens(s.total_tokens_30d)}\n"
        f"  • 7d:       {_fmt_tokens(s.total_tokens_7d)}\n"
        f"  • 24h:      {_fmt_tokens(s.total_tokens_24h)}\n"
        f"  • Avg/req (30d): {s.avg_tokens_per_request_30d:.0f}\n\n"
        f"{cost_block}"
        f"<b>Audio processed</b>\n"
        f"  • Lifetime: {_fmt_minutes(s.minutes_lifetime)}\n"
        f"  • 30d:      {_fmt_minutes(s.minutes_30d)}\n\n"
        f"<b>Throughput</b>\n"
        f"  • RPM now: {_fmt_int(s.rpm_now)}\n"
        f"  • RPD today: {_fmt_int(s.rpd_today)}"
    )
