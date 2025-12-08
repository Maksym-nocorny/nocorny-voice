import os
import logging
from datetime import datetime
from typing import Optional, Dict, List, Tuple

from supabase import create_client, Client

logger = logging.getLogger(__name__)


class Analytics:
    """Analytics tracking for the Telegram bot using Supabase."""
    
    def __init__(self):
        """Initialize analytics with Supabase connection."""
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")
        
        if not supabase_url or not supabase_key:
            logger.warning("Supabase credentials not found. Analytics will be disabled.")
            self.client = None
            return
        
        self.client: Client = create_client(supabase_url, supabase_key)
        logger.info("Supabase analytics initialized")
    
    def _is_enabled(self) -> bool:
        """Check if analytics is enabled."""
        return self.client is not None
    
    def track_user(self, user_id: int, username: Optional[str] = None, 
                   first_name: Optional[str] = None, last_name: Optional[str] = None,
                   language_code: Optional[str] = None):
        """Track or update user information."""
        if not self._is_enabled():
            return
        
        try:
            now = datetime.now().isoformat()
            
            # Check if user exists
            result = self.client.table('bot_users').select('user_id').eq('user_id', user_id).execute()
            
            if result.data:
                # Update existing user
                self.client.table('bot_users').update({
                    'username': username,
                    'first_name': first_name,
                    'last_name': last_name,
                    'language_code': language_code,
                    'last_seen': now
                }).eq('user_id', user_id).execute()
            else:
                # Insert new user
                self.client.table('bot_users').insert({
                    'user_id': user_id,
                    'username': username,
                    'first_name': first_name,
                    'last_name': last_name,
                    'language_code': language_code,
                    'first_seen': now,
                    'last_seen': now
                }).execute()
        except Exception as e:
            logger.error(f"Error tracking user: {e}")
    
    def track_event(self, user_id: int, event_type: str, 
                   media_type: Optional[str] = None, chat_type: Optional[str] = None):
        """Track an event (transcription, summary, etc.)."""
        if not self._is_enabled():
            return
        
        try:
            self.client.table('bot_events').insert({
                'user_id': user_id,
                'event_type': event_type,
                'media_type': media_type,
                'chat_type': chat_type
            }).execute()
        except Exception as e:
            logger.error(f"Error tracking event: {e}")
    
    def get_total_stats(self) -> Dict[str, int]:
        """Get overall statistics."""
        if not self._is_enabled():
            return {'total_transcriptions': 0, 'total_summaries': 0, 'total_users': 0, 'total_events': 0}
        
        try:
            # Count transcriptions
            trans_result = self.client.table('bot_events').select('id', count='exact').eq('event_type', 'transcription').execute()
            total_transcriptions = trans_result.count or 0
            
            # Count summaries
            sum_result = self.client.table('bot_events').select('id', count='exact').eq('event_type', 'summary').execute()
            total_summaries = sum_result.count or 0
            
            # Count users
            users_result = self.client.table('bot_users').select('user_id', count='exact').execute()
            total_users = users_result.count or 0
            
            # Count all events
            events_result = self.client.table('bot_events').select('id', count='exact').execute()
            total_events = events_result.count or 0
            
            return {
                'total_transcriptions': total_transcriptions,
                'total_summaries': total_summaries,
                'total_users': total_users,
                'total_events': total_events
            }
        except Exception as e:
            logger.error(f"Error getting total stats: {e}")
            return {'total_transcriptions': 0, 'total_summaries': 0, 'total_users': 0, 'total_events': 0}
    
    def get_media_type_stats(self) -> Dict[str, int]:
        """Get statistics by media type."""
        if not self._is_enabled():
            return {}
        
        try:
            result = self.client.table('bot_events').select('media_type').eq('event_type', 'transcription').not_.is_('media_type', 'null').execute()
            
            stats = {}
            for row in result.data:
                media_type = row['media_type']
                stats[media_type] = stats.get(media_type, 0) + 1
            return stats
        except Exception as e:
            logger.error(f"Error getting media type stats: {e}")
            return {}
    
    def get_top_users(self, limit: int = 10) -> List[Tuple[int, str, int]]:
        """Get top users by request count."""
        if not self._is_enabled():
            return []
        
        try:
            # Get all events with user info
            result = self.client.table('bot_events').select('user_id').execute()
            
            # Count events per user
            user_counts = {}
            for row in result.data:
                user_id = row['user_id']
                user_counts[user_id] = user_counts.get(user_id, 0) + 1
            
            # Get user details
            users_result = self.client.table('bot_users').select('user_id, username').execute()
            user_names = {u['user_id']: u['username'] for u in users_result.data}
            
            # Combine and sort
            top_users = [(user_id, user_names.get(user_id), count) 
                        for user_id, count in user_counts.items()]
            top_users.sort(key=lambda x: x[2], reverse=True)
            
            return top_users[:limit]
        except Exception as e:
            logger.error(f"Error getting top users: {e}")
            return []
    
    def get_user_stats(self, user_id: int) -> Dict[str, int]:
        """Get statistics for a specific user."""
        if not self._is_enabled():
            return {'transcriptions': 0, 'summaries': 0, 'total': 0}
        
        try:
            result = self.client.table('bot_events').select('event_type').eq('user_id', user_id).execute()
            
            stats = {'transcription': 0, 'summary': 0}
            for row in result.data:
                event_type = row['event_type']
                if event_type in stats:
                    stats[event_type] += 1
            
            return {
                'transcriptions': stats.get('transcription', 0),
                'summaries': stats.get('summary', 0),
                'total': sum(stats.values())
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {'transcriptions': 0, 'summaries': 0, 'total': 0}
    
    def get_language_distribution(self) -> Dict[str, int]:
        """Get distribution of user languages."""
        if not self._is_enabled():
            return {}
        
        try:
            result = self.client.table('bot_users').select('language_code').not_.is_('language_code', 'null').execute()
            
            stats = {}
            for row in result.data:
                lang = row['language_code']
                stats[lang] = stats.get(lang, 0) + 1
            return dict(sorted(stats.items(), key=lambda x: x[1], reverse=True))
        except Exception as e:
            logger.error(f"Error getting language distribution: {e}")
            return {}
    
    def get_chat_type_stats(self) -> Dict[str, int]:
        """Get statistics by chat type (private vs group)."""
        if not self._is_enabled():
            return {}
        
        try:
            result = self.client.table('bot_events').select('chat_type').not_.is_('chat_type', 'null').execute()
            
            stats = {}
            for row in result.data:
                chat_type = row['chat_type']
                stats[chat_type] = stats.get(chat_type, 0) + 1
            return stats
        except Exception as e:
            logger.error(f"Error getting chat type stats: {e}")
            return {}
    
    def get_daily_stats(self, days: int = 7) -> List[Tuple[str, int]]:
        """Get statistics for the last N days."""
        if not self._is_enabled():
            return []
        
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            
            result = self.client.table('bot_events').select('created_at').gte('created_at', cutoff).execute()
            
            # Group by day
            daily_counts = {}
            for row in result.data:
                day = row['created_at'][:10]  # Extract YYYY-MM-DD
                daily_counts[day] = daily_counts.get(day, 0) + 1
            
            return sorted(daily_counts.items(), reverse=True)
        except Exception as e:
            logger.error(f"Error getting daily stats: {e}")
            return []
    
    def get_monthly_stats(self, months: int = 6) -> List[Tuple[str, int]]:
        """Get statistics for the last N months."""
        if not self._is_enabled():
            return []
        
        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=months * 30)).isoformat()
            
            result = self.client.table('bot_events').select('created_at').gte('created_at', cutoff).execute()
            
            # Group by month
            monthly_counts = {}
            for row in result.data:
                month = row['created_at'][:7]  # Extract YYYY-MM
                monthly_counts[month] = monthly_counts.get(month, 0) + 1
            
            return sorted(monthly_counts.items(), reverse=True)
        except Exception as e:
            logger.error(f"Error getting monthly stats: {e}")
            return []
    
    def get_yearly_stats(self) -> List[Tuple[str, int]]:
        """Get statistics by year."""
        if not self._is_enabled():
            return []
        
        try:
            result = self.client.table('bot_events').select('created_at').execute()
            
            # Group by year
            yearly_counts = {}
            for row in result.data:
                year = row['created_at'][:4]  # Extract YYYY
                yearly_counts[year] = yearly_counts.get(year, 0) + 1
            
            return sorted(yearly_counts.items(), reverse=True)
        except Exception as e:
            logger.error(f"Error getting yearly stats: {e}")
            return []
    
    def get_hourly_distribution(self) -> Dict[int, int]:
        """Get distribution of requests by hour of day (0-23)."""
        if not self._is_enabled():
            return {}
        
        try:
            result = self.client.table('bot_events').select('created_at').execute()
            
            # Group by hour
            hourly_counts = {}
            for row in result.data:
                # Parse ISO timestamp and extract hour
                hour = int(row['created_at'][11:13])
                hourly_counts[hour] = hourly_counts.get(hour, 0) + 1
            
            return dict(sorted(hourly_counts.items()))
        except Exception as e:
            logger.error(f"Error getting hourly distribution: {e}")
            return {}
