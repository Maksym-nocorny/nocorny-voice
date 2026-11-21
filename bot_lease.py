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

Backlog guard (primary, local Bot API): our server may hand over again what
the standby already answered (2026-11-20: 58 voices, transcribed a second time
six hours later). Armed before the first getUpdates when the standby served
(or cannot say) and in-process after a stale period without beats or with our
API server dead. Updates sent during the standby's watch — from its
held_since to our cloud deleteWebhook, less a clock-skew margin at each edge
(at an edge a second copy beats a loss) — are dropped whatever their age; ones
sent before it are answered (nobody did); when the standby cannot say, ones
older than BOT_BACKLOG_MAX_AGE_SEC are dropped. Every drop is logged and
counted. A restart of seconds (standby not holding) is untouched.
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
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut

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


CLOCK_SKEW_SEC = 3.0   # Telegram dates are whole seconds; both hosts run NTP


def backlog_verdict(msg_ts: float, now: float, start: Optional[float], end: Optional[float],
                    vouched: bool, max_age: float) -> Optional[str]:
    """Why an update found in our queue after a standby watch must not be
    answered — None = answer it.

    The watch runs from the standby's take-over (`start`, its held_since) to
    the moment we took the cloud back (`end`; None = not yet). Sent inside it
    → the standby served it, whatever its age (2026-11-20: 58 voices answered
    by the standby came back to us six hours later). The clock-skew margin
    NARROWS the watch: within CLOCK_SKEW_SEC of either edge an update may have
    reached only our server, and at an edge a second copy of a reply beats a
    lost voice. Sent after it → ours. Sent before it, and the standby vouches its record covers that time
    → nobody answered it (at most the ~210 s until a take-over) → answered,
    even late. The standby cannot say (unreachable, old format, just
    restarted) → age at the moment of the decision: older than `max_age` is
    dropped."""
    skew = CLOCK_SKEW_SEC
    if start is not None and msg_ts >= start + skew and (end is None or msg_ts <= end - skew):
        return "standby_watch"
    if end is not None and msg_ts > end - skew:
        return None
    if start is not None and vouched and msg_ts < start + skew:
        return None
    return "too_old" if now - msg_ts > max_age else None


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
        # Wall clock (time.time()) of the first setWebhook attempt of the take-over
        # that succeeded: the cloud can have been serving since then.
        self.held_since: Optional[float] = None
        self._trying_since: Optional[float] = None
        self.leaving_since: Optional[float] = None   # monotonic: first leaving beat of a hand-over
        self.last_set_ok: Optional[float] = None     # monotonic: last successful setWebhook
        self.takeovers = 0
        self.busy_fn = lambda: 0   # updates accepted but not finished (set by start())

    def handover_confirmed(self) -> bool:
        """A setWebhook succeeded AFTER the primary said it is leaving. `holding`
        alone is sticky (a failed re-set leaves it True), so it cannot confirm."""
        return (self.leaving_since is not None and self.last_set_ok is not None
                and self.last_set_ok >= self.leaving_since)

    def busy(self) -> int:
        try:
            return int(self.busy_fn() or 0)
        except Exception:  # noqa: BLE001
            return 0

    def note_beat(self, info: dict) -> None:
        if info.get("leaving"):
            # Primary is going away on purpose: treat its beat as ancient.
            self.last_beat = -1e9
            if self.leaving_since is None:
                self.leaving_since = time.monotonic()
        else:
            self.last_beat = time.monotonic()
            self.leaving_since = None
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
        # held_since: the primary's backlog guard drops what was sent during our
        # watch. handover_confirmed: a fresh setWebhook after its leaving beat.
        self.write({"holding": standby.holding, "uptime": round(standby.uptime()),
                    "busy": standby.busy(), "held_since": standby.held_since,
                    "handover_confirmed": standby.handover_confirmed()})


class StatusHandler(tornado.web.RequestHandler):
    """GET /lease/status — for the dispatcher's health check."""

    def get(self) -> None:
        if not _authorized(self.request.headers):
            raise tornado.web.HTTPError(401)
        self.set_header("Content-Type", "application/json")
        self.write(standby.status())


def standby_routes():
    return [(BEAT_PATH, BeatHandler), (STATUS_PATH, StatusHandler)]


def _outcome_unknown(e: Exception) -> bool:
    """A failed setWebhook that Telegram may still have carried out."""
    return isinstance(e, TimedOut) or (isinstance(e, NetworkError)
                                       and not isinstance(e, BadRequest))


async def standby_loop(bot, webhook_url: str) -> None:
    """Hold the cloud webhook only while the primary is gone; never touch the
    cloud while it is alive."""
    while True:
        want = standby_should_hold_webhook(standby.beat_age(), standby.uptime())
        if want:
            if not standby.holding:
                logger.warning("bot_lease_standby_taking_over primary_age=%s uptime=%.0f",
                               standby.beat_age() and round(standby.beat_age()), standby.uptime())
            attempt = time.time()
            try:
                await bot.set_webhook(url=webhook_url, drop_pending_updates=False)
                standby.last_set_ok = time.monotonic()
                if not standby.holding:
                    standby.takeovers += 1
                    standby.held_since = (standby._trying_since if standby._trying_since is not None
                                          else attempt)
                    logger.warning("bot_lease_standby_webhook_set takeovers=%d", standby.takeovers)
                standby.holding = True
            except TelegramError as e:
                # Inside the cloud's 10-minute lockout after the primary's
                # logOut the call fails with "Logged out": keep trying.
                logger.warning("bot_lease_standby_set_webhook_failed exc=%s", e)
                if not standby.holding and standby._trying_since is None and _outcome_unknown(e):
                    # Timed out / network: the cloud may have taken the webhook
                    # and be serving from here. A clear refusal ("Logged out",
                    # flood control) starts no watch: updates of that window
                    # reached only the primary's server.
                    standby._trying_since = attempt
        elif standby.holding:
            logger.info("bot_lease_standby_stepping_back primary_age=%.0f", standby.beat_age())
            standby.holding = False
            standby.held_since = None
        if not want:
            standby._trying_since = None
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
        data = r.json()
    except ValueError:
        return None
    # Anything but a JSON object is as good as no answer: callers .get() on it.
    return data if isinstance(data, dict) else None


# Primary bookkeeping, module level so the error handler can poke it.
_reclaim_owed = False
_reclaim_reason = ""
_local_logout_owed = False    # stop flag + standby confirmed -> logOut locally on exit


def _num(v) -> Optional[float]:
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


HOLD_MIN_AGE_SEC = 10.0   # younger than this is live traffic: never waits for a beat


class BacklogGuard:
    """What the primary knows about a standby watch whose updates our own Bot
    API server may hand over again: a local server that kept its session
    catches up what it missed once it reconnects (2026-11-20). Armed at
    start-up when the standby served or cannot say, and in-process after
    BOT_STALE_SEC without a beat or with our API server dead (the standby takes
    over meanwhile). Informed by beat replies, closed when we delete the cloud
    webhook, disarmed once the standby has released it."""

    def __init__(self) -> None:
        self.armed = False
        self.start: Optional[float] = None   # wall: the standby's held_since
        self.end: Optional[float] = None     # wall: our cloud deleteWebhook after the watch
        self.vouched = False                 # standby's record covers the time before start
        self.informed = False                # a beat reply arrived since arming
        self.opened_at: Optional[float] = None   # wall: when we learned of the watch (its end while open)
        self.dropped = 0
        self._event: Optional[asyncio.Event] = None

    def arm(self, reason: str) -> None:
        self.armed = True
        self.start = self.end = self.opened_at = None
        self.vouched = self.informed = False
        self.dropped = 0
        self._event = asyncio.Event()
        logger.warning("bot_lease_backlog_guard_armed reason=%s max_age=%.0fs",
                       reason, BOT_BACKLOG_MAX_AGE_SEC)

    def disarm(self, reason: str) -> None:
        if self.armed:
            logger.warning("bot_lease_backlog_guard_off reason=%s dropped=%d",
                           reason, self.dropped)
        self.armed = False
        if self._event is not None:
            self._event.set()      # whoever waits is answered now

    def inform(self, reply: dict, now: float) -> bool:
        """A beat reply while armed: did the standby take over, and since when?
        True = it took the webhook AFTER we had taken the cloud back: the watch
        is open again and the reclaim must start over from deleteWebhook."""
        if not self.armed:
            return False
        reopened = False
        up = _num(reply.get("uptime"))
        held = _num(reply.get("held_since"))
        if reply.get("holding"):
            if held is not None and held != self.start:
                if self.end is not None and held > self.end:
                    self.end, reopened = None, True   # our closure preceded this watch
                self.opened_at = now
                # Its record reaches back before the take-over only if it had
                # been up (hearing from us) a whole stale period first; a
                # standby that rebooted mid-outage may have had a predecessor
                # holding the webhook before it.
                self.vouched = (up is not None
                                and held - (now - up) >= BOT_STALE_SEC + 2 * BOT_HEARTBEAT_SEC)
                self.start = held
                logger.warning("bot_lease_backlog_guard_watch held_for=%.0fs vouched=%s",
                               now - held, self.vouched)
        elif up is not None and up >= BOT_STALE_SEC and (self.start is None or self.end is not None):
            # Up long enough to have taken over, yet not holding: it never
            # served, or it released the webhook after we took the cloud back.
            self.disarm("standby_released" if self.start is not None else "standby_not_serving")
            return False
        if not self.informed:
            self.informed = True
            if self._event is not None:
                self._event.set()
        return reopened

    def note_cloud_taken_back(self, now: float) -> None:
        """We deleted the cloud webhook, or found / left the cloud logged out:
        the watch ends here. The first closure counts and is never moved later
        (that would widen the watch); a later take-over reopens it (inform)."""
        if self.armed and self.end is None:
            self.end = now
            logger.info("bot_lease_backlog_guard_watch_closed")

    def verdict(self, msg_ts: float, now: float) -> Optional[str]:
        if not self.armed:
            return None
        end = self.end
        if end is None and self.opened_at is not None:
            # Not closed yet: only what was sent before we learned of the watch
            # counts as the standby's. From then on we answer — whether the
            # cloud is down or our closure is still on its way (~1 s of
            # possible second copies per return beats any window of loss).
            end = self.opened_at
        return backlog_verdict(msg_ts, now, self.start, end, self.vouched,
                               BOT_BACKLOG_MAX_AGE_SEC)

    async def wait_informed(self, timeout: float) -> None:
        ev = self._event
        if ev is None or self.informed or not self.armed:
            return
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except asyncio.TimeoutError:
            pass


_guard = BacklogGuard()


async def hand_over_to_standby() -> bool:
    """Stop flag: leaving beats until the standby confirms a setWebhook that
    succeeded after our first leaving beat (its `holding` is sticky and cannot
    confirm), at most ~2.5 beats (its loop looks once per beat). Polling goes
    on meanwhile, so the gap is served by us rather than by nobody. True = the
    standby holds the webhook: our own Bot API server may let go of the bot."""
    deadline = time.monotonic() + 2.5 * BOT_HEARTBEAT_SEC
    while True:
        reply = await send_beat(leaving=True)
        if reply is not None and reply.get("handover_confirmed") is True:
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
    answered; should the server keep any of it, the backlog guard drops it on
    our return (sent during the watch). The next request with the token (our
    next start) logs the bot in here again (the server just creates a new
    client for the token): a fresh session that does not catch up the time we
    were away. The count is best effort: its failure never skips the logOut."""
    left = None
    try:
        async with local_bot() as lb:
            try:
                left = len(await lb.get_updates(timeout=0, limit=100))   # no offset: confirms nothing
            except Exception as e:  # noqa: BLE001
                logger.info("bot_lease_local_queue_count_failed exc=%s", e)
            await lb.log_out()
    except Exception as e:  # noqa: BLE001
        logger.warning("bot_lease_local_logout_failed exc=%s", e)
        return False
    logger.warning("bot_lease_local_logged_out discarded=%s",
                   "?" if left is None else ("%d+" % left if left >= 100 else str(left)))
    return True


async def arm_backlog_guard() -> None:
    """Primary with a local Bot API server, before the first getUpdates: ask
    the standby whether it served while we were away. Not holding and up long
    enough to know (it had our beats: a restart of seconds) → guard off,
    everything queued is answered as before. Otherwise armed and informed by
    this reply — or by the next beats if the standby could not be reached.
    Never keeps the bot from starting."""
    if not WEBHOOK_URL or not STT_SERVER_TOKEN:
        logger.info("bot_lease_backlog_guard_off reason=no_standby")
        return
    try:
        reply = await send_beat()
    except Exception as e:  # noqa: BLE001 — unexpected: treat as unreachable
        logger.warning("bot_lease_backlog_guard_beat_failed exc=%s", e)
        reply = None
    try:
        up = _num((reply or {}).get("uptime"))
        if (reply is not None and not reply.get("holding")
                and up is not None and up >= BOT_STALE_SEC):
            logger.info("bot_lease_backlog_guard_off standby_holding=False")
            return
        _guard.arm("startup")
        if reply is not None:
            _guard.inform(reply, time.time())
    except Exception as e:  # noqa: BLE001
        logger.warning("bot_lease_backlog_guard_failed exc=%s", e)


def _msg_ts(update: object) -> Optional[float]:
    if getattr(update, "callback_query", None) is not None:
        return None   # a button press carries the date of OUR message, not of the press
    date = getattr(getattr(update, "effective_message", None), "date", None)
    return date.timestamp() if date is not None else None


def backlog_drop(update: object):
    """FairUpdateProcessor's drop predicate on the primary: True = do not
    answer (logged, counted). Armed but not yet informed: an update that is not
    live traffic waits (bounded) for the beat that says what the standby did —
    a coroutine is returned for the processor to await."""
    if not _guard.armed:
        return False
    ts = _msg_ts(update)
    if ts is None:
        return False
    if not _guard.informed and time.time() - ts > HOLD_MIN_AGE_SEC:
        return _drop_after_beat(update, ts)
    return _drop(update, ts)


async def _drop_after_beat(update: object, ts: float) -> bool:
    logger.info("bot_lease_backlog_held update_id=%s age=%.0fs",
                getattr(update, "update_id", "-"), time.time() - ts)
    await _guard.wait_informed(2 * BOT_HEARTBEAT_SEC)
    return _drop(update, ts)


def _drop(update: object, ts: float) -> bool:
    now = time.time()          # age at the moment of the decision
    why = _guard.verdict(ts, now)
    if why is None:
        return False
    _guard.dropped += 1
    logger.warning("bot_lease_backlog_dropped update_id=%s age=%.0fs reason=%s total=%d",
                   getattr(update, "update_id", "-"), now - ts, why, _guard.dropped)
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
                _guard.note_cloud_taken_back(time.time())   # a standby watch ends here
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
        _guard.note_cloud_taken_back(time.time())   # no-op if the deletion closed it
        return True, time.monotonic()
    except TelegramError as e:
        if "logged out" in str(e).lower():
            logger.info("bot_lease_cloud_already_logged_out reason=%s", _reclaim_reason)
            _webhook_deleted_at = None
            # The cloud cannot serve anyone: a watch still open ends now, or every
            # new voice would be dropped as the standby's with nobody serving it.
            _guard.note_cloud_taken_back(time.time())
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
    unreachable_armed = False
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
                    if uses_local_api():
                        # Our server may keep its session and catch up the
                        # standby's watch once it answers again.
                        _guard.arm("api_dead")
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
            if uses_local_api() and now - unreachable_since >= BOT_STALE_SEC \
                    and not unreachable_armed:
                # No beat for a stale period: the standby has taken over by
                # now if it is alive, and our server may catch that up later.
                _guard.arm("standby_unreachable")
                unreachable_armed = True
        else:
            unreachable_since, unreachable_owed, unreachable_armed = None, False, False
            if _guard.inform(reply, time.time()):   # before the reclaim: start, then end
                # It set the webhook after we had taken the cloud back: delete
                # it again (that also closes the reopened watch).
                _webhook_deleted_at = None
            up = _num(reply.get("uptime")) or 0.0
            if reply.get("holding"):
                _owe("standby_holding")
            elif last_uptime is not None and up < last_uptime:
                _owe("standby_restarted")     # previous process may have held it
            last_uptime = up
        if _reclaim_owed:
            done, last_logout = await release_cloud_staged(reply, last_logout)
            if done:
                _reclaim_owed = False
        # Armed but not told yet what the standby did: ask more often, held
        # updates are waiting for the answer.
        await asyncio.sleep(min(5.0, BOT_HEARTBEAT_SEC) if _guard.armed and not _guard.informed
                            else BOT_HEARTBEAT_SEC)


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
