# Get Your Telegram User ID

To restrict the `/stats` command to yourself only, you need to add your Telegram user ID to the `.env` file.

## How to Get Your User ID

### Option 1: Use a Bot
1. Open Telegram and search for `@userinfobot`
2. Start a chat and send any message
3. The bot will reply with your user ID

### Option 2: Use the Bot Logs
1. Send the `/start` command to your bot
2. Check the bot logs - your user ID will be visible in the analytics tracking

## Add to .env File

Once you have your user ID, add this line to your `.env` file:

```
ADMIN_USER_ID=YOUR_USER_ID_HERE
```

For example:
```
ADMIN_USER_ID=123456789
```

## How It Works

- **If `ADMIN_USER_ID` is set**: Only that user can use `/stats`
- **If `ADMIN_USER_ID` is not set**: Anyone can use `/stats` (shows global stats)

Unauthorized users will see: "⛔ You are not authorized to view statistics."
