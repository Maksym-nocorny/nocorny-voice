"""Formatter tests — pure rendering, no DB."""
from __future__ import annotations

from analytics import formatter
from analytics.queries import (
    AllUsersSection,
    ContentSection,
    CostSection,
    CountedRow,
    OverviewSection,
    PerfSection,
    TopGroup,
    TopUser,
    TopUserUsage,
    UsersSection,
)


def test_render_overview_with_none_returns_disabled_message():
    out = formatter.render_overview(None)
    assert "DATABASE_URL" in out


def _sample_overview():
    return OverviewSection(
        today=42, last_24h=87, last_7d=512, this_hour=3,
        dau=15, wau=63, mau=200, new_users_today=2, total_users=300,
        top_users_24h=[
            TopUser(7, "alice", "Alice", 23),
            TopUser(8, None, "Bob", 11),
        ],
        error_rate_24h=0.007,
        total_tokens_24h=1_234_567, minutes_24h=42.5,
        rpm_now=3, rpd_today=87,
        cost_usd_24h=0.1234, cost_usd_30d=2.4567,
        price_per_1m_input=0.10, price_per_1m_output=0.40,
    )


def test_render_overview_includes_key_sections():
    out = formatter.render_overview(_sample_overview())
    assert "<b>Volume</b>" in out
    assert "<b>Top users (24h)</b>" in out
    assert "<b>Cost</b>" in out
    assert "Performance" not in out
    assert "@alice" in out
    assert "Bob" in out
    assert "1.23M" in out   # tokens formatted
    assert "$0.1234" in out  # 24h cost (<$1 → 4 decimals)
    assert "$2.46" in out    # 30d cost (≥$1 → 2 decimals)
    assert "Error rate (24h)" in out
    assert "0.7%" in out     # error_rate 0.007 → 0.7%


def test_render_overview_hides_usd_when_pricing_zero():
    s = _sample_overview()
    s.price_per_1m_input = 0.0
    s.price_per_1m_output = 0.0
    s.cost_usd_24h = 0.0
    s.cost_usd_30d = 0.0
    out = formatter.render_overview(s)
    assert "USD" not in out
    assert "Tokens 24h" in out  # tokens still shown


def test_top_user_label_falls_back_to_first_name_then_user_id():
    no_username_no_name = TopUser(99, None, None, 5)
    label = formatter._user_label(no_username_no_name)
    assert "user_99" in label
    assert 'href="tg://user?id=99"' in label
    assert "<code>99</code>" in label  # copyable id

    only_first = TopUser(99, None, "Carol", 5)
    label = formatter._user_label(only_first)
    assert ">Carol<" in label
    assert "<code>99</code>" in label

    with_username = TopUser(99, "carol", "Carol", 5)
    # @username is already clickable in Telegram clients — keep it plain.
    assert formatter._user_label(with_username) == "@carol"


def test_html_special_chars_in_username_are_escaped():
    """Usernames and first_names can contain HTML-meta chars; must be escaped."""
    nasty_username = TopUser(1, "<script>", "<b>", 1)
    out = formatter._user_label(nasty_username)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out

    nasty_first_name = TopUser(2, None, "<img onerror>", 1)
    out = formatter._user_label(nasty_first_name)
    assert "<img" not in out
    assert "&lt;img onerror&gt;" in out
    assert "<code>2</code>" in out


def test_render_users_handles_empty_lists():
    s = UsersSection(
        total_users=0, dau=0, wau=0, mau=0,
        new_users_today=0, new_users_7d=0,
        top_users_30d=[],
        languages=[],
    )
    out = formatter.render_users(s)
    assert "Top users" in out
    assert "Groups using the bot" in out
    assert "(none)" in out


def test_render_users_lists_top_groups():
    s = UsersSection(
        total_users=10, dau=2, wau=5, mau=8,
        new_users_today=0, new_users_7d=1,
        top_users_30d=[],
        languages=[],
        top_groups=[
            TopGroup(chat_id=-1001234, chat_type="supergroup",
                     title="Devs UA", total_events=42),
            TopGroup(chat_id=-99, chat_type="group",
                     title=None, total_events=3),
        ],
        total_groups=2,
    )
    out = formatter.render_users(s)
    assert "Groups using the bot (2 total)" in out
    assert "Devs UA" in out
    assert "supergroup" in out
    assert "<code>-1001234</code>" in out
    # Title falls back to a chat_<id> placeholder when title is missing.
    assert "chat_-99" in out
    assert "42" in out


def test_render_users_escapes_group_titles():
    s = UsersSection(
        total_users=1, dau=1, wau=1, mau=1,
        new_users_today=0, new_users_7d=0,
        top_users_30d=[], languages=[],
        top_groups=[
            TopGroup(chat_id=-1, chat_type="supergroup",
                     title="<script>x</script>", total_events=1),
        ],
        total_groups=1,
    )
    out = formatter.render_users(s)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_users_renders_languages_with_full_names():
    s = UsersSection(
        total_users=10, dau=1, wau=1, mau=1,
        new_users_today=0, new_users_7d=0,
        top_users_30d=[],
        languages=[
            CountedRow("uk", 7),
            CountedRow("en", 3),
            CountedRow("pt-br", 1),
            CountedRow("xx", 1),  # unknown code falls through to raw
        ],
    )
    out = formatter.render_users(s)
    assert "Ukrainian: 7" in out
    assert "English: 3" in out
    assert "Portuguese (Brazil): 1" in out
    assert "xx: 1" in out  # graceful fallback


def test_render_users_merges_tokens_and_cost_when_pricing_active():
    s = UsersSection(
        total_users=1, dau=1, wau=1, mau=1,
        new_users_today=0, new_users_7d=0,
        top_users_30d=[
            TopUserUsage(7, "alice", "Alice", total_events=12, tokens=72_500, cost_usd=0.0089),
            TopUserUsage(8, None, "Bob", total_events=3, tokens=4_200, cost_usd=0.0006),
        ],
        languages=[],
        price_per_1m_input=0.10, price_per_1m_output=0.40,
    )
    out = formatter.render_users(s)
    # Header advertises the merged columns
    assert "ev · tokens · USD" in out
    assert "@alice" in out
    # Single row contains all three metrics
    assert "12 ev · 72.5K · $0.0089" in out
    # No more separate sections
    assert "by cost" not in out
    assert "by tokens" not in out


def test_render_users_hides_cost_when_pricing_zero():
    s = UsersSection(
        total_users=1, dau=1, wau=1, mau=1,
        new_users_today=0, new_users_7d=0,
        top_users_30d=[
            TopUserUsage(7, "alice", "Alice", total_events=5, tokens=1_000, cost_usd=0.0),
        ],
        languages=[],
        price_per_1m_input=0.0, price_per_1m_output=0.0,
    )
    out = formatter.render_users(s)
    assert "USD" not in out
    assert "$" not in out
    assert "5 ev · 1.0K" in out


def test_render_all_users_paginates():
    s = AllUsersSection(
        total_users=120,
        top_users_all=[
            TopUserUsage(7, "alice", "Alice", total_events=200, tokens=10_000, cost_usd=0.01),
        ],
        page=2, total_pages=3, page_size=50,
        price_per_1m_input=0.10, price_per_1m_output=0.40,
    )
    out = formatter.render_all_users(s)
    assert "all users" in out
    assert "page 2/3" in out
    # Offset numbering: page 2 starts at #51
    assert "51." in out
    assert "@alice" in out
    assert "200 ev · 10.0K · $0.0100" in out


def test_render_all_users_no_pagination_indicator_on_single_page():
    s = AllUsersSection(
        total_users=3,
        top_users_all=[
            TopUserUsage(1, "a", "A", total_events=5, tokens=100, cost_usd=0.0),
            TopUserUsage(2, "b", "B", total_events=3, tokens=50, cost_usd=0.0),
        ],
        page=1, total_pages=1, page_size=50,
        price_per_1m_input=0.0, price_per_1m_output=0.0,
    )
    out = formatter.render_all_users(s)
    assert "page" not in out.lower()
    # Header without USD when pricing is off
    assert "ev · tokens" in out
    assert "USD" not in out


def test_render_all_users_handles_empty():
    s = AllUsersSection(
        total_users=0, top_users_all=[],
        page=1, total_pages=1, page_size=50,
    )
    out = formatter.render_all_users(s)
    assert "Total users:" in out
    assert "(none)" in out


def test_render_content_with_data():
    s = ContentSection(
        media_types=[CountedRow("voice", 100), CountedRow("audio", 5)],
        chat_types=[CountedRow("private", 90), CountedRow("group", 15)],
        total_minutes_lifetime=720.0, total_minutes_30d=240.0,
        duration_buckets=[CountedRow("<30s", 50), CountedRow("30s-2m", 30)],
        avg_duration_sec=42.5, median_duration_sec=20,
    )
    out = formatter.render_content(s)
    assert "Media types" in out
    assert "voice" in out
    assert "12.0h" in out  # 720 minutes formatted as hours


def test_render_perf_includes_percentiles():
    s = PerfSection(
        cache_hit_rate_24h=0.5, cache_hit_rate_7d=0.45,
        latency_p50_ms=1000, latency_p95_ms=5000, latency_p99_ms=12000,
        error_rate_24h=0.01,
        errors_by_class=[CountedRow("ConnectionError", 3)],
        rate_limited_24h=8,
        rejected_24h=[CountedRow("media_rejected_too_long", 2)],
    )
    out = formatter.render_perf(s)
    assert "p50" in out and "1000ms" in out
    assert "p99" in out and "12000ms" in out
    assert "ConnectionError" in out


def test_render_cost_with_data():
    s = CostSection(
        total_tokens_lifetime=10_000_000, total_tokens_30d=2_000_000,
        total_tokens_24h=120_000,
        avg_tokens_per_request_30d=850.5,
        minutes_lifetime=1500.0, minutes_30d=300.0,
        rpm_now=2, rpd_today=87,
        cost_usd_lifetime=1.234, cost_usd_30d=0.2468,
        cost_usd_24h=0.012, avg_cost_usd_per_request_30d=0.000085,
        price_per_1m_input=0.10, price_per_1m_output=0.40,
    )
    out = formatter.render_cost(s)
    assert "10.00M" in out
    assert "2.00M" in out
    assert "RPD today: 87" in out
    assert "$1.23" in out      # lifetime cost >= $1 → 2 decimals
    assert "$0.2468" in out    # 30d cost < $1 → 4 decimals
    assert "0.10/1M" in out    # pricing footnote


def test_render_cost_hides_usd_when_pricing_zero():
    s = CostSection(
        total_tokens_lifetime=10_000_000, total_tokens_30d=2_000_000,
        total_tokens_24h=120_000,
        avg_tokens_per_request_30d=850.5,
        minutes_lifetime=1500.0, minutes_30d=300.0,
        rpm_now=2, rpd_today=87,
        cost_usd_lifetime=0.0, cost_usd_30d=0.0,
        cost_usd_24h=0.0, avg_cost_usd_per_request_30d=0.0,
        price_per_1m_input=0.0, price_per_1m_output=0.0,
    )
    out = formatter.render_cost(s)
    assert "Cost (USD)" not in out
    assert "10.00M" in out      # tokens still shown
