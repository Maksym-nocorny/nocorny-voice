// Bot logic - main handlers for Telegram updates
// Converted from Python bot.py
// Uses direct Telegram API calls instead of python-telegram-bot library

import { SupabaseClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { Analytics } from './analytics.ts';
import { getText } from './translations.ts';
import {
    TelegramUpdate,
    TelegramMessage,
    TelegramUser,
    InlineKeyboardMarkup,
    TelegramFile
} from './types.ts';

const TELEGRAM_API = 'https://api.telegram.org/bot';

// Store last transcriptions in memory
// Format: `${chatId}_${messageId}` -> transcription_text
const lastTranscriptions: Map<string, string> = new Map();

/**
 * Get Telegram Bot Token from environment
 */
function getBotToken(): string {
    const token = Deno.env.get('TELEGRAM_BOT_TOKEN');
    if (!token) {
        throw new Error('TELEGRAM_BOT_TOKEN not set');
    }
    return token;
}

/**
 * Get Gemini API Key from environment
 */
function getGeminiKey(): string {
    const key = Deno.env.get('GEMINI_API_KEY');
    if (!key) {
        throw new Error('GEMINI_API_KEY not set');
    }
    return key;
}

/**
 * Get Admin User ID from environment (optional)
 */
function getAdminUserId(): string | null {
    return Deno.env.get('ADMIN_USER_ID') || null;
}

/**
 * Send a message via Telegram API
 */
async function sendMessage(
    chatId: number,
    text: string,
    options: {
        parseMode?: string;
        replyMarkup?: InlineKeyboardMarkup;
        replyToMessageId?: number;
    } = {}
): Promise<TelegramMessage> {
    const token = getBotToken();
    const body: any = {
        chat_id: chatId,
        text: text
    };

    if (options.parseMode) {
        body.parse_mode = options.parseMode;
    }
    if (options.replyMarkup) {
        body.reply_markup = options.replyMarkup;
    }
    if (options.replyToMessageId) {
        body.reply_to_message_id = options.replyToMessageId;
    }

    const response = await fetch(`${TELEGRAM_API}${token}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    const data = await response.json();
    if (!data.ok) {
        throw new Error(`Telegram API error: ${data.description}`);
    }

    return data.result;
}

/**
 * Edit a message via Telegram API
 */
async function editMessage(
    chatId: number,
    messageId: number,
    text: string,
    options: {
        parseMode?: string;
        replyMarkup?: InlineKeyboardMarkup | null;
    } = {}
): Promise<void> {
    const token = getBotToken();
    const body: any = {
        chat_id: chatId,
        message_id: messageId,
        text: text
    };

    if (options.parseMode) {
        body.parse_mode = options.parseMode;
    }
    if (options.replyMarkup !== undefined) {
        body.reply_markup = options.replyMarkup;
    }

    await fetch(`${TELEGRAM_API}${token}/editMessageText`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

/**
 * Delete a message via Telegram API
 */
async function deleteMessage(chatId: number, messageId: number): Promise<void> {
    const token = getBotToken();
    await fetch(`${TELEGRAM_API}${token}/deleteMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            chat_id: chatId,
            message_id: messageId
        })
    });
}

/**
 * Get file info from Telegram
 */
async function getFile(fileId: string): Promise<TelegramFile> {
    const token = getBotToken();
    const response = await fetch(`${TELEGRAM_API}${token}/getFile?file_id=${fileId}`);
    const data = await response.json();

    if (!data.ok) {
        throw new Error(`Failed to get file: ${data.description}`);
    }

    return data.result;
}

/**
 * Download file from Telegram
 */
async function downloadFile(filePath: string): Promise<Uint8Array> {
    const token = getBotToken();
    const url = `https://api.telegram.org/file/bot${token}/${filePath}`;
    const response = await fetch(url);
    return new Uint8Array(await response.arrayBuffer());
}

/**
 * Upload file to Gemini and transcribe
 */
async function transcribeWithGemini(
    fileData: Uint8Array,
    mimeType: string
): Promise<string> {
    const geminiKey = getGeminiKey();

    // First, upload the file to Gemini Files API
    const uploadResponse = await fetch(
        `https://generativelanguage.googleapis.com/upload/v1beta/files?key=${geminiKey}`,
        {
            method: 'POST',
            headers: {
                'X-Goog-Upload-Protocol': 'multipart'
            },
            body: fileData
        }
    );

    if (!uploadResponse.ok) {
        throw new Error(`Gemini upload failed: ${await uploadResponse.text()}`);
    }

    const uploadData = await uploadResponse.json();
    const fileUri = uploadData.file.uri;

    // Wait for processing if needed
    // Note: This might hit Edge Function timeout - we may need async processing
    let file = uploadData.file;
    while (file.state === 'PROCESSING') {
        await new Promise(resolve => setTimeout(resolve, 2000));
        const statusResponse = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/${file.name}?key=${geminiKey}`
        );
        file = await statusResponse.json();
    }

    if (file.state === 'FAILED') {
        throw new Error('Gemini processing failed');
    }

    // Generate transcription
    const genResponse = await fetch(
        `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${geminiKey}`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                contents: [
                    {
                        parts: [
                            { text: 'Transcribe this audio/video exactly as spoken.' },
                            { file_data: { file_uri: fileUri, mime_type: mimeType } }
                        ]
                    }
                ]
            })
        }
    );

    const genData = await genResponse.json();
    return genData.candidates[0].content.parts[0].text;
}

/**
 * Generate summary with Gemini
 */
async function summarizeWithGemini(text: string, targetLang: string): Promise<string> {
    const geminiKey = getGeminiKey();
    const prompt = `Summarize the following text concisely. The summary MUST be in the language '${targetLang}':\n\n${text}`;

    const response = await fetch(
        `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${geminiKey}`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                contents: [{ parts: [{ text: prompt }] }]
            })
        }
    );

    const data = await response.json();
    return data.candidates[0].content.parts[0].text;
}

/**
 * Handle /start command
 */
export async function handleStart(
    message: TelegramMessage,
    analytics: Analytics
): Promise<void> {
    const user = message.from;
    const userLang = user.language_code || 'en';

    // Track user
    await analytics.trackUser(
        user.id,
        user.username,
        user.first_name,
        user.last_name,
        userLang
    );

    await sendMessage(message.chat.id, getText(userLang, 'welcome'));
}

/**
 * Handle voice/video messages
 */
export async function handleVoiceMessage(
    message: TelegramMessage,
    analytics: Analytics
): Promise<void> {
    const user = message.from;
    const userLang = user.language_code || 'en';
    const chatId = message.chat.id;
    const isGroup = message.chat.type === 'group' || message.chat.type === 'supergroup';

    // Track user
    await analytics.trackUser(
        user.id,
        user.username,
        user.first_name,
        user.last_name,
        userLang
    );

    // Send status message
    const statusMsg = await sendMessage(chatId, getText(userLang, 'downloading'));

    try {
        // Determine file type and get file
        let fileId: string;
        let mimeType: string;
        let mediaType: 'voice' | 'video_note' | 'audio' | 'video';

        if (message.voice) {
            fileId = message.voice.file_id;
            mimeType = 'audio/ogg';
            mediaType = 'voice';
        } else if (message.video_note) {
            fileId = message.video_note.file_id;
            mimeType = 'video/mp4';
            mediaType = 'video_note';
        } else if (message.audio) {
            fileId = message.audio.file_id;
            mimeType = message.audio.mime_type || 'audio/mpeg';
            mediaType = 'audio';
        } else if (message.video) {
            fileId = message.video.file_id;
            mimeType = message.video.mime_type || 'video/mp4';
            mediaType = 'video';
        } else {
            await editMessage(chatId, statusMsg.message_id, getText(userLang, 'unsupported'));
            return;
        }

        // Download file
        const file = await getFile(fileId);
        if (!file.file_path) {
            throw new Error('File path not available');
        }
        const fileData = await downloadFile(file.file_path);

        // Update status
        await editMessage(chatId, statusMsg.message_id, getText(userLang, 'transcribing'));

        // Transcribe
        const transcription = await transcribeWithGemini(fileData, mimeType);

        // Track event
        const chatType = isGroup ? 'group' : 'private';
        await analytics.trackEvent(user.id, 'transcription', mediaType, chatType);

        // Create keyboard (only for private chats)
        let replyMarkup: InlineKeyboardMarkup | undefined;
        if (!isGroup) {
            replyMarkup = {
                inline_keyboard: [[
                    {
                        text: getText(userLang, 'summarize_button'),
                        callback_data: 'summarize'
                    }
                ]]
            };
        }

        // Format transcription
        const transcriptionText = getText(userLang, 'transcription_label', transcription);

        // Send/edit message
        const maxLength = 4000;
        if (transcriptionText.length <= maxLength) {
            // Single message
            if (isGroup) {
                const sentMsg = await sendMessage(chatId, transcriptionText, {
                    parseMode: 'Markdown',
                    replyToMessageId: message.message_id
                });
                await deleteMessage(chatId, statusMsg.message_id);

                // Store transcription
                const key = `${chatId}_${message.message_id}`;
                lastTranscriptions.set(key, transcription);
            } else {
                await editMessage(chatId, statusMsg.message_id, transcriptionText, {
                    parseMode: 'Markdown',
                    replyMarkup
                });

                // Store transcription
                const key = `${chatId}_${statusMsg.message_id}`;
                lastTranscriptions.set(key, transcription);
            }
        } else {
            // Split into multiple messages
            await deleteMessage(chatId, statusMsg.message_id);

            const label = getText(userLang, 'transcription_label', '');
            const chunks: string[] = [];

            for (let i = 0; i < transcription.length; i += maxLength) {
                chunks.push(transcription.substring(i, i + maxLength));
            }

            let lastMsg: TelegramMessage | null = null;
            for (let i = 0; i < chunks.length; i++) {
                const text = i === 0 ? `${label}${chunks[i]}` : chunks[i];
                const markup = (i === chunks.length - 1 && !isGroup) ? replyMarkup : undefined;

                lastMsg = await sendMessage(chatId, text, {
                    parseMode: 'Markdown',
                    replyMarkup: markup,
                    replyToMessageId: isGroup ? message.message_id : undefined
                });
            }

            // Store transcription
            if (lastMsg) {
                const key = isGroup ? `${chatId}_${message.message_id}` : `${chatId}_${lastMsg.message_id}`;
                lastTranscriptions.set(key, transcription);
            }
        }
    } catch (error) {
        console.error('Error processing message:', error);
        await editMessage(chatId, statusMsg.message_id, getText(userLang, 'error', String(error)));
    }
}

/**
 * Handle summary button callback
 */
export async function handleSummaryCallback(
    callbackQuery: any,
    analytics: Analytics
): Promise<void> {
    const user = callbackQuery.from;
    const userLang = user.language_code || 'en';
    const message = callbackQuery.message;
    const chatId = message.chat.id;

    // Track user
    await analytics.trackUser(
        user.id,
        user.username,
        user.first_name,
        user.last_name,
        userLang
    );

    // Get transcription key
    const messageId = message.message_id;
    const key = `${chatId}_${messageId}`;
    const originalText = lastTranscriptions.get(key);

    if (!originalText) {
        // Answer callback query with alert
        const token = getBotToken();
        await fetch(`${TELEGRAM_API}${token}/answerCallbackQuery`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                callback_query_id: callbackQuery.id,
                text: getText(userLang, 'session_expired'),
                show_alert: true
            })
        });
        return;
    }

    // Answer callback query
    const token = getBotToken();
    await fetch(`${TELEGRAM_API}${token}/answerCallbackQuery`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callback_query_id: callbackQuery.id })
    });

    // Remove button
    await editMessage(chatId, messageId, message.text, {
        parseMode: 'Markdown',
        replyMarkup: null
    });

    // Send status
    const statusMsg = await sendMessage(chatId, getText(userLang, 'summarizing', '...'), {
        parseMode: 'Markdown'
    });

    try {
        // Generate summary
        const summary = await summarizeWithGemini(originalText, userLang);

        // Track event
        const chatType = message.chat.type === 'private' ? 'private' : 'group';
        await analytics.trackEvent(user.id, 'summary', undefined, chatType);

        // Delete status and send summary
        await deleteMessage(chatId, statusMsg.message_id);
        await sendMessage(chatId, getText(userLang, 'summary_label', summary), {
            parseMode: 'Markdown'
        });
    } catch (error) {
        console.error('Error summarizing:', error);
        await editMessage(chatId, statusMsg.message_id, getText(userLang, 'summary_error', String(error)));
    }
}

/**
 * Handle /stats command
 */
export async function handleStats(
    message: TelegramMessage,
    analytics: Analytics
): Promise<void> {
    const user = message.from;
    const userLang = user.language_code || 'en';
    const adminUserId = getAdminUserId();

    // Check authorization
    if (adminUserId && String(user.id) !== adminUserId) {
        await sendMessage(message.chat.id, getText(userLang, 'stats_unauthorized'));
        return;
    }

    // Track user
    await analytics.trackUser(
        user.id,
        user.username,
        user.first_name,
        user.last_name,
        userLang
    );

    try {
        // Get statistics
        const totalStats = await analytics.getTotalStats();

        if (totalStats.total_events === 0) {
            await sendMessage(message.chat.id, getText(userLang, 'stats_no_data'));
            return;
        }

        // Build message
        let msg = getText(userLang, 'stats_title') + '\n\n';
        msg += getText(userLang, 'stats_total') + '\n';
        msg += getText(userLang, 'stats_transcriptions', totalStats.total_transcriptions) + '\n';
        msg += getText(userLang, 'stats_summaries', totalStats.total_summaries) + '\n';
        msg += getText(userLang, 'stats_users', totalStats.total_users) + '\n';

        // Media types
        const mediaStats = await analytics.getMediaTypeStats();
        if (Object.keys(mediaStats).length > 0) {
            msg += getText(userLang, 'stats_media_types') + '\n';
            for (const [type, count] of Object.entries(mediaStats)) {
                msg += `• ${type}: ${count}\n`;
            }
        }

        // Chat types
        const chatStats = await analytics.getChatTypeStats();
        if (Object.keys(chatStats).length > 0) {
            msg += getText(userLang, 'stats_chat_types') + '\n';
            for (const [type, count] of Object.entries(chatStats)) {
                msg += `• ${type}: ${count}\n`;
            }
        }

        // Top users
        const topUsers = await analytics.getTopUsers(10);
        if (topUsers.length > 0) {
            msg += getText(userLang, 'stats_top_users') + '\n';
            topUsers.forEach(([userId, username, count], i) => {
                const displayName = username ? `@${username}` : `User ${userId}`;
                msg += getText(userLang, 'stats_user_rank', i + 1, displayName, count) + '\n';
            });
        }

        // Language distribution
        const langStats = await analytics.getLanguageDistribution();
        if (Object.keys(langStats).length > 0) {
            msg += getText(userLang, 'stats_languages') + '\n';
            for (const [lang, count] of Object.entries(langStats)) {
                msg += ` ${lang}: ${count}\n`;
            }
        }

        // Time-based stats
        msg += getText(userLang, 'stats_time_based') + '\n';

        const dailyStats = await analytics.getDailyStats(7);
        if (dailyStats.length > 0) {
            msg += `\n${getText(userLang, 'stats_last_7_days')}\n`;
            dailyStats.forEach(([day, count]) => {
                msg += `• ${day}: ${count}\n`;
            });
        }

        const monthlyStats = await analytics.getMonthlyStats(6);
        if (monthlyStats.length > 0) {
            msg += `\n${getText(userLang, 'stats_last_6_months')}\n`;
            monthlyStats.forEach(([month, count]) => {
                msg += `• ${month}: ${count}\n`;
            });
        }

        const yearlyStats = await analytics.getYearlyStats();
        if (yearlyStats.length > 0) {
            msg += `\n${getText(userLang, 'stats_by_year')}\n`;
            yearlyStats.forEach(([year, count]) => {
                msg += `• ${year}: ${count}\n`;
            });
        }

        // Hourly distribution
        const hourlyStats = await analytics.getHourlyDistribution();
        if (Object.keys(hourlyStats).length > 0) {
            msg += getText(userLang, 'stats_hourly') + '\n';
            const sorted = Object.entries(hourlyStats)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5);
            sorted.forEach(([hour, count]) => {
                msg += `• ${String(hour).padStart(2, '0')}:00 - ${count} requests\n`;
            });
        }

        await sendMessage(message.chat.id, msg, { parseMode: 'Markdown' });
    } catch (error) {
        console.error('Error getting stats:', error);
        await sendMessage(message.chat.id, getText(userLang, 'error', String(error)));
    }
}
