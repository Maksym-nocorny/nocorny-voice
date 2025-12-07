// Translations for the Telegram Bot

export type TranslationKey =
    | "welcome"
    | "downloading"
    | "transcribing"
    | "unsupported"
    | "error"
    | "transcription_label"
    | "summarize_button"
    | "session_expired"
    | "summarizing"
    | "summary_label"
    | "summary_error"
    | "processing_failed"
    | "stats_title"
    | "stats_total"
    | "stats_transcriptions"
    | "stats_summaries"
    | "stats_users"
    | "stats_media_types"
    | "stats_top_users"
    | "stats_languages"
    | "stats_chat_types"
    | "stats_user_rank"
    | "stats_no_data"
    | "stats_unauthorized"
    | "stats_time_based"
    | "stats_last_7_days"
    | "stats_last_6_months"
    | "stats_by_year"
    | "stats_hourly";

type Translations = {
    [lang: string]: {
        [key in TranslationKey]: string;
    };
};

export const TRANSLATIONS: Translations = {
    en: {
        welcome:
            "Hi! I'm a transcription bot. Send me a voice message or a video note, and I'll transcribe it for you using Gemini.",
        downloading: "Downloading media...",
        transcribing: "Transcribing with Gemini...",
        unsupported: "Unsupported media type.",
        error: "An error occurred: {}",
        transcription_label: "**Transcription:**\n\n{}",
        summarize_button: "📝 Summarize",
        session_expired:
            "Session expired or no text found. Please resend the media.",
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
        stats_hourly: "\n**Peak Hours (UTC):**",
    },
    uk: {
        welcome:
            "Привіт! Я бот для транскрипції. Надішліть мені голосове повідомлення або відеоповідомлення, і я транскрибую його за допомогою Gemini.",
        downloading: "Завантаження медіа...",
        transcribing: "Транскрипція за допомогою Gemini...",
        unsupported: "Непідтримуваний тип медіа.",
        error: "Сталася помилка: {}",
        transcription_label: "**Транскрипція:**\n\n{}",
        summarize_button: "📝 Підсумувати",
        session_expired:
            "Сесія закінчилася або текст не знайдено. Будь ласка, надішліть медіа знову.",
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
        stats_hourly: "\n**Пікові години (UTC):**",
    },
    ru: {
        welcome:
            "Привет! Я бот для транскрипции. Отправьте мне голосовое сообщение или видеосообщение, и я транскрибирую его с помощью Gemini.",
        downloading: "Загрузка медиа...",
        transcribing: "Транскрипция с помощью Gemini...",
        unsupported: "Неподдерживаемый тип медиа.",
        error: "Произошла ошибка: {}",
        transcription_label: "**Транскрипция:**\n\n{}",
        summarize_button: "📝 Резюмировать",
        session_expired:
            "Сессия истекла или текст не найден. Пожалуйста, отправьте медиа снова.",
        summarizing: "**Транскрипция:**\n\n{}\n\n_Резюмирую..._",
        summary_label: "**Резюме:**\n\n{}",
        summary_error: "Ошибка создания резюме: {}",
        processing_failed: "Gemini не удалось обработить медиафайл.",
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
        stats_hourly: "\n**Пиковые часы (UTC):**",
    },
    es: {
        welcome:
            "¡Hola! Soy un bot de transcripción. Envíame un mensaje de voz o una nota de video, y lo transcribiré usando Gemini.",
        downloading: "Descargando medios...",
        transcribing: "Transcribiendo con Gemini...",
        unsupported: "Tipo de medio no compatible.",
        error: "Ocurrió un error: {}",
        transcription_label: "**Transcripción:**\n\n{}",
        summarize_button: "📝 Resumir",
        session_expired:
            "Sesión expirada o texto no encontrado. Por favor, reenvía el medio.",
        summarizing: "**Transcripción:**\n\n{}\n\n_Resumiendo..._",
        summary_label: "**Resumen:**\n\n{}",
        summary_error: "Error al generar resumen: {}",
        processing_failed: "Gemini no pudo procesar el archivo multimedia.",
        stats_title: "📊 **Estadísticas del Bot**",
        stats_total: "**Estadísticas Totales:**",
        stats_transcriptions: "• Transcripciones: {}",
        stats_summaries: "• Resúmenes: {}",
        stats_users: "• Usuarios únicos: {}",
        stats_media_types: "\n**Tipos de Medios:**",
        stats_top_users: "\n**Top 10 Usuarios:**",
        stats_languages: "\n**Distribución de Idiomas:**",
        stats_chat_types: "\n**Tipos de Chat:**",
        stats_user_rank: "{}. {} - {} solicitudes",
        stats_no_data: "Aún no hay estadísticas disponibles.",
        stats_unauthorized: "⛔ No estás autorizado para ver las estadísticas.",
        stats_time_based: "\n**Uso a lo Largo del Tiempo:**",
        stats_last_7_days: "Últimos 7 días:",
        stats_last_6_months: "Últimos 6 meses:",
        stats_by_year: "Por año:",
        stats_hourly: "\n**Horas Pico (UTC):**",
    },
    de: {
        welcome:
            "Hallo! Ich bin ein Transkriptions-Bot. Sende mir eine Sprachnachricht oder eine Videonotiz, und ich transkribiere sie mit Gemini.",
        downloading: "Medien werden heruntergeladen...",
        transcribing: "Transkribieren mit Gemini...",
        unsupported: "Nicht unterstützter Medientyp.",
        error: "Ein Fehler ist aufgetreten: {}",
        transcription_label: "**Transkription:**\n\n{}",
        summarize_button: "📝 Zusammenfassen",
        session_expired:
            "Sitzung abgelaufen oder Text nicht gefunden. Bitte sende die Medien erneut.",
        summarizing: "**Transkription:**\n\n{}\n\n_Zusammenfassen..._",
        summary_label: "**Zusammenfassung:**\n\n{}",
        summary_error: "Fehler beim Erstellen der Zusammenfassung: {}",
        processing_failed: "Gemini konnte die Mediendatei nicht verarbeiten.",
        stats_title: "📊 **Bot-Statistiken**",
        stats_total: "**Gesamtstatistiken:**",
        stats_transcriptions: "• Transkriptionen: {}",
        stats_summaries: "• Zusammenfassungen: {}",
        stats_users: "• Eindeutige Benutzer: {}",
        stats_media_types: "\n**Medientypen:**",
        stats_top_users: "\n**Top 10 Benutzer:**",
        stats_languages: "\n**Sprachverteilung:**",
        stats_chat_types: "\n**Chat-Typen:**",
        stats_user_rank: "{}. {} - {} Anfragen",
        stats_no_data: "Noch keine Statistiken verfügbar.",
        stats_unauthorized:
            "⛔ Sie sind nicht berechtigt, Statistiken anzuzeigen.",
        stats_time_based: "\n**Nutzung im Zeitverlauf:**",
        stats_last_7_days: "Letzte 7 Tage:",
        stats_last_6_months: "Letzte 6 Monate:",
        stats_by_year: "Nach Jahr:",
        stats_hourly: "\n**Spitzenstunden (UTC):**",
    },
    fr: {
        welcome:
            "Salut! Je suis un bot de transcription. Envoyez-moi un message vocal ou une note vidéo, et je le transcrirai avec Gemini.",
        downloading: "Téléchargement du média...",
        transcribing: "Transcription avec Gemini...",
        unsupported: "Type de média non pris en charge.",
        error: "Une erreur s'est produite: {}",
        transcription_label: "**Transcription:**\n\n{}",
        summarize_button: "📝 Résumer",
        session_expired:
            "Session expirée ou texte introuvable. Veuillez renvoyer le média.",
        summarizing: "**Transcription:**\n\n{}\n\n_Résumé en cours..._",
        summary_label: "**Résumé:**\n\n{}",
        summary_error: "Erreur lors de la génération du résumé: {}",
        processing_failed: "Gemini n'a pas pu traiter le fichier multimédia.",
        stats_title: "📊 **Statistiques du Bot**",
        stats_total: "**Statistiques Totales:**",
        stats_transcriptions: "• Transcriptions: {}",
        stats_summaries: "• Résumés: {}",
        stats_users: "• Utilisateurs uniques: {}",
        stats_media_types: "\n**Types de Médias:**",
        stats_top_users: "\n**Top 10 Utilisateurs:**",
        stats_languages: "\n**Distribution des Langues:**",
        stats_chat_types: "\n**Types de Chat:**",
        stats_user_rank: "{}. {} - {} requêtes",
        stats_no_data: "Aucune statistique disponible pour le moment.",
        stats_unauthorized:
            "⛔ Vous n'êtes pas autorisé à consulter les statistiques.",
        stats_time_based: "\n**Utilisation au Fil du Temps:**",
        stats_last_7_days: "7 derniers jours:",
        stats_last_6_months: "6 derniers mois:",
        stats_by_year: "Par année:",
        stats_hourly: "\n**Heures de Pointe (UTC):**",
    },
    it: {
        welcome:
            "Ciao! Sono un bot di trascrizione. Inviami un messaggio vocale o una nota video e lo trascriverò usando Gemini.",
        downloading: "Download del media...",
        transcribing: "Trascrizione con Gemini...",
        unsupported: "Tipo di media non supportato.",
        error: "Si è verificato un errore: {}",
        transcription_label: "**Trascrizione:**\n\n{}",
        summarize_button: "📝 Riassumere",
        session_expired:
            "Sessione scaduta o testo non trovato. Invia nuovamente il media.",
        summarizing: "**Trascrizione:**\n\n{}\n\n_Riassumendo..._",
        summary_label: "**Riassunto:**\n\n{}",
        summary_error: "Errore nella generazione del riassunto: {}",
        processing_failed: "Gemini non è riuscito a elaborare il file multimediale.",
        stats_title: "📊 **Statistiche del Bot**",
        stats_total: "**Statistiche Totali:**",
        stats_transcriptions: "• Trascrizioni: {}",
        stats_summaries: "• Riassunti: {}",
        stats_users: "• Utenti unici: {}",
        stats_media_types: "\n**Tipi di Media:**",
        stats_top_users: "\n**Top 10 Utenti:**",
        stats_languages: "\n**Distribuzione delle Lingue:**",
        stats_chat_types: "\n**Tipi di Chat:**",
        stats_user_rank: "{}. {} - {} richieste",
        stats_no_data: "Nessuna statistica ancora disponibile.",
        stats_unauthorized:
            "⛔ Non sei autorizzato a visualizzare le statistiche.",
        stats_time_based: "\n**Utilizzo nel Tempo:**",
        stats_last_7_days: "Ultimi 7 giorni:",
        stats_last_6_months: "Ultimi 6 mesi:",
        stats_by_year: "Per anno:",
        stats_hourly: "\n**Ore di Punta (UTC):**",
    },
    pl: {
        welcome:
            "Cześć! Jestem botem do transkrypcji. Wyślij mi wiadomość głosową lub notatkę wideo, a przepiszę ją za pomocą Gemini.",
        downloading: "Pobieranie mediów...",
        transcribing: "Transkrypcja za pomocą Gemini...",
        unsupported: "Nieobsługiwany typ mediów.",
        error: "Wystąpił błąd: {}",
        transcription_label: "**Transkrypcja:**\n\n{}",
        summarize_button: "📝 Podsumuj",
        session_expired:
            "Sesja wygasła lub nie znaleziono tekstu. Wyślij ponownie media.",
        summarizing: "**Transkrypcja:**\n\n{}\n\n_Podsumowuję..._",
        summary_label: "**Podsumowanie:**\n\n{}",
        summary_error: "Błąd generowania podsumowania: {}",
        processing_failed: "Gemini nie udało się przetworzyć pliku multimedialnego.",
        stats_title: "📊 **Statystyki Bota**",
        stats_total: "**Statystyki Ogólne:**",
        stats_transcriptions: "• Transkrypcje: {}",
        stats_summaries: "• Podsumowania: {}",
        stats_users: "• Unikalni użytkownicy: {}",
        stats_media_types: "\n**Typy Mediów:**",
        stats_top_users: "\n**Top 10 Użytkowników:**",
        stats_languages: "\n**Rozkład Języków:**",
        stats_chat_types: "\n**Typy Czatów:**",
        stats_user_rank: "{}. {} - {} żądań",
        stats_no_data: "Brak dostępnych statystyk.",
        stats_unauthorized: "⛔ Nie masz uprawnień do przeglądania statystyk.",
        stats_time_based: "\n**Wykorzystanie w Czasie:**",
        stats_last_7_days: "Ostatnie 7 dni:",
        stats_last_6_months: "Ostatnie 6 miesięcy:",
        stats_by_year: "Według roku:",
        stats_hourly: "\n**Godziny Szczytu (UTC):**",
    },
};

export function getText(
    langCode: string | undefined,
    key: TranslationKey,
    ...args: (string | number)[]
): string {
    const lang = langCode && langCode in TRANSLATIONS ? langCode : "en";
    let text = TRANSLATIONS[lang][key] || TRANSLATIONS["en"][key];

    // Replace {} placeholders with args
    args.forEach((arg) => {
        text = text.replace("{}", String(arg));
    });

    return text;
}
