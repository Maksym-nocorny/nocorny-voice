"""bot_lease: primary beats to the standby over HTTPS and reclaims the cloud when told;
standby holds the webhook only while beats are stale; nothing touches the database."""
from __future__ import annotations

import asyncio
import datetime
import logging
import signal
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from telegram import CallbackQuery, Chat, Message, Update, User, Voice
from telegram.error import BadRequest

import bot_lease


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(bot_lease, "BOT_HEARTBEAT_SEC", 0.01)
    monkeypatch.setattr(bot_lease, "BOT_STALE_SEC", 1.0)
    monkeypatch.setattr(bot_lease, "STT_SERVER_TOKEN", "tok")
    monkeypatch.setattr(bot_lease, "WEBHOOK_URL", "https://standby.test")
    monkeypatch.setattr(bot_lease, "BOT_STOP_FLAG_FILE", "")
    bot_lease.standby = bot_lease.StandbyState()
    bot_lease._beat_client = None
    bot_lease._reclaim_owed = False
    bot_lease._reclaim_reason = ""
    bot_lease._webhook_deleted_at = None
    bot_lease._local_logout_owed = False
    bot_lease._backlog_guard = None
    bot_lease._backlog_dropped = 0
    yield
    bot_lease._beat_client = None
    bot_lease._reclaim_owed = False
    bot_lease._local_logout_owed = False
    bot_lease._backlog_guard = None


def _bot():
    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    bot.delete_webhook = AsyncMock()
    bot.get_webhook_info = AsyncMock()
    return bot


def _install_standby_transport(monkeypatch, handler):
    monkeypatch.setattr(bot_lease, "_beat_client", httpx.AsyncClient(
        base_url="https://standby.test", transport=httpx.MockTransport(handler)))


async def _run_briefly(coro, seconds=0.05):
    task = asyncio.create_task(coro)
    await asyncio.sleep(seconds)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ------------------------------------------------------------ decisions

def test_standby_decision_respects_grace_after_boot():
    assert bot_lease.standby_should_hold_webhook(None, uptime_sec=10, stale_sec=180) is False
    assert bot_lease.standby_should_hold_webhook(None, uptime_sec=181, stale_sec=180) is True
    assert bot_lease.standby_should_hold_webhook(10, uptime_sec=5000, stale_sec=180) is False
    assert bot_lease.standby_should_hold_webhook(181, uptime_sec=5000, stale_sec=180) is True


def test_state_leaving_beat_counts_as_ancient():
    st = bot_lease.StandbyState()
    st.note_beat({"instance": "m1"})
    assert st.beat_age() < 1
    st.note_beat({"instance": "m1", "leaving": True})
    assert st.beat_age() > 1e8


# ------------------------------------------------------------ standby loop

@pytest.mark.asyncio
async def test_standby_takes_over_after_stale_and_steps_back_on_beat():
    bot = _bot()
    bot_lease.standby.boot -= 10            # past the grace period, no beat yet
    await _run_briefly(bot_lease.standby_loop(bot, "https://standby.test/hook"))
    bot.set_webhook.assert_awaited()
    assert bot.set_webhook.await_args.kwargs["url"] == "https://standby.test/hook"
    assert bot_lease.standby.holding and bot_lease.standby.takeovers == 1
    # primary beats again -> step back without any cloud call
    bot.set_webhook.reset_mock()
    bot_lease.standby.note_beat({"instance": "m1"})
    await _run_briefly(bot_lease.standby_loop(bot, "https://standby.test/hook"))
    assert not bot_lease.standby.holding
    bot.set_webhook.assert_not_awaited()
    bot.delete_webhook.assert_not_awaited()
    bot.get_webhook_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_standby_waits_grace_after_own_boot():
    bot = _bot()
    await _run_briefly(bot_lease.standby_loop(bot, "https://standby.test/hook"))
    bot.set_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_standby_keeps_retrying_through_logged_out():
    bot = _bot()
    bot.set_webhook = AsyncMock(side_effect=BadRequest("Logged out"))
    bot_lease.standby.boot -= 10
    await _run_briefly(bot_lease.standby_loop(bot, "https://standby.test/hook"))
    assert bot.set_webhook.await_count >= 2
    assert not bot_lease.standby.holding


# ------------------------------------------------------------ primary loop

@pytest.mark.asyncio
async def test_primary_beats_and_reclaims_only_when_standby_holds(monkeypatch):
    seen = {"beats": 0, "auth": None, "released": []}
    holding = {"v": False}

    def handler(req: httpx.Request):
        seen["beats"] += 1
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"holding": holding["v"], "uptime": 100, "busy": 0})
    _install_standby_transport(monkeypatch, handler)

    async def fake_release(reply, last_logout):
        seen["released"].append(bot_lease._reclaim_reason)
        return True, last_logout
    monkeypatch.setattr(bot_lease, "release_cloud_staged", fake_release)
    app = MagicMock(); app.running = True; app.updater.running = True
    app.bot.get_me = AsyncMock()
    await _run_briefly(bot_lease.primary_loop(app))
    assert seen["beats"] >= 2 and seen["auth"] == "Bearer tok"
    assert seen["released"] == []
    holding["v"] = True
    await _run_briefly(bot_lease.primary_loop(app), 0.08)
    assert seen["released"] and set(seen["released"]) == {"standby_holding"}
    assert not bot_lease.reclaim_owed()


@pytest.mark.asyncio
async def test_primary_reclaim_stays_owed_until_cleanup_succeeds(monkeypatch):
    """A failed clean-up (e.g. inside the gap) is retried on the next beat even
    though the standby already stepped back."""
    replies = [{"holding": True, "uptime": 100, "busy": 0}] + [{"holding": False, "uptime": 101, "busy": 0}] * 50
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(200, json=replies.pop(0)))
    attempts = []

    async def flaky(reply, last_logout):
        attempts.append(1)
        return (len(attempts) >= 3), last_logout
    monkeypatch.setattr(bot_lease, "release_cloud_staged", flaky)
    app = MagicMock(); app.running = True; app.updater.running = True
    app.bot.get_me = AsyncMock()
    await _run_briefly(bot_lease.primary_loop(app), 0.1)
    assert len(attempts) == 3 and not bot_lease.reclaim_owed()


@pytest.mark.asyncio
async def test_primary_owes_reclaim_on_conflict_and_standby_restart(monkeypatch):
    uptimes = [500, 501, 3, 4, 5, 6, 7, 8, 9, 10]
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(
        200, json={"holding": False, "uptime": uptimes.pop(0) if uptimes else 10, "busy": 0}))
    reasons = []

    async def rel(reply, last_logout):
        reasons.append(bot_lease._reclaim_reason)
        return True, last_logout
    monkeypatch.setattr(bot_lease, "release_cloud_staged", rel)
    app = MagicMock(); app.running = True; app.updater.running = True
    app.bot.get_me = AsyncMock()
    await _run_briefly(bot_lease.primary_loop(app), 0.08)
    assert "standby_restarted" in reasons
    bot_lease.note_conflict()
    assert bot_lease.reclaim_owed() and bot_lease._reclaim_reason == "conflict"


@pytest.mark.asyncio
async def test_staged_release_defers_logout_while_standby_busy(monkeypatch):
    cb = MagicMock()
    cb.delete_webhook = AsyncMock(); cb.log_out = AsyncMock()
    cb.__aenter__ = AsyncMock(return_value=cb); cb.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(bot_lease, "cloud_bot", lambda: cb)
    monkeypatch.setattr(bot_lease, "TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr(bot_lease, "BOT_RECLAIM_MIN_GAP_SEC", 0.0)
    # tick 1: webhook deleted, never a logOut on the same tick (busy=0 came before the delete)
    done, last = await bot_lease.release_cloud_staged({"busy": 0}, float("-inf"))
    assert done is False
    cb.delete_webhook.assert_awaited_once(); cb.log_out.assert_not_awaited()
    # tick 2 with in-flight work: still no logOut, webhook not deleted twice
    done, last = await bot_lease.release_cloud_staged({"busy": 2}, float("-inf"))
    assert done is False and cb.log_out.await_count == 0 and cb.delete_webhook.await_count == 1
    # tick 3: idle after the delete -> logOut
    done, last = await bot_lease.release_cloud_staged({"busy": 0}, float("-inf"))
    assert done is True and cb.log_out.await_count == 1 and bot_lease._webhook_deleted_at is None
    # a new reclaim inside the gap: delete, then deferred
    monkeypatch.setattr(bot_lease, "BOT_RECLAIM_MIN_GAP_SEC", 1000.0)
    done, _ = await bot_lease.release_cloud_staged({"busy": 0}, last)
    done, _ = await bot_lease.release_cloud_staged({"busy": 0}, last)
    assert done is False and cb.log_out.await_count == 1
    # "Logged out" anywhere = already clean
    bot_lease._webhook_deleted_at = None
    cb.delete_webhook = AsyncMock(side_effect=BadRequest("Logged out"))
    done, _ = await bot_lease.release_cloud_staged({"busy": 0}, float("-inf"))
    assert done is True and bot_lease._webhook_deleted_at is None
    # other Telegram error: not done, retried next tick
    cb.delete_webhook = AsyncMock(side_effect=BadRequest("other"))
    done, _ = await bot_lease.release_cloud_staged({"busy": 0}, float("-inf"))
    assert done is False
    # cloud mode: deleteWebhook alone settles it on one tick
    bot_lease._webhook_deleted_at = None
    cb.delete_webhook = AsyncMock()
    monkeypatch.setattr(bot_lease, "TELEGRAM_API_BASE_URL", "")
    done, _ = await bot_lease.release_cloud_staged(None, float("-inf"))
    assert done is True


@pytest.mark.asyncio
async def test_primary_survives_unreachable_standby(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("down")
    _install_standby_transport(monkeypatch, boom)
    rel = AsyncMock(return_value=(True, 0.0))
    monkeypatch.setattr(bot_lease, "release_cloud_staged", rel)
    monkeypatch.setattr(bot_lease, "TELEGRAM_API_BASE_URL", "")
    app = MagicMock(); app.running = True; app.updater.running = True
    app.bot.get_me = AsyncMock()
    await _run_briefly(bot_lease.primary_loop(app))
    rel.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreachable_standby_owes_cleanup_once_per_outage(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("down")
    _install_standby_transport(monkeypatch, boom)
    monkeypatch.setattr(bot_lease, "TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr(bot_lease, "BOT_STALE_SEC", 0.02)
    monkeypatch.setattr(bot_lease, "BOT_HEARTBEAT_SEC", 0.01)
    reasons = []

    async def rel(reply, last_logout):
        reasons.append(bot_lease._reclaim_reason)
        return True, last_logout
    monkeypatch.setattr(bot_lease, "release_cloud_staged", rel)
    app = MagicMock(); app.running = True; app.updater.running = True
    app.bot.get_me = AsyncMock()
    await _run_briefly(bot_lease.primary_loop(app), 0.15)
    assert reasons.count("standby_unreachable") == 1


@pytest.mark.asyncio
async def test_dead_api_server_hands_over_and_recovers(monkeypatch):
    bodies = []

    def handler(req):
        bodies.append(req.read())
        return httpx.Response(200, json={"holding": False, "uptime": 50, "busy": 0})
    _install_standby_transport(monkeypatch, handler)
    monkeypatch.setattr(bot_lease, "BOT_STALE_SEC", 0.02)
    monkeypatch.setattr(bot_lease, "BOT_HEARTBEAT_SEC", 0.01)
    app = MagicMock(); app.running = True; app.updater.running = True
    app.bot.get_me = AsyncMock(side_effect=RuntimeError("refused"))
    await _run_briefly(bot_lease.primary_loop(app), 0.12)
    leaving = [b for b in bodies if b'"leaving":true' in b]
    assert len(leaving) == 1                     # once, then silence
    assert bodies[-1] in leaving                 # no ordinary beats after that
    # server back: beats resume
    app.bot.get_me = AsyncMock()
    bodies.clear()
    await _run_briefly(bot_lease.primary_loop(app), 0.05)
    assert bodies and b'"leaving":false' in bodies[-1]


@pytest.mark.asyncio
async def test_stop_sends_leaving_only_if_loop_started(monkeypatch):
    bodies = []
    _install_standby_transport(monkeypatch, lambda req: (bodies.append(req.read()), httpx.Response(200, json={}))[1])
    monkeypatch.setattr(bot_lease, "BOT_INSTANCE", "m1")
    bot_lease._task = None
    await bot_lease.stop()
    assert bodies == []
    # stop() closes the client; re-arm the fake transport for the second call
    _install_standby_transport(monkeypatch, lambda req: (bodies.append(req.read()), httpx.Response(200, json={}))[1])
    bot_lease._task = asyncio.create_task(asyncio.sleep(10))
    await bot_lease.stop()
    assert bodies and b'"leaving":true' in bodies[-1]


@pytest.mark.asyncio
async def test_primary_stop_flag_sends_leaving_beat_and_stops(monkeypatch, tmp_path):
    flag = tmp_path / ".power_low"; flag.write_text("")
    monkeypatch.setattr(bot_lease, "BOT_STOP_FLAG_FILE", str(flag))
    bodies = []

    def handler(req):
        bodies.append(req.read())
        return httpx.Response(200, json={"holding": False})
    _install_standby_transport(monkeypatch, handler)
    app = MagicMock(); app.running = True
    await asyncio.wait_for(bot_lease.primary_loop(app), 1)
    app.stop_running.assert_called_once()
    assert b'"leaving":true' in bodies[-1]


@pytest.mark.asyncio
async def test_primary_before_running_uses_sigterm(monkeypatch, tmp_path):
    flag = tmp_path / ".power_low"; flag.write_text("")
    monkeypatch.setattr(bot_lease, "BOT_STOP_FLAG_FILE", str(flag))
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(200, json={"holding": False}))
    killed = []
    monkeypatch.setattr(bot_lease.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    app = MagicMock(); app.running = False
    await asyncio.wait_for(bot_lease.primary_loop(app), 1)
    app.stop_running.assert_not_called()
    assert killed and killed[0][1] == signal.SIGTERM


@pytest.mark.asyncio
async def test_primary_hands_over_when_polling_stopped(monkeypatch):
    bodies = []

    def handler(req):
        bodies.append(req.read())
        return httpx.Response(200, json={"holding": False})
    _install_standby_transport(monkeypatch, handler)
    app = MagicMock(); app.running = False
    app.updater.running = True
    app.bot.get_me = AsyncMock()

    async def flip():
        await asyncio.sleep(0.02)
        app.updater.running = False
    asyncio.create_task(flip())
    await asyncio.wait_for(bot_lease.primary_loop(app), 1)
    assert b'"leaving":true' in bodies[-1]


# ------------------------------------------------------------ cloud release

# ------------------------------------------------------------ wiring

def test_start_picks_loop_by_instance(monkeypatch):
    app = MagicMock()
    created = []
    monkeypatch.setattr(bot_lease.asyncio, "create_task", lambda coro, name=None: (created.append(name), coro.close())[0])
    monkeypatch.setattr(bot_lease, "BOT_INSTANCE", "")
    bot_lease.start(app, webhook_url="https://x"); assert created == []
    monkeypatch.setattr(bot_lease, "BOT_INSTANCE", "m1")
    bot_lease.start(app, webhook_url=""); assert created == ["bot-lease-primary"]
    monkeypatch.setattr(bot_lease, "BOT_INSTANCE", "render")
    bot_lease.start(app, webhook_url="https://x/hook"); assert created[-1] == "bot-lease-standby"
    bot_lease.start(app, webhook_url=""); assert len(created) == 2   # standby needs a URL
    bot_lease._task = None


# ------------------------------------------------------------ stale backlog after a standby watch
# 2026-11-20: the M1's own Bot API server was never logged out while the standby
# served for six hours; after the reboot it handed the bot 58 voices the standby
# had already answered, and the M1 transcribed them all again.

def _voice_update(update_id: int, sent_at: float) -> Update:
    user = User(id=42, first_name="u", is_bot=False)
    msg = Message(message_id=update_id, chat=Chat(id=42, type=Chat.PRIVATE), from_user=user,
                  date=datetime.datetime.fromtimestamp(sent_at, tz=datetime.timezone.utc),
                  voice=Voice("f", "uf", duration=5))
    return Update(update_id=update_id, message=msg)


def test_backlog_decision():
    start, age = 10_000.0, 600.0
    stale = bot_lease.is_stale_backlog
    assert stale(start + 5, start, age) is False         # sent after we came back
    assert stale(start - 120, start, age) is False       # young: a second copy beats a lost voice
    assert stale(start - 600, start, age) is False       # boundary still answered
    assert stale(start - 601, start, age) is True
    assert stale(start - 6 * 3600, start, age) is True   # 2026-11-20: six hours late


@pytest.mark.asyncio
async def test_guard_stays_off_after_a_short_restart(monkeypatch):
    """Standby not holding (restart of seconds): everything queued is answered, as before."""
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(
        200, json={"holding": False, "uptime": 100, "busy": 0, "held_since": None}))
    await bot_lease.arm_backlog_guard()
    assert bot_lease._backlog_guard is None
    assert bot_lease.backlog_drop(_voice_update(1, time.time() - 86_000)) is False


@pytest.mark.asyncio
async def test_guard_drops_what_the_standby_served_and_keeps_new(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="bot_lease")
    held = time.time() - 6 * 3600
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(
        200, json={"holding": True, "uptime": 99_000, "busy": 0, "held_since": held}))
    await bot_lease.arm_backlog_guard()
    start, held_seen = bot_lease._backlog_guard
    assert held_seen == pytest.approx(held)
    served = [_voice_update(i, held + 60 * i) for i in range(1, 6)]   # hours old, its watch
    recent = _voice_update(10, start - 120)       # two minutes old: answered
    fresh = _voice_update(11, start + 1)          # sent after we came back
    assert all(bot_lease.backlog_drop(u) for u in served)
    assert bot_lease.backlog_drop(recent) is False
    assert bot_lease.backlog_drop(fresh) is False
    assert bot_lease._backlog_dropped == 5
    assert "bot_lease_backlog_dropped" in caplog.text and "total=5" in caplog.text
    assert "standby_watch=yes" in caplog.text


@pytest.mark.asyncio
async def test_guard_arms_when_standby_restarted_during_our_absence(monkeypatch):
    """A standby that rebooted and has not taken over yet cannot vouch for a short absence."""
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(
        200, json={"holding": False, "uptime": 0, "busy": 0, "held_since": None}))
    await bot_lease.arm_backlog_guard()
    assert bot_lease._backlog_guard is not None
    start = bot_lease._backlog_guard[0]
    assert bot_lease.backlog_drop(_voice_update(1, start - 3600)) is True


@pytest.mark.asyncio
async def test_guard_with_unreachable_standby_drops_only_old(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("down")
    _install_standby_transport(monkeypatch, boom)
    await bot_lease.arm_backlog_guard()
    start, held = bot_lease._backlog_guard
    assert held is None
    assert bot_lease.backlog_drop(_voice_update(1, start - 3600)) is True
    assert bot_lease.backlog_drop(_voice_update(2, start - 60)) is False


def test_guard_ignores_button_presses():
    now = time.time()
    bot_lease._backlog_guard = (now, now - 3600)
    user = User(id=42, first_name="u", is_bot=False)
    ours = Message(message_id=1, chat=Chat(id=42, type=Chat.PRIVATE), from_user=user, text="stats",
                   date=datetime.datetime.fromtimestamp(now - 1800, tz=datetime.timezone.utc))
    press = Update(update_id=5, callback_query=CallbackQuery("q", user, "inst", message=ours,
                                                             data="stats:x"))
    assert bot_lease.backlog_drop(press) is False


@pytest.mark.asyncio
async def test_standby_reports_when_it_took_over():
    bot = _bot()
    bot_lease.standby.boot -= 10
    before = time.time()
    await _run_briefly(bot_lease.standby_loop(bot, "https://standby.test/hook"))
    first = bot_lease.standby.held_since
    assert first is not None and first >= before
    await _run_briefly(bot_lease.standby_loop(bot, "https://standby.test/hook"))
    assert bot_lease.standby.held_since == first          # re-setting keeps the take-over time
    assert bot_lease.standby.status()["held_since"] == first
    bot_lease.standby.note_beat({"instance": "m1"})       # primary back -> step back, forget it
    await _run_briefly(bot_lease.standby_loop(bot, "https://standby.test/hook"))
    assert bot_lease.standby.held_since is None


# ------------------------------------------------------------ battery hand-over: leave our own server

@pytest.mark.asyncio
async def test_stop_flag_waits_for_standby_then_owes_local_logout(monkeypatch, tmp_path):
    flag = tmp_path / ".power_low"; flag.write_text("")
    monkeypatch.setattr(bot_lease, "BOT_STOP_FLAG_FILE", str(flag))
    monkeypatch.setattr(bot_lease, "TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr(bot_lease, "BOT_HEARTBEAT_SEC", 0.1)    # wait bound 0.25 s
    replies = [{"holding": False}, {"holding": True}]
    bodies = []

    def handler(req):
        bodies.append(req.read())
        return httpx.Response(200, json=replies.pop(0) if replies else {"holding": True})
    _install_standby_transport(monkeypatch, handler)
    app = MagicMock(); app.running = True
    await asyncio.wait_for(bot_lease.primary_loop(app), 2)
    app.stop_running.assert_called_once()
    assert len(bodies) == 2 and all(b'"leaving":true' in b for b in bodies)
    assert bot_lease._local_logout_owed is True


@pytest.mark.asyncio
async def test_stop_flag_unconfirmed_or_cloud_mode_keeps_local_login(monkeypatch, tmp_path):
    flag = tmp_path / ".power_low"; flag.write_text("")
    monkeypatch.setattr(bot_lease, "BOT_STOP_FLAG_FILE", str(flag))
    monkeypatch.setattr(bot_lease, "TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081")
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(200, json={"holding": False}))
    app = MagicMock(); app.running = True
    await asyncio.wait_for(bot_lease.primary_loop(app), 1)
    app.stop_running.assert_called_once()
    assert bot_lease._local_logout_owed is False          # standby never confirmed: stay logged in
    monkeypatch.setattr(bot_lease, "TELEGRAM_API_BASE_URL", "")   # cloud mode: no own server
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(200, json={"holding": True}))
    app = MagicMock(); app.running = True
    await asyncio.wait_for(bot_lease.primary_loop(app), 1)
    assert bot_lease._local_logout_owed is False


def _local_bot_mock(left=(), log_out_exc=None):
    lb = MagicMock()
    lb.get_updates = AsyncMock(return_value=tuple(left))
    lb.log_out = AsyncMock(side_effect=log_out_exc)
    lb.__aenter__ = AsyncMock(return_value=lb); lb.__aexit__ = AsyncMock(return_value=False)
    return lb


@pytest.mark.asyncio
async def test_stop_logs_out_of_local_server_only_when_owed(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="bot_lease")
    monkeypatch.setattr(bot_lease, "BOT_INSTANCE", "m1")
    lb = _local_bot_mock(left=[1, 2, 3])
    monkeypatch.setattr(bot_lease, "local_bot", lambda: lb)
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(200, json={}))
    bot_lease._task = None
    await bot_lease.stop()                      # ordinary restart: stays logged in locally
    lb.get_updates.assert_not_awaited(); lb.log_out.assert_not_awaited()
    _install_standby_transport(monkeypatch, lambda req: httpx.Response(200, json={}))
    bot_lease._local_logout_owed = True
    await bot_lease.stop()
    assert lb.get_updates.await_args.kwargs == {"timeout": 0, "limit": 100}   # no offset
    lb.log_out.assert_awaited_once()
    assert bot_lease._local_logout_owed is False
    assert "bot_lease_local_logged_out discarded=3" in caplog.text


@pytest.mark.asyncio
async def test_local_logout_failure_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr(bot_lease, "local_bot",
                        lambda: _local_bot_mock(log_out_exc=RuntimeError("server gone")))
    assert await bot_lease.release_local_server() is False


def test_local_bot_targets_own_server(monkeypatch):
    monkeypatch.setattr(bot_lease, "TELEGRAM_API_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr(bot_lease, "TELEGRAM_BOT_TOKEN", "123:abc")
    b = bot_lease.local_bot()
    assert b.base_url.startswith("http://127.0.0.1:8081/bot") and b.local_mode is True


@pytest.mark.asyncio
async def test_guard_never_blocks_start_up(monkeypatch):
    monkeypatch.setattr(bot_lease, "WEBHOOK_URL", "")          # no standby configured
    await bot_lease.arm_backlog_guard()
    assert bot_lease._backlog_guard is None
    monkeypatch.setattr(bot_lease, "WEBHOOK_URL", "https://standby.test")

    async def broken(leaving=False):
        raise ValueError("unexpected")
    monkeypatch.setattr(bot_lease, "send_beat", broken)
    await bot_lease.arm_backlog_guard()                        # no exception: age-only guard
    assert bot_lease._backlog_guard is not None and bot_lease._backlog_guard[1] is None
