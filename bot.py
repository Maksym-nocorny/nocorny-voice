"""Entry point: build app, register handlers, start polling or webhook."""
from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut
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
from config import (
    DATABASE_URL,
    GEMINI_API_KEY,
    PORT,
    TELEGRAM_BOT_TOKEN,
    WEBHOOK_URL,
)
from handlers.start import start
from handlers.stats import stats_callback, stats_command
from handlers.transcribe import handle_message
from utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    await analytics.init(DATABASE_URL)


async def _post_shutdown(app: Application) -> None:
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
    if isinstance(err, (TimedOut, NetworkError)):
        logger.warning("unhandled_telegram_transient class=%s msg=%s",
                       type(err).__name__, err)
        return
    if isinstance(err, Forbidden):
        logger.info("unhandled_forbidden msg=%s", err)
        return
    logger.exception("unhandled_application_error", exc_info=err)


def main() -> None:
    setup_logging()

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set in environment")
        sys.exit(1)
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment")
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
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
