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
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
TRANSCRIBE_MAX_TOKENS = _env_int("TRANSCRIBE_MAX_TOKENS", 8192)
TRANSCRIBE_TEMPERATURE = _env_float("TRANSCRIBE_TEMPERATURE", 0.0)

# --- Pricing (Google AI paid-tier rates per 1M tokens). Set to 0 to hide cost in /stats. ---
PRICE_PER_1M_INPUT_TOKENS = _env_float("PRICE_PER_1M_INPUT_TOKENS", 0.10)
PRICE_PER_1M_OUTPUT_TOKENS = _env_float("PRICE_PER_1M_OUTPUT_TOKENS", 0.40)

# --- Retry on transient Gemini errors ---
GEMINI_RETRY_ATTEMPTS = _env_int("GEMINI_RETRY_ATTEMPTS", 2)
GEMINI_RETRY_BASE_DELAY = _env_float("GEMINI_RETRY_BASE_DELAY", 1.0)

# --- Media limits ---
MAX_DURATION_SEC = _env_int("MAX_DURATION_SEC", 1800)        # 30 min
MAX_FILE_SIZE_MB = _env_int("MAX_FILE_SIZE_MB", 50)

# --- Per-user rate limit ---
RATE_LIMIT_REQUESTS = _env_int("RATE_LIMIT_REQUESTS", 5)
RATE_LIMIT_WINDOW_SEC = _env_int("RATE_LIMIT_WINDOW_SEC", 60)

# --- Cache ---
CACHE_MAX_SIZE = _env_int("CACHE_MAX_SIZE", 1000)
CACHE_TTL_SEC = _env_int("CACHE_TTL_SEC", 86400)             # 24h (L1, in-memory)
CACHE_L2_TTL_DAYS = _env_int("CACHE_L2_TTL_DAYS", 14)        # L2, Neon Postgres

# --- Telegram ---
TELEGRAM_MAX_MESSAGE_LEN = _env_int("TELEGRAM_MAX_MESSAGE_LEN", 4000)

# --- Analytics (Neon Postgres) ---
# Set DATABASE_URL to enable analytics. Bot runs fine without it (track() becomes a no-op).
DATABASE_URL = os.getenv("DATABASE_URL") or None
# Telegram user_id allowed to invoke /stats. Required only if DATABASE_URL is set.
ADMIN_USER_ID = _env_int("ADMIN_USER_ID", 0) or None
ANALYTICS_QUEUE_SIZE = _env_int("ANALYTICS_QUEUE_SIZE", 500)
ANALYTICS_BATCH_SIZE = _env_int("ANALYTICS_BATCH_SIZE", 100)
ANALYTICS_FLUSH_INTERVAL_SEC = _env_float("ANALYTICS_FLUSH_INTERVAL_SEC", 2.0)
ANALYTICS_HEARTBEAT_INTERVAL_SEC = _env_int("ANALYTICS_HEARTBEAT_INTERVAL_SEC", 240)
ANALYTICS_RETENTION_DAYS = _env_int("ANALYTICS_RETENTION_DAYS", 90)
