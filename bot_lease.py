"""Who talks to Telegram: the self-hosted primary or the Render standby.

Telegram delivers updates either to a webhook or to whoever polls getUpdates,
never both — and a bot served through a local Bot API server must not be
touched on the cloud API at all while it runs locally (any cloud call logs it
back into the cloud; Telegram then guarantees nothing about who gets the
updates). The two instances therefore coordinate directly, primary → standby,
over HTTPS — not through the database, which must be allowed to sleep:

  primary  (BOT_INSTANCE=m1, self-hosted, polling — outbound only)
      every BOT_HEARTBEAT_SEC: POST /lease/beat to the standby with the
      shared bearer token. The reply says whether the standby holds the
      cloud webhook and how many updates it still has in flight. A reclaim
      is OWED when: the standby says holding, or getUpdates answered 409
      (cloud mode), or the standby restarted (its uptime went down — the
      previous process may have held the webhook), or the standby has been
      unreachable for BOT_STALE_SEC. The flag clears only after a successful
      clean-up: delete the cloud webhook at once, and — local Bot API mode —
      log the bot out of the cloud only once the standby reports nothing in
      flight (its in-flight replies would fail "Logged out" otherwise). At
      most one logOut per BOT_RECLAIM_MIN_GAP_SEC: each restarts the cloud's
      10-minute lockout.
  standby  (BOT_INSTANCE=render, cloud webhook)
      remembers the last beat it received. Fresh → do nothing, call nothing.
      No beat for BOT_STALE_SEC (and at least BOT_STALE_SEC since its own
      boot, so a restart never steals the bot) → set the cloud webhook,
      retrying through the "Logged out" window, and answer holding=true to
      the next beat so the primary cleans up.

BOT_STOP_FLAG_FILE (primary only): when the dispatcher's low-battery marker
appears, the primary sends leaving=true beats and keeps serving until the
standby reports it holds the cloud webhook (bounded), then stops instead of
dying with the battery mid-transcription. Once its handlers have drained it
logs the bot out of its OWN Bot API server too: a local server that stays
logged in keeps the bot on two servers ("no guarantee that it will receive
all updates") and, on the next boot, catches up everything that happened
while the box was off. The next start logs in there again from scratch.

Backlog guard (primary, local Bot API): before the first getUpdates the
primary asks the standby whether it served while we were away. If it did,
queued updates older than BOT_BACKLOG_MAX_AGE_SEC are dropped (logged,
counted) instead of being answered hours late (2026-11-20: 58 voices the
standby had already answered were transcribed again by the M1 six hours
later). A restart of seconds is untouched.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import signal
import time
from typing import Optional

import httpx
import tornado.web
from telegram import Bot
from telegram.error import TelegramError

from config import (
    BOT_BACKLOG_MAX_AGE_SEC,
    BOT_HEARTBEAT_SEC,
    BOT_INSTANCE,
    BOT_RECLAIM_MIN_GAP_SEC,
    BOT_STALE_SEC,
    BOT_STOP_FLAG_FILE,
    STT_SERVER_TOKEN,
    TELEGRAM_API_BASE_URL,
    TELEGRAM_BOT_TOKEN,
    WEBHOOK_URL,
)

logger = logging.getLogger(__name__)

PRIMARY = "m1"
STANDBY = "render"
BEAT_PATH = "/lease/beat"
STATUS_PATH = "/lease/status"

_task: Optional[asyncio.Task] = None


# ----------------------------------------------------------------- pure decisions

def standby_should_hold_webhook(beat_age_sec: Optional[float], uptime_sec: float,
                                stale_sec: Optional[float] = None) -> bool:
    """True = the standby should have the webhook set (primary gone).

    `beat_age_sec` None = no beat since this standby booted: only counts as
    'gone' once the standby itself has been up long enough for a live primary
    to have reached it."""
    if stale_sec is None:
        stale_sec = BOT_STALE_SEC
    if beat_age_sec is None:
        return uptime_sec >= stale_sec
    return beat_age_sec > stale_sec


CLOCK_SKEW_SEC = 2.0   # Telegram dates are whole seconds; both hosts run NTP


def is_stale_backlog(msg_ts: float, start_wall: float, max_age: float) -> bool:
    """True = an update queued for us during a standby watch is too old to answer.

    The queue cannot tell a duplicate (the standby answered it: on 2026-11-20
    58 of them, up to six hours old) from an update only our server got. Past
    `max_age` a reply is noise either way; inside it a second copy of a reply
    beats a lost voice, so the young ones are answered."""
    return start_wall - msg_ts > max_age


def uses_local_api() -> bool:
    return bool(TELEGRAM_API_BASE_URL)


def _authorized(headers) -> bool:
    auth = headers.get("Authorization", "")
    return bool(STT_SERVER_TOKEN) and hmac.compare_digest(
        auth.encode("utf-8", "replace"), ("Bearer " + STT_SERVER_TOKEN).encode("utf-8"))


# ----------------------------------------------------------------- standby side

class StandbyState:
    """What the standby knows: when the primary last beat, whether we hold."""

    def __init__(self) -> None:
        self.boot = time.monotonic()
        self.last_beat: Optional[float] = None
        self.last_beat_info: dict = {}
        self.holding = False
        self.held_since: Optional[float] = None   # wall clock of the take-over (time.time())
        self.takeovers = 0
        self.busy_fn = lambda: 0   # updates accepted but not finished (set by start())

    def busy(self) -> int:
        try:
            return int(self.busy_fn() or 0)
        except Exception:  # noqa: BLE001
            return 0

    def note_beat(self, info: dict) -> None:
        if info.get("leaving"):
            # Primary is going away on purpose: treat its beat as ancient.
            self.last_beat = -1e9
        else:
            self.last_beat = time.monotonic()
        self.last_beat_info = info

    def beat_age(self) -> Optional[float]:
        return None if self.last_beat is None else time.monotonic() - self.last_beat

    def uptime(self) -> float:
        return time.monotonic() - self.boot

    def status(self) -> dict:
        age = self.beat_age()
        return {"holding": self.holding, "primary_age": None if age is None else round(age, 1),
                "held_since": self.held_since,
                "uptime": round(self.uptime(), 1), "takeovers": self.takeovers,
                "busy": self.busy(), "primary": self.last_beat_info}


standby = StandbyState()


class BeatHandler(tornado.web.RequestHandler):
    """POST /lease/beat — the primary's heartbeat; reply tells it whether we hold."""

    def post(self) -> None:
        if not _authorized(self.request.headers):
            raise tornado.web.HTTPError(401)
        try:
            info = json.loads(self.request.body or b"{}")
        except ValueError:
            info = {}
        standby.note_beat(info if isinstance(info, dict) else {})
        self.set_header("Content-Type", "application/json")
        # held_since: the primary's backlog guard drops what was sent during our watch.
        self.write({"holding": standby.holding, "uptime": round(standby.uptime()),
                    "busy": standby.busy(), "held_since": standby.held_since})


class StatusHandler(tornado.web.RequestHandler):
    """GET /lease/status — for the dispatcher's health check."""

    def get(self) -> None:
        if not _authorized(self.request.headers):
            raise tornado.web.HTTPError(401)
        self.set_header("Content-Type", "application/json")
        self.write(standby.status())


def standby_routes():
    return [(BEAT_PATH, BeatHandler), (STATUS_PATH, StatusHandler)]


async def standby_loop(bot, webhook_url: str) -> None:
    """Hold the cloud webhook only while the primary is gone; never touch the
    cloud while it is alive."""
    while True:
        want = standby_should_hold_webhook(standby.beat_age(), standby.uptime())
        if want:
            if not standby.holding:
                logger.warning("bot_lease_standby_taking_over primary_age=%s uptime=%.0f",
                               standby.beat_age() and round(standby.beat_age()), standby.uptime())
            try:
                await bot.set_webhook(url=webhook_url, drop_pending_updates=False)
                if not standby.holding:
                    standby.takeovers += 1
                    standby.held_since = time.time()
                    logger.warning("bot_lease_standby_webhook_set takeovers=%d", standby.takeovers)
                standby.holding = True
            except TelegramError as e:
                # Inside the cloud's 10-minute lockout after the primary's
                # logOut the call fails with "Logged out": keep trying.
                logger.warning("bot_lease_standby_set_webhook_failed exc=%s", e)
        elif standby.holding:
            logger.info("bot_lease_standby_stepping_back primary_age=%.0f", standby.beat_age())
            standby.holding = False
            standby.held_since = None
        await asyncio.sleep(BOT_HEARTBEAT_SEC)


# ----------------------------------------------------------------- primary side

def cloud_bot() -> Bot:
    """A client that always talks to api.telegram.org, whatever app.bot uses."""
    return Bot(TELEGRAM_BOT_TOKEN)


def local_bot() -> Bot:
    """A client for OUR Bot API server, independent of app.bot (which is shut
    down by the time post_shutdown runs)."""
    return Bot(TELEGRAM_BOT_TOKEN, base_url=f"{TELEGRAM_API_BASE_URL}/bot",
               base_file_url=f"{TELEGRAM_API_BASE_URL}/file/bot", local_mode=True)


_beat_client: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    global _beat_client
    if _beat_client is None:
        _beat_client = httpx.AsyncClient(base_url=WEBHOOK_URL, timeout=httpx.Timeout(10.0))
    return _beat_client


async def send_beat(leaving: bool = False) -> Optional[dict]:
    """One heartbeat to the standby. None = standby unreachable."""
    body = {"instance": PRIMARY, "pid": os.getpid(), "local_api": uses_local_api(),
            "leaving": leaving}
    try:
        r = await _client().post(BEAT_PATH, json=body,
                                 headers={"Authorization": "Bearer " + STT_SERVER_TOKEN})
    except httpx.HTTPError as e:
        logger.info("bot_lease_beat_unreachable exc=%s", e)
        return None
    if r.status_code != 200:
        logger.warning("bot_lease_beat_rejected status=%s body=%s", r.status_code, r.text[:120])
        return None
    try:
        return r.json()
    except ValueError:
        return None


# Primary bookkeeping, module level so the error handler can poke it.
_reclaim_owed = False
_reclaim_reason = ""
_local_logout_owed = False    # stop flag + standby confirmed holding -> logOut locally on exit
_backlog_guard: Optional[tuple] = None   # (start_wall, held_since or None) once armed
_backlog_dropped = 0


async def hand_over_to_standby() -> bool:
    """Stop flag: leaving beats until the standby reports it holds the cloud
    webhook, at most ~2.5 beats (its loop looks once per beat). Polling goes on
    meanwhile, so the gap is served by us rather than by nobody. True = the
    standby holds the webhook: our own Bot API server may let go of the bot."""
    deadline = time.monotonic() + 2.5 * BOT_HEARTBEAT_SEC
    while True:
        reply = await send_beat(leaving=True)
        if reply is not None and reply.get("holding"):
            logger.warning("bot_lease_primary_handover_confirmed")
            return True
        if time.monotonic() >= deadline:
            logger.warning("bot_lease_primary_handover_unconfirmed reply=%s", reply)
            return False
        await asyncio.sleep(min(5.0, BOT_HEARTBEAT_SEC))


async def release_local_server() -> bool:
    """logOut on our own Bot API server. Telegram (tdlib/telegram-bot-api
    README): "If the bot is logged in on more than one server simultaneously,
    there is no guarantee that it will receive all updates. To move a bot from
    one local server to another you can use the method logOut to log out on the
    old server". With the bot logged out here the cloud the standby holds is
    its only server and gets every update. What is still queued locally (sent
    after the standby took over — we polled until it did) is counted and not
    answered; should the server keep any of it, the backlog guard drops what
    is older than BOT_BACKLOG_MAX_AGE_SEC on our return. The next request with the token (our next start) logs the bot
    in here again (the server just creates a new client for the token): a
    fresh session that does not catch up the time we were away."""
    try:
        async with local_bot() as lb:
            left = await lb.get_updates(timeout=0, limit=100)   # no offset: confirms nothing
            await lb.log_out()
    except Exception as e:  # noqa: BLE001
        logger.warning("bot_lease_local_logout_failed exc=%s", e)
        return False
    logger.warning("bot_lease_local_logged_out discarded=%d%s", len(left),
                   "+" if len(left) >= 100 else "")
    return True


async def arm_backlog_guard() -> None:
    """Primary with a local Bot API server, before the first getUpdates: ask
    the standby whether it served while we were away. It did (or cannot say:
    unreachable, or itself restarted too recently to have taken over yet) →
    queued updates older than BOT_BACKLOG_MAX_AGE_SEC are dropped instead of
    answered hours late. It did not and has been up long enough to know (it
    had our beats: a restart of seconds) → everything queued is answered, as
    before."""
    global _backlog_guard, _backlog_dropped
    start = time.time()
    if not WEBHOOK_URL or not STT_SERVER_TOKEN:
        _backlog_guard = None    # no standby configured: nobody served while we were away
        logger.info("bot_lease_backlog_guard_off reason=no_standby")
        return
    try:
        reply = await send_beat()
    except Exception as e:  # noqa: BLE001 — must never keep the bot from starting
        logger.info("bot_lease_backlog_guard_beat_failed exc=%s", e)
        reply = None
    try:
        standby_up = float((reply or {}).get("uptime") or 0)
    except (TypeError, ValueError):
        standby_up = 0.0
    if reply is not None and not reply.get("holding") and standby_up >= BOT_STALE_SEC:
        _backlog_guard = None
        logger.info("bot_lease_backlog_guard_off standby_holding=False")
        return
    held = reply.get("held_since") if reply is not None else None
    try:
        held = float(held) if held is not None else None
    except (TypeError, ValueError):
        held = None
    _backlog_guard, _backlog_dropped = (start, held), 0
    logger.warning("bot_lease_backlog_guard_armed standby=%s held_for=%s max_age=%.0fs",
                   "unreachable" if reply is None
                   else "holding" if reply.get("holding") else "just_restarted",
                   "-" if held is None else "%.0fs" % (start - held), BOT_BACKLOG_MAX_AGE_SEC)


def backlog_drop(update: object) -> bool:
    """FairUpdateProcessor's drop predicate on the primary: True = do not answer.
    Every drop is logged with a running total."""
    global _backlog_dropped
    guard = _backlog_guard
    if guard is None:
        return False
    if getattr(update, "callback_query", None) is not None:
        return False   # a button press carries the date of OUR message, not of the press
    msg = getattr(update, "effective_message", None)
    date = getattr(msg, "date", None)
    if date is None:
        return False
    ts = date.timestamp()
    start, held = guard
    if not is_stale_backlog(ts, start, BOT_BACKLOG_MAX_AGE_SEC):
        return False
    _backlog_dropped += 1
    # Forensics only: sent while the standby held the webhook (it most likely answered).
    watch = "unknown" if held is None else ("yes" if ts >= held - CLOCK_SKEW_SEC else "no")
    logger.warning("bot_lease_backlog_dropped update_id=%s age=%.0fs standby_watch=%s total=%d",
                   getattr(update, "update_id", "-"), start - ts, watch, _backlog_dropped)
    return True


def note_conflict() -> None:
    """Called by the error handler on getUpdates 409: someone holds the webhook."""
    global _reclaim_owed, _reclaim_reason
    if uses_local_api():
        # 409 from OUR server means a second copy polls it, not a cloud webhook:
        # cleaning the cloud would only log the bot in there for nothing.
        logger.warning("bot_lease_local_conflict — another poller on the local server?")
        return
    if not _reclaim_owed:
        logger.warning("bot_lease_primary_conflict_seen — reclaim owed")
    _reclaim_owed, _reclaim_reason = True, "conflict"


def _owe(reason: str) -> None:
    global _reclaim_owed, _reclaim_reason
    if not _reclaim_owed:
        logger.warning("bot_lease_primary_reclaim_owed reason=%s", reason)
    _reclaim_owed, _reclaim_reason = True, reason


def reclaim_owed() -> bool:
    return _reclaim_owed


def _stop(app) -> None:
    """Stop PTB from inside a task: stop_running() only works once the app is
    running; before that (we are started from post_init) a SIGTERM reaches
    the same graceful shutdown path."""
    if getattr(app, "running", False):
        app.stop_running()
    else:
        os.kill(os.getpid(), signal.SIGTERM)


_webhook_deleted_at: Optional[float] = None   # staging state of the current reclaim


async def release_cloud_staged(reply: Optional[dict], last_logout: float) -> tuple:
    """One reclaim step. Returns (done, last_logout).

    Cloud mode: deleteWebhook settles it. Local-API mode, two ticks at least:
    tick 1 deletes the webhook; a later tick logs out only once the standby
    has reported nothing in flight AFTER that deletion (an update the cloud
    handed it in between would otherwise die "Logged out"), and only once
    the logOut gap has passed. Standby unreachable: logOut after one tick."""
    global _webhook_deleted_at
    busy = None if reply is None else int(reply.get("busy") or 0)
    try:
        async with cloud_bot() as cb:
            if _webhook_deleted_at is None:
                await cb.delete_webhook(drop_pending_updates=False)
                _webhook_deleted_at = time.monotonic()
                if not uses_local_api():
                    logger.warning("bot_lease_cloud_webhook_deleted reason=%s", _reclaim_reason)
                    _webhook_deleted_at = None
                    return True, last_logout
                logger.info("bot_lease_cloud_webhook_deleted reason=%s — logOut next tick",
                            _reclaim_reason)
                return False, last_logout
            if busy:
                logger.info("bot_lease_logout_deferred standby_busy=%s", busy)
                return False, last_logout
            if time.monotonic() - last_logout < BOT_RECLAIM_MIN_GAP_SEC:
                logger.info("bot_lease_logout_deferred reason=gap")
                return False, last_logout
            await cb.log_out()
        logger.warning("bot_lease_cloud_released reason=%s logout=True", _reclaim_reason)
        _webhook_deleted_at = None
        return True, time.monotonic()
    except TelegramError as e:
        if "logged out" in str(e).lower():
            logger.info("bot_lease_cloud_already_logged_out reason=%s", _reclaim_reason)
            _webhook_deleted_at = None
            return True, last_logout
        logger.warning("bot_lease_cloud_release_failed reason=%s exc=%s", _reclaim_reason, e)
        return False, last_logout


async def _api_alive(bot) -> bool:
    """getMe against the server WE poll (local or cloud): a heartbeat must mean
    'serving', not just 'process alive' — PTB retries a dead getUpdates forever."""
    try:
        await bot.get_me(read_timeout=5, connect_timeout=5)
        return True
    except Exception as e:  # noqa: BLE001
        logger.info("bot_lease_api_probe_failed exc=%s", e)
        return False


async def primary_loop(app) -> None:
    """Beat to the standby; clean the cloud whenever a reclaim is owed."""
    global _reclaim_owed, _webhook_deleted_at, _local_logout_owed
    last_logout = float("-inf")
    polling_seen = False
    unreachable_since: Optional[float] = None
    unreachable_owed = False
    api_dead_since: Optional[float] = None
    api_leaving_sent = False
    last_uptime: Optional[float] = None
    while True:
        if BOT_STOP_FLAG_FILE and os.path.exists(BOT_STOP_FLAG_FILE):
            logger.warning("bot_lease_primary_handing_over reason=stop_flag file=%s",
                           BOT_STOP_FLAG_FILE)
            # Long absence ahead: once the standby holds the cloud, our own
            # Bot API server must let go of the bot (done in stop(), after the
            # handlers drained — any request with the token logs it back in).
            # Unconfirmed → stay logged in; the backlog guard covers the return.
            if await hand_over_to_standby() and uses_local_api():
                _local_logout_owed = True
            _stop(app)
            return
        updater = getattr(app, "updater", None)
        if updater is not None:
            if updater.running:
                polling_seen = True
            elif polling_seen:
                # Polling stopped (shutdown draining handlers): tell the
                # standby now rather than after the last transcription.
                logger.warning("bot_lease_primary_polling_stopped — handing over")
                await send_beat(leaving=True)
                return
        now = time.monotonic()
        if polling_seen and not await _api_alive(app.bot):
            # Our own API server is not answering: do not claim to serve.
            api_dead_since = api_dead_since or now
            if now - api_dead_since >= BOT_STALE_SEC:
                if not api_leaving_sent:
                    logger.warning("bot_lease_primary_api_dead_handing_over since=%.0fs",
                                   now - api_dead_since)
                    await send_beat(leaving=True)
                    api_leaving_sent = True
                    # The standby will set its webhook again: a reclaim in
                    # progress must start over from deleteWebhook.
                    _webhook_deleted_at = None
                await asyncio.sleep(BOT_HEARTBEAT_SEC)
                continue
        else:
            if api_leaving_sent:
                logger.warning("bot_lease_primary_api_back — resuming beats")
            api_dead_since, api_leaving_sent = None, False
        reply = await send_beat()
        if reply is None:
            unreachable_since = unreachable_since or now
            if uses_local_api() and now - unreachable_since >= BOT_STALE_SEC \
                    and not _reclaim_owed and not unreachable_owed:
                _owe("standby_unreachable")   # it may have died holding the webhook
                unreachable_owed = True       # once per outage, not every tick
        else:
            unreachable_since, unreachable_owed = None, False
            up = float(reply.get("uptime") or 0)
            if reply.get("holding"):
                _owe("standby_holding")
            elif last_uptime is not None and up < last_uptime:
                _owe("standby_restarted")     # previous process may have held it
            last_uptime = up
        if _reclaim_owed:
            done, last_logout = await release_cloud_staged(reply, last_logout)
            if done:
                _reclaim_owed = False
        await asyncio.sleep(BOT_HEARTBEAT_SEC)


# ----------------------------------------------------------------- lifecycle

def stop_flag_present() -> bool:
    return bool(BOT_STOP_FLAG_FILE) and os.path.exists(BOT_STOP_FLAG_FILE)


def start(app, *, webhook_url: str = "") -> None:
    """Called from post_init: pick the loop for this instance (or none)."""
    global _task
    if BOT_INSTANCE == STANDBY:
        processor = getattr(app, "update_processor", None)
        standby.busy_fn = lambda: app.update_queue.qsize() + (
            processor.busy_count() if processor is not None and hasattr(processor, "busy_count") else 0)
    if BOT_INSTANCE == PRIMARY:
        if not WEBHOOK_URL or not STT_SERVER_TOKEN:
            logger.error("bot_lease_primary_misconfigured WEBHOOK_URL/STT_SERVER_TOKEN missing — "
                         "polling without a standby")
            return
        _task = asyncio.create_task(primary_loop(app), name="bot-lease-primary")
    elif BOT_INSTANCE == STANDBY:
        if not webhook_url:
            logger.error("bot_lease_standby_no_webhook_url — standby cannot take over")
            return
        _task = asyncio.create_task(standby_loop(app.bot, webhook_url),
                                    name="bot-lease-standby")
    else:
        logger.info("bot_lease_disabled BOT_INSTANCE=%r", BOT_INSTANCE)
        return
    logger.info("bot_lease_started instance=%s heartbeat=%.0fs stale=%.0fs local_api=%s",
                BOT_INSTANCE, BOT_HEARTBEAT_SEC, BOT_STALE_SEC, uses_local_api())


async def stop() -> None:
    global _task, _beat_client, _local_logout_owed
    started = _task is not None
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None
    if BOT_INSTANCE == PRIMARY and started:
        await send_beat(leaving=True)
    if _local_logout_owed:
        # post_shutdown: polling stopped and every handler finished, so nothing
        # of ours will call the local server again before the next start.
        _local_logout_owed = False
        await release_local_server()
    if _beat_client is not None:
        await _beat_client.aclose()
        _beat_client = None
