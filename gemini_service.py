"""Wraps Gemini API: singleton model, async wrapper, retries, usage logging."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Tuple

import google.generativeai as genai
from google.api_core import exceptions

from config import (
    GEMINI_API_KEY,
    GEMINI_RETRY_ATTEMPTS,
    GEMINI_RETRY_BASE_DELAY,
    MODEL_NAME,
    TRANSCRIBE_MAX_TOKENS,
    TRANSCRIBE_TEMPERATURE,
)

logger = logging.getLogger(__name__)


_TRANSCRIBE_INSTRUCTION = (
    "You are a verbatim transcription engine. "
    "Output ONLY the transcribed text exactly as spoken in the original language. "
    "No preamble, no commentary, no headers, no markdown."
)

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


@dataclass
class GeminiResult:
    text: str
    prompt_tokens: int
    candidates_tokens: int
    total_tokens: int


_configured = False
_transcribe_model: Optional[genai.GenerativeModel] = None


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


async def transcribe(file_path: str, mime_type: str) -> GeminiResult:
    """Upload media to Gemini, wait for processing, transcribe, clean up."""
    _configure_once()
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
                ["Transcribe this audio/video.", gemini_file],
            )

        response = await _retry(_call)
    finally:
        try:
            await asyncio.to_thread(genai.delete_file, gemini_file.name)
        except Exception as e:  # noqa: BLE001 - cleanup best-effort
            logger.warning("gemini_delete_file_failed name=%s exc=%s", gemini_file.name, e)

    p, c, t = _log_usage("transcribe", response)
    return GeminiResult(
        text=(response.text or "").strip(),
        prompt_tokens=p,
        candidates_tokens=c,
        total_tokens=t,
    )
