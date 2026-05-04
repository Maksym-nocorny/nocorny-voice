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

    huge = 100 * 1024 * 1024  # 100 MB > 50 MB default
    update, context, _, _ = _make_update_context(
        voice=_make_voice(file_size=huge)
    )
    await t.handle_message(update, context)

    transcribe_spy.assert_not_called()


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
