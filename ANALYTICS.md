# Analytics Feature

## Overview

The bot now includes built-in analytics tracking to monitor usage statistics.

## Usage

Send `/stats` command to the bot to view analytics:
- Total transcriptions and summaries
- Unique user count
- Media type breakdown (voice, video, audio, etc.)
- Top 10 most active users
- Language distribution
- Private vs group chat usage

## Data Tracked

- **User Information**: User ID, username, language preference
- **Transcription Requests**: Media type, chat type, timestamp
- **Summary Requests**: Chat type, timestamp
- **Activity Timeline**: First and last interaction times

## Database

Analytics data is stored in `bot_analytics.db` (SQLite) in the bot's root directory. The database is created automatically on first run and persists across restarts.

## Privacy

All analytics are stored locally and are not shared with third parties. Only basic usage metrics are tracked to help understand bot usage patterns.
