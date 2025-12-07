// Telegram API Types

export interface TelegramUpdate {
    update_id: number;
    message?: TelegramMessage;
    callback_query?: TelegramCallbackQuery;
    migrate_to_chat_id?: number;
    migrate_from_chat_id?: number;
}

export interface TelegramMessage {
    message_id: number;
    from: TelegramUser;
    chat: TelegramChat;
    date: number;
    text?: string;
    voice?: TelegramVoice;
    video_note?: TelegramVideoNote;
    audio?: TelegramAudio;
    video?: TelegramVideo;
}

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
    type: 'private' | 'group' | 'supergroup' | 'channel';
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
    thumbnail?: TelegramPhotoSize;
    file_size?: number;
}

export interface TelegramAudio {
    file_id: string;
    file_unique_id: string;
    duration: number;
    performer?: string;
    title?: string;
    mime_type?: string;
    file_size?: number;
}

export interface TelegramVideo {
    file_id: string;
    file_unique_id: string;
    width: number;
    height: number;
    duration: number;
    thumbnail?: TelegramPhotoSize;
    mime_type?: string;
    file_size?: number;
}

export interface TelegramPhotoSize {
    file_id: string;
    file_unique_id: string;
    width: number;
    height: number;
    file_size?: number;
}

export interface TelegramCallbackQuery {
    id: string;
    from: TelegramUser;
    message?: TelegramMessage;
    inline_message_id?: string;
    chat_instance: string;
    data?: string;
}

export interface TelegramFile {
    file_id: string;
    file_unique_id: string;
    file_size?: number;
    file_path?: string;
}

// Inline Keyboard Types
export interface InlineKeyboardMarkup {
    inline_keyboard: InlineKeyboardButton[][];
}

export interface InlineKeyboardButton {
    text: string;
    callback_data?: string;
    url?: string;
}

// Analytics Types
export interface UserRecord {
    user_id: number;
    username?: string;
    first_name?: string;
    last_name?: string;
    language_code?: string;
    first_seen: Date;
    last_seen: Date;
}

export interface EventRecord {
    id?: number;
    user_id: number;
    event_type: 'transcription' | 'summary';
    media_type?: 'voice' | 'video_note' | 'audio' | 'video';
    chat_type?: 'private' | 'group';
    timestamp: Date;
}

export interface TotalStats {
    total_transcriptions: number;
    total_summaries: number;
    total_users: number;
    total_events: number;
}
