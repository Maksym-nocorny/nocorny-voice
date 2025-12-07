// Analytics module for tracking bot usage
// Converted from Python analytics.py
// Uses Supabase PostgreSQL instead of SQLite

import { SupabaseClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { TotalStats, UserRecord, EventRecord } from './types.ts';

export class Analytics {
    private supabase: SupabaseClient;

    constructor(supabase: SupabaseClient) {
        this.supabase = supabase;
    }

    /**
     * Track or update user information
     */
    async trackUser(
        userId: number,
        username?: string,
        firstName?: string,
        lastName?: string,
        languageCode?: string
    ): Promise<void> {
        try {
            const now = new Date();

            // Check if user exists
            const { data: existingUser } = await this.supabase
                .from('users')
                .select('user_id')
                .eq('user_id', userId)
                .single();

            if (existingUser) {
                // Update existing user
                await this.supabase
                    .from('users')
                    .update({
                        username,
                        first_name: firstName,
                        last_name: lastName,
                        language_code: languageCode,
                        last_seen: now.toISOString()
                    })
                    .eq('user_id', userId);
            } else {
                // Insert new user
                await this.supabase
                    .from('users')
                    .insert({
                        user_id: userId,
                        username,
                        first_name: firstName,
                        last_name: lastName,
                        language_code: languageCode,
                        first_seen: now.toISOString(),
                        last_seen: now.toISOString()
                    });
            }
        } catch (error) {
            console.error('Error tracking user:', error);
        }
    }

    /**
     * Track an event (transcription, summary, etc.)
     */
    async trackEvent(
        userId: number,
        eventType: 'transcription' | 'summary',
        mediaType?: 'voice' | 'video_note' | 'audio' | 'video',
        chatType?: 'private' | 'group'
    ): Promise<void> {
        try {
            await this.supabase
                .from('events')
                .insert({
                    user_id: userId,
                    event_type: eventType,
                    media_type: mediaType,
                    chat_type: chatType,
                    timestamp: new Date().toISOString()
                });
        } catch (error) {
            console.error('Error tracking event:', error);
        }
    }

    /**
     * Get overall statistics
     */
    async getTotalStats(): Promise<TotalStats> {
        try {
            // Get event counts by type
            const { data: events } = await this.supabase
                .from('events')
                .select('event_type');

            const transcriptions = events?.filter(e => e.event_type === 'transcription').length || 0;
            const summaries = events?.filter(e => e.event_type === 'summary').length || 0;

            // Get unique users count
            const { count: userCount } = await this.supabase
                .from('users')
                .select('user_id', { count: 'exact', head: true });

            // Get total events count
            const { count: eventCount } = await this.supabase
                .from('events')
                .select('id', { count: 'exact', head: true });

            return {
                total_transcriptions: transcriptions,
                total_summaries: summaries,
                total_users: userCount || 0,
                total_events: eventCount || 0
            };
        } catch (error) {
            console.error('Error getting total stats:', error);
            return {
                total_transcriptions: 0,
                total_summaries: 0,
                total_users: 0,
                total_events: 0
            };
        }
    }

    /**
     * Get statistics by media type
     */
    async getMediaTypeStats(): Promise<Record<string, number>> {
        try {
            const { data } = await this.supabase
                .from('events')
                .select('media_type')
                .eq('event_type', 'transcription')
                .not('media_type', 'is', null);

            const stats: Record<string, number> = {};
            data?.forEach(row => {
                if (row.media_type) {
                    stats[row.media_type] = (stats[row.media_type] || 0) + 1;
                }
            });

            return stats;
        } catch (error) {
            console.error('Error getting media type stats:', error);
            return {};
        }
    }

    /**
     * Get top users by request count
     */
    async getTopUsers(limit: number = 10): Promise<Array<[number, string | null, number]>> {
        try {
            const { data } = await this.supabase
                .from('users')
                .select(`
          user_id,
          username,
          events:events(count)
        `)
                .order('events.count', { ascending: false })
                .limit(limit);

            if (!data) return [];

            // Transform to match Python format: [user_id, username, count]
            return data.map(row => [
                row.user_id,
                row.username || null,
                (row.events as any)?.length || 0
            ]);
        } catch (error) {
            console.error('Error getting top users:', error);
            return [];
        }
    }

    /**
     * Get distribution of user languages
     */
    async getLanguageDistribution(): Promise<Record<string, number>> {
        try {
            const { data } = await this.supabase
                .from('users')
                .select('language_code')
                .not('language_code', 'is', null);

            const stats: Record<string, number> = {};
            data?.forEach(row => {
                if (row.language_code) {
                    stats[row.language_code] = (stats[row.language_code] || 0) + 1;
                }
            });

            return stats;
        } catch (error) {
            console.error('Error getting language distribution:', error);
            return {};
        }
    }

    /**
     * Get statistics by chat type (private vs group)
     */
    async getChatTypeStats(): Promise<Record<string, number>> {
        try {
            const { data } = await this.supabase
                .from('events')
                .select('chat_type')
                .not('chat_type', 'is', null);

            const stats: Record<string, number> = {};
            data?.forEach(row => {
                if (row.chat_type) {
                    stats[row.chat_type] = (stats[row.chat_type] || 0) + 1;
                }
            });

            return stats;
        } catch (error) {
            console.error('Error getting chat type stats:', error);
            return {};
        }
    }

    /**
     * Get statistics for the last N days
     */
    async getDailyStats(days: number = 7): Promise<Array<[string, number]>> {
        try {
            const daysAgo = new Date();
            daysAgo.setDate(daysAgo.getDate() - days);

            const { data } = await this.supabase
                .from('events')
                .select('timestamp')
                .gte('timestamp', daysAgo.toISOString());

            // Group by date
            const stats: Record<string, number> = {};
            data?.forEach(row => {
                const date = new Date(row.timestamp).toISOString().split('T')[0];
                stats[date] = (stats[date] || 0) + 1;
            });

            // Convert to array and sort by date descending
            return Object.entries(stats).sort((a, b) => b[0].localeCompare(a[0]));
        } catch (error) {
            console.error('Error getting daily stats:', error);
            return [];
        }
    }

    /**
     * Get statistics for the last N months
     */
    async getMonthlyStats(months: number = 6): Promise<Array<[string, number]>> {
        try {
            const monthsAgo = new Date();
            monthsAgo.setMonth(monthsAgo.getMonth() - months);

            const { data } = await this.supabase
                .from('events')
                .select('timestamp')
                .gte('timestamp', monthsAgo.toISOString());

            // Group by year-month
            const stats: Record<string, number> = {};
            data?.forEach(row => {
                const yearMonth = new Date(row.timestamp).toISOString().substring(0, 7); // YYYY-MM
                stats[yearMonth] = (stats[yearMonth] || 0) + 1;
            });

            // Convert to array and sort by month descending
            return Object.entries(stats).sort((a, b) => b[0].localeCompare(a[0]));
        } catch (error) {
            console.error('Error getting monthly stats:', error);
            return [];
        }
    }

    /**
     * Get statistics by year
     */
    async getYearlyStats(): Promise<Array<[string, number]>> {
        try {
            const { data } = await this.supabase
                .from('events')
                .select('timestamp');

            // Group by year
            const stats: Record<string, number> = {};
            data?.forEach(row => {
                const year = new Date(row.timestamp).getFullYear().toString();
                stats[year] = (stats[year] || 0) + 1;
            });

            // Convert to array and sort by year descending
            return Object.entries(stats).sort((a, b) => b[0].localeCompare(a[0]));
        } catch (error) {
            console.error('Error getting yearly stats:', error);
            return [];
        }
    }

    /**
     * Get distribution of requests by hour of day (0-23)
     */
    async getHourlyDistribution(): Promise<Record<number, number>> {
        try {
            const { data } = await this.supabase
                .from('events')
                .select('timestamp');

            // Group by hour
            const stats: Record<number, number> = {};
            data?.forEach(row => {
                const hour = new Date(row.timestamp).getUTCHours();
                stats[hour] = (stats[hour] || 0) + 1;
            });

            return stats;
        } catch (error) {
            console.error('Error getting hourly distribution:', error);
            return {};
        }
    }
}
