"""Entry point: build app, register handlers, start polling or webhook."""
from __future__ import annotations

import logging
import sys

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
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
from handlers.stats import stats_command
from handlers.transcribe import handle_message
from utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    await analytics.init(DATABASE_URL)


async def _post_shutdown(app: Application) -> None:
    await analytics.close()


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
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.VIDEO_NOTE | filters.AUDIO | filters.VIDEO,
            handle_message,
        )
    )

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
