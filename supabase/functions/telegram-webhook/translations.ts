// Translation strings for the bot
// Converted from Python bot.py TRANSLATIONS dictionary

interface TranslationDict {
    [key: string]: string;
}

interface Translations {
    [langCode: string]: TranslationDict;
}

export const TRANSLATIONS: Translations = {
    en: {
        welcome: "Hi! I'm a transcription bot. Send me a voice message or a video note, and I'll transcribe it for you using Gemini.",
        downloading: "Downloading media...",
        transcribing: "Transcribing with Gemini...",
        unsupported: "Unsupported media type.",
        error: "An error occurred: {}",
        transcription_label: "**Transcription:**\n\n{}",
        summarize_button: "📝 Summarize",
        session_expired: "Session expired or no text found. Please resend the media.",
        summarizing: "**Transcription:**\n\n{}\n\n_Summarizing..._",
        summary_label: "**Summary:**\n\n{}",
        summary_error: "Error generating summary: {}",
        processing_failed: "Gemini failed to process the media file.",
        stats_title: "📊 **Bot Statistics**",
        stats_total: "**Total Statistics:**",
        stats_transcriptions: "• Transcriptions: {}",
        stats_summaries: "• Summaries: {}",
        stats_users: "• Unique users: {}",
        stats_media_types: "\n**Media Types:**",
        stats_top_users: "\n**Top 10 Users:**",
        stats_languages: "\n**Language Distribution:**",
        stats_chat_types: "\n**Chat Types:**",
        stats_user_rank: "{}. {} - {} requests",
        stats_no_data: "No statistics available yet.",
        stats_unauthorized: "⛔ You are not authorized to view statistics.",
        stats_time_based: "\n**Usage Over Time:**",
        stats_last_7_days: "Last 7 days:",
        stats_last_6_months: "Last 6 months:",
        stats_by_year: "By year:",
        stats_hourly: "\n**Peak Hours (UTC):**"
    },
    uk: {
        welcome: "Привіт! Я бот для транскрипції. Надішліть мені голосове повідомлення або відеоповідомлення, і я транскрибую його за допомогою Gemini.",
        downloading: "Завантаження медіа...",
        transcribing: "Транскрипція за допомогою Gemini...",
        unsupported: "Непідтримуваний тип медіа.",
        error: "Сталася помилка: {}",
        transcription_label: "**Транскрипція:**\n\n{}",
        summarize_button: "📝 Підсумувати",
        session_expired: "Сесія закінчилася або текст не знайдено. Будь ласка, надішліть медіа знову.",
        summarizing: "**Транскрипція:**\n\n{}\n\n_Підсумовую..._",
        summary_label: "**Підсумок:**\n\n{}",
        summary_error: "Помилка створення підсумку: {}",
        processing_failed: "Gemini не вдалося обробити медіафайл.",
        stats_title: "📊 **Статистика бота**",
        stats_total: "**Загальна статистика:**",
        stats_transcriptions: "• Транскрипцій: {}",
        stats_summaries: "• Підсумків: {}",
        stats_users: "• Унікальних користувачів: {}",
        stats_media_types: "\n**Типи медіа:**",
        stats_top_users: "\n**Топ 10 користувачів:**",
        stats_languages: "\n**Розподіл мов:**",
        stats_chat_types: "\n**Типи чатів:**",
        stats_user_rank: "{}. {} - {} запитів",
        stats_no_data: "Статистика ще недоступна.",
        stats_unauthorized: "⛔ Ви не маєте доступу до перегляду статистики.",
        stats_time_based: "\n**Використання за часом:**",
        stats_last_7_days: "Останні 7 днів:",
        stats_last_6_months: "Останні 6 місяців:",
        stats_by_year: "За роками:",
        stats_hourly: "\n**Пікові години (UTC):**"
    },
    ru: {
        welcome: "Привет! Я бот для транскрипции. Отправьте мне голосовое сообщение или видеосообщение, и я транскрибирую его с помощью Gemini.",
        downloading: "Загрузка медиа...",
        transcribing: "Транскрипция с помощью Gemini...",
        unsupported: "Неподдерживаемый тип медиа.",
        error: "Произошла ошибка: {}",
        transcription_label: "**Транскрипция:**\n\n{}",
        summarize_button: "📝 Резюмировать",
        session_expired: "Сессия истекла или текст не найден. Пожалуйста, отправьте медиа снова.",
        summarizing: "**Транскрипция:**\n\n{}\n\n_Резюмирую..._",
        summary_label: "**Резюме:**\n\n{}",
        summary_error: "Ошибка создания резюме: {}",
        processing_failed: "Gemini не удалось обработать медиафайл.",
        stats_title: "📊 **Статистика бота**",
        stats_total: "**Общая статистика:**",
        stats_transcriptions: "• Транскрипций: {}",
        stats_summaries: "• Резюме: {}",
        stats_users: "• Уникальных пользователей: {}",
        stats_media_types: "\n**Типы медиа:**",
        stats_top_users: "\n**Топ 10 пользователей:**",
        stats_languages: "\n**Распределение языков:**",
        stats_chat_types: "\n**Типы чатов:**",
        stats_user_rank: "{}. {} - {} запросов",
        stats_no_data: "Статистика пока недоступна.",
        stats_unauthorized: "⛔ Вы не авторизованы для просмотра статистики.",
        stats_time_based: "\n**Использование по времени:**",
        stats_last_7_days: "Последние 7 дней:",
        stats_last_6_months: "Последние 6 месяцев:",
        stats_by_year: "По годам:",
        stats_hourly: "\n**Пиковые часы (UTC):**"
    },
    es: {
        welcome: "¡Hola! Soy un bot de transcripción. Envíame un mensaje de voz o una nota de video, y lo transcribiré usando Gemini.",
        downloading: "Descargando medios...",
        transcribing: "Transcribiendo con Gemini...",
        unsupported: "Tipo de medio no compatible.",
        error: "Ocurrió un error: {}",
        transcription_label: "**Transcripción:**\n\n{}",
        summarize_button: "📝 Resumir",
        session_expired: "Sesión expirada o texto no encontrado. Por favor, reenvía el medio.",
        summarizing: "**Transcripción:**\n\n{}\n\n_Resumiendo..._",
        summary_label: "**Resumen:**\n\n{}",
        summary_error: "Error al generar resumen: {}",
        processing_failed: "Gemini no pudo procesar el archivo multimedia."
    },
    de: {
        welcome: "Hallo! Ich bin ein Transkriptions-Bot. Sende mir eine Sprachnachricht oder eine Videonotiz, und ich transkribiere sie mit Gemini.",
        downloading: "Medien werden heruntergeladen...",
        transcribing: "Transkribieren mit Gemini...",
        unsupported: "Nicht unterstützter Medientyp.",
        error: "Ein Fehler ist aufgetreten: {}",
        transcription_label: "**Transkription:**\n\n{}",
        summarize_button: "📝 Zusammenfassen",
        session_expired: "Sitzung abgelaufen oder Text nicht gefunden. Bitte sende die Medien erneut.",
        summarizing: "**Transkription:**\n\n{}\n\n_Zusammenfassen..._",
        summary_label: "**Zusammenfassung:**\n\n{}",
        summary_error: "Fehler beim Erstellen der Zusammenfassung: {}",
        processing_failed: "Gemini konnte die Mediendatei nicht verarbeiten."
    },
    fr: {
        welcome: "Salut! Je suis un bot de transcription. Envoyez-moi un message vocal ou une note vidéo, et je le transcrirai avec Gemini.",
        downloading: "Téléchargement du média...",
        transcribing: "Transcription avec Gemini...",
        unsupported: "Type de média non pris en charge.",
        error: "Une erreur s'est produite: {}",
        transcription_label: "**Transcription:**\n\n{}",
        summarize_button: "📝 Résumer",
        session_expired: "Session expirée ou texte introuvable. Veuillez renvoyer le média.",
        summarizing: "**Transcription:**\n\n{}\n\n_Résumé en cours..._",
        summary_label: "**Résumé:**\n\n{}",
        summary_error: "Erreur lors de la génération du résumé: {}",
        processing_failed: "Gemini n'a pas pu traiter le fichier multimédia."
    },
    it: {
        welcome: "Ciao! Sono un bot di trascrizione. Inviami un messaggio vocale o una nota video e lo trascriverò usando Gemini.",
        downloading: "Download del media...",
        transcribing: "Trascrizione con Gemini...",
        unsupported: "Tipo di media non supportato.",
        error: "Si è verificato un errore: {}",
        transcription_label: "**Trascrizione:**\n\n{}",
        summarize_button: "📝 Riassumere",
        session_expired: "Sessione scaduta o testo non trovato. Invia nuovamente il media.",
        summarizing: "**Trascrizione:**\n\n{}\n\n_Riassumendo..._",
        summary_label: "**Riassunto:**\n\n{}",
        summary_error: "Errore nella generazione del riassunto: {}",
        processing_failed: "Gemini non è riuscito a elaborare il file multimediale."
    },
    pl: {
        welcome: "Cześć! Jestem botem do transkrypcji. Wyślij mi wiadomość głosową lub notatkę wideo, a przepiszę ją za pomocą Gemini.",
        downloading: "Pobieranie mediów...",
        transcribing: "Transkrypcja za pomocą Gemini...",
        unsupported: "Nieobsługiwany typ mediów.",
        error: "Wystąpił błąd: {}",
        transcription_label: "**Transkrypcja:**\n\n{}",
        summarize_button: "📝 Podsumuj",
        session_expired: "Sesja wygasła lub nie znaleziono tekstu. Wyślij ponownie media.",
        summarizing: "**Transkrypcja:**\n\n{}\n\n_Podsumowuję..._",
        summary_label: "**Podsumowanie:**\n\n{}",
        summary_error: "Błąd generowania podsumowania: {}",
        processing_failed: "Gemini nie udało się przetworzyć pliku multimedialnego."
    }
};

/**
 * Get translated text for the given language code and key
 * Similar to Python's get_text() function
 */
export function getText(langCode: string, key: string, ...args: any[]): string {
    const lang = langCode in TRANSLATIONS ? langCode : 'en';
    let text = TRANSLATIONS[lang][key] || TRANSLATIONS['en'][key] || key;

    // Replace {} placeholders with arguments
    for (const arg of args) {
        text = text.replace('{}', String(arg));
    }

    return text;
}
