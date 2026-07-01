"""Handler tests with mocked Gemini service and Telegram objects."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import cache
import gemini_service
import rate_limit
from gemini_service import GeminiResult


@pytest.fixture(autouse=True)
def _reset_state():
    cache.clear_all()
    rate_limit.reset()
    yield
    cache.clear_all()
    rate_limit.reset()


def _make_voice(file_id="file_a", file_unique_id="uniq_a", duration=10, file_size=1000):
    return SimpleNamespace(
        file_id=file_id,
        file_unique_id=file_unique_id,
        duration=duration,
        file_size=file_size,
        mime_type="audio/ogg",
    )


def _make_audio_file(file_id="aud_a", file_unique_id="uniq_aud", duration=60, file_size=10000):
    return SimpleNamespace(
        file_id=file_id,
        file_unique_id=file_unique_id,
        duration=duration,
        file_size=file_size,
        mime_type="audio/mpeg",
    )


def _make_status_message():
    sent = MagicMock()
    sent.message_id = 999
    sent.edit_text = AsyncMock(return_value=sent)
    sent.delete = AsyncMock()
    return sent


def _make_message(*, message_id=100, voice=None, audio=None, video=None, video_note=None):
    status = _make_status_message()
    final_sent = MagicMock()
    final_sent.message_id = 555
    final_sent.edit_text = AsyncMock(return_value=final_sent)
    final_sent.delete = AsyncMock()

    msg = MagicMock()
    msg.message_id = message_id
    msg.voice = voice
    msg.audio = audio
    msg.video = video
    msg.video_note = video_note
    msg.reply_text = AsyncMock(side_effect=[status, final_sent, final_sent, final_sent])
    msg.edit_text = AsyncMock(return_value=msg)
    msg.delete = AsyncMock()
    return msg, status, final_sent


def _make_update_context(*, user_lang="en", chat_type="private", chat_id=42, **media):
    user = SimpleNamespace(id=7, language_code=user_lang)
    chat = SimpleNamespace(id=chat_id, type=chat_type)
    msg, status, final_sent = _make_message(**media)

    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.message = msg

    new_file = MagicMock()
    new_file.download_to_drive = AsyncMock()
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=new_file)
    context = MagicMock()
    context.bot = bot

    return update, context, status, final_sent


# --------------------------------------------------------------------- Group filter


async def test_group_audio_file_is_silently_ignored(monkeypatch):
    from handlers import transcribe as t

    transcribe_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_spy)

    update, context, _, _ = _make_update_context(
        chat_type="group", audio=_make_audio_file()
    )
    await t.handle_message(update, context)

    update.message.reply_text.assert_not_called()
    transcribe_spy.assert_not_called()


# --------------------------------------------------------------------- Validation


async def test_too_long_voice_rejected(monkeypatch):
    from handlers import transcribe as t

    transcribe_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_spy)

    update, context, _, _ = _make_update_context(
        voice=_make_voice(duration=5000)  # > 1800
    )
    await t.handle_message(update, context)

    transcribe_spy.assert_not_called()
    sent_text = update.message.reply_text.call_args.args[0]
    assert "too long" in sent_text.lower() or "30" in sent_text


async def test_too_large_voice_rejected(monkeypatch):
    from handlers import transcribe as t

    transcribe_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_spy)

    huge = 100 * 1024 * 1024  # 100 MB, well over the 20 MB default
    update, context, _, _ = _make_update_context(
        voice=_make_voice(file_size=huge)
    )
    await t.handle_message(update, context)

    transcribe_spy.assert_not_called()


async def test_telegram_file_too_big_returns_friendly_message(monkeypatch):
    """Telegram caps getFile at 20 MB; some clients under-report file_size so the
    download still fails. The handler should surface our 'too large' message
    instead of the generic error."""
    from telegram.error import BadRequest

    from handlers import transcribe as t

    transcribe_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_spy)
    track_spy = MagicMock()
    monkeypatch.setattr(t.analytics, "track", track_spy)

    # file_size below our limit so pre-flight passes; failure happens in getFile.
    update, context, status, _ = _make_update_context(
        voice=_make_voice(file_size=1000)
    )
    context.bot.get_file = AsyncMock(side_effect=BadRequest("File is too big"))

    await t.handle_message(update, context)

    transcribe_spy.assert_not_called()
    sent_text = status.edit_text.call_args.args[0]
    assert "too large" in sent_text.lower() or "20" in sent_text
    tracked_events = [c.args[0] for c in track_spy.call_args_list]
    assert "media_rejected_too_large" in tracked_events
    assert "error_unknown" not in tracked_events


# --------------------------------------------------------------------- Rate limit


async def test_per_user_rate_limit_blocks_after_budget(monkeypatch):
    from handlers import transcribe as t

    transcribe_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_spy)

    # Pre-fill rate limiter for user_id=7 to exceed budget
    for _ in range(10):
        rate_limit.is_allowed(7)

    update, context, _, _ = _make_update_context(voice=_make_voice())
    await t.handle_message(update, context)

    transcribe_spy.assert_not_called()
    sent_text = update.message.reply_text.call_args.args[0]
    assert "fast" in sent_text.lower() or "wait" in sent_text.lower()


# --------------------------------------------------------------------- Cache hit


async def test_cache_hit_skips_gemini_and_responds(monkeypatch):
    from handlers import transcribe as t

    transcribe_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_spy)

    cache.store_transcription("uniq_a", "previously transcribed text", "en")
    update, context, _, _ = _make_update_context(voice=_make_voice())

    await t.handle_message(update, context)

    transcribe_spy.assert_not_called()
    sent_call = update.message.reply_text.call_args
    assert "previously transcribed text" in sent_call.args[0]
    assert sent_call.kwargs.get("parse_mode") == "HTML"


# --------------------------------------------------------------------- Successful flow


async def test_private_voice_transcription_full_flow(monkeypatch):
    from handlers import transcribe as t

    fake_result = GeminiResult(text="hello world", prompt_tokens=10, candidates_tokens=5, total_tokens=15)
    transcribe_mock = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_mock)

    update, context, status, _ = _make_update_context(voice=_make_voice())
    await t.handle_message(update, context)

    transcribe_mock.assert_awaited_once()
    # Status was edited (Downloading → Transcribing → final transcription)
    assert status.edit_text.await_count >= 2
    final_call = status.edit_text.await_args_list[-1]
    assert "hello world" in final_call.args[0]
    assert final_call.kwargs.get("parse_mode") == "HTML"
    # No reply_markup since the summarize feature has been removed
    assert final_call.kwargs.get("reply_markup") is None

    cached = cache.get_transcription("uniq_a")
    assert cached is not None
    assert cached.text == "hello world"


async def test_partial_chunked_emits_extra_degraded_event(monkeypatch):
    """When Gemini returns a partially-degraded chunked result (some chunks
    blanked by RECITATION but the rest succeeded), the user still gets text
    but /stats must see it as degraded — otherwise error_rate hides it."""
    import analytics
    from handlers import transcribe as t

    partial_result = GeminiResult(
        text="part 1\n[фрагмент 2: не вдалося розпізнати]",
        prompt_tokens=100, candidates_tokens=20, total_tokens=120,
        prompt_audio_tokens=80, degraded_chunks=1,
    )
    monkeypatch.setattr(gemini_service, "transcribe",
                        AsyncMock(return_value=partial_result))

    tracked: list[tuple[str, dict]] = []
    monkeypatch.setattr(analytics, "track",
                        lambda et, **kw: tracked.append((et, kw)))

    update, context, _status, _ = _make_update_context(voice=_make_voice(duration=300))
    await t.handle_message(update, context)

    event_types = [et for et, _ in tracked]
    assert "transcribe_success" in event_types
    assert "transcribe_degraded" in event_types
    # The success carries the full token count; the degraded marker carries
    # zero tokens so we don't double-bill cost reporting.
    degraded_kw = next(kw for et, kw in tracked if et == "transcribe_degraded")
    assert degraded_kw["error_class"] == "partial_chunks"
    assert degraded_kw["result"].total_tokens == 0


async def test_fully_successful_chunked_does_not_emit_degraded(monkeypatch):
    """Counterpart: a chunked transcribe with `degraded_chunks == 0` must NOT
    log a spurious transcribe_degraded event."""
    import analytics
    from handlers import transcribe as t

    full = GeminiResult(
        text="all good", prompt_tokens=10, candidates_tokens=5, total_tokens=15,
        degraded_chunks=0,
    )
    monkeypatch.setattr(gemini_service, "transcribe", AsyncMock(return_value=full))

    tracked: list[str] = []
    monkeypatch.setattr(analytics, "track",
                        lambda et, **kw: tracked.append(et))

    update, context, _status, _ = _make_update_context(voice=_make_voice())
    await t.handle_message(update, context)

    assert "transcribe_success" in tracked
    assert "transcribe_degraded" not in tracked


async def test_group_voice_replies_without_button(monkeypatch):
    from handlers import transcribe as t

    fake_result = GeminiResult(text="group hello", prompt_tokens=1, candidates_tokens=1, total_tokens=2)
    monkeypatch.setattr(gemini_service, "transcribe", AsyncMock(return_value=fake_result))

    update, context, status, _ = _make_update_context(
        chat_type="group", voice=_make_voice()
    )
    await t.handle_message(update, context)

    # In groups, status is deleted and a new reply is sent — never with a button
    status.delete.assert_awaited()
    reply_calls = update.message.reply_text.await_args_list
    last_reply = reply_calls[-1]
    assert "group hello" in last_reply.args[0]
    assert last_reply.kwargs.get("reply_markup") is None


async def test_rate_limited_error_shows_friendly_message(monkeypatch):
    from handlers import transcribe as t

    monkeypatch.setattr(
        gemini_service,
        "transcribe",
        AsyncMock(side_effect=gemini_service.RateLimitedError("quota")),
    )

    update, context, status, _ = _make_update_context(voice=_make_voice())
    await t.handle_message(update, context)

    final_text = status.edit_text.await_args_list[-1].args[0]
    assert "busy" in final_text.lower() or "wait" in final_text.lower()
    assert cache.get_transcription("uniq_a") is None


# --------------------------------------------------------------------- Resilience: status message gone


async def test_status_message_deleted_falls_back_to_reply(monkeypatch):
    """If user deletes the status message before we can edit it with the
    transcription, fall back to update.message.reply_text so they still see
    the result. No ERROR-level log/traceback."""
    from telegram.error import BadRequest

    from handlers import transcribe as t

    fake_result = GeminiResult(
        text="recovered", prompt_tokens=1, candidates_tokens=1, total_tokens=2,
    )
    monkeypatch.setattr(gemini_service, "transcribe",
                        AsyncMock(return_value=fake_result))

    update, context, status, _ = _make_update_context(voice=_make_voice())
    # Every edit on the status message fails — user deleted it.
    status.edit_text = AsyncMock(side_effect=BadRequest("Message_id_invalid"))

    await t.handle_message(update, context)

    # Final transcription was sent via a fresh reply_text (status came from the
    # FIRST reply_text call; the transcription is a subsequent one).
    reply_calls = [c for c in update.message.reply_text.await_args_list
                   if "recovered" in c.args[0]]
    assert reply_calls, "transcription should be delivered via reply_text fallback"


async def test_telegram_timeout_logs_warning_no_error(monkeypatch, caplog):
    """getFile timeouts are transient; should be WARNING + friendly status,
    not an unhandled exception with stack trace."""
    import logging

    from telegram.error import TimedOut

    from handlers import transcribe as t

    transcribe_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_spy)
    track_spy = MagicMock()
    monkeypatch.setattr(t.analytics, "track", track_spy)

    update, context, status, _ = _make_update_context(voice=_make_voice())
    context.bot.get_file = AsyncMock(side_effect=TimedOut())

    with caplog.at_level(logging.DEBUG, logger="handlers.transcribe"):
        await t.handle_message(update, context)

    transcribe_spy.assert_not_called()
    # No ERROR-level log from this handler — only WARNING for transient.
    transcribe_errors = [
        r for r in caplog.records
        if r.name == "handlers.transcribe" and r.levelno >= logging.ERROR
    ]
    assert not transcribe_errors, (
        f"unexpected ERROR-level log: {[r.getMessage() for r in transcribe_errors]}"
    )
    # error_unknown is still tracked so the metric is honest.
    tracked = [c.args[0] for c in track_spy.call_args_list]
    assert "error_unknown" in tracked


async def test_telegram_download_retries_once_on_timeout(monkeypatch):
    """A single transient TimedOut on get_file should be retried — the second
    attempt usually succeeds, so the user gets their transcription instead of
    a generic error. Without this, ~1 in ~200 requests would land on 'error'
    purely because of a Telegram flake."""
    from telegram.error import TimedOut

    from handlers import transcribe as t

    new_file = MagicMock()
    new_file.download_to_drive = AsyncMock()
    # First get_file call times out; second succeeds.
    bot_get_file = AsyncMock(side_effect=[TimedOut(), new_file])

    fake_result = GeminiResult(
        text="recovered", prompt_tokens=1, candidates_tokens=1, total_tokens=2,
    )
    monkeypatch.setattr(gemini_service, "transcribe",
                        AsyncMock(return_value=fake_result))
    # Skip the real 1s sleep between attempts.
    monkeypatch.setattr(t.asyncio, "sleep", AsyncMock())

    update, context, status, _ = _make_update_context(voice=_make_voice())
    context.bot.get_file = bot_get_file

    await t.handle_message(update, context)

    assert bot_get_file.await_count == 2
    final_text = status.edit_text.await_args_list[-1].args[0]
    assert "recovered" in final_text


async def test_initial_status_reply_timeout_does_not_kill_transcription(monkeypatch, caplog):
    """If the initial 'Downloading…' reply_text times out (Telegram slow on
    that chat), we still proceed with transcription and deliver via final
    reply_text. The transient is logged at WARNING, not ERROR."""
    import logging

    from telegram.error import TimedOut

    from handlers import transcribe as t

    fake_result = GeminiResult(
        text="hello", prompt_tokens=1, candidates_tokens=1, total_tokens=2,
    )
    monkeypatch.setattr(gemini_service, "transcribe",
                        AsyncMock(return_value=fake_result))

    update, context, _, final_sent = _make_update_context(voice=_make_voice())
    # First reply_text (status) times out; subsequent reply_text (final
    # transcription) succeeds. side_effect must yield exception then a value.
    update.message.reply_text = AsyncMock(side_effect=[TimedOut(), final_sent])

    with caplog.at_level(logging.DEBUG, logger="handlers.transcribe"):
        await t.handle_message(update, context)

    # Transcription was delivered via the second reply_text call.
    assert update.message.reply_text.await_count >= 2
    delivered = update.message.reply_text.await_args_list[-1].args[0]
    assert "hello" in delivered
    # No ERROR-level log from the handler.
    handler_errors = [
        r for r in caplog.records
        if r.name == "handlers.transcribe" and r.levelno >= logging.ERROR
    ]
    assert not handler_errors


async def test_initial_status_reply_message_gone_aborts_with_analytics(monkeypatch, caplog):
    """If the source voice was deleted before our initial status reply could
    land, Telegram returns BadRequest 'message to be replied not found'. We
    can't deliver anything — abort early but still track error_unknown."""
    import logging

    from telegram.error import BadRequest

    from handlers import transcribe as t

    transcribe_spy = AsyncMock()
    monkeypatch.setattr(gemini_service, "transcribe", transcribe_spy)
    track_spy = MagicMock()
    monkeypatch.setattr(t.analytics, "track", track_spy)

    update, context, _, _ = _make_update_context(voice=_make_voice())
    update.message.reply_text = AsyncMock(
        side_effect=BadRequest("Message to be replied not found")
    )

    with caplog.at_level(logging.DEBUG, logger="handlers.transcribe"):
        await t.handle_message(update, context)

    transcribe_spy.assert_not_called()
    # error_unknown was tracked for honest metrics.
    tracked = [c.args[0] for c in track_spy.call_args_list]
    assert "error_unknown" in tracked
    handler_errors = [
        r for r in caplog.records
        if r.name == "handlers.transcribe" and r.levelno >= logging.ERROR
    ]
    assert not handler_errors


async def test_safe_edit_swallows_retry_after_after_one_retry(monkeypatch):
    """Telegram flood-control on editMessageText must NOT bubble up — sleep
    the suggested interval (capped) and retry once. If the second try also
    429s, return False without raising. Production scenario: user sends a
    second voice message right after a long chunked reply finished."""
    from telegram.error import RetryAfter

    from handlers.transcribe import _safe_edit

    slept: list[float] = []

    async def _fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("handlers.transcribe.asyncio.sleep", _fake_sleep)

    # First call 429s, second succeeds.
    m = MagicMock()
    m.edit_text = AsyncMock(side_effect=[RetryAfter(5), None])
    result = await _safe_edit(m, "transcribing")
    assert result is True
    assert slept == [5.0]
    assert m.edit_text.await_count == 2

    # Persistent 429 — second call also raises — return False, no raise.
    m2 = MagicMock()
    m2.edit_text = AsyncMock(side_effect=[RetryAfter(3), RetryAfter(3)])
    result2 = await _safe_edit(m2, "error_generic")
    assert result2 is False


async def test_handler_swallows_persistent_retry_after_on_status_edits(monkeypatch, caplog):
    """Production bug repro: every editMessageText hits flood-control 429.
    Before the fix, the RetryAfter propagated out of the first `_safe_edit`,
    hit the catch-all `except Exception`, and the recovery `_safe_edit` ALSO
    raised RetryAfter — surfacing as `unhandled_application_error` in the
    global handler. After the fix: `_safe_edit` swallows RetryAfter, the
    flow continues, transcription is delivered via `reply_text` fallback,
    NO ERROR-level log appears."""
    import logging

    from telegram.error import RetryAfter

    from handlers import transcribe as t

    fake_result = GeminiResult(
        text="delivered", prompt_tokens=1, candidates_tokens=1, total_tokens=2,
    )
    monkeypatch.setattr(gemini_service, "transcribe",
                        AsyncMock(return_value=fake_result))
    # Don't actually sleep through retry-after waits.
    monkeypatch.setattr("handlers.transcribe.asyncio.sleep", AsyncMock())

    update, context, status, _ = _make_update_context(voice=_make_voice())
    # Every status edit hits flood-control. _safe_edit's internal retry
    # exhausts and returns False — the request must not raise.
    status.edit_text = AsyncMock(side_effect=RetryAfter(5))

    with caplog.at_level(logging.DEBUG, logger="handlers.transcribe"):
        await t.handle_message(update, context)

    handler_errors = [
        r for r in caplog.records
        if r.name == "handlers.transcribe" and r.levelno >= logging.ERROR
    ]
    assert not handler_errors, (
        f"unexpected ERROR-level log: {[r.getMessage() for r in handler_errors]}"
    )
    # Transcription still reaches the user via the reply_text fallback
    # (since status.edit_text never lands).
    reply_calls = [c for c in update.message.reply_text.await_args_list
                   if "delivered" in c.args[0]]
    assert reply_calls, "transcription should be delivered via reply_text fallback"


async def test_safe_edit_swallows_message_gone_badrequest():
    """Unit-level: the helper returns False (not raise) on benign 'message gone'
    BadRequests, regardless of the exact wording Telegram uses."""
    from telegram.error import BadRequest

    from handlers.transcribe import _safe_edit

    for msg in (
        "Message_id_invalid",
        "Message to edit not found",
        "Bad Request: message to be replied not found",
        "Message can't be edited",
    ):
        m = MagicMock()
        m.edit_text = AsyncMock(side_effect=BadRequest(msg))
        result = await _safe_edit(m, "anything")
        assert result is False, f"expected False for {msg!r}"


async def test_safe_edit_propagates_other_badrequests():
    """A BadRequest that ISN'T 'message gone' is a real bug and must not be
    swallowed."""
    from telegram.error import BadRequest

    from handlers.transcribe import _safe_edit

    m = MagicMock()
    m.edit_text = AsyncMock(side_effect=BadRequest("Some other validation error"))
    with pytest.raises(BadRequest):
        await _safe_edit(m, "anything")
