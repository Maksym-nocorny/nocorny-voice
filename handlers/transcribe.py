"""Voice/audio/video transcription handler — main bot logic."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import analytics
import cache
import gemini_service
import rate_limit
from config import (
    MAX_DURATION_SEC,
    MAX_FILE_SIZE_MB,
    TELEGRAM_MAX_MESSAGE_LEN,
)
from i18n import get_text
from utils.logging_setup import new_request_id, set_chat, set_user
from utils.markdown import chunk_text, escape_html

logger = logging.getLogger(__name__)

# Leave headroom for HTML escape expansion (worst-case ~10% overhead in real speech).
_RAW_CHUNK_SIZE = int(TELEGRAM_MAX_MESSAGE_LEN * 0.85)


@dataclass
class _MediaInfo:
    file_id: str
    file_unique_id: str
    file_ext: str
    mime_type: str
    duration: int
    file_size: Optional[int]


def _extract_media_info(message: Message) -> Optional[_MediaInfo]:
    if message.voice:
        m = message.voice
        return _MediaInfo(m.file_id, m.file_unique_id, ".ogg", "audio/ogg", m.duration or 0, m.file_size)
    if message.video_note:
        m = message.video_note
        return _MediaInfo(m.file_id, m.file_unique_id, ".mp4", "video/mp4", m.duration or 0, m.file_size)
    if message.audio:
        m = message.audio
        return _MediaInfo(
            m.file_id,
            m.file_unique_id,
            ".mp3",
            m.mime_type or "audio/mpeg",
            m.duration or 0,
            m.file_size,
        )
    if message.video:
        m = message.video
        return _MediaInfo(
            m.file_id,
            m.file_unique_id,
            ".mp4",
            m.mime_type or "video/mp4",
            m.duration or 0,
            m.file_size,
        )
    return None


async def _send_transcription(
    update: Update,
    user_lang: str,
    text: str,
    is_group: bool,
    *,
    status_message: Optional[Message] = None,
) -> None:
    """Send the transcription. Handles chunking for messages over the Telegram limit."""
    prefix = get_text(user_lang, "transcription_label", "")  # ends with the {} formatting

    escaped_full = escape_html(text)
    full_message = prefix + escaped_full
    if len(full_message) <= TELEGRAM_MAX_MESSAGE_LEN:
        if status_message is not None and not is_group:
            await status_message.edit_text(full_message, parse_mode="HTML")
        else:
            if status_message is not None:
                try:
                    await status_message.delete()
                except TelegramError:
                    pass
            await update.message.reply_text(full_message, parse_mode="HTML")
        return

    # Chunked path
    if status_message is not None:
        try:
            await status_message.delete()
        except TelegramError:
            pass
    chunks = chunk_text(text, _RAW_CHUNK_SIZE)
    for i, chunk in enumerate(chunks):
        body = escape_html(chunk)
        msg_text = (prefix + body) if i == 0 else body
        await update.message.reply_text(msg_text, parse_mode="HTML")


def _validation_error(info: _MediaInfo, user_lang: str) -> Optional[Tuple[str, str]]:
    """Return (error_text, log_reason) if media fails validation, else None."""
    if info.duration is not None and info.duration < 1:
        return (
            get_text(user_lang, "media_too_short"),
            f"too_short duration={info.duration}",
        )
    if info.duration and info.duration > MAX_DURATION_SEC:
        return (
            get_text(user_lang, "media_too_long", MAX_DURATION_SEC // 60),
            f"too_long duration={info.duration}",
        )
    if info.file_size and info.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        return (
            get_text(user_lang, "media_too_large", MAX_FILE_SIZE_MB),
            f"too_large size={info.file_size}",
        )
    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message:
        return

    new_request_id()
    set_user(user.id)
    set_chat(chat.id)
    t0 = time.monotonic()

    user_lang = user.language_code or "en"
    is_group = chat.type in ("group", "supergroup")
    is_voice_or_video_note = bool(update.message.voice or update.message.video_note)

    # In groups: only process voice/video_note. Silently ignore audio/video files.
    if is_group and not is_voice_or_video_note:
        return

    # Per-user rate limit
    if not rate_limit.is_allowed(user.id):
        logger.info("rate_limit_user_blocked")
        analytics.track("rate_limited_user", user=user, chat=chat)
        await update.message.reply_text(get_text(user_lang, "rate_limit_user"))
        return

    info = _extract_media_info(update.message)
    if info is None:
        analytics.track("media_rejected_unsupported", user=user, chat=chat)
        await update.message.reply_text(get_text(user_lang, "unsupported"))
        return

    # Pre-flight validation (duration, size)
    err = _validation_error(info, user_lang)
    if err:
        msg, reason = err
        logger.info("media_rejected reason=%s", reason)
        reason_key = reason.split(" ", 1)[0]   # "too_long" | "too_large"
        analytics.track(f"media_rejected_{reason_key}", user=user, chat=chat, info=info)
        await update.message.reply_text(msg)
        return

    # Cache hit short-circuit: respond immediately without touching Gemini
    cached = cache.get_transcription(info.file_unique_id)
    if cached is not None:
        logger.info("cache_hit file_unique_id=%s", info.file_unique_id)
        analytics.track(
            "cache_hit", user=user, chat=chat, info=info,
            latency_ms=int((time.monotonic() - t0) * 1000),
            detected_language=cached.detected_language,
        )
        await _send_transcription(
            update, user_lang, cached.text, is_group, status_message=None
        )
        return

    # Fresh request: download → L2 cache check → transcribe → respond
    status_message = await update.message.reply_text(get_text(user_lang, "downloading"))
    temp_path: Optional[str] = None
    try:
        new_file = await context.bot.get_file(info.file_id)
        with tempfile.NamedTemporaryFile(suffix=info.file_ext, delete=False) as f:
            temp_path = f.name
        await new_file.download_to_drive(temp_path)

        # L2: same content, different file_unique_id (e.g. forwarded between users
        # or re-uploaded after restart). SHA-256 here adds ~150-300ms for 50MB but
        # saves the entire Gemini call on hit.
        content_hash = await asyncio.to_thread(cache.hash_file, temp_path)
        l2 = await cache.get_by_hash(content_hash)
        if l2 is not None:
            logger.info("cache_l2_hit content_hash=%s", content_hash[:12])
            cache.store_transcription(info.file_unique_id, l2.text, l2.detected_language)
            analytics.track(
                "cache_l2_hit", user=user, chat=chat, info=info,
                latency_ms=int((time.monotonic() - t0) * 1000),
                detected_language=l2.detected_language,
            )
            await _send_transcription(
                update, user_lang, l2.text, is_group, status_message=status_message
            )
            return

        await status_message.edit_text(get_text(user_lang, "transcribing"))

        analytics.track("transcribe_request", user=user, chat=chat, info=info)
        try:
            result = await gemini_service.transcribe(temp_path, info.mime_type)
        except gemini_service.RateLimitedError:
            analytics.track("rate_limited_gemini", user=user, chat=chat, info=info,
                            latency_ms=int((time.monotonic() - t0) * 1000))
            await status_message.edit_text(get_text(user_lang, "rate_limit_error"))
            return
        except gemini_service.ProcessingFailedError:
            analytics.track("processing_failed", user=user, chat=chat, info=info,
                            latency_ms=int((time.monotonic() - t0) * 1000))
            await status_message.edit_text(get_text(user_lang, "processing_failed"))
            return

        cache.store_transcription(info.file_unique_id, result.text, result.detected_language)
        await cache.store_by_hash(content_hash, result.text, result.detected_language)
        analytics.track(
            "transcribe_success", user=user, chat=chat, info=info, result=result,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        await _send_transcription(
            update, user_lang, result.text, is_group, status_message=status_message
        )
    except Exception as e:
        logger.exception("transcribe_handler_error")
        analytics.track(
            "error_unknown", user=user, chat=chat, info=info,
            error_class=type(e).__name__,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        try:
            await status_message.edit_text(get_text(user_lang, "error_generic"))
        except TelegramError:
            pass
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
