// Supabase Edge Function - Telegram Webhook Handler
// Entry point for the bot

import { serve } from 'https://deno.land/std@0.168.0/http/server.ts';
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { Analytics } from './analytics.ts';
import {
    handleStart,
    handleVoiceMessage,
    handleSummaryCallback,
    handleStats
} from './bot.ts';
import { TelegramUpdate } from './types.ts';

console.log('Telegram Webhook Handler started');

serve(async (req) => {
    // Only accept POST requests
    if (req.method !== 'POST') {
        return new Response('Method not allowed', { status: 405 });
    }

    try {
        // Parse Telegram update
        const update: TelegramUpdate = await req.json();
        console.log('Received update:', update.update_id);

        // Initialize Supabase client
        const supabaseUrl = Deno.env.get('SUPABASE_URL');
        const supabaseKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');

        if (!supabaseUrl || !supabaseKey) {
            throw new Error('Supabase credentials not configured');
        }

        const supabase = createClient(supabaseUrl, supabaseKey);
        const analytics = new Analytics(supabase);

        // Route to appropriate handler
        if (update.message) {
            const message = update.message;

            // Handle commands
            if (message.text) {
                if (message.text === '/start') {
                    await handleStart(message, analytics);
                    return new Response('OK', { status: 200 });
                } else if (message.text === '/stats') {
                    await handleStats(message, analytics);
                    return new Response('OK', { status: 200 });
                }
            }

            // Handle voice/video messages
            if (message.voice || message.video_note || message.audio || message.video) {
                await handleVoiceMessage(message, analytics);
                return new Response('OK', { status: 200 });
            }
        }

        // Handle callback queries (button presses)
        if (update.callback_query) {
            const callbackData = update.callback_query.data;

            if (callbackData?.startsWith('summarize')) {
                await handleSummaryCallback(update.callback_query, analytics);
                return new Response('OK', { status: 200 });
            }
        }

        // Unknown update type - still return OK to Telegram
        console.log('Unknown update type, ignoring');
        return new Response('OK', { status: 200 });

    } catch (error) {
        console.error('Error processing update:', error);

        // Still return 200 to Telegram to avoid retries
        // Log error for debugging
        return new Response('OK', { status: 200 });
    }
});
