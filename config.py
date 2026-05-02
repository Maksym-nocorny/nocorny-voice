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
CACHE_TTL_SEC = _env_int("CACHE_TTL_SEC", 86400)             # 24h

# --- Telegram ---
TELEGRAM_MAX_MESSAGE_LEN = _env_int("TELEGRAM_MAX_MESSAGE_LEN", 4000)
