# Analytics Database Schema for Supabase
# Run this in the Supabase SQL Editor

-- Bot Users table (tracking Telegram users)
CREATE TABLE IF NOT EXISTS bot_users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    language_code TEXT,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW()
);

-- Bot Events table (tracking transcriptions, summaries, etc.)
CREATE TABLE IF NOT EXISTS bot_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES bot_users(user_id),
    event_type TEXT NOT NULL,
    media_type TEXT,
    chat_type TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_bot_events_user_id ON bot_events(user_id);
CREATE INDEX IF NOT EXISTS idx_bot_events_type ON bot_events(event_type);
CREATE INDEX IF NOT EXISTS idx_bot_events_created_at ON bot_events(created_at);

-- Disable RLS for server-side access (bot runs with anon key)
ALTER TABLE bot_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_events ENABLE ROW LEVEL SECURITY;

-- Create policies to allow all operations (since this is a trusted server-side bot)
CREATE POLICY "Allow all for bot_users" ON bot_users FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for bot_events" ON bot_events FOR ALL USING (true) WITH CHECK (true);
