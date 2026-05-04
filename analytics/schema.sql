-- Analytics schema for Nocorny.voice. Idempotent; safe to run on every startup.

CREATE SCHEMA IF NOT EXISTS nocorny_voice;

CREATE TABLE IF NOT EXISTS nocorny_voice.users (
    user_id        bigint       PRIMARY KEY,
    username       text,
    first_name     text,
    last_name      text,
    language_code  text,
    first_seen_at  timestamptz  NOT NULL DEFAULT now(),
    last_seen_at   timestamptz  NOT NULL DEFAULT now(),
    total_events   integer      NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS nocorny_voice.events (
    id                 bigserial    PRIMARY KEY,
    ts                 timestamptz  NOT NULL DEFAULT now(),
    user_id            bigint       NOT NULL,
    chat_id            bigint       NOT NULL,
    chat_type          text         NOT NULL,
    request_id         text         NOT NULL,
    event_type         text         NOT NULL,
    media_type         text,
    duration_sec       integer,
    file_size_bytes    bigint,
    mime_type          text,
    prompt_tokens      integer,
    candidates_tokens  integer,
    total_tokens       integer,
    latency_ms         integer,
    error_class        text,
    detected_language  text
);

ALTER TABLE nocorny_voice.events ADD COLUMN IF NOT EXISTS detected_language text;

CREATE TABLE IF NOT EXISTS nocorny_voice.chats (
    chat_id        bigint       PRIMARY KEY,
    chat_type      text         NOT NULL,
    title          text,
    first_seen_at  timestamptz  NOT NULL DEFAULT now(),
    last_seen_at   timestamptz  NOT NULL DEFAULT now(),
    total_events   integer      NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS events_ts_desc           ON nocorny_voice.events (ts DESC);
CREATE INDEX IF NOT EXISTS events_user_ts           ON nocorny_voice.events (user_id, ts DESC);
CREATE INDEX IF NOT EXISTS events_type_ts           ON nocorny_voice.events (event_type, ts DESC);
CREATE INDEX IF NOT EXISTS events_success_user      ON nocorny_voice.events (user_id) WHERE event_type = 'transcribe_success';
CREATE INDEX IF NOT EXISTS events_detected_language ON nocorny_voice.events (detected_language) WHERE detected_language IS NOT NULL;
CREATE INDEX IF NOT EXISTS users_last_seen_desc     ON nocorny_voice.users (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS users_total_events_desc  ON nocorny_voice.users (total_events DESC);
CREATE INDEX IF NOT EXISTS chats_total_events_desc  ON nocorny_voice.chats (total_events DESC);
CREATE INDEX IF NOT EXISTS chats_type               ON nocorny_voice.chats (chat_type);

-- Backfill chats from historical events. Only runs for chats not yet present
-- (ON CONFLICT DO NOTHING). Live tracker updates take over from there.
INSERT INTO nocorny_voice.chats (chat_id, chat_type, total_events, first_seen_at, last_seen_at)
SELECT chat_id,
       (array_agg(chat_type ORDER BY ts DESC))[1],
       count(*),
       min(ts),
       max(ts)
FROM nocorny_voice.events
GROUP BY chat_id
ON CONFLICT (chat_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS nocorny_voice.transcription_cache (
    content_hash       text         PRIMARY KEY,
    text               text         NOT NULL,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    last_hit_at        timestamptz  NOT NULL DEFAULT now(),
    hit_count          integer      NOT NULL DEFAULT 0,
    detected_language  text
);

ALTER TABLE nocorny_voice.transcription_cache ADD COLUMN IF NOT EXISTS detected_language text;

CREATE INDEX IF NOT EXISTS transcription_cache_last_hit ON nocorny_voice.transcription_cache (last_hit_at);
