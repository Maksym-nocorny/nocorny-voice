import os
import logging
import asyncio
import tempfile
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, CallbackQueryHandler, filters
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Configure Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not found in .env file")
    exit(1)

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not found in .env file")
    exit(1)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Store last transcription for each user: {chat_id: text}
last_transcriptions = {}

# Translations
TRANSLATIONS = {
    'en': {
        'welcome': "Hi! I'm a transcription bot. Send me a voice message or a video note, and I'll transcribe it for you using Gemini.",
        'downloading': "Downloading media...",
        'transcribing': "Transcribing with Gemini...",
        'unsupported': "Unsupported media type.",
        'error': "An error occurred: {}",
        'transcription_label': "**Transcription:**\n\n{}",
        'summarize_button': "📝 Summarize",
        'session_expired': "Session expired or no text found. Please resend the media.",
        'summarizing': "**Transcription:**\n\n{}\n\n_Summarizing..._",
        'summary_label': "**Summary:**\n\n{}",
        'summary_error': "Error generating summary: {}",
        'processing_failed': "Gemini failed to process the media file."
    },
    'uk': {
        'welcome': "Привіт! Я бот для транскрипції. Надішліть мені голосове повідомлення або відеоповідомлення, і я транскрибую його за допомогою Gemini.",
        'downloading': "Завантаження медіа...",
        'transcribing': "Транскрипція за допомогою Gemini...",
        'unsupported': "Непідтримуваний тип медіа.",
        'error': "Сталася помилка: {}",
        'transcription_label': "**Транскрипція:**\n\n{}",
        'summarize_button': "📝 Підсумувати",
        'session_expired': "Сесія закінчилася або текст не знайдено. Будь ласка, надішліть медіа знову.",
        'summarizing': "**Транскрипція:**\n\n{}\n\n_Підсумовую..._",
        'summary_label': "**Підсумок:**\n\n{}",
        'summary_error': "Помилка створення підсумку: {}",
        'processing_failed': "Gemini не вдалося обробити медіафайл."
    },
    'ru': {
        'welcome': "Привет! Я бот для транскрипции. Отправьте мне голосовое сообщение или видеосообщение, и я транскрибирую его с помощью Gemini.",
        'downloading': "Загрузка медиа...",
        'transcribing': "Транскрипция с помощью Gemini...",
        'unsupported': "Неподдерживаемый тип медиа.",
        'error': "Произошла ошибка: {}",
        'transcription_label': "**Транскрипция:**\n\n{}",
        'summarize_button': "📝 Резюмировать",
        'session_expired': "Сессия истекла или текст не найден. Пожалуйста, отправьте медиа снова.",
        'summarizing': "**Транскрипция:**\n\n{}\n\n_Резюмирую..._",
        'summary_label': "**Резюме:**\n\n{}",
        'summary_error': "Ошибка создания резюме: {}",
        'processing_failed': "Gemini не удалось обработать медиафайл."
    },
    'es': {
        'welcome': "¡Hola! Soy un bot de transcripción. Envíame un mensaje de voz o una nota de video, y lo transcribiré usando Gemini.",
        'downloading': "Descargando medios...",
        'transcribing': "Transcribiendo con Gemini...",
        'unsupported': "Tipo de medio no compatible.",
        'error': "Ocurrió un error: {}",
        'transcription_label': "**Transcripción:**\n\n{}",
        'summarize_button': "📝 Resumir",
        'session_expired': "Sesión expirada o texto no encontrado. Por favor, reenvía el medio.",
        'summarizing': "**Transcripción:**\n\n{}\n\n_Resumiendo..._",
        'summary_label': "**Resumen:**\n\n{}",
        'summary_error': "Error al generar resumen: {}",
        'processing_failed': "Gemini no pudo procesar el archivo multimedia."
    },
    'de': {
        'welcome': "Hallo! Ich bin ein Transkriptions-Bot. Sende mir eine Sprachnachricht oder eine Videonotiz, und ich transkribiere sie mit Gemini.",
        'downloading': "Medien werden heruntergeladen...",
        'transcribing': "Transkribieren mit Gemini...",
        'unsupported': "Nicht unterstützter Medientyp.",
        'error': "Ein Fehler ist aufgetreten: {}",
        'transcription_label': "**Transkription:**\n\n{}",
        'summarize_button': "📝 Zusammenfassen",
        'session_expired': "Sitzung abgelaufen oder Text nicht gefunden. Bitte sende die Medien erneut.",
        'summarizing': "**Transkription:**\n\n{}\n\n_Zusammenfassen..._",
        'summary_label': "**Zusammenfassung:**\n\n{}",
        'summary_error': "Fehler beim Erstellen der Zusammenfassung: {}",
        'processing_failed': "Gemini konnte die Mediendatei nicht verarbeiten."
    },
    'fr': {
        'welcome': "Salut! Je suis un bot de transcription. Envoyez-moi un message vocal ou une note vidéo, et je le transcrirai avec Gemini.",
        'downloading': "Téléchargement du média...",
        'transcribing': "Transcription avec Gemini...",
        'unsupported': "Type de média non pris en charge.",
        'error': "Une erreur s'est produite: {}",
        'transcription_label': "**Transcription:**\n\n{}",
        'summarize_button': "📝 Résumer",
        'session_expired': "Session expirée ou texte introuvable. Veuillez renvoyer le média.",
        'summarizing': "**Transcription:**\n\n{}\n\n_Résumé en cours..._",
        'summary_label': "**Résumé:**\n\n{}",
        'summary_error': "Erreur lors de la génération du résumé: {}",
        'processing_failed': "Gemini n'a pas pu traiter le fichier multimédia."
    },
    'it': {
        'welcome': "Ciao! Sono un bot di trascrizione. Inviami un messaggio vocale o una nota video e lo trascriverò usando Gemini.",
        'downloading': "Download del media...",
        'transcribing': "Trascrizione con Gemini...",
        'unsupported': "Tipo di media non supportato.",
        'error': "Si è verificato un errore: {}",
        'transcription_label': "**Trascrizione:**\n\n{}",
        'summarize_button': "📝 Riassumere",
        'session_expired': "Sessione scaduta o testo non trovato. Invia nuovamente il media.",
        'summarizing': "**Trascrizione:**\n\n{}\n\n_Riassumendo..._",
        'summary_label': "**Riassunto:**\n\n{}",
        'summary_error': "Errore nella generazione del riassunto: {}",
        'processing_failed': "Gemini non è riuscito a elaborare il file multimediale."
    },
    'pl': {
        'welcome': "Cześć! Jestem botem do transkrypcji. Wyślij mi wiadomość głosową lub notatkę wideo, a przepiszę ją za pomocą Gemini.",
        'downloading': "Pobieranie mediów...",
        'transcribing': "Transkrypcja za pomocą Gemini...",
        'unsupported': "Nieobsługiwany typ mediów.",
        'error': "Wystąpił błąd: {}",
        'transcription_label': "**Transkrypcja:**\n\n{}",
        'summarize_button': "📝 Podsumuj",
        'session_expired': "Sesja wygasła lub nie znaleziono tekstu. Wyślij ponownie media.",
        'summarizing': "**Transkrypcja:**\n\n{}\n\n_Podsumowuję..._",
        'summary_label': "**Podsumowanie:**\n\n{}",
        'summary_error': "Błąd generowania podsumowania: {}",
        'processing_failed': "Gemini nie udało się przetworzyć pliku multimedialnego."
    }
}

def get_text(lang_code, key, *args):
    """Get translated text for the given language code and key."""
    lang = lang_code if lang_code in TRANSLATIONS else 'en'
    text = TRANSLATIONS[lang].get(key, TRANSLATIONS['en'][key])
    return text.format(*args) if args else text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message."""
    user_lang = update.effective_user.language_code or 'en'
    await update.message.reply_text(get_text(user_lang, 'welcome'))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles voice and video note messages."""
    user_lang = update.effective_user.language_code or 'en'
    
    status_message = await update.message.reply_text(get_text(user_lang, 'downloading'))
    
    try:
        # Determine file type and get file object
        if update.message.voice:
            file_id = update.message.voice.file_id
            file_ext = ".ogg"
            mime_type = "audio/ogg"
        elif update.message.video_note:
            file_id = update.message.video_note.file_id
            file_ext = ".mp4"
            mime_type = "video/mp4"
        elif update.message.audio:
             file_id = update.message.audio.file_id
             file_ext = ".mp3"
             mime_type = update.message.audio.mime_type or "audio/mpeg"
        elif update.message.video:
             file_id = update.message.video.file_id
             file_ext = ".mp4"
             mime_type = update.message.video.mime_type or "video/mp4"
        else:
            await status_message.edit_text(get_text(user_lang, 'unsupported'))
            return

        new_file = await context.bot.get_file(file_id)
        
        # Create a temporary file to save the media
        with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as temp_file:
            temp_path = temp_file.name
            await new_file.download_to_drive(temp_path)
        
        await status_message.edit_text(get_text(user_lang, 'transcribing'))

        # Upload to Gemini
        gemini_file = genai.upload_file(path=temp_path, mime_type=mime_type)
        
        # Wait for processing to complete (essential for video)
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = genai.get_file(gemini_file.name)
            
        if gemini_file.state.name == "FAILED":
            raise ValueError(get_text(user_lang, 'processing_failed'))

        # Generate content
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content(["Transcribe this audio/video exactly as spoken.", gemini_file])
        
        # Cleanup local file
        os.remove(temp_path)
        
        # Store transcription
        chat_id = update.effective_chat.id
        last_transcriptions[chat_id] = response.text

        # Create keyboard
        button_text = get_text(user_lang, 'summarize_button')
        keyboard = [[InlineKeyboardButton(button_text, callback_data="summarize")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Split message if too long (Telegram limit is 4096 characters)
        transcription_text = get_text(user_lang, 'transcription_label', response.text)
        max_length = 4000  # Leave some margin for formatting
        
        if len(transcription_text) <= max_length:
            await status_message.edit_text(
                transcription_text, 
                parse_mode='Markdown', 
                reply_markup=reply_markup
            )
        else:
            # Delete status message
            await status_message.delete()
            
            # Send transcription in chunks
            label = get_text(user_lang, 'transcription_label', '')
            chunks = [response.text[i:i+max_length] for i in range(0, len(response.text), max_length)]
            
            for i, chunk in enumerate(chunks):
                if i == 0:
                    text = f"{label}{chunk}"
                else:
                    text = chunk
                    
                # Add button only to the last chunk
                if i == len(chunks) - 1:
                    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
                else:
                    await update.message.reply_text(text, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await status_message.edit_text(get_text(user_lang, 'error', str(e)))

async def handle_summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the summarize button click."""
    query = update.callback_query
    await query.answer()

    user_lang = update.effective_user.language_code or 'en'
    chat_id = update.effective_chat.id
    original_text = last_transcriptions.get(chat_id)

    if not original_text:
        await query.edit_message_text(text=get_text(user_lang, 'session_expired'))
        return

    await query.edit_message_text(
        text=get_text(user_lang, 'summarizing', original_text), 
        parse_mode='Markdown'
    )

    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        prompt = f"Summarize the following text concisely. The summary MUST be in the language '{user_lang}':\n\n{original_text}"
        response = model.generate_content([prompt])
        
        # Restore original text without button
        await query.edit_message_text(
            text=get_text(user_lang, 'transcription_label', original_text), 
            parse_mode='Markdown'
        )
        
        # Send summary
        await context.bot.send_message(
            chat_id=chat_id, 
            text=get_text(user_lang, 'summary_label', response.text), 
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Error summarizing: {e}")
        await context.bot.send_message(
            chat_id=chat_id, 
            text=get_text(user_lang, 'summary_error', str(e))
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    start_handler = CommandHandler('start', start)
    message_handler = MessageHandler(filters.VOICE | filters.VIDEO_NOTE | filters.AUDIO | filters.VIDEO, handle_message)
    summary_handler = CallbackQueryHandler(handle_summary_callback, pattern="^summarize$")
    
    application.add_handler(start_handler)
    application.add_handler(message_handler)
    application.add_handler(summary_handler)
    
    # Check for Render environment
    PORT = os.getenv("PORT")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")

    if PORT and WEBHOOK_URL:
        logger.info(f"Starting webhook on port {PORT}...")
        application.run_webhook(
            listen="0.0.0.0",
            port=int(PORT),
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
        )
    else:
        logger.info("Starting polling (Local Mode)...")
        application.run_polling()
