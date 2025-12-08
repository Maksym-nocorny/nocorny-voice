# Analytics Feature

## Overview

The bot includes built-in analytics tracking to monitor usage statistics, stored in Supabase PostgreSQL.

## Usage

Send `/stats` command to the bot to view analytics:
- Total transcriptions and summaries
- Unique user count
- Media type breakdown (voice, video, audio, etc.)
- Top 10 most active users
- Language distribution
- Private vs group chat usage
- Time-based statistics (daily, monthly, yearly)
- Peak usage hours

## Data Tracked

- **User Information**: User ID, username, language preference
- **Transcription Requests**: Media type, chat type, timestamp
- **Summary Requests**: Chat type, timestamp
- **Activity Timeline**: First and last interaction times

## Database

Analytics data is stored in **Supabase PostgreSQL** with two tables:
- `bot_users` - User profiles and activity timestamps
- `bot_events` - Transcription and summary events

Data persists across deployments and is accessible via Supabase dashboard.

## Configuration

Required environment variables:
- `SUPABASE_URL` - Your Supabase project URL
- `SUPABASE_KEY` - Your Supabase service role key

## Privacy

All analytics are stored in your private Supabase project. Only basic usage metrics are tracked.
