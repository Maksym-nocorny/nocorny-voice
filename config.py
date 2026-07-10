"""Configuration constants. Override any of them via environment variables."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return float(raw)


# --- Required secrets ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- Webhook (Render) ---
PORT = os.getenv("PORT")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# --- Gemini model ---
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
TRANSCRIBE_MAX_TOKENS = _env_int("TRANSCRIBE_MAX_TOKENS", 8192)
TRANSCRIBE_TEMPERATURE = _env_float("TRANSCRIBE_TEMPERATURE", 0.0)
# Fallback temperature for a single semantic retry when the first attempt
# tripped RECITATION (false-positive copyright refusal) or hit the loop
# detector. At temp=0.0 flash-lite is deterministic — the same audio
# reproduces the same refusal/loop — so a small jitter usually unblocks it.
TRANSCRIBE_RETRY_TEMPERATURE = _env_float("TRANSCRIBE_RETRY_TEMPERATURE", 0.3)
# Last-ditch retry temperature when both the deterministic attempt and the
# small-jitter attempt still degrade. A high T widens the next-token
# distribution enough to break stubborn pathological loops (seen on some
# noisy/short voice clips where flash-lite repeats at T=0.0 and T=0.3 alike).
TRANSCRIBE_RETRY_FINAL_TEMPERATURE = _env_float("TRANSCRIBE_RETRY_FINAL_TEMPERATURE", 1.0)

# --- Long-audio chunking (mitigates flash-lite hallucinations on long files) ---
# Files longer than this are split into chunks of the same length via ffmpeg.
# Set to 0 to disable chunking entirely. Threshold sits below the 3-4 min
# "danger zone" where flash-lite reliably looped into MAX_TOKENS at the prior
# 240s setting — chunking earlier avoids the loop preemptively instead of
# relying on the post-degrade fallback.
TRANSCRIBE_CHUNK_SEC = _env_int("TRANSCRIBE_CHUNK_SEC", 150)
# Max parallel Gemini transcribe calls when chunking. Keep modest to avoid rate limits.
TRANSCRIBE_CHUNK_CONCURRENCY = _env_int("TRANSCRIBE_CHUNK_CONCURRENCY", 3)
# Path to ffmpeg binary; resolved via PATH if just "ffmpeg".
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

# --- Hallucination / loop detection ---
# Reject responses whose output token rate exceeds this (real speech tops out
# around 5-7 tokens/sec; >8 means the model is repeating itself).
TRANSCRIBE_MAX_OUT_PER_SEC = _env_float("TRANSCRIBE_MAX_OUT_PER_SEC", 8.0)
# Reject responses whose output is at this fraction of MAX_TOKENS — likely truncated runaway.
TRANSCRIBE_MAX_OUT_FRACTION = _env_float("TRANSCRIBE_MAX_OUT_FRACTION", 0.95)

# --- Pricing (Google AI paid-tier rates per 1M tokens). Set to 0 to hide cost in /stats. ---
# Audio input is 3× text input on gemini-2.5-flash-lite (Standard tier). For voice/audio
# transcription almost all prompt tokens are audio (Gemini tariffs audio at 32 t/s),
# so a single rate would undercount cost ~3×.
PRICE_PER_1M_INPUT_TOKENS = _env_float("PRICE_PER_1M_INPUT_TOKENS", 0.10)
PRICE_PER_1M_AUDIO_INPUT_TOKENS = _env_float("PRICE_PER_1M_AUDIO_INPUT_TOKENS", 0.30)
PRICE_PER_1M_OUTPUT_TOKENS = _env_float("PRICE_PER_1M_OUTPUT_TOKENS", 0.40)

# --- Retry on transient Gemini errors ---
GEMINI_RETRY_ATTEMPTS = _env_int("GEMINI_RETRY_ATTEMPTS", 2)
GEMINI_RETRY_BASE_DELAY = _env_float("GEMINI_RETRY_BASE_DELAY", 1.0)

# --- Media limits ---
MAX_DURATION_SEC = _env_int("MAX_DURATION_SEC", 1800)        # 30 min
MAX_FILE_SIZE_MB = _env_int("MAX_FILE_SIZE_MB", 20)

# --- Per-user rate limit ---
RATE_LIMIT_REQUESTS = _env_int("RATE_LIMIT_REQUESTS", 5)
RATE_LIMIT_WINDOW_SEC = _env_int("RATE_LIMIT_WINDOW_SEC", 60)

# --- Cache ---
# L1 lives in-process; on Render Free (512Mi) a large L1 adds to the RSS that
# OOM-killed the service on 2026-06-13. Keep it small — L2 (Neon Postgres) backs
# every miss, so a tight cap costs an extra DB lookup, not a Gemini call.
CACHE_MAX_SIZE = _env_int("CACHE_MAX_SIZE", 200)
CACHE_TTL_SEC = _env_int("CACHE_TTL_SEC", 86400)             # 24h (L1, in-memory)
CACHE_L2_TTL_DAYS = _env_int("CACHE_L2_TTL_DAYS", 14)        # L2, Neon Postgres

# --- Telegram ---
TELEGRAM_MAX_MESSAGE_LEN = _env_int("TELEGRAM_MAX_MESSAGE_LEN", 4000)

# --- Keep-alive (Render Free anti-sleep) ---
# Render Free spins the web service down after ~15 min idle; the cold start
# (~25-40s) outlives Telegram's ~15s callback TTL and makes /stats buttons return
# "Query is too old". Self-ping the public URL to stay warm. 300s leaves a wide
# margin under the 15-min timeout even if a ping or two is dropped. This pings the
# bot's OWN URL only — never the DB, so it costs zero Neon compute hours.
KEEP_ALIVE_INTERVAL_SEC = _env_float("KEEP_ALIVE_INTERVAL_SEC", 300.0)

# --- Analytics (Neon Postgres) ---
# Set DATABASE_URL to enable analytics. Bot runs fine without it (track() becomes a no-op).
DATABASE_URL = os.getenv("DATABASE_URL") or None
# Telegram user_id allowed to invoke /stats. Required only if DATABASE_URL is set.
ADMIN_USER_ID = _env_int("ADMIN_USER_ID", 0) or None
ANALYTICS_QUEUE_SIZE = _env_int("ANALYTICS_QUEUE_SIZE", 2000)
ANALYTICS_BATCH_SIZE = _env_int("ANALYTICS_BATCH_SIZE", 100)
ANALYTICS_FLUSH_INTERVAL_SEC = _env_float("ANALYTICS_FLUSH_INTERVAL_SEC", 1800.0)
ANALYTICS_HEARTBEAT_INTERVAL_SEC = _env_int("ANALYTICS_HEARTBEAT_INTERVAL_SEC", 0)
ANALYTICS_RETENTION_DAYS = _env_int("ANALYTICS_RETENTION_DAYS", 90)
