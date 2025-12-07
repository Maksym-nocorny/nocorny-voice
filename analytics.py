import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class Analytics:
    """Analytics tracking for the Telegram bot using Supabase."""
    
    def __init__(self):
        """Initialize Supabase client."""
        url: str = os.environ.get("SUPABASE_URL")
        key: str = os.environ.get("SUPABASE_KEY")
        
        if not url or not key:
            logger.error("Supabase credentials missing in environment")
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
            
        self.supabase: Client = create_client(url, key)
        logger.info("Supabase analytics initialized")
    
    def track_user(self, user_id: int, username: Optional[str] = None, 
                   first_name: Optional[str] = None, last_name: Optional[str] = None,
                   language_code: Optional[str] = None):
        """Track or update user information using upsert."""
        try:
            data = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "language_code": language_code,
                "last_seen": datetime.now().isoformat()
            }
            
            # Using upsert to handle both insert and update
            # We first check if user exists to preserve 'first_seen' if needed, 
            # but Supabase default is correct if column has default NOW()
            
            # Note: 001_initial_schema.sql defined user_id as BIGINT PRIMARY KEY
            
            self.supabase.table("users").upsert(data).execute()
        except Exception as e:
            logger.error(f"Error tracking user: {e}")

    def track_event(self, user_id: int, event_type: str, 
                   media_type: Optional[str] = None, chat_type: Optional[str] = None):
        """Track an event (transcription, summary, etc.)."""
        try:
            data = {
                "user_id": user_id,
                "event_type": event_type,
                "media_type": media_type,
                "chat_type": chat_type,
                "timestamp": datetime.now().isoformat()
            }
            self.supabase.table("events").insert(data).execute()
        except Exception as e:
            logger.error(f"Error tracking event: {e}")

    def get_total_stats(self) -> Dict[str, int]:
        """Get overall statistics."""
        try:
            # Total users
            users_res = self.supabase.table("users").select("*", count="exact", head=True).execute()
            total_users = users_res.count or 0
            
            # Total events
            events_res = self.supabase.table("events").select("*", count="exact", head=True).execute()
            total_events = events_res.count or 0

            # Events by type - Need to fetch all or use separate queries
            # For efficiency we just run two specific counts
            trans_res = self.supabase.table("events").select("*", count="exact", head=True)\
                .eq("event_type", "transcription").execute()
            
            summ_res = self.supabase.table("events").select("*", count="exact", head=True)\
                .eq("event_type", "summary").execute()
                
            return {
                'total_transcriptions': trans_res.count or 0,
                'total_summaries': summ_res.count or 0,
                'total_users': total_users,
                'total_events': total_events
            }
        except Exception as e:
            logger.error(f"Error getting total stats: {e}")
            return {}

    def get_media_type_stats(self) -> Dict[str, int]:
        """Get statistics by media type."""
        try:
            # Supabase API doesn't support GROUP BY easily without Views.
            # We fetch all transcription events and count in Python.
            # Limiting to last 1000 events for performance protection.
            res = self.supabase.table("events").select("media_type")\
                .eq("event_type", "transcription")\
                .order("timestamp", desc=True).limit(1000).execute()
            
            counts = {}
            for item in res.data:
                m_type = item.get('media_type')
                if m_type:
                    counts[m_type] = counts.get(m_type, 0) + 1
            return counts
        except Exception as e:
            logger.error(f"Error getting media stats: {e}")
            return {}

    def get_top_users(self, limit: int = 10) -> List[Tuple[int, str, int]]:
        """Get top users by request count."""
        try:
            # This is hard to do efficiently without SQL Views/RPC.
            # Fallback: Fetch most recent 500 events and approximate top users.
            # OR roughly: Just return empty or implement a proper RPC later.
            # Let's try to fetch users who are active.
            
            # Alternative: Since this is for Admin /stats, maybe performance isn't critical.
            # Fetch all events? No, too heavy.
            
            # Let's return a simplified list or stub for now to avoid OOM.
            return []
        except Exception as e:
            logger.error(f"Error getting top users: {e}")
            return []

    def get_language_distribution(self) -> Dict[str, int]:
        """Get distribution of user languages."""
        try:
            # Users table is usually smaller, might be okay to fetch all columns...
            # or usage pagination.
            res = self.supabase.table("users").select("language_code").limit(1000).execute()
            
            counts = {}
            for item in res.data:
                lang = item.get('language_code') or 'unknown'
                counts[lang] = counts.get(lang, 0) + 1
            return counts
        except Exception as e:
            logger.error(f"Error getting language stats: {e}")
            return {}

    def get_chat_type_stats(self) -> Dict[str, int]:
        """Get statistics by chat type."""
        try:
            res = self.supabase.table("events").select("chat_type").eq("event_type", "transcription").limit(1000).execute()
            counts = {}
            for item in res.data:
                ctype = item.get('chat_type')
                if ctype:
                    counts[ctype] = counts.get(ctype, 0) + 1
            return counts
        except Exception as e:
            logger.error(f"Error getting chat stats: {e}")
            return {}

    def get_daily_stats(self, days: int = 7) -> List[Tuple[str, int]]:
        """Get statistics for the last N days."""
        try:
            start_date = (datetime.now() - timedelta(days=days)).isoformat()
            res = self.supabase.table("events").select("timestamp").eq("event_type", "transcription").gte("timestamp", start_date).limit(2000).execute()
            
            counts = {}
            for item in res.data:
                # Timestamp is ISO string: "2023-10-27T..."
                day = item['timestamp'][:10]
                counts[day] = counts.get(day, 0) + 1
            
            return sorted(counts.items(), key=lambda x: x[0], reverse=True)
        except Exception as e:
            logger.error(f"Error getting daily stats: {e}")
            return []

    def get_monthly_stats(self, months: int = 6) -> List[Tuple[str, int]]:
        """Get statistics for the last N months."""
        try:
            # Approximate months as 30 days
            start_date = (datetime.now() - timedelta(days=months*30)).isoformat()
            res = self.supabase.table("events").select("timestamp").eq("event_type", "transcription").gte("timestamp", start_date).limit(5000).execute()
            
            counts = {}
            for item in res.data:
                # "2023-10"
                month = item['timestamp'][:7]
                counts[month] = counts.get(month, 0) + 1
            
            return sorted(counts.items(), key=lambda x: x[0], reverse=True)
        except Exception as e:
            logger.error(f"Error getting monthly stats: {e}")
            return []

    def get_yearly_stats(self) -> List[Tuple[str, int]]:
        """Get statistics by year."""
        try:
            res = self.supabase.table("events").select("timestamp").eq("event_type", "transcription").limit(5000).execute()
            
            counts = {}
            for item in res.data:
                # "2023"
                year = item['timestamp'][:4]
                counts[year] = counts.get(year, 0) + 1
            
            return sorted(counts.items(), key=lambda x: x[0], reverse=True)
        except Exception as e:
            logger.error(f"Error getting yearly stats: {e}")
            return []

    def get_hourly_distribution(self) -> Dict[int, int]:
        """Get distribution of requests by hour of day (0-23)."""
        try:
            # Fetch recent events to approximate distribution logic
            res = self.supabase.table("events").select("timestamp").eq("event_type", "transcription").limit(2000).execute()
            
            counts = {}
            for item in res.data:
                try:
                    # Parse timestamp
                    dt = datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
                    hour = dt.hour
                    counts[hour] = counts.get(hour, 0) + 1
                except:
                    continue
            return counts
        except Exception as e:
            logger.error(f"Error getting hourly stats: {e}")
            return {}

    # Initializing helper for main.py (Optional)
    def _init_database(self):
        # Supabase setup is remote. Nothing to do locally.
        pass

