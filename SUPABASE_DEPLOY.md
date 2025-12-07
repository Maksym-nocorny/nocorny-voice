# Deploying to Supabase Edge Functions

This guide covers deploying the Telegram bot to Supabase Edge Functions.

## Prerequisites

1. **Supabase CLI**: Install via Homebrew
   ```bash
   brew install supabase/tap/supabase
   ```

2. **Supabase Account**: Create or login at [supabase.com](https://supabase.com)

3. **Deno** (optional, for local development):
   ```bash
   brew install deno
   ```

## Step 1: Create Supabase Project

1. Go to [supabase.com/dashboard](https://supabase.com/dashboard)
2. Click "New project"
3. Name: `nocorny-voice`
4. Choose region closest to your users
5. Set a database password (save it!)
6. Wait for project to be created

## Step 2: Link Local Project

```bash
# Login to Supabase
supabase login

# Link to your remote project
supabase link --project-ref YOUR_PROJECT_REF
```

**Find your project ref**: Dashboard → Project Settings → General → Reference ID

## Step 3: Run Database Migration

**Option A: Via CLI** (if linked)
```bash
supabase db push
```

**Option B: Via Dashboard** (manual)
1. Go to Supabase Dashboard → SQL Editor
2. Copy contents of `supabase/migrations/20241207_initial_schema.sql`
3. Run the SQL

## Step 4: Configure Secrets

Set environment variables for the Edge Function:

```bash
supabase secrets set TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
supabase secrets set GEMINI_API_KEY=YOUR_GEMINI_KEY
supabase secrets set ADMIN_USER_ID=YOUR_USER_ID  # Optional
```

**For testing, use the test bot token:**
```bash
supabase secrets set TELEGRAM_BOT_TOKEN=8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw
```

## Step 5: Deploy Edge Function

```bash
supabase functions deploy telegram-webhook --no-verify-jwt
```

The `--no-verify-jwt` flag is required because Telegram doesn't send JWTs.

## Step 6: Get Function URL

After deployment, you'll see output like:
```
Deployed function telegram-webhook to https://YOUR_PROJECT_REF.supabase.co/functions/v1/telegram-webhook
```

Copy this URL.

## Step 7: Set Telegram Webhook

Replace `YOUR_BOT_TOKEN` and `YOUR_FUNCTION_URL`:

```bash
curl "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=YOUR_FUNCTION_URL"
```

**For the test bot:**
```bash
curl "https://api.telegram.org/bot8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw/setWebhook?url=https://YOUR_PROJECT_REF.supabase.co/functions/v1/telegram-webhook"
```

## Step 8: Verify Deployment

1. **Check webhook status**:
   ```bash
   curl "https://api.telegram.org/bot8057336327:AAHTnOn8GVLUAIxb21qXzUZh2TK0pt36Rqw/getWebhookInfo"
   ```

2. **Test the bot**: Send `/start` to the test bot

3. **Check logs**:
   ```bash
   supabase functions logs telegram-webhook
   ```

## Monitoring & Debugging

### View Logs
```bash
supabase functions logs telegram-webhook --tail
```

### Re-deploy After Changes
```bash
supabase functions deploy telegram-webhook --no-verify-jwt
```

## Switching to Production

When ready to switch the production bot:

1. Update secrets with production token:
   ```bash
   supabase secrets set TELEGRAM_BOT_TOKEN=YOUR_PRODUCTION_TOKEN
   ```

2. Set webhook for production bot:
   ```bash
   curl "https://api.telegram.org/botYOUR_PRODUCTION_TOKEN/setWebhook?url=YOUR_FUNCTION_URL"
   ```

3. Remove webhook from Render (if still set):
   ```bash
   curl "https://api.telegram.org/botYOUR_PRODUCTION_TOKEN/deleteWebhook"
   ```

## Troubleshooting

### Function Times Out
- Edge Functions have a 10-second timeout on free tier (60s on paid)
- Long audio files may timeout during transcription
- Consider upgrading to paid tier if needed

### Database Connection Issues
- Verify SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set
- These are automatically available in Edge Functions

### Telegram Returns 404
- Verify the function is deployed: `supabase functions list`
- Check the webhook URL is correct
- Ensure `--no-verify-jwt` was used during deployment
