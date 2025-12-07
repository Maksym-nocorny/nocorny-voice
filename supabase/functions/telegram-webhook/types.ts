// Types for Telegram Bot API

export interface TelegramUser {
    id: number;
    is_bot: boolean;
    first_name: string;
    last_name?: string;
    username?: string;
    language_code?: string;
}

export interface TelegramChat {
    id: number;
    type: "private" | "group" | "supergroup" | "channel";
    title?: string;
    username?: string;
    first_name?: string;
    last_name?: string;
}

export interface TelegramVoice {
    file_id: string;
    file_unique_id: string;
    duration: number;
    mime_type?: string;
    file_size?: number;
}

export interface TelegramVideoNote {
    file_id: string;
    file_unique_id: string;
    length: number;
    duration: number;
    file_size?: number;
}

export interface TelegramAudio {
    file_id: string;
    file_unique_id: string;
    duration: number;
    mime_type?: string;
    file_size?: number;
}

export interface TelegramVideo {
    file_id: string;
    file_unique_id: string;
    width: number;
    height: number;
    duration: number;
    mime_type?: string;
    file_size?: number;
}

export interface TelegramMessage {
    message_id: number;
    from?: TelegramUser;
    chat: TelegramChat;
    date: number;
    text?: string;
    voice?: TelegramVoice;
    video_note?: TelegramVideoNote;
    audio?: TelegramAudio;
    video?: TelegramVideo;
}

export interface TelegramCallbackQuery {
    id: string;
    from: TelegramUser;
    message?: TelegramMessage;
    chat_instance: string;
    data?: string;
}

export interface TelegramUpdate {
    update_id: number;
    message?: TelegramMessage;
    callback_query?: TelegramCallbackQuery;
}

export interface InlineKeyboardButton {
    text: string;
    callback_data?: string;
}

export interface InlineKeyboardMarkup {
    inline_keyboard: InlineKeyboardButton[][];
}

export interface SendMessageParams {
    chat_id: number;
    text: string;
    parse_mode?: "Markdown" | "MarkdownV2" | "HTML";
    reply_markup?: InlineKeyboardMarkup;
    reply_to_message_id?: number;
}

export interface EditMessageTextParams {
    chat_id: number;
    message_id: number;
    text: string;
    parse_mode?: "Markdown" | "MarkdownV2" | "HTML";
    reply_markup?: InlineKeyboardMarkup;
}

export interface TelegramFile {
    file_id: string;
    file_unique_id: string;
    file_size?: number;
    file_path?: string;
}

// Media info for processing
export interface MediaInfo {
    fileId: string;
    fileExt: string;
    mimeType: string;
    mediaType: "voice" | "video_note" | "audio" | "video";
}
