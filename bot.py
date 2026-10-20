"""Entry point: build app, register handlers, start polling or webhook."""
from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
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
import stt_remote
from config import (
    DATABASE_URL,
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


async def _post_init(app: Application) -> None:
    await analytics.init(DATABASE_URL)
    # Only meaningful in webhook mode (Render); local polling needs no anti-sleep.
    if PORT and WEBHOOK_URL:
        keep_alive.start(WEBHOOK_URL, interval_sec=KEEP_ALIVE_INTERVAL_SEC)
        # Controlled restart before Render's 512Mi OOM kill (incident
        # 2026-08-04). Webhook mode only: locally there is no supervisor to
        # bring the process back. busy_fn counts updates Telegram already got
        # a 200 for but a handler hasn't finished yet (still queued, or waiting
        # in the fair processor) — exiting would lose those exactly like an
        # OOM kill would.
        processor = app.update_processor
        mem_guard.start(busy_fn=lambda: app.update_queue.qsize()
                        + getattr(processor, "busy_count", lambda: 0)())


async def _post_shutdown(app: Application) -> None:
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
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(15)
        .write_timeout(15)
        .connect_timeout(15)
        .pool_timeout(15)
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

    if PORT and WEBHOOK_URL:
        install_worker_route(STT_SERVER_TOKEN)
        logger.info("Starting webhook on port %s", PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=int(PORT),
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}",
        )
    else:
        logger.info("Starting polling (local mode)")
        app.run_polling()


if __name__ == "__main__":
    main()
