"""Wraps Gemini API: singleton model, async wrapper, retries, usage logging.

For long files (> TRANSCRIBE_CHUNK_SEC) we split via ffmpeg and transcribe
chunks in parallel. This avoids the flash-lite hallucination loops we observed
on multi-minute audio (model would repeat tokens until hitting MAX_TOKENS).
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import random
import shutil
import tempfile
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Tuple

import google.generativeai as genai
from google.api_core import exceptions

from config import (
    FFMPEG_PATH,
    GEMINI_API_KEY,
    GEMINI_RETRY_ATTEMPTS,
    GEMINI_RETRY_BASE_DELAY,
    MODEL_NAME,
    TRANSCRIBE_CHUNK_CONCURRENCY,
    TRANSCRIBE_CHUNK_SEC,
    TRANSCRIBE_MAX_OUT_FRACTION,
    TRANSCRIBE_MAX_OUT_PER_SEC,
    TRANSCRIBE_MAX_TOKENS,
    TRANSCRIBE_RETRY_FINAL_TEMPERATURE,
    TRANSCRIBE_RETRY_TEMPERATURE,
    TRANSCRIBE_TEMPERATURE,
)

logger = logging.getLogger(__name__)


_TRANSCRIBE_INSTRUCTION = (
    "You are a verbatim transcription engine. "
    "On the first line, output exactly: LANG:<ISO 639-1 code> "
    "(use the lowercase two-letter code of the spoken language, "
    "e.g. LANG:en, LANG:uk, LANG:es; for regional variants append a hyphen, "
    "e.g. LANG:pt-br). "
    "On the next line and onwards, output ONLY the transcribed text, "
    "exactly as spoken in the original language. "
    "No preamble, no commentary, no headers, no markdown."
)

_LANG_PREFIX = "LANG:"

_RETRY_EXCEPTIONS = (exceptions.ResourceExhausted, exceptions.ServiceUnavailable)

_SAFETY_BLOCK_NONE = {
    "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
    "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
    "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
}


class RateLimitedError(Exception):
    """Gemini still returned ResourceExhausted after exhausting retries."""


class ProcessingFailedError(Exception):
    """Gemini Files API reported FAILED state for the uploaded file."""


class TranscriptionDegradedError(Exception):
    """Gemini hallucinated/looped or returned a blocked/truncated response.

    Carries partial usage info — Gemini still bills for the call even when
    the response was unusable, so we surface tokens to the handler so cost
    accounting matches the API bill. `finish_reason` (RECITATION /
    PROHIBITED_CONTENT / MAX_TOKENS / …) is recorded in analytics so /stats
    can break down WHY transcriptions degraded.
    """

    def __init__(self, msg: str, *, prompt_tokens: int = 0,
                 prompt_audio_tokens: int = 0,
                 candidates_tokens: int = 0,
                 total_tokens: int = 0,
                 finish_reason: Optional[str] = None):
        super().__init__(msg)
        self.prompt_tokens = prompt_tokens
        self.prompt_audio_tokens = prompt_audio_tokens
        self.candidates_tokens = candidates_tokens
        self.total_tokens = total_tokens
        self.finish_reason = finish_reason


@dataclass
class GeminiResult:
    text: str
    prompt_tokens: int
    candidates_tokens: int
    total_tokens: int
    prompt_audio_tokens: int = 0
    detected_language: Optional[str] = None
    # >0 only on chunked transcribes where at least one chunk degraded but
    # the rest succeeded. The handler still surfaces the partial text but
    # tracks an extra `transcribe_degraded` event so /stats reflects it.
    degraded_chunks: int = 0


_configured = False
_transcribe_model: Optional[genai.GenerativeModel] = None
_ffmpeg_path: Optional[str] = None  # resolved on first use; "" means unavailable


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=GEMINI_API_KEY)
    _configured = True


def _get_transcribe_model() -> genai.GenerativeModel:
    global _transcribe_model
    if _transcribe_model is None:
        _configure_once()
        _transcribe_model = genai.GenerativeModel(
            MODEL_NAME,
            system_instruction=_TRANSCRIBE_INSTRUCTION,
            generation_config=genai.GenerationConfig(
                temperature=TRANSCRIBE_TEMPERATURE,
                max_output_tokens=TRANSCRIBE_MAX_TOKENS,
            ),
            safety_settings=_SAFETY_BLOCK_NONE,
        )
    return _transcribe_model


# Gemini tariffs audio at exactly 32 tokens/sec — used as fallback when the API
# response doesn't include modality breakdown.
_AUDIO_TOKENS_PER_SEC = 32

# Track which audio-token path we've taken at least once. Both branches are
# normal (the SDK either surfaces prompt_tokens_details or it doesn't, and
# the shape doesn't change at runtime), so we log each path once at INFO and
# stay silent afterward. Logging on every request was producing ~200 lines/day
# of noise for what is steady-state behaviour.
_audio_modality_logged = False
_audio_fallback_logged = False


def _extract_audio_tokens(usage, prompt_total: int,
                          duration_sec: Optional[int]) -> int:
    """Extract audio-modality token count from usage_metadata.prompt_tokens_details.

    Returns 0 for non-audio requests. For audio requests where the API doesn't
    return AUDIO entry (or returns 0), falls back to `min(duration*32, prompt_total)`
    — Gemini's deterministic 32 t/s tariff makes this exact, not an estimate.
    """
    global _audio_modality_logged, _audio_fallback_logged
    details = getattr(usage, "prompt_tokens_details", None) or []
    audio_from_details = 0
    for entry in details:
        modality = getattr(entry, "modality", None)
        modality_name = getattr(modality, "name", str(modality)).upper()
        if modality_name == "AUDIO":
            audio_from_details = int(getattr(entry, "token_count", 0) or 0)
            break

    if audio_from_details > 0:
        if not _audio_modality_logged:
            _audio_modality_logged = True
            logger.info(
                "gemini_audio_modality_detected audio_tokens=%d prompt_total=%d",
                audio_from_details, prompt_total,
            )
        return min(audio_from_details, prompt_total)

    if duration_sec and duration_sec > 0 and prompt_total > 0:
        fallback = min(duration_sec * _AUDIO_TOKENS_PER_SEC, prompt_total)
        if fallback > 0:
            if not _audio_fallback_logged:
                _audio_fallback_logged = True
                logger.info(
                    "gemini_audio_details_missing using_fallback "
                    "(SDK doesn't surface prompt_tokens_details; "
                    "computing audio tokens from duration*32)",
                )
            return fallback
    return 0


def _extract_usage(response, duration_sec: Optional[int] = None
                   ) -> Tuple[int, int, int, int]:
    """Returns (prompt_total, prompt_audio, candidates, total)."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0, 0, 0
    prompt = getattr(usage, "prompt_token_count", 0) or 0
    cand = getattr(usage, "candidates_token_count", 0) or 0
    total = getattr(usage, "total_token_count", 0) or 0
    audio = _extract_audio_tokens(usage, prompt, duration_sec)
    return prompt, audio, cand, total


def _log_usage(operation: str, response, duration_sec: Optional[int] = None
               ) -> Tuple[int, int, int, int]:
    p, pa, c, t = _extract_usage(response, duration_sec)
    logger.info(
        "gemini_usage operation=%s prompt_tokens=%d audio_tokens=%d "
        "candidates_tokens=%d total_tokens=%d model=%s",
        operation, p, pa, c, t, MODEL_NAME,
    )
    return p, pa, c, t


_LOOP_RATE_MIN_DURATION_SEC = 5
# Even fast Cyrillic speakers don't sustain this rate — anything above is
# almost certainly the model babbling.
_LOOP_RATE_HARD_TOK_PER_SEC = 20.0


def _is_likely_loop(out_tokens: int, duration_sec: Optional[int],
                    finish_reason: Optional[str] = None,
                    max_tokens: int = TRANSCRIBE_MAX_TOKENS) -> bool:
    """Heuristic: did the model run away generating repetitive output?

    Signals:
      1. Output is at >=95% of max_tokens (response was capped — almost always bad).
      2. Output rate exceeds plausible-speech ceiling (>20 tok/s). Skipped on
         clips <5s where tok/sec is too noisy.
      3. Output rate > soft threshold (8 tok/s) AND the model did NOT finish
         naturally with STOP. Cyrillic tokenises 2-3x denser than English, so
         normal Ukrainian/Russian speech routinely runs 8-12 tok/s and finishes
         with STOP — those are real transcriptions, not loops. A genuine loop
         that finishes with STOP at moderate rate is rare; loops typically hit
         MAX_TOKENS or get stuck (caught by signal 1).
    """
    if max_tokens > 0 and out_tokens >= int(max_tokens * TRANSCRIBE_MAX_OUT_FRACTION):
        return True
    if duration_sec and duration_sec >= _LOOP_RATE_MIN_DURATION_SEC:
        rate = out_tokens / duration_sec
        if rate > _LOOP_RATE_HARD_TOK_PER_SEC:
            return True
        if rate > TRANSCRIBE_MAX_OUT_PER_SEC and finish_reason != "STOP":
            return True
    return False


async def _retry(
    coro_factory: Callable[[], Awaitable],
    *,
    attempts: int = GEMINI_RETRY_ATTEMPTS,
    base_delay: float = GEMINI_RETRY_BASE_DELAY,
):
    last_exc: Optional[BaseException] = None
    for attempt in range(attempts + 1):
        try:
            return await coro_factory()
        except _RETRY_EXCEPTIONS as e:
            last_exc = e
            if attempt >= attempts:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning(
                "gemini_retry attempt=%d/%d delay=%.2f exc=%s",
                attempt + 1,
                attempts,
                delay,
                type(e).__name__,
            )
            await asyncio.sleep(delay)
    raise RateLimitedError(str(last_exc) if last_exc else "rate limited")


def _ffmpeg_binary() -> Optional[str]:
    """Resolve ffmpeg path once. Returns None if unavailable.

    Lookup order:
      1. System ffmpeg via PATH (or `FFMPEG_PATH` if it's an absolute path).
      2. Bundled binary from `imageio_ffmpeg` (works on Render Free where
         apt-get is unavailable).
    """
    global _ffmpeg_path
    if _ffmpeg_path is None:
        resolved = shutil.which(FFMPEG_PATH) or ""
        if resolved:
            logger.info("ffmpeg_resolved path=%s", resolved)
        else:
            try:
                import imageio_ffmpeg
                resolved = imageio_ffmpeg.get_ffmpeg_exe() or ""
                if resolved:
                    logger.info("ffmpeg_via_imageio path=%s", resolved)
            except Exception as e:  # noqa: BLE001 — package missing or download failed
                logger.warning("ffmpeg_imageio_unavailable exc=%s", e)
        _ffmpeg_path = resolved
        if not resolved:
            logger.warning("ffmpeg_not_found path=%s — chunking disabled", FFMPEG_PATH)
    return _ffmpeg_path or None


async def _split_audio(file_path: str, chunk_sec: int) -> Tuple[List[str], str, str]:
    """Split audio into chunks via ffmpeg. Re-encodes to opus mono 16kHz so
    chunk boundaries are clean regardless of the source codec.

    Returns (chunk_paths, mime_type, temp_dir). Caller owns temp_dir cleanup.
    Raises RuntimeError on ffmpeg failure.
    """
    ffmpeg = _ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available")

    temp_dir = tempfile.mkdtemp(prefix="nv_chunks_")
    pattern = os.path.join(temp_dir, "chunk_%04d.ogg")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-y", "-i", file_path,
        "-vn",
        "-ac", "1", "-ar", "16000",
        "-c:a", "libopus", "-b:a", "32k", "-application", "voip",
        "-f", "segment", "-segment_time", str(chunk_sec),
        "-reset_timestamps", "1",
        pattern,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    chunks = sorted(glob.glob(os.path.join(temp_dir, "chunk_*.ogg")))
    if proc.returncode != 0 or not chunks:
        shutil.rmtree(temp_dir, ignore_errors=True)
        tail = (stderr or b"")[-300:].decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {tail}")
    logger.info("ffmpeg_split chunks=%d chunk_sec=%d", len(chunks), chunk_sec)
    return chunks, "audio/ogg", temp_dir


# When single-shot fully degrades, fall back to chunking IF the file is long
# enough that smaller chunks would actually be different (below ~40s, a "half"
# chunk is too short to materially change what the model sees).
_DEGRADED_FALLBACK_MIN_DURATION_SEC = 40
# Floor chunk size for the degraded fallback — keeps very short clips from
# being sliced into useless slivers.
_DEGRADED_FALLBACK_MIN_CHUNK_SEC = 30


async def transcribe(
    file_path: str,
    mime_type: str,
    duration_sec: Optional[int] = None,
) -> GeminiResult:
    """Public entry: single-shot for short files, chunked for long ones."""
    _configure_once()

    ffmpeg_ready = _ffmpeg_binary() is not None
    chunking_eligible = (
        TRANSCRIBE_CHUNK_SEC > 0
        and duration_sec is not None
        and ffmpeg_ready
    )

    if chunking_eligible and duration_sec > TRANSCRIBE_CHUNK_SEC:
        return await _transcribe_chunked(file_path, mime_type, duration_sec)

    try:
        return await _transcribe_one(file_path, mime_type, duration_sec)
    except TranscriptionDegradedError as degraded:
        # Last-resort: if every single-shot retry degraded (MAX_TOKENS loops,
        # RECITATION refusals), try the chunked path with smaller pieces. A
        # fresh model context per chunk usually breaks the loop, and a different
        # audio slice usually bypasses the false-positive content filter.
        # Only fires when ffmpeg is available and the clip is long enough for
        # halving to produce non-trivial chunks.
        if not chunking_eligible or duration_sec < _DEGRADED_FALLBACK_MIN_DURATION_SEC:
            raise
        fallback_chunk_sec = max(
            _DEGRADED_FALLBACK_MIN_CHUNK_SEC,
            min(TRANSCRIBE_CHUNK_SEC, duration_sec // 2),
        )
        logger.info(
            "transcribe_chunked_fallback_on_degraded duration=%s chunk_sec=%d finish=%s",
            duration_sec, fallback_chunk_sec, degraded.finish_reason,
        )
        try:
            return await _transcribe_chunked(
                file_path, mime_type, duration_sec,
                chunk_sec=fallback_chunk_sec,
            )
        except TranscriptionDegradedError as chunked_err:
            # Both paths degraded. Combine usage so the handler bills for
            # what we actually consumed in both attempts.
            raise TranscriptionDegradedError(
                f"single_shot+chunked_both_degraded single={degraded.finish_reason} "
                f"chunked={chunked_err.finish_reason}",
                prompt_tokens=degraded.prompt_tokens + chunked_err.prompt_tokens,
                prompt_audio_tokens=degraded.prompt_audio_tokens + chunked_err.prompt_audio_tokens,
                candidates_tokens=degraded.candidates_tokens + chunked_err.candidates_tokens,
                total_tokens=degraded.total_tokens + chunked_err.total_tokens,
                finish_reason=degraded.finish_reason or chunked_err.finish_reason,
            ) from chunked_err


async def _transcribe_chunked(file_path: str, mime_type: str,
                              duration_sec: int,
                              *, chunk_sec: Optional[int] = None) -> GeminiResult:
    """Split, transcribe each chunk in parallel (bounded), assemble.

    `mime_type` is the source file's MIME (used for the single-shot fallback
    if ffmpeg fails). Chunks themselves are always opus/ogg after re-encoding.
    `chunk_sec` overrides the default split size — used by the degraded
    fallback to slice more aggressively than the normal long-file path.
    """
    split_sec = chunk_sec if chunk_sec and chunk_sec > 0 else TRANSCRIBE_CHUNK_SEC
    try:
        chunk_paths, chunk_mime, temp_dir = await _split_audio(file_path, split_sec)
    except RuntimeError as e:
        logger.warning("ffmpeg_split_failed falling_back_to_single_shot exc=%s", e)
        return await _transcribe_one(file_path, mime_type, duration_sec)

    try:
        sem = asyncio.Semaphore(max(1, TRANSCRIBE_CHUNK_CONCURRENCY))
        n = len(chunk_paths)

        async def _do(idx: int, path: str) -> GeminiResult:
            async with sem:
                # Per-chunk duration is at most `split_sec`; the last one may
                # be shorter but using the cap is fine for loop detection
                # (it only loosens the per-second threshold).
                logger.info("chunk_transcribe_start idx=%d/%d", idx + 1, n)
                return await _transcribe_one(path, chunk_mime, split_sec)

        results = await asyncio.gather(
            *[_do(i, p) for i, p in enumerate(chunk_paths)],
            return_exceptions=True,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Re-raise the first hard error if any chunk failed unrecoverably.
    for r in results:
        if isinstance(r, (RateLimitedError, ProcessingFailedError)):
            raise r

    # Surviving chunks (degraded ones contribute a placeholder). Token totals
    # accumulate across BOTH success and degraded chunks — Gemini bills us per
    # API call, so a degraded chunk that returned a usable response.usage_metadata
    # but unusable text still costs money.
    parts: List[str] = []
    p_total = pa_total = c_total = t_total = 0
    detected: Optional[str] = None
    degraded_count = 0
    for idx, r in enumerate(results):
        if isinstance(r, TranscriptionDegradedError):
            degraded_count += 1
            parts.append(f"[фрагмент {idx + 1}: не вдалося розпізнати]")
            p_total += getattr(r, "prompt_tokens", 0)
            pa_total += getattr(r, "prompt_audio_tokens", 0)
            c_total += getattr(r, "candidates_tokens", 0)
            t_total += getattr(r, "total_tokens", 0)
            continue
        if isinstance(r, BaseException):
            # Unexpected — treat as degraded chunk, keep the rest. No tokens
            # to recover (we never got past the API client).
            logger.warning("chunk_unexpected_error idx=%d exc=%s", idx, r)
            degraded_count += 1
            parts.append(f"[фрагмент {idx + 1}: не вдалося розпізнати]")
            continue
        # success
        parts.append(r.text)
        p_total += r.prompt_tokens
        pa_total += r.prompt_audio_tokens
        c_total += r.candidates_tokens
        t_total += r.total_tokens
        if detected is None and r.detected_language:
            detected = r.detected_language

    if degraded_count == len(results):
        # All chunks failed — propagate so the user sees an error, not garbage.
        # Pass accumulated tokens through so the handler can still record cost.
        raise TranscriptionDegradedError(
            f"all {len(results)} chunks degraded (duration={duration_sec}s)",
            prompt_tokens=p_total, prompt_audio_tokens=pa_total,
            candidates_tokens=c_total, total_tokens=t_total,
            finish_reason="all_chunks_degraded",
        )

    logger.info(
        "transcribe_chunked_done chunks=%d degraded=%d duration=%ds tokens=%d",
        len(results), degraded_count, duration_sec, t_total,
    )
    return GeminiResult(
        text="\n".join(parts).strip(),
        prompt_tokens=p_total,
        candidates_tokens=c_total,
        total_tokens=t_total,
        prompt_audio_tokens=pa_total,
        detected_language=detected,
        degraded_chunks=degraded_count,
    )


async def _attempt_transcribe(
    model: genai.GenerativeModel,
    audio_part,
    duration_sec: Optional[int],
    *,
    temperature: Optional[float] = None,
) -> GeminiResult:
    """One Gemini generate_content call + response sanity checks.

    Raises TranscriptionDegradedError on detected loop or response.text failure
    (the exception carries usage info so the caller can still bill the call).
    `temperature` overrides the model's default per-call when not None — used
    for the jittered retry after a RECITATION refusal or loop on the first try.
    """
    async def _call():
        kwargs = {}
        if temperature is not None:
            kwargs["generation_config"] = genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=TRANSCRIBE_MAX_TOKENS,
            )
        return await asyncio.to_thread(
            model.generate_content, [audio_part], **kwargs,
        )

    response = await _retry(_call)
    p, pa, c, t = _log_usage("transcribe", response, duration_sec)

    # Loop check BEFORE touching response.text — bad responses often raise
    # there too, but the rate signal is more informative.
    finish = _finish_reason(response)
    if _is_likely_loop(c, duration_sec, finish_reason=finish):
        logger.warning(
            "transcribe_loop_detected duration=%s out_tokens=%d finish=%s",
            duration_sec, c, finish,
        )
        raise TranscriptionDegradedError(
            f"loop detected: out={c} duration={duration_sec} finish={finish}",
            prompt_tokens=p, prompt_audio_tokens=pa,
            candidates_tokens=c, total_tokens=t,
            finish_reason=finish,
        )

    try:
        raw = (response.text or "").strip()
    except ValueError as e:
        logger.warning(
            "transcribe_response_text_failed duration=%s finish=%s exc=%s",
            duration_sec, finish, e,
        )
        raise TranscriptionDegradedError(
            f"response.text raised: finish={finish}",
            prompt_tokens=p, prompt_audio_tokens=pa,
            candidates_tokens=c, total_tokens=t,
            finish_reason=finish,
        ) from None

    text, detected_language = _split_language_prefix(raw)
    return GeminiResult(
        text=text,
        prompt_tokens=p,
        candidates_tokens=c,
        total_tokens=t,
        prompt_audio_tokens=pa,
        detected_language=detected_language,
    )


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _transcribe_one(
    file_path: str,
    mime_type: str,
    duration_sec: Optional[int],
) -> GeminiResult:
    """Single Gemini call with up to two semantic retries at rising temperatures.

    Sends audio inline (base64 blob in the generate_content payload) rather
    than via the Files API. Gemini's Files API endpoint started returning
    "User location is not supported" 400s from Render's EU IPs on
    2026-05-29 — generateContent itself is unaffected, so the inline path
    sidesteps the geo-block entirely. Telegram caps downloads at 20 MB and
    chunks land well under that, comfortably inside the inline limit.

    flash-lite at temp=0.0 deterministically reproduces RECITATION refusals
    and runaway-token loops on the same audio. A small positive temperature
    usually unblocks the false positives; for stubborn cases where even the
    jittered retry loops (some noisy/short clips do this reliably), a final
    attempt at a high temperature widens the next-token distribution enough
    to break out. Only after all three degrade does the error bubble up.

    A transient Gemini 5xx (`InternalServerError`, `ServiceUnavailable`,
    `DeadlineExceeded`) on any single attempt is treated the same as our
    internal degraded signal — push the next attempt at a different
    temperature rather than surfacing a generic error to the user. `_retry`
    already short-cycles `ServiceUnavailable` within one attempt; this outer
    catch covers the case where it (or an `InternalServerError`) escapes
    after exhausting that inner budget.
    """
    audio_bytes = await asyncio.to_thread(_read_file_bytes, file_path)
    audio_part = {"mime_type": mime_type, "data": audio_bytes}

    retryable = (
        TranscriptionDegradedError,
        exceptions.InternalServerError,
        exceptions.ServiceUnavailable,
        exceptions.DeadlineExceeded,
    )

    # Transient Gemini server-side errors (NOT TranscriptionDegradedError, which
    # is a content/model issue). After the final-temperature attempt, one more
    # call at the same temperature lets the API recover from a flake without
    # surfacing a generic error to the user. Capped at +1 call, so worst-case
    # cost per request is 4 billed attempts instead of 3 — only on the narrow
    # path where 3 prior tries already failed.
    transient_5xx = (
        exceptions.InternalServerError,
        exceptions.ServiceUnavailable,
        exceptions.DeadlineExceeded,
    )

    model = _get_transcribe_model()
    try:
        return await _attempt_transcribe(model, audio_part, duration_sec)
    except retryable as e1:
        logger.info(
            "transcribe_retry_jittered duration=%s temperature=%.2f first_attempt=%s",
            duration_sec, TRANSCRIBE_RETRY_TEMPERATURE, type(e1).__name__,
        )
        try:
            return await _attempt_transcribe(
                model, audio_part, duration_sec,
                temperature=TRANSCRIBE_RETRY_TEMPERATURE,
            )
        except retryable as e2:
            # RECITATION is a content-safety block (copyrighted song lyrics,
            # quoted text). Raising temperature can't unblock it; the model
            # keeps reproducing the same protected sequence. Skip the final
            # high-temp attempt — it just wastes a billed call and ~10s of
            # user wait time. Loop-style degradations (MAX_TOKENS) still get
            # the temp=1.0 escape hatch, which empirically recovers them.
            if (isinstance(e2, TranscriptionDegradedError)
                    and getattr(e2, "finish_reason", None) == "RECITATION"):
                logger.info(
                    "transcribe_skip_final_on_recitation duration=%s",
                    duration_sec,
                )
                raise
            # Two MAX_TOKENS in a row at near-cap output means the model is
            # truly looping on the audio (typically song lyrics), not just
            # wobbling — the temp=1.0 escape almost always hits MAX_TOKENS
            # too. Skip it to save a billed call. Logged separately from the
            # RECITATION skip so /stats can split the two patterns.
            if (isinstance(e1, TranscriptionDegradedError)
                    and isinstance(e2, TranscriptionDegradedError)
                    and getattr(e1, "finish_reason", None) == "MAX_TOKENS"
                    and getattr(e2, "finish_reason", None) == "MAX_TOKENS"):
                logger.info(
                    "transcribe_skip_final_on_double_max_tokens duration=%s",
                    duration_sec,
                )
                raise
            logger.info(
                "transcribe_retry_final duration=%s temperature=%.2f second_attempt=%s",
                duration_sec, TRANSCRIBE_RETRY_FINAL_TEMPERATURE, type(e2).__name__,
            )
            try:
                return await _attempt_transcribe(
                    model, audio_part, duration_sec,
                    temperature=TRANSCRIBE_RETRY_FINAL_TEMPERATURE,
                )
            except transient_5xx as e3:
                logger.info(
                    "transcribe_retry_after_5xx duration=%s temperature=%.2f exc=%s",
                    duration_sec, TRANSCRIBE_RETRY_FINAL_TEMPERATURE, type(e3).__name__,
                )
                try:
                    return await _attempt_transcribe(
                        model, audio_part, duration_sec,
                        temperature=TRANSCRIBE_RETRY_FINAL_TEMPERATURE,
                    )
                except transient_5xx as e4:
                    # Two 5xx in a row from Gemini — treat as a degraded
                    # outcome so the chunked aggregator marks the chunk and
                    # the handler still records cost (currently zero — the
                    # API never returned usage_metadata for the failed calls,
                    # but we account for what we know: nothing).
                    logger.warning(
                        "transcribe_5xx_exhausted duration=%s exc=%s",
                        duration_sec, type(e4).__name__,
                    )
                    raise TranscriptionDegradedError(
                        f"two 5xx in a row: {type(e4).__name__}",
                        finish_reason="gemini_5xx",
                    ) from e4


def _finish_reason(response) -> str:
    """Best-effort extraction of finish_reason for diagnostic logging."""
    try:
        cands = getattr(response, "candidates", None) or []
        if cands:
            fr = getattr(cands[0], "finish_reason", None)
            return getattr(fr, "name", str(fr))
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _split_language_prefix(raw: str) -> Tuple[str, Optional[str]]:
    """Strip the leading 'LANG:xx' marker from Gemini's output.

    Returns (text_without_marker, language_code_or_None). If the marker is
    missing or malformed, returns the input unchanged with language=None so
    the bot still sends a sensible reply.
    """
    if not raw.startswith(_LANG_PREFIX):
        return raw, None
    newline = raw.find("\n")
    if newline == -1:
        # Whole response was just the marker — no transcription.
        candidate = raw[len(_LANG_PREFIX):].strip().lower()
        return "", _normalise_lang(candidate)
    candidate = raw[len(_LANG_PREFIX):newline].strip().lower()
    return raw[newline + 1:].lstrip(), _normalise_lang(candidate)


def _normalise_lang(code: str) -> Optional[str]:
    if not (2 <= len(code) <= 10):
        return None
    if not all(ch.isalnum() or ch == "-" for ch in code):
        return None
    return code
