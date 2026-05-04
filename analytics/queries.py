"""Read path: all aggregations executed in SQL, returned as typed dataclasses.

Each section function fans out queries via asyncio.gather() — every query
acquires its own connection from the pool (asyncpg connections cannot be
shared by concurrent operations).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import asyncpg

from config import PRICE_PER_1M_INPUT_TOKENS, PRICE_PER_1M_OUTPUT_TOKENS

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
    price_per_1m_input: float = 0.0
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
    total_tokens_7d: int = 0
    cost_usd_7d: float = 0.0


# --------------------------------------------------------------------------- helpers


async def _scalar(p: asyncpg.Pool, sql: str, *args) -> int:
    val = await p.fetchval(sql, *args)
    return int(val or 0)


async def _scalar_float(p: asyncpg.Pool, sql: str, *args) -> float:
    val = await p.fetchval(sql, *args)
    return float(val or 0.0)


# --------------------------------------------------------------------------- overview


async def get_overview() -> Optional[OverviewSection]:
    p = pool.get()
    if p is None:
        return None
    cost_select = (
        f"SUM(prompt_tokens) * {PRICE_PER_1M_INPUT_TOKENS} / 1e6 + "
        f"SUM(candidates_tokens) * {PRICE_PER_1M_OUTPUT_TOKENS} / 1e6"
    )
    results = await asyncio.gather(
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE ts >= date_trunc('day', now())"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE ts >= now() - interval '24 hours'"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE ts >= now() - interval '7 days'"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE ts >= date_trunc('hour', now())"),
        _scalar(p,
            "SELECT count(DISTINCT user_id) FROM nocorny_voice.events "
            "WHERE ts >= now() - interval '1 day'"),
        _scalar(p,
            "SELECT count(DISTINCT user_id) FROM nocorny_voice.events "
            "WHERE ts >= now() - interval '7 days'"),
        _scalar(p,
            "SELECT count(DISTINCT user_id) FROM nocorny_voice.events "
            "WHERE ts >= now() - interval '30 days'"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.users "
            "WHERE first_seen_at >= date_trunc('day', now())"),
        _scalar(p, "SELECT count(*) FROM nocorny_voice.users"),
        _top_users_in(p, "1 day", limit=5),
        _error_rate(p, "24 hours"),
        _scalar(p,
            "SELECT COALESCE(SUM(total_tokens),0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '24 hours'"),
        _scalar_float(p,
            "SELECT COALESCE(SUM(duration_sec),0)::float / 60 FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '24 hours'"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '1 minute'"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= date_trunc('day', now())"),
        _scalar_float(p,
            f"SELECT COALESCE({cost_select},0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '24 hours'"),
        _scalar_float(p,
            f"SELECT COALESCE({cost_select},0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '30 days'"),
    )
    return OverviewSection(
        today=results[0],
        last_24h=results[1],
        last_7d=results[2],
        this_hour=results[3],
        dau=results[4],
        wau=results[5],
        mau=results[6],
        new_users_today=results[7],
        total_users=results[8],
        top_users_24h=results[9],
        error_rate_24h=results[10],
        total_tokens_24h=results[11],
        minutes_24h=results[12],
        rpm_now=results[13],
        rpd_today=results[14],
        cost_usd_24h=results[15],
        cost_usd_30d=results[16],
        price_per_1m_input=PRICE_PER_1M_INPUT_TOKENS,
        price_per_1m_output=PRICE_PER_1M_OUTPUT_TOKENS,
    )


# --------------------------------------------------------------------------- users


async def get_users_section() -> Optional[UsersSection]:
    p = pool.get()
    if p is None:
        return None
    results = await asyncio.gather(
        _scalar(p, "SELECT count(*) FROM nocorny_voice.users"),
        _scalar(p,
            "SELECT count(DISTINCT user_id) FROM nocorny_voice.events "
            "WHERE ts >= now() - interval '1 day'"),
        _scalar(p,
            "SELECT count(DISTINCT user_id) FROM nocorny_voice.events "
            "WHERE ts >= now() - interval '7 days'"),
        _scalar(p,
            "SELECT count(DISTINCT user_id) FROM nocorny_voice.events "
            "WHERE ts >= now() - interval '30 days'"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.users "
            "WHERE first_seen_at >= date_trunc('day', now())"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.users "
            "WHERE first_seen_at >= now() - interval '7 days'"),
        _top_users_usage_in(p, "30 days", 10),
        _languages(p),
    )
    return UsersSection(
        total_users=results[0],
        dau=results[1],
        wau=results[2],
        mau=results[3],
        new_users_today=results[4],
        new_users_7d=results[5],
        top_users_30d=results[6],
        languages=results[7],
        price_per_1m_input=PRICE_PER_1M_INPUT_TOKENS,
        price_per_1m_output=PRICE_PER_1M_OUTPUT_TOKENS,
    )


async def get_all_users_section(page: int = 1, page_size: int = 50
                                ) -> Optional[AllUsersSection]:
    p = pool.get()
    if p is None:
        return None
    total_users = await _scalar(p, "SELECT count(*) FROM nocorny_voice.users")
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
        price_per_1m_output=PRICE_PER_1M_OUTPUT_TOKENS,
    )


# --------------------------------------------------------------------------- content


async def get_content_section() -> Optional[ContentSection]:
    p = pool.get()
    if p is None:
        return None
    results = await asyncio.gather(
        _grouped(p,
            "SELECT COALESCE(media_type,'unknown') AS k, count(*) AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' "
            "GROUP BY 1 ORDER BY c DESC"),
        _grouped(p,
            "SELECT chat_type AS k, count(*) AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' "
            "GROUP BY 1 ORDER BY c DESC"),
        _scalar_float(p,
            "SELECT COALESCE(SUM(duration_sec),0)::float / 60 "
            "FROM nocorny_voice.events WHERE event_type='transcribe_success'"),
        _scalar_float(p,
            "SELECT COALESCE(SUM(duration_sec),0)::float / 60 "
            "FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '30 days'"),
        _duration_buckets(p),
        _scalar_float(p,
            "SELECT COALESCE(AVG(duration_sec),0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success'"),
        _scalar(p,
            "SELECT COALESCE(percentile_disc(0.5) WITHIN GROUP (ORDER BY duration_sec),0) "
            "FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND duration_sec IS NOT NULL"),
    )
    return ContentSection(
        media_types=results[0],
        chat_types=results[1],
        total_minutes_lifetime=results[2],
        total_minutes_30d=results[3],
        duration_buckets=results[4],
        avg_duration_sec=results[5],
        median_duration_sec=results[6],
    )


# --------------------------------------------------------------------------- perf


async def get_perf_section() -> Optional[PerfSection]:
    p = pool.get()
    if p is None:
        return None
    results = await asyncio.gather(
        _cache_hit_rate(p, "24 hours"),
        _cache_hit_rate(p, "7 days"),
        _latency_percentile(p, 0.5, "24 hours"),
        _latency_percentile(p, 0.95, "24 hours"),
        _latency_percentile(p, 0.99, "24 hours"),
        _error_rate(p, "24 hours"),
        _grouped(p,
            "SELECT COALESCE(error_class,'unknown') AS k, count(*) AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type='error_unknown' AND ts >= now() - interval '7 days' "
            "GROUP BY 1 ORDER BY c DESC LIMIT 10"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE event_type='rate_limited_user' AND ts >= now() - interval '24 hours'"),
        _grouped(p,
            "SELECT event_type AS k, count(*) AS c "
            "FROM nocorny_voice.events "
            "WHERE event_type LIKE 'media_rejected_%' AND ts >= now() - interval '24 hours' "
            "GROUP BY 1 ORDER BY c DESC"),
    )
    return PerfSection(
        cache_hit_rate_24h=results[0],
        cache_hit_rate_7d=results[1],
        latency_p50_ms=results[2],
        latency_p95_ms=results[3],
        latency_p99_ms=results[4],
        error_rate_24h=results[5],
        errors_by_class=results[6],
        rate_limited_24h=results[7],
        rejected_24h=results[8],
    )


# --------------------------------------------------------------------------- cost


async def get_cost_section() -> Optional[CostSection]:
    p = pool.get()
    if p is None:
        return None
    cost_select = (
        f"SUM(prompt_tokens) * {PRICE_PER_1M_INPUT_TOKENS} / 1e6 + "
        f"SUM(candidates_tokens) * {PRICE_PER_1M_OUTPUT_TOKENS} / 1e6"
    )
    avg_cost_select = (
        f"AVG(prompt_tokens) * {PRICE_PER_1M_INPUT_TOKENS} / 1e6 + "
        f"AVG(candidates_tokens) * {PRICE_PER_1M_OUTPUT_TOKENS} / 1e6"
    )
    results = await asyncio.gather(
        _scalar(p,
            "SELECT COALESCE(SUM(total_tokens),0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success'"),
        _scalar(p,
            "SELECT COALESCE(SUM(total_tokens),0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '30 days'"),
        _scalar(p,
            "SELECT COALESCE(SUM(total_tokens),0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '7 days'"),
        _scalar(p,
            "SELECT COALESCE(SUM(total_tokens),0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '24 hours'"),
        _scalar_float(p,
            "SELECT COALESCE(AVG(total_tokens),0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '30 days'"),
        _scalar_float(p,
            "SELECT COALESCE(SUM(duration_sec),0)::float / 60 "
            "FROM nocorny_voice.events WHERE event_type='transcribe_success'"),
        _scalar_float(p,
            "SELECT COALESCE(SUM(duration_sec),0)::float / 60 "
            "FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '30 days'"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '1 minute'"),
        _scalar(p,
            "SELECT count(*) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= date_trunc('day', now())"),
        _scalar_float(p,
            f"SELECT COALESCE({cost_select},0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success'"),
        _scalar_float(p,
            f"SELECT COALESCE({cost_select},0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '30 days'"),
        _scalar_float(p,
            f"SELECT COALESCE({cost_select},0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '7 days'"),
        _scalar_float(p,
            f"SELECT COALESCE({cost_select},0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '24 hours'"),
        _scalar_float(p,
            f"SELECT COALESCE({avg_cost_select},0) FROM nocorny_voice.events "
            "WHERE event_type='transcribe_success' AND ts >= now() - interval '30 days'"),
    )
    return CostSection(
        total_tokens_lifetime=results[0],
        total_tokens_30d=results[1],
        total_tokens_7d=results[2],
        total_tokens_24h=results[3],
        avg_tokens_per_request_30d=results[4],
        minutes_lifetime=results[5],
        minutes_30d=results[6],
        rpm_now=results[7],
        rpd_today=results[8],
        cost_usd_lifetime=results[9],
        cost_usd_30d=results[10],
        cost_usd_7d=results[11],
        cost_usd_24h=results[12],
        avg_cost_usd_per_request_30d=results[13],
        price_per_1m_input=PRICE_PER_1M_INPUT_TOKENS,
        price_per_1m_output=PRICE_PER_1M_OUTPUT_TOKENS,
    )


# --------------------------------------------------------------------------- subqueries


async def _top_users_in(p: asyncpg.Pool, interval: str, limit: int) -> list[TopUser]:
    rows = await p.fetch(
        "SELECT e.user_id, u.username, u.first_name, count(*) AS c "
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
    cost_select = (
        f"COALESCE(SUM(e.prompt_tokens),0) * {PRICE_PER_1M_INPUT_TOKENS} / 1e6 + "
        f"COALESCE(SUM(e.candidates_tokens),0) * {PRICE_PER_1M_OUTPUT_TOKENS} / 1e6"
    )
    rows = await p.fetch(
        "SELECT e.user_id, u.username, u.first_name, "
        "count(*) AS ev, "
        "COALESCE(SUM(e.total_tokens),0) AS tk, "
        f"({cost_select}) AS c "
        "FROM nocorny_voice.events e "
        "LEFT JOIN nocorny_voice.users u USING (user_id) "
        f"WHERE e.event_type='transcribe_success' AND e.ts >= now() - interval '{interval}' "
        "GROUP BY e.user_id, u.username, u.first_name "
        "ORDER BY tk DESC LIMIT $1",
        limit,
    )
    return [TopUserUsage(
        r["user_id"], r["username"], r["first_name"],
        int(r["ev"]), int(r["tk"]), float(r["c"]),
    ) for r in rows]


async def _top_users_all_page_with_usage(p: asyncpg.Pool, page: int, page_size: int
                                         ) -> list[TopUserUsage]:
    """Page through all users sorted by lifetime total_events; left-join lifetime usage."""
    cost_select = (
        f"COALESCE(SUM(e.prompt_tokens),0) * {PRICE_PER_1M_INPUT_TOKENS} / 1e6 + "
        f"COALESCE(SUM(e.candidates_tokens),0) * {PRICE_PER_1M_OUTPUT_TOKENS} / 1e6"
    )
    rows = await p.fetch(
        "WITH page_users AS ("
        "  SELECT user_id, username, first_name, total_events "
        "  FROM nocorny_voice.users "
        "  ORDER BY total_events DESC LIMIT $1 OFFSET $2"
        "), usage AS ("
        "  SELECT e.user_id, "
        "    COALESCE(SUM(e.total_tokens),0) AS tk, "
        f"   ({cost_select}) AS c "
        "  FROM nocorny_voice.events e "
        "  WHERE e.event_type='transcribe_success' "
        "    AND e.user_id IN (SELECT user_id FROM page_users) "
        "  GROUP BY e.user_id"
        ") "
        "SELECT p.user_id, p.username, p.first_name, p.total_events, "
        "       COALESCE(u.tk, 0) AS tk, COALESCE(u.c, 0) AS c "
        "FROM page_users p LEFT JOIN usage u USING (user_id) "
        "ORDER BY p.total_events DESC",
        page_size,
        (page - 1) * page_size,
    )
    return [TopUserUsage(
        r["user_id"], r["username"], r["first_name"],
        int(r["total_events"]), int(r["tk"]), float(r["c"]),
    ) for r in rows]


async def _languages(p: asyncpg.Pool) -> list[CountedRow]:
    rows = await p.fetch(
        "SELECT detected_language AS k, count(DISTINCT user_id) AS c "
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
            count(*) AS c
        FROM nocorny_voice.events
        WHERE event_type='transcribe_success'
        GROUP BY 1
    """)
    # Six rows max — sort in Python.
    counted = [CountedRow(r["k"], int(r["c"])) for r in rows]
    counted.sort(key=lambda c: _DURATION_BUCKET_ORDER.get(c.label, 99))
    return counted


async def _cache_hit_rate(p: asyncpg.Pool, interval: str) -> float:
    val = await p.fetchval(f"""
        SELECT
            COALESCE(
                count(*) FILTER (WHERE event_type IN ('cache_hit','cache_l2_hit'))::float /
                NULLIF(
                    count(*) FILTER (
                        WHERE event_type IN ('cache_hit','cache_l2_hit','transcribe_success')
                    ),
                    0
                ),
                0
            )
        FROM nocorny_voice.events
        WHERE ts >= now() - interval '{interval}'
    """)
    return float(val or 0.0)


async def _latency_percentile(p: asyncpg.Pool, q: float, interval: str) -> int:
    val = await p.fetchval(f"""
        SELECT COALESCE(
            percentile_disc($1) WITHIN GROUP (ORDER BY latency_ms),
            0
        )::int
        FROM nocorny_voice.events
        WHERE event_type='transcribe_success'
          AND latency_ms IS NOT NULL
          AND ts >= now() - interval '{interval}'
    """, q)
    return int(val or 0)


async def _error_rate(p: asyncpg.Pool, interval: str) -> float:
    val = await p.fetchval(f"""
        SELECT
            COALESCE(
                count(*) FILTER (WHERE event_type IN
                    ('error_unknown','processing_failed','rate_limited_gemini'))::float /
                NULLIF(count(*) FILTER (WHERE event_type='transcribe_request'),0),
                0
            )
        FROM nocorny_voice.events
        WHERE ts >= now() - interval '{interval}'
    """)
    return float(val or 0.0)
