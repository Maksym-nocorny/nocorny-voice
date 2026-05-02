"""Multilingual UI strings. All formatting uses Telegram HTML mode (<b>, <i>)."""
from __future__ import annotations

from typing import Any

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "welcome": "Hi! I'm a transcription bot. Send me a voice message, audio file, video, or video note, and I'll transcribe it for you using Gemini.",
        "downloading": "Downloading media...",
        "transcribing": "Transcribing with Gemini...",
        "unsupported": "Unsupported media type.",
        "error_generic": "An error occurred. Please try again.",
        "transcription_label": "<b>Transcription:</b>\n\n{}",
        "processing_failed": "Gemini failed to process the media file.",
        "rate_limit_error": "Gemini is a bit busy right now due to high demand. ⏳ Please wait a minute and try again!",
        "rate_limit_user": "You're sending requests too fast. Please wait a moment.",
        "media_too_long": "The file is too long. Please send media shorter than {} minutes.",
        "media_too_large": "The file is too large. Please send media smaller than {} MB.",
    },
    "uk": {
        "welcome": "Привіт! Я бот для транскрипції. Надішліть мені голосове повідомлення, аудіофайл, відео або відеоповідомлення, і я транскрибую його за допомогою Gemini.",
        "downloading": "Завантаження медіа...",
        "transcribing": "Транскрипція за допомогою Gemini...",
        "unsupported": "Непідтримуваний тип медіа.",
        "error_generic": "Сталася помилка. Спробуйте ще раз.",
        "transcription_label": "<b>Транскрипція:</b>\n\n{}",
        "processing_failed": "Gemini не вдалося обробити медіафайл.",
        "rate_limit_error": "Gemini зараз трохи зайнятий через високий попит. ⏳ Будь ласка, зачекайте хвилинку і спробуйте ще раз!",
        "rate_limit_user": "Ви надсилаєте запити надто швидко. Зачекайте хвилинку.",
        "media_too_long": "Файл занадто довгий. Будь ласка, надішліть медіа коротше за {} хвилин.",
        "media_too_large": "Файл занадто великий. Надішліть медіа менше за {} МБ.",
    },
    "ru": {
        "welcome": "Привет! Я бот для транскрипции. Отправьте мне голосовое сообщение, аудиофайл, видео или видеосообщение, и я транскрибирую его с помощью Gemini.",
        "downloading": "Загрузка медиа...",
        "transcribing": "Транскрипция с помощью Gemini...",
        "unsupported": "Неподдерживаемый тип медиа.",
        "error_generic": "Произошла ошибка. Попробуйте еще раз.",
        "transcription_label": "<b>Транскрипция:</b>\n\n{}",
        "processing_failed": "Gemini не удалось обработать медиафайл.",
        "rate_limit_error": "Gemini сейчас немного занят из-за высокого спроса. ⏳ Пожалуйста, подождите минутку и попробуйте еще раз!",
        "rate_limit_user": "Вы отправляете запросы слишком быстро. Подождите немного.",
        "media_too_long": "Файл слишком длинный. Пожалуйста, отправьте медиа короче {} минут.",
        "media_too_large": "Файл слишком большой. Отправьте медиа меньше {} МБ.",
    },
    "es": {
        "welcome": "¡Hola! Soy un bot de transcripción. Envíame un mensaje de voz, archivo de audio, video o nota de video, y lo transcribiré usando Gemini.",
        "downloading": "Descargando medios...",
        "transcribing": "Transcribiendo con Gemini...",
        "unsupported": "Tipo de medio no compatible.",
        "error_generic": "Ocurrió un error. Por favor, inténtalo de nuevo.",
        "transcription_label": "<b>Transcripción:</b>\n\n{}",
        "processing_failed": "Gemini no pudo procesar el archivo multimedia.",
        "rate_limit_error": "Gemini está un poco ocupado en este momento debido a la alta demanda. ⏳ ¡Por favor, espera un minuto e inténtalo de nuevo!",
        "rate_limit_user": "Estás enviando solicitudes demasiado rápido. Espera un momento.",
        "media_too_long": "El archivo es demasiado largo. Por favor, envía medios de menos de {} minutos.",
        "media_too_large": "El archivo es demasiado grande. Por favor, envía medios de menos de {} MB.",
    },
    "de": {
        "welcome": "Hallo! Ich bin ein Transkriptions-Bot. Sende mir eine Sprachnachricht, Audiodatei, Video oder Videonotiz, und ich transkribiere sie mit Gemini.",
        "downloading": "Medien werden heruntergeladen...",
        "transcribing": "Transkribieren mit Gemini...",
        "unsupported": "Nicht unterstützter Medientyp.",
        "error_generic": "Ein Fehler ist aufgetreten. Bitte versuche es erneut.",
        "transcription_label": "<b>Transkription:</b>\n\n{}",
        "processing_failed": "Gemini konnte die Mediendatei nicht verarbeiten.",
        "rate_limit_error": "Gemini ist momentan aufgrund hoher Nachfrage etwas beschäftigt. ⏳ Bitte warte eine Minute und versuche es erneut!",
        "rate_limit_user": "Du sendest Anfragen zu schnell. Bitte warte einen Moment.",
        "media_too_long": "Die Datei ist zu lang. Bitte sende Medien kürzer als {} Minuten.",
        "media_too_large": "Die Datei ist zu groß. Bitte sende Medien kleiner als {} MB.",
    },
    "fr": {
        "welcome": "Salut! Je suis un bot de transcription. Envoyez-moi un message vocal, fichier audio, vidéo ou note vidéo, et je le transcrirai avec Gemini.",
        "downloading": "Téléchargement du média...",
        "transcribing": "Transcription avec Gemini...",
        "unsupported": "Type de média non pris en charge.",
        "error_generic": "Une erreur s'est produite. Veuillez réessayer.",
        "transcription_label": "<b>Transcription:</b>\n\n{}",
        "processing_failed": "Gemini n'a pas pu traiter le fichier multimédia.",
        "rate_limit_error": "Gemini est un peu occupé en ce moment en raison d'une forte demande. ⏳ Veuillez attendre une minute et réessayer !",
        "rate_limit_user": "Vous envoyez des requêtes trop rapidement. Veuillez patienter.",
        "media_too_long": "Le fichier est trop long. Veuillez envoyer des médias de moins de {} minutes.",
        "media_too_large": "Le fichier est trop volumineux. Veuillez envoyer des médias de moins de {} Mo.",
    },
    "it": {
        "welcome": "Ciao! Sono un bot di trascrizione. Inviami un messaggio vocale, file audio, video o nota video e lo trascriverò usando Gemini.",
        "downloading": "Download del media...",
        "transcribing": "Trascrizione con Gemini...",
        "unsupported": "Tipo di media non supportato.",
        "error_generic": "Si è verificato un errore. Per favore, riprova.",
        "transcription_label": "<b>Trascrizione:</b>\n\n{}",
        "processing_failed": "Gemini non è riuscito a elaborare il file multimediale.",
        "rate_limit_error": "Gemini è un po' occupato al momento a causa dell'alta domanda. ⏳ Per favore, aspetta un minuto e riprova!",
        "rate_limit_user": "Stai inviando richieste troppo velocemente. Attendi un momento.",
        "media_too_long": "Il file è troppo lungo. Per favore, invia file più corti di {} minuti.",
        "media_too_large": "Il file è troppo grande. Per favore, invia file più piccoli di {} MB.",
    },
    "pl": {
        "welcome": "Cześć! Jestem botem do transkrypcji. Wyślij mi wiadomość głosową, plik audio, wideo lub notatkę wideo, a przepiszę ją za pomocą Gemini.",
        "downloading": "Pobieranie mediów...",
        "transcribing": "Transkrypcja za pomocą Gemini...",
        "unsupported": "Nieobsługiwany typ mediów.",
        "error_generic": "Wystąpił błąd. Spróbuj ponownie.",
        "transcription_label": "<b>Transkrypcja:</b>\n\n{}",
        "processing_failed": "Gemini nie udało się przetworzyć pliku multimedialnego.",
        "rate_limit_error": "Gemini jest teraz nieco zajęty ze względu na duże zainteresowanie. ⏳ Poczekaj chwilkę i spróbuj ponownie!",
        "rate_limit_user": "Wysyłasz zapytania zbyt szybko. Poczekaj chwilę.",
        "media_too_long": "Plik jest za długi. Proszę wysłać media krótsze niż {} minut.",
        "media_too_large": "Plik jest za duży. Proszę wysłać media mniejsze niż {} MB.",
    },
}

DEFAULT_LANG = "en"


def get_text(lang_code: str | None, key: str, *args: Any) -> str:
    """Look up `key` for `lang_code`. Falls back to English if missing."""
    lang = lang_code if lang_code in TRANSLATIONS else DEFAULT_LANG
    text = TRANSLATIONS[lang].get(key) or TRANSLATIONS[DEFAULT_LANG][key]
    return text.format(*args) if args else text
