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

    Distinct from ProcessingFailedError so the handler can show a more
    specific message and analytics can track these separately.
    """


@dataclass
class GeminiResult:
    text: str
    prompt_tokens: int
    candidates_tokens: int
    total_tokens: int
    detected_language: Optional[str] = None


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


def _extract_usage(response) -> Tuple[int, int, int]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0, 0
    return (
        getattr(usage, "prompt_token_count", 0) or 0,
        getattr(usage, "candidates_token_count", 0) or 0,
        getattr(usage, "total_token_count", 0) or 0,
    )


def _log_usage(operation: str, response) -> Tuple[int, int, int]:
    p, c, t = _extract_usage(response)
    logger.info(
        "gemini_usage operation=%s prompt_tokens=%d candidates_tokens=%d "
        "total_tokens=%d model=%s",
        operation,
        p,
        c,
        t,
        MODEL_NAME,
    )
    return p, c, t


def _is_likely_loop(out_tokens: int, duration_sec: Optional[int],
                    max_tokens: int = TRANSCRIBE_MAX_TOKENS) -> bool:
    """Heuristic: did the model run away generating repetitive output?

    Two signals (either triggers):
      1. Output is at >=95% of max_tokens (response was capped — almost always bad).
      2. Output rate > N tokens/sec (real speech tops out ~5-7).
    """
    if max_tokens > 0 and out_tokens >= int(max_tokens * TRANSCRIBE_MAX_OUT_FRACTION):
        return True
    if duration_sec and duration_sec > 0:
        if out_tokens / duration_sec > TRANSCRIBE_MAX_OUT_PER_SEC:
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


async def transcribe(
    file_path: str,
    mime_type: str,
    duration_sec: Optional[int] = None,
) -> GeminiResult:
    """Public entry: single-shot for short files, chunked for long ones."""
    _configure_once()

    chunking = (
        TRANSCRIBE_CHUNK_SEC > 0
        and duration_sec is not None
        and duration_sec > TRANSCRIBE_CHUNK_SEC
        and _ffmpeg_binary() is not None
    )

    if not chunking:
        # Short file or ffmpeg unavailable → original single-shot path.
        return await _transcribe_one(file_path, mime_type, duration_sec)

    return await _transcribe_chunked(file_path, mime_type, duration_sec)


async def _transcribe_chunked(file_path: str, mime_type: str,
                              duration_sec: int) -> GeminiResult:
    """Split, transcribe each chunk in parallel (bounded), assemble.

    `mime_type` is the source file's MIME (used for the single-shot fallback
    if ffmpeg fails). Chunks themselves are always opus/ogg after re-encoding.
    """
    try:
        chunk_paths, chunk_mime, temp_dir = await _split_audio(file_path, TRANSCRIBE_CHUNK_SEC)
    except RuntimeError as e:
        logger.warning("ffmpeg_split_failed falling_back_to_single_shot exc=%s", e)
        return await _transcribe_one(file_path, mime_type, duration_sec)

    try:
        sem = asyncio.Semaphore(max(1, TRANSCRIBE_CHUNK_CONCURRENCY))
        n = len(chunk_paths)

        async def _do(idx: int, path: str) -> GeminiResult:
            async with sem:
                # Per-chunk duration is at most TRANSCRIBE_CHUNK_SEC; the last
                # one may be shorter but using the cap is fine for loop
                # detection (it only loosens the per-second threshold).
                logger.info("chunk_transcribe_start idx=%d/%d", idx + 1, n)
                return await _transcribe_one(path, chunk_mime, TRANSCRIBE_CHUNK_SEC)

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

    # Surviving chunks (degraded ones contribute a placeholder).
    parts: List[str] = []
    p_total = c_total = t_total = 0
    detected: Optional[str] = None
    degraded_count = 0
    for idx, r in enumerate(results):
        if isinstance(r, TranscriptionDegradedError):
            degraded_count += 1
            parts.append(f"[фрагмент {idx + 1}: не вдалося розпізнати]")
            continue
        if isinstance(r, BaseException):
            # Unexpected — treat as degraded chunk, keep the rest.
            logger.warning("chunk_unexpected_error idx=%d exc=%s", idx, r)
            degraded_count += 1
            parts.append(f"[фрагмент {idx + 1}: не вдалося розпізнати]")
            continue
        # success
        parts.append(r.text)
        p_total += r.prompt_tokens
        c_total += r.candidates_tokens
        t_total += r.total_tokens
        if detected is None and r.detected_language:
            detected = r.detected_language

    if degraded_count == len(results):
        # All chunks failed — propagate so the user sees an error, not garbage.
        raise TranscriptionDegradedError(
            f"all {len(results)} chunks degraded (duration={duration_sec}s)"
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
        detected_language=detected,
    )


async def _transcribe_one(
    file_path: str,
    mime_type: str,
    duration_sec: Optional[int],
) -> GeminiResult:
    """Single Gemini call with ValueError catch + loop detection.

    Raises TranscriptionDegradedError on response.text() failure or detected loops.
    """
    gemini_file = await asyncio.to_thread(
        genai.upload_file, path=file_path, mime_type=mime_type
    )
    try:
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = await asyncio.to_thread(genai.get_file, gemini_file.name)
        if gemini_file.state.name == "FAILED":
            raise ProcessingFailedError("gemini reported FAILED state")

        model = _get_transcribe_model()

        async def _call():
            return await asyncio.to_thread(
                model.generate_content,
                [gemini_file],
            )

        response = await _retry(_call)
    finally:
        try:
            await asyncio.to_thread(genai.delete_file, gemini_file.name)
        except Exception as e:  # noqa: BLE001 - cleanup best-effort
            logger.warning("gemini_delete_file_failed name=%s exc=%s", gemini_file.name, e)

    p, c, t = _log_usage("transcribe", response)

    # Loop check BEFORE touching response.text — bad responses often raise
    # there too, but the rate signal is more informative.
    if _is_likely_loop(c, duration_sec):
        finish = _finish_reason(response)
        logger.warning(
            "transcribe_loop_detected duration=%s out_tokens=%d finish=%s",
            duration_sec, c, finish,
        )
        raise TranscriptionDegradedError(
            f"loop detected: out={c} duration={duration_sec} finish={finish}"
        )

    try:
        raw = (response.text or "").strip()
    except ValueError as e:
        finish = _finish_reason(response)
        logger.warning(
            "transcribe_response_text_failed duration=%s finish=%s exc=%s",
            duration_sec, finish, e,
        )
        raise TranscriptionDegradedError(
            f"response.text raised: finish={finish}"
        ) from None

    text, detected_language = _split_language_prefix(raw)
    return GeminiResult(
        text=text,
        prompt_tokens=p,
        candidates_tokens=c,
        total_tokens=t,
        detected_language=detected_language,
    )


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
