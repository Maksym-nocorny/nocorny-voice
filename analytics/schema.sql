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
    error_class        text
);

CREATE INDEX IF NOT EXISTS events_ts_desc           ON nocorny_voice.events (ts DESC);
CREATE INDEX IF NOT EXISTS events_user_ts           ON nocorny_voice.events (user_id, ts DESC);
CREATE INDEX IF NOT EXISTS events_type_ts           ON nocorny_voice.events (event_type, ts DESC);
CREATE INDEX IF NOT EXISTS events_success_user      ON nocorny_voice.events (user_id) WHERE event_type = 'transcribe_success';
CREATE INDEX IF NOT EXISTS users_last_seen_desc     ON nocorny_voice.users (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS users_total_events_desc  ON nocorny_voice.users (total_events DESC);

CREATE TABLE IF NOT EXISTS nocorny_voice.transcription_cache (
    content_hash   text         PRIMARY KEY,
    text           text         NOT NULL,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    last_hit_at    timestamptz  NOT NULL DEFAULT now(),
    hit_count      integer      NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS transcription_cache_last_hit ON nocorny_voice.transcription_cache (last_hit_at);
