import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

class Analytics:
    """Analytics tracking for the Telegram bot."""
    
    def __init__(self, db_path: str = "bot_analytics.db"):
        """Initialize analytics with SQLite database."""
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Create database tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language_code TEXT,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP
                )
            """)
            
            # Events table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_type TEXT,
                    media_type TEXT,
                    chat_type TEXT,
                    timestamp TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            # Create indexes for better query performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_user_id 
                ON events(user_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_type 
                ON events(event_type)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp 
                ON events(timestamp)
            """)
            
            conn.commit()
            logger.info("Analytics database initialized")
    
    def track_user(self, user_id: int, username: Optional[str] = None, 
                   first_name: Optional[str] = None, last_name: Optional[str] = None,
                   language_code: Optional[str] = None):
        """Track or update user information."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            now = datetime.now()
            
            # Check if user exists
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone()
            
            if exists:
                # Update existing user
                cursor.execute("""
                    UPDATE users 
                    SET username = ?, first_name = ?, last_name = ?, 
                        language_code = ?, last_seen = ?
                    WHERE user_id = ?
                """, (username, first_name, last_name, language_code, now, user_id))
            else:
                # Insert new user
                cursor.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name, 
                                     language_code, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (user_id, username, first_name, last_name, language_code, now, now))
            
            conn.commit()
    
    def track_event(self, user_id: int, event_type: str, 
                   media_type: Optional[str] = None, chat_type: Optional[str] = None):
        """Track an event (transcription, summary, etc.)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO events (user_id, event_type, media_type, chat_type, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, event_type, media_type, chat_type, datetime.now()))
            conn.commit()
    
    def get_total_stats(self) -> Dict[str, int]:
        """Get overall statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Total events by type
            cursor.execute("""
                SELECT event_type, COUNT(*) 
                FROM events 
                GROUP BY event_type
            """)
            event_counts = dict(cursor.fetchall())
            
            # Total unique users
            cursor.execute("SELECT COUNT(DISTINCT user_id) FROM users")
            total_users = cursor.fetchone()[0]
            
            # Total events
            cursor.execute("SELECT COUNT(*) FROM events")
            total_events = cursor.fetchone()[0]
            
            return {
                'total_transcriptions': event_counts.get('transcription', 0),
                'total_summaries': event_counts.get('summary', 0),
                'total_users': total_users,
                'total_events': total_events
            }
    
    def get_media_type_stats(self) -> Dict[str, int]:
        """Get statistics by media type."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT media_type, COUNT(*) 
                FROM events 
                WHERE event_type = 'transcription' AND media_type IS NOT NULL
                GROUP BY media_type
            """)
            return dict(cursor.fetchall())
    
    def get_top_users(self, limit: int = 10) -> List[Tuple[int, str, int]]:
        """Get top users by request count."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.user_id, u.username, COUNT(e.id) as request_count
                FROM users u
                LEFT JOIN events e ON u.user_id = e.user_id
                GROUP BY u.user_id
                ORDER BY request_count DESC
                LIMIT ?
            """, (limit,))
            return cursor.fetchall()
    
    def get_user_stats(self, user_id: int) -> Dict[str, int]:
        """Get statistics for a specific user."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Count by event type
            cursor.execute("""
                SELECT event_type, COUNT(*) 
                FROM events 
                WHERE user_id = ?
                GROUP BY event_type
            """, (user_id,))
            event_counts = dict(cursor.fetchall())
            
            return {
                'transcriptions': event_counts.get('transcription', 0),
                'summaries': event_counts.get('summary', 0),
                'total': sum(event_counts.values())
            }
    
    def get_language_distribution(self) -> Dict[str, int]:
        """Get distribution of user languages."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT language_code, COUNT(*) 
                FROM users 
                WHERE language_code IS NOT NULL
                GROUP BY language_code
                ORDER BY COUNT(*) DESC
            """)
            return dict(cursor.fetchall())
    
    def get_chat_type_stats(self) -> Dict[str, int]:
        """Get statistics by chat type (private vs group)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT chat_type, COUNT(*) 
                FROM events 
                WHERE chat_type IS NOT NULL
                GROUP BY chat_type
            """)
            return dict(cursor.fetchall())
    
    def get_daily_stats(self, days: int = 7) -> List[Tuple[str, int]]:
        """Get statistics for the last N days."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DATE(timestamp) as day, COUNT(*) as count
                FROM events
                WHERE timestamp >= datetime('now', '-' || ? || ' days')
                GROUP BY DATE(timestamp)
                ORDER BY day DESC
            """, (days,))
            return cursor.fetchall()
    
    def get_monthly_stats(self, months: int = 6) -> List[Tuple[str, int]]:
        """Get statistics for the last N months."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT strftime('%Y-%m', timestamp) as month, COUNT(*) as count
                FROM events
                WHERE timestamp >= datetime('now', '-' || ? || ' months')
                GROUP BY strftime('%Y-%m', timestamp)
                ORDER BY month DESC
            """, (months,))
            return cursor.fetchall()
    
    def get_yearly_stats(self) -> List[Tuple[str, int]]:
        """Get statistics by year."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT strftime('%Y', timestamp) as year, COUNT(*) as count
                FROM events
                GROUP BY strftime('%Y', timestamp)
                ORDER BY year DESC
            """)
            return cursor.fetchall()
    
    def get_hourly_distribution(self) -> Dict[int, int]:
        """Get distribution of requests by hour of day (0-23)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour, COUNT(*) as count
                FROM events
                GROUP BY hour
                ORDER BY hour
            """)
            return dict(cursor.fetchall())
