"""Entry point: build app, register handlers, start polling or webhook."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import tornado.httpserver
import tornado.web
from telegram import User
from telegram.ext import ExtBot
from telegram.request import HTTPXRequest

from telegram import Update
from telegram.error import BadRequest, Conflict, Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import analytics
import bot_lease
import stt_remote
from config import (
    BOT_INSTANCE,
    BOT_USERNAME,
    DATABASE_URL,
    TELEGRAM_API_BASE_URL,
    GEMINI_API_KEY,
    KEEP_ALIVE_INTERVAL_SEC,
    MAX_CONCURRENT_PER_USER,
    MAX_CONCURRENT_TRANSCRIPTIONS,
    PORT,
    STT_SERVER_TOKEN,
    TELEGRAM_BOT_TOKEN,
    WEBHOOK_URL,
)
from handlers.start import start
from handlers.stats import stats_callback, stats_command
from handlers.transcribe import handle_message
from utils import keep_alive, mem_guard
from utils.logging_setup import setup_logging
from utils.update_processor import FairUpdateProcessor

logger = logging.getLogger(__name__)


def _webhook_mode() -> bool:
    """Webhook on Render; the primary polls even though a WEBHOOK_URL is set
    (it needs the URL only to know what the standby would use)."""
    return bool(PORT and WEBHOOK_URL) and BOT_INSTANCE != bot_lease.PRIMARY


async def _post_init(app: Application) -> None:
    await analytics.init(DATABASE_URL)
    # Primary through a local Bot API server: Telegram allows one server per
    # bot, so the cloud must be cleaned before getUpdates goes local. Mark the
    # clean-up as owed; the lease loop does it staged (webhook now, logOut
    # once the standby has nothing in flight).
    if BOT_INSTANCE == bot_lease.PRIMARY and bot_lease.uses_local_api():
        bot_lease._owe("startup")
    # Primary beats to the standby over HTTPS and cleans the cloud whenever a
    # reclaim is owed; standby sets the cloud webhook only while those beats
    # are stale. No-op when BOT_INSTANCE is unset.
    bot_lease.start(app, webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}" if WEBHOOK_URL else "")
    # First worker probe now, so the first voice after a restart is not sent
    # to Gemini just because nobody had asked the worker yet.
    await stt_remote.prime()
    # Only meaningful in webhook mode (Render); local polling needs no anti-sleep.
    if _webhook_mode():
        keep_alive.start(WEBHOOK_URL, interval_sec=KEEP_ALIVE_INTERVAL_SEC)
        # Controlled restart before Render's 512Mi OOM kill (incident
        # 2026-08-04). Webhook mode only: locally there is no supervisor to
        # bring the process back. busy_fn counts updates Telegram already got
        # a 200 for but a handler hasn't finished yet (still queued, or waiting
        # in the fair processor) — exiting would lose those exactly like an
        # OOM kill would.
        processor = app.update_processor
        mem_guard.start(busy_fn=lambda: app.update_queue.qsize()
                        + getattr(processor, "busy_count", lambda: 0)(),
                        # The standby runs under its own runner: SIGTERM takes
                        # the graceful path there, sys.exit would not.
                        exit_fn=(lambda: os.kill(os.getpid(), signal.SIGTERM))
                        if BOT_INSTANCE == bot_lease.STANDBY else None)


async def _post_shutdown(app: Application) -> None:
    await bot_lease.stop()
    await stt_remote.close()
    await mem_guard.stop()
    await keep_alive.stop()
    await analytics.close()


# BadRequest substrings that mean "the thing you tried to act on is gone" —
# user deleted the message, callback expired, etc. Benign.
_BENIGN_BADREQUEST_MARKERS = (
    "query is too old",
    "message_id_invalid",
    "message to edit not found",
    "message to be replied not found",
    "message to delete not found",
)


async def _global_error_handler(update: object,
                                context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all so leftover Telegram errors don't surface as ERROR with no handler.

    Most paths already handle their own exceptions. This exists for things like
    `query.answer()` on an expired callback in `/stats`, or a Forbidden after a
    user blocks the bot — situations where there's nothing we can do anyway.
    """
    err = context.error
    if isinstance(err, Conflict):
        # Someone holds the cloud webhook while we poll: the standby took over
        # (or died holding it). Tell the lease loop a clean-up is owed.
        logger.info("polling_conflict_handover msg=%s", err)
        bot_lease.note_conflict()
        return
    if isinstance(err, BadRequest):
        s = str(err).lower()
        if any(m in s for m in _BENIGN_BADREQUEST_MARKERS):
            logger.info("benign_telegram_badrequest msg=%s", err)
            return
        logger.warning("unhandled_badrequest msg=%s", err)
        return
    if isinstance(err, (TimedOut, NetworkError, RetryAfter)):
        logger.warning("unhandled_telegram_transient class=%s msg=%s",
                       type(err).__name__, err)
        return
    if isinstance(err, Forbidden):
        logger.info("unhandled_forbidden msg=%s", err)
        return
    logger.exception("unhandled_application_error", exc_info=err)


def install_worker_route(token: str) -> bool:
    """Add the `/stt/worker` WebSocket route to PTB's webhook server.

    PTB builds its tornado Application inside Updater.start_webhook from the
    module-level name `WebhookAppClass`; swapping that name for a subclass is
    the one seam it offers. Pinned to python-telegram-bot 22.5 in
    requirements.txt — if the seam ever moves, this logs and the bot runs
    without the worker (Gemini only), it never fails to start.
    """
    if not token:
        logger.info("stt_worker_route_disabled STT_SERVER_TOKEN not set")
        return False
    try:
        import telegram.ext._updater as updater_mod
        base = updater_mod.WebhookAppClass
    except (ImportError, AttributeError) as e:
        logger.warning("stt_worker_route_unavailable %s", e)
        return False

    spec = stt_remote.tornado_handler_spec(token)

    class WebhookAppWithWorker(base):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.add_handlers(r".*$", [spec])
            # Keep the worker's long-lived socket alive through Render's proxy.
            # tornado clamps ping_timeout to ping_interval, so set them equal.
            self.settings.setdefault("websocket_ping_interval", 30)
            self.settings.setdefault("websocket_ping_timeout", 30)
            # Inbound from the worker is JSON results only; audio goes outbound.
            self.settings.setdefault("websocket_max_message_size", 4 * 1024 * 1024)

    updater_mod.WebhookAppClass = WebhookAppWithWorker
    logger.info("stt_worker_route_installed path=%s", stt_remote.WORKER_PATH)
    return True


class QuietCloudBot(ExtBot):
    """The standby's bot: identical to ExtBot except that initialize() does
    not call getMe. Any cloud call logs the bot back into the cloud, and the
    standby must stay silent while the primary serves through its local Bot
    API server. Identity comes from the token (id) and BOT_USERNAME."""

    async def initialize(self) -> None:
        if self._initialized:
            return
        if self.rate_limiter:
            await self.rate_limiter.initialize()
        await asyncio.gather(self._request[0].initialize(), self._request[1].initialize())
        try:
            bot_id = int(str(self.token).split(":", 1)[0])
        except ValueError:
            bot_id = 0
        self._bot_user = User(id=bot_id, first_name=BOT_USERNAME, is_bot=True,
                              username=BOT_USERNAME)
        self._initialized = True


_draining = False


class _HealthHandler(tornado.web.RequestHandler):
    """200 on `/` for the keep-alive self-ping and Render's own checks."""

    def get(self) -> None:
        self.set_header("Content-Type", "text/plain")
        self.write("ok\n")


def standby_web_app(app: Application) -> tornado.web.Application:
    """The standby's own HTTP server: Telegram's webhook POST goes straight into
    PTB's update queue (same handler PTB uses), `/` answers the keep-alive, and
    the Whisper worker's socket route lives here too. Built by hand instead of
    `run_webhook` because that would call setWebhook at boot — and the standby
    must not touch the cloud API while the primary is alive."""
    from telegram.ext._utils.webhookhandler import TelegramHandler

    class DrainingTelegramHandler(TelegramHandler):
        """Once we are stopping, answer 503 so Telegram redelivers the update
        to whoever serves next instead of us acknowledging it into a queue
        that will never be drained."""

        async def post(self) -> None:
            if _draining:
                raise tornado.web.HTTPError(503, reason="stopping")
            await super().post()

    routes = [
        (rf"/{TELEGRAM_BOT_TOKEN}/?", DrainingTelegramHandler,
         {"bot": app.bot, "update_queue": app.update_queue, "secret_token": None}),
        (r"/", _HealthHandler),
    ]
    if STT_SERVER_TOKEN:
        routes.append(stt_remote.tornado_handler_spec(STT_SERVER_TOKEN))
        routes.extend(bot_lease.standby_routes())
    return tornado.web.Application(
        routes,
        websocket_ping_interval=30,
        websocket_ping_timeout=30,
        websocket_max_message_size=4 * 1024 * 1024,
        log_function=lambda handler: None,   # beats every 30 s would flood INFO
    )


async def _run_standby(app: Application) -> None:
    """Mirror of Application.run_webhook's lifecycle without the setWebhook."""
    global _draining
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    server = tornado.httpserver.HTTPServer(standby_web_app(app))
    try:
        async with app:
            if app.post_init:
                await app.post_init(app)
            server.listen(int(PORT), address="0.0.0.0")
            logger.info("standby_http_started port=%s", PORT)
            await app.start()
            try:
                await stop.wait()
            finally:
                _draining = True          # late webhook POSTs get 503 from now on
                server.stop()
                await app.stop()
                if app.post_stop:
                    await app.post_stop(app)
    finally:
        if app.post_shutdown:
            await app.post_shutdown(app)


def main() -> None:
    setup_logging()

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set in environment")
        sys.exit(1)
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment")
        sys.exit(1)

    # PTB's default read/connect timeouts (5s) are tight for Render Free —
    # a single Telegram getFile/sendMessage during a network blip surfaces as
    # TimedOut and the user sees an error reply. Bumping to 15s catches the
    # transient slowness without changing happy-path latency.
    if BOT_INSTANCE == bot_lease.STANDBY:
        # Quiet bot: no getMe at start-up (see QuietCloudBot). Timeouts as below.
        req = lambda: HTTPXRequest(read_timeout=15, write_timeout=15,  # noqa: E731
                                   connect_timeout=15, pool_timeout=15)
        builder = ApplicationBuilder().bot(
            QuietCloudBot(TELEGRAM_BOT_TOKEN, request=req(), get_updates_request=req()))
    else:
        builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN)
    if TELEGRAM_API_BASE_URL and BOT_INSTANCE == bot_lease.STANDBY:
        logger.warning("telegram_api_local_ignored — the standby must stay on the cloud API")
    elif TELEGRAM_API_BASE_URL:
        # Self-hosted Bot API server: no 20 MB getFile limit, files arrive as
        # local paths. (`local_mode` makes PTB treat file paths as local.)
        builder = (
            builder.base_url(f"{TELEGRAM_API_BASE_URL}/bot")
            .base_file_url(f"{TELEGRAM_API_BASE_URL}/file/bot")
            .local_mode(True)
        )
        logger.info("telegram_api_local base_url=%s", TELEGRAM_API_BASE_URL)
    if BOT_INSTANCE != bot_lease.STANDBY:
        builder = builder.read_timeout(15).write_timeout(15).connect_timeout(15).pool_timeout(15)
    app = (
        builder
        # Fair concurrency: one user's backlog no longer blocks everyone else
        # (2026-09-27: 11 forwarded voices held the whole bot ~3.5 min).
        .concurrent_updates(FairUpdateProcessor(MAX_CONCURRENT_TRANSCRIPTIONS,
                                                MAX_CONCURRENT_PER_USER))
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(stats_callback, pattern="^stats:"))
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.VIDEO_NOTE | filters.AUDIO | filters.VIDEO,
            handle_message,
        )
    )
    app.add_error_handler(_global_error_handler)

    if BOT_INSTANCE == bot_lease.PRIMARY and bot_lease.stop_flag_present():
        # Low-battery marker already there: the standby is serving. Do not
        # start polling (it would steal updates with no lease loop to give
        # them back); the launchd wrapper waits for the marker to go.
        logger.warning("primary_not_starting reason=stop_flag")
        sys.exit(0)
    if _webhook_mode() and BOT_INSTANCE == bot_lease.STANDBY:
        logger.info("Starting standby http on port %s (webhook only on take-over)", PORT)
        asyncio.run(_run_standby(app))
    elif _webhook_mode():
        install_worker_route(STT_SERVER_TOKEN)
        logger.info("Starting webhook on port %s instance=%s", PORT, BOT_INSTANCE or "-")
        app.run_webhook(
            listen="0.0.0.0",
            port=int(PORT),
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}",
        )
    else:
        logger.info("Starting polling instance=%s", BOT_INSTANCE or "local")
        # drop_pending_updates=False: whatever queued up during a hand-over is
        # still wanted; python-telegram-bot deletes the webhook before polling.
        app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
