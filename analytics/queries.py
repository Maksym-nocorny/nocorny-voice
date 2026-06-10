"""Read path: per-section consolidated SQL.

Each section runs a small number of wide aggregations using
`count(*) FILTER (WHERE …)` / `SUM(...) FILTER (WHERE …)` so a single Postgres
round-trip yields all the scalars the formatter needs. Independent queries
within a section fan out via `asyncio.gather` — every query takes its own
connection from the pool (asyncpg connections cannot be shared concurrently).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import asyncpg

from config import (
    PRICE_PER_1M_AUDIO_INPUT_TOKENS,
    PRICE_PER_1M_INPUT_TOKENS,
    PRICE_PER_1M_OUTPUT_TOKENS,
)

from . import pool


# --------------------------------------------------------------------------- types


@dataclass
class TopUser:
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    total_events: int


@dataclass
class TopUserUsage:
    """Per-user usage metrics: events, tokens, cost — joined for one row."""
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    total_events: int
    tokens: int
    cost_usd: float


@dataclass
class CountedRow:
    label: str
    count: int


@dataclass
class TopGroup:
    chat_id: int
    chat_type: str
    title: Optional[str]
    total_events: int


@dataclass
class OverviewSection:
    today: int
    last_24h: int
    last_7d: int
    this_hour: int
    dau: int
    wau: int
    mau: int
    new_users_today: int
    total_users: int
    top_users_24h: list[TopUser] = field(default_factory=list)
    error_rate_24h: float = 0.0
    total_tokens_24h: int = 0
    minutes_24h: float = 0.0
    rpm_now: int = 0
    rpd_today: int = 0
    cost_usd_24h: float = 0.0
    cost_usd_30d: float = 0.0
    price_per_1m_input: float = 0.0
    price_per_1m_audio_input: float = 0.0
    price_per_1m_output: float = 0.0


@dataclass
class UsersSection:
    total_users: int
    dau: int
    wau: int
    mau: int
    new_users_today: int
    new_users_7d: int
    top_users_30d: list[TopUserUsage]
    languages: list[CountedRow]
    top_groups: list[TopGroup] = field(default_factory=list)
    total_groups: int = 0
    price_per_1m_input: float = 0.0
    price_per_1m_audio_input: float = 0.0
    price_per_1m_output: float = 0.0


@dataclass
class AllUsersSection:
    """Paginated all-time list with per-user events/tokens/cost."""
    total_users: int
    top_users_all: list[TopUserUsage]
    page: int = 1
    total_pages: int = 1
    page_size: int = 50
    price_per_1m_input: float = 0.0
    price_per_1m_audio_input: float = 0.0
    price_per_1m_output: float = 0.0


@dataclass
class ContentSection:
    media_types: list[CountedRow]
    chat_types: list[CountedRow]
    total_minutes_lifetime: float
    total_minutes_30d: float
    duration_buckets: list[CountedRow]
    avg_duration_sec: float
    median_duration_sec: int


@dataclass
class PerfSection:
    cache_hit_rate_24h: float
    cache_hit_rate_7d: float
    latency_p50_ms: int
    latency_p95_ms: int
    latency_p99_ms: int
    error_rate_24h: float
    errors_by_class: list[CountedRow]
    rate_limited_24h: int
    rejected_24h: list[CountedRow]
    degraded_by_reason: list[CountedRow] = field(default_factory=list)


@dataclass
class CostSection:
    total_tokens_lifetime: int
    total_tokens_30d: int
    total_tokens_24h: int
    avg_tokens_per_request_30d: float
    minutes_lifetime: float
    minutes_30d: float
    rpm_now: int
    rpd_today: int
    cost_usd_lifetime: float
    cost_usd_30d: float
    cost_usd_24h: float
    avg_cost_usd_per_request_30d: float
    price_per_1m_input: float
    price_per_1m_output: float
    price_per_1m_audio_input: float = 0.0
    total_tokens_7d: int = 0
    cost_usd_7d: float = 0.0


# --------------------------------------------------------------------------- overview


_OVERVIEW_WINDOWED_SQL = """
WITH e AS (
    SELECT ts, user_id, event_type, total_tokens, duration_sec,
           prompt_tokens, candidates_tokens, prompt_audio_tokens
    FROM nocorny_voice.events
    WHERE ts >= now() - interval '7 days'
)
SELECT
    count(*) FILTER (WHERE ts >= date_trunc('day', now()))::bigint        AS today,
    count(*) FILTER (WHERE ts >= now() - interval '24 hours')::bigint     AS last_24h,
    count(*)::bigint                                                       AS last_7d,
    count(*) FILTER (WHERE ts >= date_trunc('hour', now()))::bigint       AS this_hour,
    count(DISTINCT user_id) FILTER (WHERE ts >= now() - interval '1 day')::bigint  AS dau,
    count(DISTINCT user_id) FILTER (WHERE ts >= now() - interval '7 days')::bigint AS wau,
    count(*) FILTER (WHERE event_type='transcribe_success'
                       AND ts >= now() - interval '1 minute')::bigint     AS rpm_now,
    count(*) FILTER (WHERE event_type='transcribe_success'
                       AND ts >= date_trunc('day', now()))::bigint        AS rpd_today,
    COALESCE(SUM(total_tokens) FILTER (WHERE event_type='transcribe_success'
                       AND ts >= now() - interval '24 hours'), 0)::bigint AS tokens_24h,
    (COALESCE(SUM(duration_sec) FILTER (WHERE event_type='transcribe_success'
                       AND ts >= now() - interval '24 hours'), 0)::float / 60) AS minutes_24h,
    COALESCE(SUM(prompt_tokens) FILTER (WHERE event_type='transcribe_success'
                       AND ts >= now() - interval '24 hours'), 0)::bigint  AS prompt_24h,
    COALESCE(SUM(prompt_audio_tokens) FILTER (WHERE event_type='transcribe_success'
                       AND ts >= now() - interval '24 hours'), 0)::bigint  AS audio_24h,
    COALESCE(SUM(candidates_tokens) FILTER (WHERE event_type='transcribe_success'
                       AND ts >= now() - interval '24 hours'), 0)::bigint  AS cand_24h,
    COALESCE(
        count(*) FILTER (WHERE event_type IN
            ('error_unknown','processing_failed','rate_limited_gemini','transcribe_degraded')
            AND ts >= now() - interval '24 hours')::float
        / NULLIF(count(*) FILTER (WHERE event_type='transcribe_request'
            AND ts >= now() - interval '24 hours'), 0),
        0
    )::float                                                               AS error_rate_24h
FROM e
"""


_OVERVIEW_30D_SQL = """
SELECT
    count(DISTINCT user_id)::bigint AS mau,
    COALESCE(SUM(prompt_tokens) FILTER (WHERE event_type='transcribe_success'), 0)::bigint        AS prompt_30d,
    COALESCE(SUM(prompt_audio_tokens) FILTER (WHERE event_type='transcribe_success'), 0)::bigint  AS audio_30d,
    COALESCE(SUM(candidates_tokens) FILTER (WHERE event_type='transcribe_success'), 0)::bigint    AS cand_30d
FROM nocorny_voice.events
WHERE ts >= now() - interval '30 days'
"""


_USERS_TABLE_OVERVIEW_SQL = """
SELECT
    count(*)::bigint AS total,
    count(*) FILTER (WHERE first_seen_at >= date_trunc('day', now()))::bigint AS new_today
FROM nocorny_voice.users
"""


async def get_overview() -> Optional[OverviewSection]:
    p = pool.get()
    if p is None:
        return None
    win, win30, users_row, top24 = await asyncio.gather(
        p.fetchrow(_OVERVIEW_WINDOWED_SQL),
        p.fetchrow(_OVERVIEW_30D_SQL),
        p.fetchrow(_USERS_TABLE_OVERVIEW_SQL),
        _top_users_in(p, "1 day", limit=5),
    )
    return OverviewSection(
        today=int(win["today"]),
        last_24h=int(win["last_24h"]),
        last_7d=int(win["last_7d"]),
        this_hour=int(win["this_hour"]),
        dau=int(win["dau"]),
        wau=int(win["wau"]),
        mau=int(win30["mau"]),
        new_users_today=int(users_row["new_today"]),
        total_users=int(users_row["total"]),
        top_users_24h=top24,
        error_rate_24h=float(win["error_rate_24h"]),
        total_tokens_24h=int(win["tokens_24h"]),
        minutes_24h=float(win["minutes_24h"]),
        rpm_now=int(win["rpm_now"]),
        rpd_today=int(win["rpd_today"]),
        cost_usd_24h=_cost_usd(int(win["prompt_24h"]), int(win["cand_24h"]),
                               int(win["audio_24h"])),
        cost_usd_30d=_cost_usd(int(win30["prompt_30d"]), int(win30["cand_30d"]),
                               int(win30["audio_30d"])),
        price_per_1m_input=PRICE_PER_1M_INPUT_TOKENS,
        price_per_1m_audio_input=PRICE_PER_1M_AUDIO_INPUT_TOKENS,
        price_per_1m_output=PRICE_PER_1M_OUTPUT_TOKENS,
    )


# --------------------------------------------------------------------------- users


_USERS_COHORTS_SQL = """
SELECT
    count(DISTINCT user_id) FILTER (WHERE ts >= now() - interval '1 day')::bigint  AS dau,
    count(DISTINCT user_id) FILTER (WHERE ts >= now() - interval '7 days')::bigint AS wau,
    count(DISTINCT user_id)::bigint                                                AS mau
FROM nocorny_voice.events
WHERE ts >= now() - interval '30 days'
"""


_USERS_TABLE_SECTION_SQL = """
SELECT
    count(*)::bigint AS total,
    count(*) FILTER (WHERE first_seen_at >= date_trunc('day', now()))::bigint AS new_today,
    count(*) FILTER (WHERE first_seen_at >= now() - interval '7 days')::bigint AS new_7d
FROM nocorny_voice.users
"""


_TOTAL_GROUPS_SQL = (
    "SELECT count(*)::bigint AS total FROM nocorny_voice.chats "
    "WHERE chat_type IN ('group','supergroup','channel')"
)


async def get_users_section() -> Optional[UsersSection]:
    p = pool.get()
    if p is None:
        return None
    cohorts, users_row, top30, langs, groups, total_groups_row = await asyncio.gather(
        p.fetchrow(_USERS_COHORTS_SQL),
        p.fetchrow(_USERS_TABLE_SECTION_SQL),
        _top_users_usage_in(p, "30 days", 10),
        _languages(p),
        _top_groups(p, limit=15),
        p.fetchrow(_TOTAL_GROUPS_SQL),
    )
    return UsersSection(
        total_users=int(users_row["total"]),
        dau=int(cohorts["dau"]),
        wau=int(cohorts["wau"]),
        mau=int(cohorts["mau"]),
        new_users_today=int(users_row["new_today"]),
        new_users_7d=int(users_row["new_7d"]),
        top_users_30d=top30,
        languages=langs,
        top_groups=groups,
        total_groups=int(total_groups_row["total"]),
        price_per_1m_input=PRICE_PER_1M_INPUT_TOKENS,
        price_per_1m_audio_input=PRICE_PER_1M_AUDIO_INPUT_TOKENS,
        price_per_1m_output=PRICE_PER_1M_OUTPUT_TOKENS,
    )


async def get_all_users_section(page: int = 1, page_size: int = 50
                                ) -> Optional[AllUsersSection]:
    p = pool.get()
    if p is None:
        return None
    total_users_row = await p.fetchrow("SELECT count(*)::bigint AS total FROM nocorny_voice.users")
    total_users = int(total_users_row["total"])
    total_pages = max(1, (total_users + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    rows = await _top_users_all_page_with_usage(p, page, page_size)
    return AllUsersSection(
        total_users=total_users,
        top_users_all=rows,
        page=page,
        total_pages=total_pages,
        page_size=page_size,
        price_per_1m_input=PRICE_PER_1M_INPUT_TOKENS,
        price_per_1m_audio_input=PRICE_PER_1M_AUDIO_INPUT_TOKENS,
        price_per_1m_output=PRICE_PER_1M_OUTPUT_TOKENS,
    )


# --------------------------------------------------------------------------- content


_CONTENT_DURATIONS_SQL = """
SELECT
    (COALESCE(SUM(duration_sec), 0)::float / 60)                                       AS min_lifetime,
    (COALESCE(SUM(duration_sec) FILTER (WHERE ts >= now() - interval '30 days'), 0)::float / 60) AS min_30d,
    COALESCE(AVG(duration_sec), 0)::float                                              AS avg_dur,
    COALESCE(percentile_disc(0.5) WITHIN GROUP (ORDER BY duration_sec)
             FILTER (WHERE duration_sec IS NOT NULL), 0)::int                          AS median_dur
FROM nocorny_voice.events
WHERE event_type='transcribe_success'
"""


async def get_content_section() -> Optional[ContentSection]:
    p = pool.get()
    if p is None:
        return None
    durations, media_types, chat_types, buckets = await asyncio.gather(
        p.fetchrow(_CONTENT_DURATIONS_SQL),
        _grouped(p,
            "SELECT COALESCE(media_type,'unknown') AS k, count(*)::bigint AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' "
            "GROUP BY 1 ORDER BY c DESC"),
        _grouped(p,
            "SELECT chat_type AS k, count(*)::bigint AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' "
            "GROUP BY 1 ORDER BY c DESC"),
        _duration_buckets(p),
    )
    return ContentSection(
        media_types=media_types,
        chat_types=chat_types,
        total_minutes_lifetime=float(durations["min_lifetime"]),
        total_minutes_30d=float(durations["min_30d"]),
        duration_buckets=buckets,
        avg_duration_sec=float(durations["avg_dur"]),
        median_duration_sec=int(durations["median_dur"]),
    )


# --------------------------------------------------------------------------- perf


_PERF_24H_SQL = """
WITH e AS (
    SELECT ts, event_type, latency_ms
    FROM nocorny_voice.events
    WHERE ts >= now() - interval '24 hours'
)
SELECT
    COALESCE(
        count(*) FILTER (WHERE event_type IN ('cache_hit','cache_l2_hit'))::float
        / NULLIF(count(*) FILTER (WHERE event_type IN
            ('cache_hit','cache_l2_hit','transcribe_success')), 0),
        0
    )::float AS cache_24h,
    COALESCE(percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms)
             FILTER (WHERE event_type='transcribe_success' AND latency_ms IS NOT NULL),
             0)::int AS p50,
    COALESCE(percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)
             FILTER (WHERE event_type='transcribe_success' AND latency_ms IS NOT NULL),
             0)::int AS p95,
    COALESCE(percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms)
             FILTER (WHERE event_type='transcribe_success' AND latency_ms IS NOT NULL),
             0)::int AS p99,
    COALESCE(
        count(*) FILTER (WHERE event_type IN
            ('error_unknown','processing_failed','rate_limited_gemini','transcribe_degraded'))::float
        / NULLIF(count(*) FILTER (WHERE event_type='transcribe_request'), 0),
        0
    )::float AS error_rate,
    count(*) FILTER (WHERE event_type='rate_limited_user')::bigint AS rate_limited
FROM e
"""


_PERF_7D_CACHE_SQL = """
SELECT COALESCE(
    count(*) FILTER (WHERE event_type IN ('cache_hit','cache_l2_hit'))::float
    / NULLIF(count(*) FILTER (WHERE event_type IN
        ('cache_hit','cache_l2_hit','transcribe_success')), 0),
    0
)::float AS cache_7d
FROM nocorny_voice.events
WHERE ts >= now() - interval '7 days'
"""


async def get_perf_section() -> Optional[PerfSection]:
    p = pool.get()
    if p is None:
        return None
    h24, h7d, errors_by_class, rejected_24h, degraded_by_reason = await asyncio.gather(
        p.fetchrow(_PERF_24H_SQL),
        p.fetchrow(_PERF_7D_CACHE_SQL),
        _grouped(p,
            "SELECT COALESCE(error_class,'unknown') AS k, count(*)::bigint AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type='error_unknown' AND ts >= now() - interval '7 days' "
            "GROUP BY 1 ORDER BY c DESC LIMIT 10"),
        _grouped(p,
            "SELECT event_type AS k, count(*)::bigint AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type LIKE 'media_rejected_%' AND ts >= now() - interval '24 hours' "
            "GROUP BY 1 ORDER BY c DESC"),
        _grouped(p,
            "SELECT COALESCE(error_class,'unknown') AS k, count(*)::bigint AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type='transcribe_degraded' AND ts >= now() - interval '7 days' "
            "GROUP BY 1 ORDER BY c DESC LIMIT 10"),
    )
    return PerfSection(
        cache_hit_rate_24h=float(h24["cache_24h"]),
        cache_hit_rate_7d=float(h7d["cache_7d"]),
        latency_p50_ms=int(h24["p50"]),
        latency_p95_ms=int(h24["p95"]),
        latency_p99_ms=int(h24["p99"]),
        error_rate_24h=float(h24["error_rate"]),
        errors_by_class=errors_by_class,
        rate_limited_24h=int(h24["rate_limited"]),
        rejected_24h=rejected_24h,
        degraded_by_reason=degraded_by_reason,
    )


# --------------------------------------------------------------------------- cost


_COST_LIFETIME_SQL = """
SELECT
    COALESCE(SUM(total_tokens), 0)::bigint            AS tk_lifetime,
    COALESCE(SUM(prompt_tokens), 0)::bigint           AS prompt_lifetime,
    COALESCE(SUM(prompt_audio_tokens), 0)::bigint     AS audio_lifetime,
    COALESCE(SUM(candidates_tokens), 0)::bigint       AS cand_lifetime,
    (COALESCE(SUM(duration_sec), 0)::float / 60)      AS min_lifetime
FROM nocorny_voice.events
WHERE event_type='transcribe_success'
"""


_COST_WINDOWED_SQL = """
WITH e AS (
    SELECT ts, total_tokens, prompt_tokens, candidates_tokens,
           prompt_audio_tokens, duration_sec
    FROM nocorny_voice.events
    WHERE event_type='transcribe_success' AND ts >= now() - interval '30 days'
)
SELECT
    COALESCE(SUM(total_tokens), 0)::bigint                                     AS tk_30d,
    COALESCE(SUM(total_tokens) FILTER (WHERE ts >= now() - interval '7 days'), 0)::bigint   AS tk_7d,
    COALESCE(SUM(total_tokens) FILTER (WHERE ts >= now() - interval '24 hours'), 0)::bigint AS tk_24h,
    COALESCE(AVG(total_tokens), 0)::float                                      AS avg_tk_30d,
    COALESCE(SUM(prompt_tokens), 0)::bigint                                    AS prompt_30d,
    COALESCE(SUM(prompt_audio_tokens), 0)::bigint                              AS audio_30d,
    COALESCE(SUM(candidates_tokens), 0)::bigint                                AS cand_30d,
    COALESCE(SUM(prompt_tokens) FILTER (WHERE ts >= now() - interval '7 days'), 0)::bigint        AS prompt_7d,
    COALESCE(SUM(prompt_audio_tokens) FILTER (WHERE ts >= now() - interval '7 days'), 0)::bigint  AS audio_7d,
    COALESCE(SUM(candidates_tokens) FILTER (WHERE ts >= now() - interval '7 days'), 0)::bigint    AS cand_7d,
    COALESCE(SUM(prompt_tokens) FILTER (WHERE ts >= now() - interval '24 hours'), 0)::bigint        AS prompt_24h,
    COALESCE(SUM(prompt_audio_tokens) FILTER (WHERE ts >= now() - interval '24 hours'), 0)::bigint  AS audio_24h,
    COALESCE(SUM(candidates_tokens) FILTER (WHERE ts >= now() - interval '24 hours'), 0)::bigint    AS cand_24h,
    COALESCE(AVG(prompt_tokens), 0)::float                                     AS avg_prompt_30d,
    COALESCE(AVG(prompt_audio_tokens), 0)::float                               AS avg_audio_30d,
    COALESCE(AVG(candidates_tokens), 0)::float                                 AS avg_cand_30d,
    (COALESCE(SUM(duration_sec), 0)::float / 60)                               AS min_30d,
    count(*) FILTER (WHERE ts >= now() - interval '1 minute')::bigint          AS rpm_now,
    count(*) FILTER (WHERE ts >= date_trunc('day', now()))::bigint             AS rpd_today
FROM e
"""


def _cost_usd(prompt_tokens: float, candidates_tokens: float,
              prompt_audio_tokens: float = 0.0) -> float:
    """Cost in USD. Audio prompt-tokens priced at the audio rate, the rest at
    text-input rate. `prompt_tokens` is the total prompt count (incl. audio)."""
    audio = max(0.0, prompt_audio_tokens)
    text = max(0.0, prompt_tokens - audio)
    return (text * PRICE_PER_1M_INPUT_TOKENS / 1e6
            + audio * PRICE_PER_1M_AUDIO_INPUT_TOKENS / 1e6
            + candidates_tokens * PRICE_PER_1M_OUTPUT_TOKENS / 1e6)


async def get_cost_section() -> Optional[CostSection]:
    p = pool.get()
    if p is None:
        return None
    lifetime, win = await asyncio.gather(
        p.fetchrow(_COST_LIFETIME_SQL),
        p.fetchrow(_COST_WINDOWED_SQL),
    )
    cost_lifetime = _cost_usd(int(lifetime["prompt_lifetime"]),
                              int(lifetime["cand_lifetime"]),
                              int(lifetime["audio_lifetime"]))
    cost_30d = _cost_usd(int(win["prompt_30d"]), int(win["cand_30d"]),
                         int(win["audio_30d"]))
    cost_7d = _cost_usd(int(win["prompt_7d"]), int(win["cand_7d"]),
                        int(win["audio_7d"]))
    cost_24h = _cost_usd(int(win["prompt_24h"]), int(win["cand_24h"]),
                         int(win["audio_24h"]))
    avg_cost_30d = _cost_usd(float(win["avg_prompt_30d"]),
                             float(win["avg_cand_30d"]),
                             float(win["avg_audio_30d"]))
    return CostSection(
        total_tokens_lifetime=int(lifetime["tk_lifetime"]),
        total_tokens_30d=int(win["tk_30d"]),
        total_tokens_7d=int(win["tk_7d"]),
        total_tokens_24h=int(win["tk_24h"]),
        avg_tokens_per_request_30d=float(win["avg_tk_30d"]),
        minutes_lifetime=float(lifetime["min_lifetime"]),
        minutes_30d=float(win["min_30d"]),
        rpm_now=int(win["rpm_now"]),
        rpd_today=int(win["rpd_today"]),
        cost_usd_lifetime=cost_lifetime,
        cost_usd_30d=cost_30d,
        cost_usd_7d=cost_7d,
        cost_usd_24h=cost_24h,
        avg_cost_usd_per_request_30d=avg_cost_30d,
        price_per_1m_input=PRICE_PER_1M_INPUT_TOKENS,
        price_per_1m_audio_input=PRICE_PER_1M_AUDIO_INPUT_TOKENS,
        price_per_1m_output=PRICE_PER_1M_OUTPUT_TOKENS,
    )


# --------------------------------------------------------------------------- subqueries


async def _top_users_in(p: asyncpg.Pool, interval: str, limit: int) -> list[TopUser]:
    rows = await p.fetch(
        "SELECT e.user_id, u.username, u.first_name, count(*)::bigint AS c "
        "FROM nocorny_voice.events e "
        "LEFT JOIN nocorny_voice.users u USING (user_id) "
        f"WHERE e.ts >= now() - interval '{interval}' "
        "GROUP BY e.user_id, u.username, u.first_name "
        "ORDER BY c DESC LIMIT $1",
        limit,
    )
    return [TopUser(r["user_id"], r["username"], r["first_name"], int(r["c"]))
            for r in rows]


async def _top_users_usage_in(p: asyncpg.Pool, interval: str, limit: int
                              ) -> list[TopUserUsage]:
    """Top users in a window with events/tokens/cost computed from transcribe_success."""
    rows = await p.fetch(
        "SELECT e.user_id, u.username, u.first_name, "
        "count(*)::bigint AS ev, "
        "COALESCE(SUM(e.total_tokens),0)::bigint AS tk, "
        "COALESCE(SUM(e.prompt_tokens),0)::bigint AS pt, "
        "COALESCE(SUM(e.prompt_audio_tokens),0)::bigint AS pa, "
        "COALESCE(SUM(e.candidates_tokens),0)::bigint AS ct "
        "FROM nocorny_voice.events e "
        "LEFT JOIN nocorny_voice.users u USING (user_id) "
        f"WHERE e.event_type='transcribe_success' AND e.ts >= now() - interval '{interval}' "
        "GROUP BY e.user_id, u.username, u.first_name "
        "ORDER BY tk DESC LIMIT $1",
        limit,
    )
    return [TopUserUsage(
        r["user_id"], r["username"], r["first_name"],
        int(r["ev"]), int(r["tk"]),
        _cost_usd(int(r["pt"]), int(r["ct"]), int(r["pa"])),
    ) for r in rows]


async def _top_users_all_page_with_usage(p: asyncpg.Pool, page: int, page_size: int
                                         ) -> list[TopUserUsage]:
    """Page through all users sorted by lifetime total_events; left-join lifetime usage."""
    rows = await p.fetch(
        "WITH page_users AS ("
        "  SELECT user_id, username, first_name, total_events "
        "  FROM nocorny_voice.users "
        "  ORDER BY total_events DESC LIMIT $1 OFFSET $2"
        "), usage AS ("
        "  SELECT e.user_id, "
        "    COALESCE(SUM(e.total_tokens),0)::bigint AS tk, "
        "    COALESCE(SUM(e.prompt_tokens),0)::bigint AS pt, "
        "    COALESCE(SUM(e.prompt_audio_tokens),0)::bigint AS pa, "
        "    COALESCE(SUM(e.candidates_tokens),0)::bigint AS ct "
        "  FROM nocorny_voice.events e "
        "  WHERE e.event_type='transcribe_success' "
        "    AND e.user_id IN (SELECT user_id FROM page_users) "
        "  GROUP BY e.user_id"
        ") "
        "SELECT p.user_id, p.username, p.first_name, p.total_events, "
        "       COALESCE(u.tk, 0) AS tk, "
        "       COALESCE(u.pt, 0) AS pt, "
        "       COALESCE(u.pa, 0) AS pa, "
        "       COALESCE(u.ct, 0) AS ct "
        "FROM page_users p LEFT JOIN usage u USING (user_id) "
        "ORDER BY p.total_events DESC",
        page_size,
        (page - 1) * page_size,
    )
    return [TopUserUsage(
        r["user_id"], r["username"], r["first_name"],
        int(r["total_events"]), int(r["tk"]),
        _cost_usd(int(r["pt"]), int(r["ct"]), int(r["pa"])),
    ) for r in rows]


async def _top_groups(p: asyncpg.Pool, limit: int) -> list[TopGroup]:
    rows = await p.fetch(
        "SELECT chat_id, chat_type, title, total_events "
        "FROM nocorny_voice.chats "
        "WHERE chat_type IN ('group','supergroup','channel') "
        "ORDER BY total_events DESC LIMIT $1",
        limit,
    )
    return [TopGroup(int(r["chat_id"]), str(r["chat_type"]),
                     r["title"], int(r["total_events"])) for r in rows]


async def _languages(p: asyncpg.Pool) -> list[CountedRow]:
    rows = await p.fetch(
        "SELECT detected_language AS k, count(DISTINCT user_id)::bigint AS c "
        "FROM nocorny_voice.events "
        "WHERE detected_language IS NOT NULL "
        "GROUP BY 1 ORDER BY c DESC"
    )
    return [CountedRow(r["k"], int(r["c"])) for r in rows]


async def _grouped(p: asyncpg.Pool, sql: str) -> list[CountedRow]:
    rows = await p.fetch(sql)
    return [CountedRow(str(r["k"]), int(r["c"])) for r in rows]


_DURATION_BUCKET_ORDER = {
    "<30s": 1, "30s-2m": 2, "2-10m": 3, "10-30m": 4, ">30m": 5, "unknown": 6,
}


async def _duration_buckets(p: asyncpg.Pool) -> list[CountedRow]:
    rows = await p.fetch("""
        SELECT
            CASE
                WHEN duration_sec IS NULL THEN 'unknown'
                WHEN duration_sec < 30 THEN '<30s'
                WHEN duration_sec < 120 THEN '30s-2m'
                WHEN duration_sec < 600 THEN '2-10m'
                WHEN duration_sec < 1800 THEN '10-30m'
                ELSE '>30m'
            END AS k,
            count(*)::bigint AS c
        FROM nocorny_voice.events
        WHERE event_type='transcribe_success'
        GROUP BY 1
    """)
    counted = [CountedRow(r["k"], int(r["c"])) for r in rows]
    counted.sort(key=lambda c: _DURATION_BUCKET_ORDER.get(c.label, 99))
    return counted
