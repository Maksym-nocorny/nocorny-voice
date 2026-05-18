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
from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    TelegramError,
    TimedOut,
)
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


# Telegram returns BadRequest with one of these messages when the message we're
# trying to edit/reply-to is gone. The user deleted it, or it expired. Benign —
# don't log a stack trace for this.
_MSG_GONE_MARKERS = (
    "message_id_invalid",
    "message to edit not found",
    "message to be replied not found",
    "message to delete not found",
    "message can't be edited",
)


def _is_message_gone(exc: BadRequest) -> bool:
    s = str(exc).lower()
    return any(m in s for m in _MSG_GONE_MARKERS)


async def _safe_edit(message: Optional[Message], text: str, **kwargs) -> bool:
    """Edit a status message; swallow benign 'message gone' / 'bot blocked' errors.

    Returns True on success, False if the edit was skipped because the user
    deleted the status message or blocked the bot. Other TelegramErrors
    propagate so the caller can decide.
    """
    if message is None:
        return False
    try:
        await message.edit_text(text, **kwargs)
        return True
    except BadRequest as e:
        if _is_message_gone(e):
            logger.info("status_message_gone reason=%s", e)
            return False
        raise
    except Forbidden as e:
        logger.info("status_message_forbidden reason=%s", e)
        return False


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
            edited = await _safe_edit(status_message, full_message, parse_mode="HTML")
            if edited:
                return
            # Status message was deleted by the user — fall back to a fresh reply
            # so the transcription still reaches them.
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

    # Fresh request: download → L2 cache check → transcribe → respond.
    # The initial status reply sits *outside* the main try/except: if it raises,
    # we lose analytics + can't deliver anything. Treat its two benign failure
    # modes (Telegram slow → TimedOut/NetworkError, source voice already deleted
    # → BadRequest "message to be replied not found") as recoverable: status
    # stays None and the rest of the flow proceeds; the final reply_text inside
    # _send_transcription gets its own chance to land.
    status_message: Optional[Message] = None
    try:
        status_message = await update.message.reply_text(get_text(user_lang, "downloading"))
    except BadRequest as e:
        # BadRequest must precede NetworkError (PTB makes BadRequest a
        # NetworkError subclass).
        if _is_message_gone(e):
            logger.info("transcribe_initial_status_message_gone msg=%s", e)
            analytics.track(
                "error_unknown", user=user, chat=chat, info=info,
                error_class=type(e).__name__,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return
        raise
    except (TimedOut, NetworkError) as e:
        logger.warning(
            "transcribe_initial_status_failed class=%s msg=%s",
            type(e).__name__, e,
        )
    temp_path: Optional[str] = None
    # Holds the GeminiResult once transcribe returns. Read by the catch-all
    # `except Exception` so cost is still recorded if the failure happens
    # AFTER Gemini billed us (e.g. cache.store_* or _send_transcription).
    result: Optional[gemini_service.GeminiResult] = None
    try:
        try:
            new_file = await context.bot.get_file(info.file_id)
            with tempfile.NamedTemporaryFile(suffix=info.file_ext, delete=False) as f:
                temp_path = f.name
            await new_file.download_to_drive(temp_path)
        except BadRequest as e:
            # Telegram caps getFile downloads at 20 MB. Some clients report a
            # smaller `file_size` than the actual upload (or omit it), so this
            # slips past pre-flight validation.
            if "too big" in str(e).lower():
                logger.info("media_rejected reason=too_large_telegram size=%s", info.file_size)
                analytics.track("media_rejected_too_large", user=user, chat=chat, info=info)
                await _safe_edit(
                    status_message,
                    get_text(user_lang, "media_too_large", MAX_FILE_SIZE_MB),
                )
                return
            raise

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

        await _safe_edit(status_message, get_text(user_lang, "transcribing"))

        analytics.track("transcribe_request", user=user, chat=chat, info=info)
        try:
            result = await gemini_service.transcribe(
                temp_path, info.mime_type, duration_sec=info.duration
            )
        except gemini_service.RateLimitedError:
            analytics.track("rate_limited_gemini", user=user, chat=chat, info=info,
                            latency_ms=int((time.monotonic() - t0) * 1000))
            await _safe_edit(status_message, get_text(user_lang, "rate_limit_error"))
            return
        except gemini_service.ProcessingFailedError:
            analytics.track("processing_failed", user=user, chat=chat, info=info,
                            latency_ms=int((time.monotonic() - t0) * 1000))
            await _safe_edit(status_message, get_text(user_lang, "processing_failed"))
            return
        except gemini_service.TranscriptionDegradedError as e:
            # Gemini still bills for degraded calls — surface the partial usage
            # carried by the exception so cost accounting matches the API bill.
            partial = gemini_service.GeminiResult(
                text="",
                prompt_tokens=getattr(e, "prompt_tokens", 0),
                prompt_audio_tokens=getattr(e, "prompt_audio_tokens", 0),
                candidates_tokens=getattr(e, "candidates_tokens", 0),
                total_tokens=getattr(e, "total_tokens", 0),
            )
            analytics.track("transcribe_degraded", user=user, chat=chat, info=info,
                            result=partial,
                            latency_ms=int((time.monotonic() - t0) * 1000))
            await _safe_edit(status_message, get_text(user_lang, "transcribe_degraded"))
            return

        cache.store_transcription(info.file_unique_id, result.text, result.detected_language)
        cache.fire_and_forget_store_by_hash(
            content_hash, result.text, result.detected_language
        )
        analytics.track(
            "transcribe_success", user=user, chat=chat, info=info, result=result,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        await _send_transcription(
            update, user_lang, result.text, is_group, status_message=status_message
        )
    except BadRequest as e:
        # NOTE: BadRequest must precede the (TimedOut, NetworkError) arm —
        # in python-telegram-bot, BadRequest subclasses NetworkError, so a
        # broader NetworkError catch would shadow this branch.
        # Benign 'message gone' BadRequests get logged at INFO and analytics-tracked,
        # but don't deserve a stack trace. Other BadRequests are real bugs.
        if _is_message_gone(e):
            logger.info("transcribe_status_message_gone msg=%s", e)
        else:
            logger.exception("transcribe_handler_error")
        analytics.track(
            "error_unknown", user=user, chat=chat, info=info, result=result,
            error_class=type(e).__name__,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        await _safe_edit(status_message, get_text(user_lang, "error_generic"))
    except (TimedOut, NetworkError) as e:
        # Transient: Telegram API slow/flaky (e.g. getFile timeout). The user
        # likely sees nothing happen — show a generic error and move on.
        # No traceback: the cause is on Telegram's side, not ours.
        logger.warning(
            "transcribe_telegram_transient class=%s msg=%s",
            type(e).__name__, e,
        )
        analytics.track(
            "error_unknown", user=user, chat=chat, info=info, result=result,
            error_class=type(e).__name__,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        await _safe_edit(status_message, get_text(user_lang, "error_generic"))
    except Forbidden as e:
        # User blocked the bot mid-flight. Can't reply at all; just record it.
        logger.info("transcribe_forbidden msg=%s", e)
        analytics.track(
            "error_unknown", user=user, chat=chat, info=info, result=result,
            error_class=type(e).__name__,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as e:
        logger.exception("transcribe_handler_error")
        # If we got past the transcribe() call before the failure, `result` is
        # populated and Gemini already billed us — pass it through so tokens
        # land in analytics. For pre-Gemini failures `result` is still None.
        analytics.track(
            "error_unknown", user=user, chat=chat, info=info, result=result,
            error_class=type(e).__name__,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        await _safe_edit(status_message, get_text(user_lang, "error_generic"))
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
