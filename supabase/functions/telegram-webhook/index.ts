// Nocorny Voice Bot - Supabase Edge Function
// Transcribes voice messages and video notes using Google Gemini

import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
    TelegramUpdate,
    TelegramUser,
    TelegramMessage,
    MediaInfo,
    SendMessageParams,
    InlineKeyboardMarkup,
} from "./types.ts";
import { getText, TranslationKey } from "./translations.ts";

// Environment variables
const TELEGRAM_BOT_TOKEN = Deno.env.get("TELEGRAM_BOT_TOKEN")!;
const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY")!;
const ADMIN_USER_ID = Deno.env.get("ADMIN_USER_ID");
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const TELEGRAM_API = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}`;

// Initialize Supabase client
const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

// Telegram API helpers
async function sendMessage(params: SendMessageParams): Promise<TelegramMessage | null> {
    try {
        const response = await fetch(`${TELEGRAM_API}/sendMessage`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(params),
        });
        const data = await response.json();
        return data.ok ? data.result : null;
    } catch (error) {
        console.error("sendMessage error:", error);
        return null;
    }
}

async function editMessageText(
    chatId: number,
    messageId: number,
    text: string,
    parseMode?: "Markdown" | "HTML",
    replyMarkup?: InlineKeyboardMarkup
): Promise<boolean> {
    try {
        const response = await fetch(`${TELEGRAM_API}/editMessageText`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                chat_id: chatId,
                message_id: messageId,
                text,
                parse_mode: parseMode,
                reply_markup: replyMarkup,
            }),
        });
        const data = await response.json();
        return data.ok;
    } catch (error) {
        console.error("editMessageText error:", error);
        return false;
    }
}

async function deleteMessage(chatId: number, messageId: number): Promise<boolean> {
    try {
        const response = await fetch(`${TELEGRAM_API}/deleteMessage`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ chat_id: chatId, message_id: messageId }),
        });
        const data = await response.json();
        return data.ok;
    } catch (error) {
        console.error("deleteMessage error:", error);
        return false;
    }
}

async function answerCallbackQuery(callbackQueryId: string, text?: string, showAlert = false): Promise<boolean> {
    try {
        const response = await fetch(`${TELEGRAM_API}/answerCallbackQuery`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                callback_query_id: callbackQueryId,
                text,
                show_alert: showAlert,
            }),
        });
        const data = await response.json();
        return data.ok;
    } catch (error) {
        console.error("answerCallbackQuery error:", error);
        return false;
    }
}

async function editMessageReplyMarkup(chatId: number, messageId: number): Promise<boolean> {
    try {
        const response = await fetch(`${TELEGRAM_API}/editMessageReplyMarkup`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                chat_id: chatId,
                message_id: messageId,
                reply_markup: null,
            }),
        });
        const data = await response.json();
        return data.ok;
    } catch (error) {
        console.error("editMessageReplyMarkup error:", error);
        return false;
    }
}

async function getFile(fileId: string): Promise<string | null> {
    try {
        const response = await fetch(`${TELEGRAM_API}/getFile`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ file_id: fileId }),
        });
        const data = await response.json();
        if (data.ok && data.result.file_path) {
            return `https://api.telegram.org/file/bot${TELEGRAM_BOT_TOKEN}/${data.result.file_path}`;
        }
        return null;
    } catch (error) {
        console.error("getFile error:", error);
        return null;
    }
}

// Database helpers
async function trackUser(user: TelegramUser): Promise<void> {
    const now = new Date().toISOString();

    const { error } = await supabase.from("users").upsert(
        {
            user_id: user.id,
            username: user.username || null,
            first_name: user.first_name,
            last_name: user.last_name || null,
            language_code: user.language_code || null,
            first_seen: now,
            last_seen: now,
        },
        { onConflict: "user_id", ignoreDuplicates: false }
    );

    if (error) {
        console.error("trackUser error:", error);
    }
}

async function trackEvent(
    userId: number,
    eventType: string,
    mediaType?: string,
    chatType?: string
): Promise<void> {
    const { error } = await supabase.from("events").insert({
        user_id: userId,
        event_type: eventType,
        media_type: mediaType || null,
        chat_type: chatType || null,
        timestamp: new Date().toISOString(),
    });

    if (error) {
        console.error("trackEvent error:", error);
    }
}

async function saveTranscription(
    chatId: number,
    messageId: number,
    text: string
): Promise<void> {
    const { error } = await supabase.from("transcriptions").upsert(
        {
            chat_id: chatId,
            message_id: messageId,
            transcription_text: text,
            created_at: new Date().toISOString(),
        },
        { onConflict: "chat_id,message_id" }
    );

    if (error) {
        console.error("saveTranscription error:", error);
    }
}

async function getTranscription(
    chatId: number,
    messageId: number
): Promise<string | null> {
    const { data, error } = await supabase
        .from("transcriptions")
        .select("transcription_text")
        .eq("chat_id", chatId)
        .eq("message_id", messageId)
        .single();

    if (error || !data) {
        return null;
    }

    return data.transcription_text;
}

// Gemini File API helpers
async function uploadToGemini(
    fileUrl: string,
    mimeType: string,
    fileSize: number
): Promise<string | null> {
    try {
        // 1. Start resumable upload session
        const startUploadUrl = `https://generativelanguage.googleapis.com/upload/v1beta/files?key=${GEMINI_API_KEY}`;

        const initialRes = await fetch(startUploadUrl, {
            method: "POST",
            headers: {
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": fileSize.toString(),
                "X-Goog-Upload-Header-Content-Type": mimeType,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ file: { display_name: "telegram_media" } }),
        });

        const uploadUrl = initialRes.headers.get("x-goog-upload-url");
        if (!uploadUrl) {
            console.error("Failed to get upload URL");
            return null;
        }

        // 2. Stream file content
        const fileRes = await fetch(fileUrl);
        if (!fileRes.body) {
            console.error("No body in file response");
            return null;
        }

        const uploadRes = await fetch(uploadUrl, {
            method: "POST", // Resumable protocol uses POST/PUT with offset
            headers: {
                "Content-Length": fileSize.toString(),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            body: fileRes.body, // Stream directly
        });

        const uploadData = await uploadRes.json();
        const fileUri = uploadData.file?.uri;

        if (!fileUri) {
            console.error("Failed to get file URI", uploadData);
            return null;
        }

        // 3. Wait for file to be active
        let state = uploadData.file?.state;
        while (state === "PROCESSING") {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            const getRes = await fetch(
                `https://generativelanguage.googleapis.com/v1beta/files/${uploadData.file.name.split("/").pop()}?key=${GEMINI_API_KEY}`
            );
            const getData = await getRes.json();
            state = getData.state;
            if (state === "FAILED") {
                console.error("File processing failed");
                return null;
            }
        }

        return fileUri;
    } catch (error) {
        console.error("uploadToGemini error:", error);
        return null;
    }
}

async function deleteGeminiFile(fileUri: string): Promise<void> {
    try {
        // fileUri format: https://generativelanguage.googleapis.com/v1beta/files/NAME
        // We need the name part for deletion URL
        // Actually the API expects DELETE https://generativelanguage.googleapis.com/v1beta/files/NAME
        // But fileUri IS that URL? No, fileUri is generic.
        // The previous response had `file.name` like `files/123`.
        // Let's assume we can extract it or use the one we got.
        // Simplifying: we'll just use the file name from upload response if we had it, but here we return URI.
        // Let's parse the URI? URI: https://generativelanguage.googleapis.com/v1beta/files/c2...

        // We can just fetch DELETE on the URI?
        // Official docs: DELETE https://generativelanguage.googleapis.com/v1beta/files/name

        // Let's try to extract name from URI
        const parts = fileUri.split("/files/");
        if (parts.length < 2) return;
        const name = "files/" + parts[1];

        await fetch(
            `https://generativelanguage.googleapis.com/v1beta/${name}?key=${GEMINI_API_KEY}`,
            { method: "DELETE" }
        );
    } catch (error) {
        console.error("deleteGeminiFile error:", error);
    }
}

// Gemini API helper
async function transcribeWithGemini(
    fileUrl: string,
    mimeType: string,
    fileSize: number
): Promise<string | null> {
    let fileUri: string | null = null;
    try {
        // Use File API for upload (better for memory/large files)
        fileUri = await uploadToGemini(fileUrl, mimeType, fileSize);

        if (!fileUri) {
            return null;
        }

        // Call Gemini API with file_data
        const response = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${GEMINI_API_KEY}`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    contents: [
                        {
                            parts: [
                                { text: "Transcribe this audio/video exactly as spoken." },
                                {
                                    file_data: {
                                        mime_type: mimeType,
                                        file_uri: fileUri,
                                    },
                                },
                            ],
                        },
                    ],
                }),
            }
        );

        const data = await response.json();

        // Cleanup
        await deleteGeminiFile(fileUri).catch(console.error);

        if (data.candidates && data.candidates[0]?.content?.parts?.[0]?.text) {
            return data.candidates[0].content.parts[0].text;
        }

        console.error("Gemini response error:", JSON.stringify(data));
        return null;
    } catch (error) {
        console.error("transcribeWithGemini error:", error);
        if (fileUri) await deleteGeminiFile(fileUri).catch(console.error);
        return null;
    }
}

async function summarizeWithGemini(
    text: string,
    lang: string
): Promise<string | null> {
    try {
        const response = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${GEMINI_API_KEY}`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    contents: [
                        {
                            parts: [
                                {
                                    text: `Summarize the following text concisely. The summary MUST be in the language '${lang}':\n\n${text}`,
                                },
                            ],
                        },
                    ],
                }),
            }
        );

        const data = await response.json();

        if (data.candidates && data.candidates[0]?.content?.parts?.[0]?.text) {
            return data.candidates[0].content.parts[0].text;
        }

        return null;
    } catch (error) {
        console.error("summarizeWithGemini error:", error);
        return null;
    }
}

// Get media info from message
function getMediaInfo(message: TelegramMessage): MediaInfo | null {
    if (message.voice) {
        return {
            fileId: message.voice.file_id,
            fileExt: ".ogg",
            mimeType: "audio/ogg",
            mediaType: "voice",
        };
    }
    if (message.video_note) {
        return {
            fileId: message.video_note.file_id,
            fileExt: ".mp4",
            mimeType: "video/mp4",
            mediaType: "video_note",
        };
    }
    if (message.audio) {
        return {
            fileId: message.audio.file_id,
            fileExt: ".mp3",
            mimeType: message.audio.mime_type || "audio/mpeg",
            mediaType: "audio",
        };
    }
    if (message.video) {
        return {
            fileId: message.video.file_id,
            fileExt: ".mp4",
            mimeType: message.video.mime_type || "video/mp4",
            mediaType: "video",
        };
    }
    return null;
}

// Command handlers
async function handleStart(message: TelegramMessage): Promise<void> {
    const user = message.from;
    if (!user) return;

    const lang = user.language_code || "en";
    await trackUser(user);

    await sendMessage({
        chat_id: message.chat.id,
        text: getText(lang, "welcome"),
    });
}

async function handleStats(message: TelegramMessage): Promise<void> {
    const user = message.from;
    if (!user) return;

    const lang = user.language_code || "en";
    await trackUser(user);

    // Check admin access
    if (ADMIN_USER_ID && String(user.id) !== ADMIN_USER_ID) {
        await sendMessage({
            chat_id: message.chat.id,
            text: getText(lang, "stats_unauthorized"),
        });
        return;
    }

    // Get statistics
    const { data: totalEvents } = await supabase
        .from("events")
        .select("event_type", { count: "exact" });

    const { count: totalUsers } = await supabase
        .from("users")
        .select("*", { count: "exact", head: true });

    const { data: eventCounts } = await supabase
        .from("events")
        .select("event_type")
        .then((res) => {
            const counts: Record<string, number> = {};
            res.data?.forEach((e) => {
                counts[e.event_type] = (counts[e.event_type] || 0) + 1;
            });
            return { data: counts };
        });

    if (!eventCounts || Object.keys(eventCounts).length === 0) {
        await sendMessage({
            chat_id: message.chat.id,
            text: getText(lang, "stats_no_data"),
        });
        return;
    }

    // Build stats message
    let msg = getText(lang, "stats_title") + "\n\n";
    msg += getText(lang, "stats_total") + "\n";
    msg += getText(lang, "stats_transcriptions", eventCounts["transcription"] || 0) + "\n";
    msg += getText(lang, "stats_summaries", eventCounts["summary"] || 0) + "\n";
    msg += getText(lang, "stats_users", totalUsers || 0) + "\n";

    await sendMessage({
        chat_id: message.chat.id,
        text: msg,
        parse_mode: "Markdown",
    });
}

// Media message handler
async function handleMediaMessage(message: TelegramMessage): Promise<void> {
    const user = message.from;
    if (!user) return;

    const lang = user.language_code || "en";
    await trackUser(user);

    const mediaInfo = getMediaInfo(message);
    if (!mediaInfo) {
        await sendMessage({
            chat_id: message.chat.id,
            text: getText(lang, "unsupported"),
        });
        return;
    }

    // Send status message
    const statusMsg = await sendMessage({
        chat_id: message.chat.id,
        text: getText(lang, "downloading"),
    });

    if (!statusMsg) return;

    const isGroup = message.chat.type !== "private";
    const chatType = isGroup ? "group" : "private";

    try {
        // Get file URL
        const fileUrl = await getFile(mediaInfo.fileId);
        if (!fileUrl) {
            await editMessageText(
                message.chat.id,
                statusMsg.message_id,
                getText(lang, "error", "Failed to get file")
            );
            return;
        }

        // Get file size from message directly (more reliable)
        // Note: types might be missing file_size for some media, usually it's there
        let fileSize = 0;
        if (message.voice) fileSize = message.voice.file_size || 0;
        else if (message.audio) fileSize = message.audio.file_size || 0;
        else if (message.video) fileSize = message.video.file_size || 0;
        else if (message.video_note) fileSize = message.video_note.file_size || 0;

        await editMessageText(
            message.chat.id,
            statusMsg.message_id,
            getText(lang, "transcribing")
        );

        // Transcribe with Gemini (Streaming Upload)
        const transcription = await transcribeWithGemini(fileUrl, mediaInfo.mimeType, fileSize);

        if (!transcription) {
            await editMessageText(
                message.chat.id,
                statusMsg.message_id,
                getText(lang, "processing_failed")
            );
            return;
        }

        // Track event
        await trackEvent(user.id, "transcription", mediaInfo.mediaType, chatType);

        // Prepare response
        const responseText = getText(lang, "transcription_label", transcription);
        const callbackData = isGroup
            ? `summarize_${message.message_id}`
            : "summarize";

        const replyMarkup: InlineKeyboardMarkup = {
            inline_keyboard: [
                [{ text: getText(lang, "summarize_button"), callback_data: callbackData }],
            ],
        };

        // For groups, reply to original message
        if (isGroup) {
            await deleteMessage(message.chat.id, statusMsg.message_id);
            const sentMsg = await sendMessage({
                chat_id: message.chat.id,
                text: responseText,
                parse_mode: "Markdown",
                reply_markup: replyMarkup,
                reply_to_message_id: message.message_id,
            });

            if (sentMsg) {
                await saveTranscription(message.chat.id, message.message_id, transcription);
            }
        } else {
            await editMessageText(
                message.chat.id,
                statusMsg.message_id,
                responseText,
                "Markdown",
                replyMarkup
            );

            await saveTranscription(message.chat.id, statusMsg.message_id, transcription);
        }
    } catch (error) {
        console.error("handleMediaMessage error:", error);
        await editMessageText(
            message.chat.id,
            statusMsg.message_id,
            getText(lang, "error", String(error))
        );
    }
}

// Callback query handler (summarize button)
async function handleCallbackQuery(
    callbackQueryId: string,
    user: TelegramUser,
    message: TelegramMessage,
    data: string
): Promise<void> {
    const lang = user.language_code || "en";
    const chatId = message.chat.id;

    await answerCallbackQuery(callbackQueryId);
    await trackUser(user);

    // Extract message_id from callback_data
    let lookupMessageId: number;
    if (data.startsWith("summarize_")) {
        lookupMessageId = parseInt(data.split("_")[1], 10);
    } else {
        lookupMessageId = message.message_id;
    }

    // Get transcription from database
    const transcription = await getTranscription(chatId, lookupMessageId);

    if (!transcription) {
        await answerCallbackQuery(
            callbackQueryId,
            getText(lang, "session_expired"),
            true
        );
        return;
    }

    // Remove the button
    await editMessageReplyMarkup(chatId, message.message_id);

    // Send status message
    const statusMsg = await sendMessage({
        chat_id: chatId,
        text: getText(lang, "summarizing", "..."),
        parse_mode: "Markdown",
    });

    if (!statusMsg) return;

    try {
        // Summarize with Gemini
        const summary = await summarizeWithGemini(transcription, lang);

        if (!summary) {
            await editMessageText(
                chatId,
                statusMsg.message_id,
                getText(lang, "summary_error", "Failed to generate summary")
            );
            return;
        }

        // Track event
        const chatType = message.chat.type === "private" ? "private" : "group";
        await trackEvent(user.id, "summary", undefined, chatType);

        // Delete status and send summary
        await deleteMessage(chatId, statusMsg.message_id);
        await sendMessage({
            chat_id: chatId,
            text: getText(lang, "summary_label", summary),
            parse_mode: "Markdown",
        });
    } catch (error) {
        console.error("handleCallbackQuery error:", error);
        await editMessageText(
            chatId,
            statusMsg.message_id,
            getText(lang, "summary_error", String(error))
        );
    }
}

// Dispatch update to appropriate handler
async function handleUpdate(update: TelegramUpdate): Promise<void> {
    // Handle callback queries (summarize button)
    if (update.callback_query) {
        const cq = update.callback_query;
        if (cq.data?.startsWith("summarize") && cq.message) {
            await handleCallbackQuery(cq.id, cq.from, cq.message, cq.data);
        }
        return;
    }

    // Handle messages
    if (update.message) {
        const message = update.message;

        // Check for commands
        if (message.text?.startsWith("/start")) {
            await handleStart(message);
            return;
        }

        if (message.text?.startsWith("/stats")) {
            await handleStats(message);
            return;
        }

        // Check for media
        if (message.voice || message.video_note || message.audio || message.video) {
            await handleMediaMessage(message);
            return;
        }
    }
}

// Main handler
serve(async (req) => {
    try {
        // Handle only POST requests
        if (req.method !== "POST") {
            return new Response("OK", { status: 200 });
        }

        const update: TelegramUpdate = await req.json();
        console.log("Received update:", update.update_id);

        // Process in background using EdgeRuntime.waitUntil
        const processingPromise = handleUpdate(update).catch((err) => {
            console.error("Error processing update:", err);
        });

        if (typeof EdgeRuntime !== "undefined") {
            // @ts-ignore: EdgeRuntime is defined in Supabase environment
            EdgeRuntime.waitUntil(processingPromise);
        } else {
            // Local development fallback
            await processingPromise;
        }

        return new Response("OK", { status: 200 });
    } catch (error) {
        console.error("Handler error:", error);
        return new Response("OK", { status: 200 }); // Always return 200 to stop retries
    }
});
