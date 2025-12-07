# 🚀 Supabase Deployment Guide

## What You Need

- ✅ Supabase account (free at [supabase.com](https://supabase.com))
- ✅ Your existing Gemini API key
- ✅ Telegram test bot token: `8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw`

---

## Step 1: Create Supabase Project

1. Go to [supabase.com/dashboard](https://supabase.com/dashboard)
2. Click **"New Project"**
3. Name it: `nocorny-voice`
4. Set a database password (save it somewhere!)
5. Choose region closest to you
6. Wait ~2 minutes for setup

---

## Step 2: Create Database Tables

1. In your project, go to **SQL Editor** (left sidebar)
2. Click **"New Query"**
3. Paste this SQL:

```sql
-- Users table
CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    language_code TEXT,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW()
);

-- Events table
CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(user_id),
    event_type TEXT NOT NULL,
    media_type TEXT,
    chat_type TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Transcriptions table
CREATE TABLE transcriptions (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    transcription_text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(chat_id, message_id)
);

-- Indexes
CREATE INDEX idx_events_user_id ON events(user_id);
CREATE INDEX idx_events_type ON events(event_type);
```

4. Click **"Run"** (or Cmd+Enter)
5. You should see "Success" ✅

---

## Step 3: Set Environment Variables

1. Go to **Edge Functions** (left sidebar)
2. Click **"Manage Secrets"** button (top right)
3. Add these secrets one by one:

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | `8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw` |
| `GEMINI_API_KEY` | Your Gemini API key |
| `ADMIN_USER_ID` | Your Telegram user ID (for /stats access) |

---

## Step 4: Install Supabase CLI

Open Terminal and run:

```bash
npm install -g supabase
```

Then login:

```bash
supabase login
```

This opens a browser - click "Authorize".

---

## Step 5: Link Project

1. In Supabase Dashboard, go to **Settings** → **General**
2. Copy your **Reference ID** (looks like `abcdefghijklmnop`)
3. In Terminal, navigate to your project:

```bash
cd "/Users/maksym/telegram bot Nocorny.voice"
```

4. Link the project:

```bash
supabase link --project-ref YOUR_REFERENCE_ID
```

---

## Step 6: Deploy the Function

Run this command:

```bash
supabase functions deploy telegram-webhook --no-verify-jwt
```

You'll see output like:
```
Deploying function telegram-webhook...
Function deployed to https://YOUR_PROJECT.supabase.co/functions/v1/telegram-webhook
```

**Copy this URL!** ☝️

---

## Step 7: Connect to Telegram

Replace `YOUR_FUNCTION_URL` with the URL from Step 6:

```bash
curl -X POST "https://api.telegram.org/bot8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "YOUR_FUNCTION_URL"}'
```

You should see: `{"ok":true,"result":true,"description":"Webhook was set"}`

---

## Step 8: Test It! 🎉

1. Open Telegram
2. Search for your test bot (the one with token starting `8057336327`)
3. Send `/start`
4. Send a voice message
5. It should transcribe and show "Summarize" button!

---

## Troubleshooting

**Check logs:**
- Supabase Dashboard → Edge Functions → Select `telegram-webhook` → Logs

**Check database:**
- Supabase Dashboard → Table Editor → See if users/events appear

**Reset webhook:**
```bash
curl "https://api.telegram.org/bot8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw/deleteWebhook"
```

---

## When Ready for Production

1. Update secrets with production bot token
2. Redeploy function
3. Set webhook with production bot token URL
