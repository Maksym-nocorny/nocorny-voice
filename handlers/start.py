"""/start command handler."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

import analytics
from i18n import get_text
from utils.logging_setup import new_request_id, set_chat, set_user


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    new_request_id()
    set_user(user.id if user else None)
    set_chat(chat.id if chat else None)

    user_lang = (user.language_code if user else None) or "en"
    if update.message:
        await update.message.reply_text(get_text(user_lang, "welcome"))

    analytics.track("start_command", user=user, chat=chat)
