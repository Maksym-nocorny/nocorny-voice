# Telegram Voice & Video Transcription Bot

Transcribes voice messages, audio, video, and video notes via Google Gemini 2.5 Flash-Lite. Supports private chats and groups.

## Project layout

```
bot.py                    # entry point — registers handlers, starts polling/webhook
config.py                 # constants + env-var overrides (model, limits, rate limit, cache)
i18n.py                   # multilingual UI strings (HTML formatting)
cache.py                  # TTL cache: file_unique_id → transcription text
rate_limit.py             # per-user sliding-window request budget
gemini_service.py         # Gemini wrapper: model singleton, async, retry, usage logging
handlers/
  start.py                # /start
  transcribe.py           # voice/audio/video/video_note pipeline
utils/
  markdown.py             # HTML escape + chunking for long messages
  logging_setup.py        # structured logger with request/user/chat context
tests/                    # pytest unit + handler tests
```

## Setup

### 1. Telegram Bot Token
1.  Open Telegram and search for **@BotFather**.
2.  Send `/newbot`, name it, give it a username ending in `bot`.
3.  Copy the API token.

### 2. Gemini API Key
Get an API key from <https://aistudio.google.com/app/apikey>.

### 3. Configure environment
Copy `.env.example` to `.env` and fill in:

```
TELEGRAM_BOT_TOKEN=...
GEMINI_API_KEY=...
```

All other variables are optional — see `.env.example` for the full list of tunables (model name, token limits, rate limit, cache TTL, max media duration, etc.).

### 4. Install and run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

The bot starts in **polling** mode by default. To use **webhooks** (e.g. on Render), set both `PORT` and `WEBHOOK_URL`.

## Usage

- Send a **voice message**, **audio file**, **video**, or **video note** in a private chat → bot replies with the transcription.
- In groups: only voice messages and video notes are processed (audio/video files are ignored to avoid spam).

## Limits and protections

Defaults (override via env vars in `.env`):

| Setting | Default | Description |
|---|---|---|
| `MAX_DURATION_SEC` | 1800 | Reject media longer than 30 min |
| `MAX_FILE_SIZE_MB` | 50 | Reject files larger than 50 MB |
| `RATE_LIMIT_REQUESTS` | 5 | Per-user requests allowed... |
| `RATE_LIMIT_WINDOW_SEC` | 60 | ...within this many seconds |
| `CACHE_TTL_SEC` | 86400 | Transcription cache lifetime (24 h) |

Identical files (same Telegram `file_unique_id`) are served from cache without re-calling Gemini.

## Token usage logging

Every Gemini call logs `prompt_tokens`, `candidates_tokens`, and `total_tokens` to stdout for cost monitoring. On Render, watch the service logs for lines starting with `gemini_usage`.

## Tests

```sh
pip install -r requirements-dev.txt
pytest
```

Tests cover i18n fallback, cache, rate limiter, HTML escape and chunking, plus handler integration with mocked Gemini and Telegram objects.

## Troubleshooting

- **Frequent rate-limit messages**: tune `RATE_LIMIT_REQUESTS` upward or check that the Gemini API key has sufficient quota.
- **"File too long" / "File too large"**: raise `MAX_DURATION_SEC` / `MAX_FILE_SIZE_MB` in `.env` if you need to handle bigger files (note: cost scales with audio duration).
