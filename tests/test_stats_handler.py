"""Tests for /stats command — admin-only auth, rendering hookup, error path."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import analytics
from handlers import stats as stats_handler


def _make_update(user_id):
    user = SimpleNamespace(id=user_id, language_code="en")
    chat = SimpleNamespace(id=10, type="private")
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.message = msg
    return update, msg


def _make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


@pytest.fixture(autouse=True)
def _reset_cache():
    stats_handler.reset_cache()
    yield
    stats_handler.reset_cache()


async def test_non_admin_gets_no_response(monkeypatch):
    monkeypatch.setattr("handlers.stats.ADMIN_USER_ID", 999)
    update, msg = _make_update(user_id=42)
    await stats_handler.stats_command(update, _make_context())
    msg.reply_text.assert_not_called()


async def test_admin_unconfigured_gets_no_response(monkeypatch):
    monkeypatch.setattr("handlers.stats.ADMIN_USER_ID", None)
    update, msg = _make_update(user_id=42)
    await stats_handler.stats_command(update, _make_context())
    msg.reply_text.assert_not_called()


async def test_analytics_disabled_returns_friendly_message(monkeypatch):
    monkeypatch.setattr("handlers.stats.ADMIN_USER_ID", 42)
    monkeypatch.setattr(analytics, "is_enabled", lambda: False)
    update, msg = _make_update(user_id=42)
    await stats_handler.stats_command(update, _make_context())
    msg.reply_text.assert_called_once()
    sent = msg.reply_text.call_args.args[0]
    assert "DATABASE_URL" in sent


async def test_admin_overview_renders(monkeypatch):
    monkeypatch.setattr("handlers.stats.ADMIN_USER_ID", 42)
    monkeypatch.setattr(analytics, "is_enabled", lambda: True)

    fake_overview = MagicMock()
    monkeypatch.setattr(analytics, "get_overview", AsyncMock(return_value=fake_overview))
    monkeypatch.setattr(analytics, "render_overview", lambda s: "<b>OK</b>")

    update, msg = _make_update(user_id=42)
    await stats_handler.stats_command(update, _make_context())
    msg.reply_text.assert_called()
    sent = msg.reply_text.call_args
    assert sent.args[0] == "<b>OK</b>"
    assert sent.kwargs.get("parse_mode") == "HTML"


async def test_subcommand_users_dispatched(monkeypatch):
    monkeypatch.setattr("handlers.stats.ADMIN_USER_ID", 42)
    monkeypatch.setattr(analytics, "is_enabled", lambda: True)

    monkeypatch.setattr(analytics, "get_users_section", AsyncMock(return_value="data"))
    rendered = []
    monkeypatch.setattr(analytics, "render_users",
                        lambda s: (rendered.append(s) or "USERS"))

    update, msg = _make_update(user_id=42)
    await stats_handler.stats_command(update, _make_context(args=["users"]))
    assert rendered == ["data"]
    assert msg.reply_text.call_args.args[0] == "USERS"


async def test_unknown_subcommand_returns_help(monkeypatch):
    monkeypatch.setattr("handlers.stats.ADMIN_USER_ID", 42)
    monkeypatch.setattr(analytics, "is_enabled", lambda: True)
    update, msg = _make_update(user_id=42)
    await stats_handler.stats_command(update, _make_context(args=["nonsense"]))
    sent = msg.reply_text.call_args.args[0]
    assert "Unknown subcommand" in sent


async def test_query_error_returns_friendly_message(monkeypatch):
    monkeypatch.setattr("handlers.stats.ADMIN_USER_ID", 42)
    monkeypatch.setattr(analytics, "is_enabled", lambda: True)
    monkeypatch.setattr(analytics, "get_overview",
                        AsyncMock(side_effect=RuntimeError("boom")))

    update, msg = _make_update(user_id=42)
    await stats_handler.stats_command(update, _make_context())
    sent = msg.reply_text.call_args.args[0]
    assert "unavailable" in sent.lower()


async def test_overview_caches_for_5_seconds(monkeypatch):
    monkeypatch.setattr("handlers.stats.ADMIN_USER_ID", 42)
    monkeypatch.setattr(analytics, "is_enabled", lambda: True)

    call_count = {"n": 0}

    async def fake_get():
        call_count["n"] += 1
        return MagicMock()

    monkeypatch.setattr(analytics, "get_overview", fake_get)
    monkeypatch.setattr(analytics, "render_overview", lambda s: "html")

    update, msg = _make_update(user_id=42)
    await stats_handler.stats_command(update, _make_context())
    await stats_handler.stats_command(update, _make_context())
    assert call_count["n"] == 1
