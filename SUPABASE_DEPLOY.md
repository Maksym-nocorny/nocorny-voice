# Deploying to Supabase Edge Functions

## Prerequisites

1. **Supabase CLI**: Install the Supabase CLI
   ```bash
   npm install -g supabase
   ```

2. **Login to Supabase**:
   ```bash
   supabase login
   ```

3. **Link your project**:
   ```bash
   supabase link --project-ref YOUR_PROJECT_REF
   ```

## Step 1: Create Database Tables

Run the schema migration in Supabase SQL Editor:

1. Go to [Supabase Dashboard](https://supabase.com/dashboard)
2. Open your project → SQL Editor
3. Copy contents of `supabase/migrations/001_initial_schema.sql`
4. Run the query

## Step 2: Set Environment Variables

In Supabase Dashboard → Edge Functions → Environment Variables:

```
TELEGRAM_BOT_TOKEN=8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw
GEMINI_API_KEY=your-gemini-api-key
ADMIN_USER_ID=your-telegram-user-id
```

> **Note**: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are auto-injected.

## Step 3: Deploy Edge Function

```bash
supabase functions deploy telegram-webhook --no-verify-jwt
```

The `--no-verify-jwt` flag is required for webhook endpoints.

## Step 4: Set Telegram Webhook

Get your function URL (shown after deploy):
```
https://YOUR_PROJECT_REF.supabase.co/functions/v1/telegram-webhook
```

Set the webhook:
```bash
curl -X POST "https://api.telegram.org/bot8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://YOUR_PROJECT_REF.supabase.co/functions/v1/telegram-webhook"}'
```

## Step 5: Test the Bot

1. Open Telegram
2. Search for your bot
3. Send `/start`
4. Send a voice message
5. Click "Summarize" button

## Local Development

1. Create `.env` file:
   ```
   TELEGRAM_BOT_TOKEN=8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw
   GEMINI_API_KEY=your-gemini-api-key
   ADMIN_USER_ID=your-telegram-user-id
   SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
   ```

2. Run locally:
   ```bash
   supabase functions serve telegram-webhook --env-file .env
   ```

3. Use [ngrok](https://ngrok.com) to expose local endpoint for testing.

## Monitoring

- **Logs**: Supabase Dashboard → Edge Functions → Logs
- **Database**: Supabase Dashboard → Table Editor

## Rollback

If issues occur:
1. Delete webhook: `curl "https://api.telegram.org/botTOKEN/deleteWebhook"`
2. Original bot on Render continues working on the `main` branch
