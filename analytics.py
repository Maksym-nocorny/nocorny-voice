"""
Analytics module using Supabase for persistent storage.
Tracks user activity, transcription events, and summary requests.
"""

import os
import logging
from datetime import datetime, timedelta
from supabase import create_client, Client

logger = logging.getLogger(__name__)


class Analytics:
    """Analytics tracker using Supabase PostgreSQL database."""
    
    def __init__(self):
        """Initialize Supabase client."""
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")
        
        if not supabase_url or not supabase_key:
            logger.warning("Supabase credentials not found. Analytics will be disabled.")
            self.client = None
        else:
            try:
                self.client: Client = create_client(supabase_url, supabase_key)
                logger.info("Supabase analytics initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Supabase: {e}")
                self.client = None
    
    def _is_enabled(self) -> bool:
        """Check if analytics is enabled."""
        return self.client is not None
    
    def track_user(self, user_id: int, username: str = None, first_name: str = None, 
                   last_name: str = None, language_code: str = None):
        """Track or update user information."""
        if not self._is_enabled():
            return
            
        try:
            # Upsert user - insert or update on conflict
            self.client.table("bot_users").upsert({
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "language_code": language_code,
                "last_seen": datetime.utcnow().isoformat()
            }, on_conflict="user_id").execute()
        except Exception as e:
            logger.error(f"Failed to track user: {e}")
    
    def track_event(self, user_id: int, event_type: str, media_type: str = None, 
                    chat_type: str = None):
        """Track an analytics event (transcription or summary)."""
        if not self._is_enabled():
            return
            
        try:
            self.client.table("bot_events").insert({
                "user_id": user_id,
                "event_type": event_type,
                "media_type": media_type,
                "chat_type": chat_type
            }).execute()
        except Exception as e:
            logger.error(f"Failed to track event: {e}")
    
    def get_total_stats(self) -> dict:
        """Get total statistics."""
        if not self._is_enabled():
            return {"total_events": 0, "total_transcriptions": 0, "total_summaries": 0, "total_users": 0}
        
        try:
            # Total events by type
            events = self.client.table("bot_events").select("event_type").execute()
            total_transcriptions = sum(1 for e in events.data if e["event_type"] == "transcription")
            total_summaries = sum(1 for e in events.data if e["event_type"] == "summary")
            
            # Total users
            users = self.client.table("bot_users").select("user_id", count="exact").execute()
            total_users = users.count if users.count else len(users.data)
            
            return {
                "total_events": len(events.data),
                "total_transcriptions": total_transcriptions,
                "total_summaries": total_summaries,
                "total_users": total_users
            }
        except Exception as e:
            logger.error(f"Failed to get total stats: {e}")
            return {"total_events": 0, "total_transcriptions": 0, "total_summaries": 0, "total_users": 0}
    
    def get_media_type_stats(self) -> dict:
        """Get statistics by media type."""
        if not self._is_enabled():
            return {}
        
        try:
            events = self.client.table("bot_events").select("media_type").not_.is_("media_type", "null").execute()
            stats = {}
            for event in events.data:
                media_type = event["media_type"]
                if media_type:
                    stats[media_type] = stats.get(media_type, 0) + 1
            return stats
        except Exception as e:
            logger.error(f"Failed to get media type stats: {e}")
            return {}
    
    def get_chat_type_stats(self) -> dict:
        """Get statistics by chat type."""
        if not self._is_enabled():
            return {}
        
        try:
            events = self.client.table("bot_events").select("chat_type").not_.is_("chat_type", "null").execute()
            stats = {}
            for event in events.data:
                chat_type = event["chat_type"]
                if chat_type:
                    stats[chat_type] = stats.get(chat_type, 0) + 1
            return stats
        except Exception as e:
            logger.error(f"Failed to get chat type stats: {e}")
            return {}
    
    def get_top_users(self, limit: int = 10) -> list:
        """Get top users by number of events."""
        if not self._is_enabled():
            return []
        
        try:
            # Get event counts per user
            events = self.client.table("bot_events").select("user_id").execute()
            user_counts = {}
            for event in events.data:
                user_id = event["user_id"]
                user_counts[user_id] = user_counts.get(user_id, 0) + 1
            
            # Get top users
            top_user_ids = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
            
            # Fetch user details
            result = []
            for user_id, count in top_user_ids:
                user = self.client.table("bot_users").select("username").eq("user_id", user_id).execute()
                username = user.data[0]["username"] if user.data else None
                result.append((user_id, username, count))
            
            return result
        except Exception as e:
            logger.error(f"Failed to get top users: {e}")
            return []
    
    def get_language_distribution(self) -> dict:
        """Get distribution of user languages."""
        if not self._is_enabled():
            return {}
        
        try:
            users = self.client.table("bot_users").select("language_code").execute()
            stats = {}
            for user in users.data:
                lang = user["language_code"] or "unknown"
                stats[lang] = stats.get(lang, 0) + 1
            return stats
        except Exception as e:
            logger.error(f"Failed to get language distribution: {e}")
            return {}
    
    def get_daily_stats(self, days: int = 7) -> list:
        """Get daily statistics for the last N days."""
        if not self._is_enabled():
            return []
        
        try:
            # Calculate date range
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            events = self.client.table("bot_events").select("created_at").gte(
                "created_at", start_date.isoformat()
            ).execute()
            
            # Group by date
            daily_counts = {}
            for event in events.data:
                date = event["created_at"][:10]  # Extract YYYY-MM-DD
                daily_counts[date] = daily_counts.get(date, 0) + 1
            
            # Sort by date
            return sorted(daily_counts.items())
        except Exception as e:
            logger.error(f"Failed to get daily stats: {e}")
            return []
    
    def get_monthly_stats(self, months: int = 6) -> list:
        """Get monthly statistics for the last N months."""
        if not self._is_enabled():
            return []
        
        try:
            # Calculate date range
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=months * 30)
            
            events = self.client.table("bot_events").select("created_at").gte(
                "created_at", start_date.isoformat()
            ).execute()
            
            # Group by month
            monthly_counts = {}
            for event in events.data:
                month = event["created_at"][:7]  # Extract YYYY-MM
                monthly_counts[month] = monthly_counts.get(month, 0) + 1
            
            # Sort by month
            return sorted(monthly_counts.items())
        except Exception as e:
            logger.error(f"Failed to get monthly stats: {e}")
            return []
    
    def get_yearly_stats(self) -> list:
        """Get yearly statistics."""
        if not self._is_enabled():
            return []
        
        try:
            events = self.client.table("bot_events").select("created_at").execute()
            
            # Group by year
            yearly_counts = {}
            for event in events.data:
                year = event["created_at"][:4]  # Extract YYYY
                yearly_counts[year] = yearly_counts.get(year, 0) + 1
            
            # Sort by year
            return sorted(yearly_counts.items())
        except Exception as e:
            logger.error(f"Failed to get yearly stats: {e}")
            return []
    
    def get_hourly_distribution(self) -> dict:
        """Get distribution of events by hour of day."""
        if not self._is_enabled():
            return {}
        
        try:
            events = self.client.table("bot_events").select("created_at").execute()
            
            # Group by hour
            hourly_counts = {}
            for event in events.data:
                # Parse ISO timestamp and extract hour
                hour = int(event["created_at"][11:13])  # Extract HH
                hourly_counts[hour] = hourly_counts.get(hour, 0) + 1
            
            return hourly_counts
        except Exception as e:
            logger.error(f"Failed to get hourly distribution: {e}")
            return {}
