import os
import logging
import asyncio
import tempfile
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, CallbackQueryHandler, filters
import google.generativeai as genai
from analytics import Analytics

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
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")  # Optional: restrict /stats to admin only

if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not found in .env file")
    exit(1)

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not found in .env file")
    exit(1)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Initialize Analytics
analytics = Analytics()

# Store last transcription: {(chat_id, message_id): text}
# For private chats, message_id is the transcription message id
# For groups, message_id is the original voice message id
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
        'processing_failed': "Gemini failed to process the media file.",
        'stats_title': "📊 **Bot Statistics**",
        'stats_total': "**Total Statistics:**",
        'stats_transcriptions': "• Transcriptions: {}",
        'stats_summaries': "• Summaries: {}",
        'stats_users': "• Unique users: {}",
        'stats_media_types': "\n**Media Types:**",
        'stats_top_users': "\n**Top 10 Users:**",
        'stats_languages': "\n**Language Distribution:**",
        'stats_chat_types': "\n**Chat Types:**",
        'stats_user_rank': "{}. {} - {} requests",
        'stats_no_data': "No statistics available yet.",
        'stats_unauthorized': "⛔ You are not authorized to view statistics.",
        'stats_time_based': "\n**Usage Over Time:**",
        'stats_last_7_days': "Last 7 days:",
        'stats_last_6_months': "Last 6 months:",
        'stats_by_year': "By year:",
        'stats_hourly': "\n**Peak Hours (UTC):**"
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
        'processing_failed': "Gemini не вдалося обробити медіафайл.",
        'stats_title': "📊 **Статистика бота**",
        'stats_total': "**Загальна статистика:**",
        'stats_transcriptions': "• Транскрипцій: {}",
        'stats_summaries': "• Підсумків: {}",
        'stats_users': "• Унікальних користувачів: {}",
        'stats_media_types': "\n**Типи медіа:**",
        'stats_top_users': "\n**Топ 10 користувачів:**",
        'stats_languages': "\n**Розподіл мов:**",
        'stats_chat_types': "\n**Типи чатів:**",
        'stats_user_rank': "{}. {} - {} запитів",
        'stats_no_data': "Статистика ще недоступна.",
        'stats_unauthorized': "⛔ Ви не маєте доступу до перегляду статистики.",
        'stats_time_based': "\n**Використання за часом:**",
        'stats_last_7_days': "Останні 7 днів:",
        'stats_last_6_months': "Останні 6 місяців:",
        'stats_by_year': "За роками:",
        'stats_hourly': "\n**Пікові години (UTC):**"
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
        'processing_failed': "Gemini не удалось обработать медиафайл.",
        'stats_title': "📊 **Статистика бота**",
        'stats_total': "**Общая статистика:**",
        'stats_transcriptions': "• Транскрипций: {}",
        'stats_summaries': "• Резюме: {}",
        'stats_users': "• Уникальных пользователей: {}",
        'stats_media_types': "\n**Типы медиа:**",
        'stats_top_users': "\n**Топ 10 пользователей:**",
        'stats_languages': "\n**Распределение языков:**",
        'stats_chat_types': "\n**Типы чатов:**",
        'stats_user_rank': "{}. {} - {} запросов",
        'stats_no_data': "Статистика пока недоступна.",
        'stats_unauthorized': "⛔ Вы не авторизованы для просмотра статистики.",
        'stats_time_based': "\n**Использование по времени:**",
        'stats_last_7_days': "Последние 7 дней:",
        'stats_last_6_months': "Последние 6 месяцев:",
        'stats_by_year': "По годам:",
        'stats_hourly': "\n**Пиковые часы (UTC):**"
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
    user = update.effective_user
    user_lang = user.language_code or 'en'
    
    # Track user
    analytics.track_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user_lang
    )
    
    await update.message.reply_text(get_text(user_lang, 'welcome'))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles voice and video note messages."""
    user = update.effective_user
    user_lang = user.language_code or 'en'
    
    # Track user
    analytics.track_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user_lang
    )
    
    status_message = await update.message.reply_text(get_text(user_lang, 'downloading'))
    
    try:
        # Determine file type and get file object
        media_type = None
        if update.message.voice:
            file_id = update.message.voice.file_id
            file_ext = ".ogg"
            mime_type = "audio/ogg"
            media_type = "voice"
        elif update.message.video_note:
            file_id = update.message.video_note.file_id
            file_ext = ".mp4"
            mime_type = "video/mp4"
            media_type = "video_note"
        elif update.message.audio:
             file_id = update.message.audio.file_id
             file_ext = ".mp3"
             mime_type = update.message.audio.mime_type or "audio/mpeg"
             media_type = "audio"
        elif update.message.video:
             file_id = update.message.video.file_id
             file_ext = ".mp4"
             mime_type = update.message.video.mime_type or "video/mp4"
             media_type = "video"
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
        
        # Track transcription event
        chat_type = 'private' if update.effective_chat.type == 'private' else 'group'
        analytics.track_event(
            user_id=user.id,
            event_type='transcription',
            media_type=media_type,
            chat_type=chat_type
        )
        
        # Store transcription
        chat_id = update.effective_chat.id
        is_group = update.effective_chat.type in ['group', 'supergroup']
        
        # For groups, use original message id; for private, we'll use the sent message id
        storage_key_message_id = update.message.message_id if is_group else None

        # Create keyboard with callback data containing message_id (only for private chats)
        button_text = get_text(user_lang, 'summarize_button')
        callback_data = f"summarize_{update.message.message_id}" if is_group else "summarize"
        
        # Only add summarize button in private chats
        if is_group:
            reply_markup = None
        else:
            keyboard = [[InlineKeyboardButton(button_text, callback_data=callback_data)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

        # Split message if too long (Telegram limit is 4096 characters)
        transcription_text = get_text(user_lang, 'transcription_label', response.text)
        max_length = 4000  # Leave some margin for formatting
        
        if len(transcription_text) <= max_length:
            # In groups, reply to the original message
            if is_group:
                sent_msg = await update.message.reply_text(
                    transcription_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                await status_message.delete()
            else:
                sent_msg = await status_message.edit_text(
                    transcription_text, 
                    parse_mode='Markdown', 
                    reply_markup=reply_markup
                )
            
            # Store with appropriate key
            if not is_group:
                storage_key_message_id = sent_msg.message_id
            last_transcriptions[(chat_id, storage_key_message_id)] = response.text
        else:
            # Delete status message
            await status_message.delete()
            
            # Send transcription in chunks
            label = get_text(user_lang, 'transcription_label', '')
            chunks = [response.text[i:i+max_length] for i in range(0, len(response.text), max_length)]
            
            sent_msg = None
            for i, chunk in enumerate(chunks):
                if i == 0:
                    text = f"{label}{chunk}"
                else:
                    text = chunk
                    
                # Add button only to the last chunk
                if i == len(chunks) - 1:
                    if is_group:
                        sent_msg = await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
                    else:
                        sent_msg = await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
                else:
                    if is_group:
                        await update.message.reply_text(text, parse_mode='Markdown')
                    else:
                        await update.message.reply_text(text, parse_mode='Markdown')
            
            # Store with appropriate key
            if not is_group and sent_msg:
                storage_key_message_id = sent_msg.message_id
            last_transcriptions[(chat_id, storage_key_message_id)] = response.text

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await status_message.edit_text(get_text(user_lang, 'error', str(e)))

async def handle_summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the summarize button click."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    user_lang = user.language_code or 'en'
    chat_id = update.effective_chat.id
    
    # Track user
    analytics.track_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user_lang
    )
    
    # Extract message_id from callback_data
    callback_data = query.data
    if callback_data.startswith("summarize_"):
        # Group chat - extract message_id
        message_id = int(callback_data.split("_")[1])
    else:
        # Private chat - use the message with the button
        message_id = query.message.message_id
    
    original_text = last_transcriptions.get((chat_id, message_id))

    if not original_text:
        await query.answer(text=get_text(user_lang, 'session_expired'), show_alert=True)
        return

    # Remove the button immediately to prevent multiple clicks
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except:
        pass  # Ignore if message can't be edited

    # Send a status message
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=get_text(user_lang, 'summarizing', '...'),
        parse_mode='Markdown'
    )

    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        prompt = f"Summarize the following text concisely. The summary MUST be in the language '{user_lang}':\n\n{original_text}"
        response = model.generate_content([prompt])
        
        # Track summary event
        chat_type = 'private' if update.effective_chat.type == 'private' else 'group'
        analytics.track_event(
            user_id=user.id,
            event_type='summary',
            chat_type=chat_type
        )
        
        # Delete status message
        await status_msg.delete()
        
        # Send summary
        await context.bot.send_message(
            chat_id=chat_id, 
            text=get_text(user_lang, 'summary_label', response.text), 
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Error summarizing: {e}")
        await status_msg.edit_text(get_text(user_lang, 'summary_error', str(e)))

async def handle_chat_migration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle group to supergroup migration."""
    old_chat_id = update.message.migrate_from_chat_id
    new_chat_id = update.message.chat_id
    
    logger.info(f"Chat migration: {old_chat_id} -> {new_chat_id}")
    
    # Migrate all stored transcriptions
    keys_to_migrate = [key for key in last_transcriptions.keys() if key[0] == old_chat_id]
    for old_key in keys_to_migrate:
        new_key = (new_chat_id, old_key[1])
        last_transcriptions[new_key] = last_transcriptions.pop(old_key)
    
    logger.info(f"Migrated {len(keys_to_migrate)} transcriptions")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display bot statistics."""
    user = update.effective_user
    user_lang = user.language_code or 'en'
    
    # Check if user is admin (if ADMIN_USER_ID is set)
    if ADMIN_USER_ID and str(user.id) != ADMIN_USER_ID:
        await update.message.reply_text(get_text(user_lang, 'stats_unauthorized'))
        logger.warning(f"Unauthorized stats access attempt by user {user.id} (@{user.username})")
        return
    
    # Track user
    analytics.track_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user_lang
    )
    
    try:
        # Get statistics
        total_stats = analytics.get_total_stats()
        
        if total_stats['total_events'] == 0:
            await update.message.reply_text(get_text(user_lang, 'stats_no_data'))
            return
        
        # Build stats message
        message = get_text(user_lang, 'stats_title') + "\n\n"
        message += get_text(user_lang, 'stats_total') + "\n"
        message += get_text(user_lang, 'stats_transcriptions', total_stats['total_transcriptions']) + "\n"
        message += get_text(user_lang, 'stats_summaries', total_stats['total_summaries']) + "\n"
        message += get_text(user_lang, 'stats_users', total_stats['total_users']) + "\n"
        
        # Media types
        media_stats = analytics.get_media_type_stats()
        if media_stats:
            message += get_text(user_lang, 'stats_media_types') + "\n"
            for media_type, count in media_stats.items():
                message += f"• {media_type}: {count}\n"
        
        # Chat types
        chat_stats = analytics.get_chat_type_stats()
        if chat_stats:
            message += get_text(user_lang, 'stats_chat_types') + "\n"
            for chat_type, count in chat_stats.items():
                message += f"• {chat_type}: {count}\n"
        
        # Top users
        top_users = analytics.get_top_users(10)
        if top_users:
            message += get_text(user_lang, 'stats_top_users') + "\n"
            for i, (user_id, username, count) in enumerate(top_users, 1):
                display_name = f"@{username}" if username else f"User {user_id}"
                message += get_text(user_lang, 'stats_user_rank', i, display_name, count) + "\n"
        
        # Language distribution
        lang_stats = analytics.get_language_distribution()
        if lang_stats:
            message += get_text(user_lang, 'stats_languages') + "\n"
            for lang, count in lang_stats.items():
                message += f"• {lang}: {count}\n"
        
        # Time-based statistics
        message += get_text(user_lang, 'stats_time_based') + "\n"
        
        # Last 7 days
        daily_stats = analytics.get_daily_stats(7)
        if daily_stats:
            message += f"\n{get_text(user_lang, 'stats_last_7_days')}\n"
            for day, count in daily_stats:
                message += f"• {day}: {count}\n"
        
        # Last 6 months
        monthly_stats = analytics.get_monthly_stats(6)
        if monthly_stats:
            message += f"\n{get_text(user_lang, 'stats_last_6_months')}\n"
            for month, count in monthly_stats:
                message += f"• {month}: {count}\n"
        
        # By year
        yearly_stats = analytics.get_yearly_stats()
        if yearly_stats:
            message += f"\n{get_text(user_lang, 'stats_by_year')}\n"
            for year, count in yearly_stats:
                message += f"• {year}: {count}\n"
        
        # Hourly distribution (top 5 peak hours)
        hourly_stats = analytics.get_hourly_distribution()
        if hourly_stats:
            message += get_text(user_lang, 'stats_hourly') + "\n"
            sorted_hours = sorted(hourly_stats.items(), key=lambda x: x[1], reverse=True)[:5]
            for hour, count in sorted_hours:
                message += f"• {hour:02d}:00 - {count} requests\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        await update.message.reply_text(get_text(user_lang, 'error', str(e)))



if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    start_handler = CommandHandler('start', start)
    stats_handler = CommandHandler('stats', stats)
    message_handler = MessageHandler(filters.VOICE | filters.VIDEO_NOTE | filters.AUDIO | filters.VIDEO, handle_message)
    summary_handler = CallbackQueryHandler(handle_summary_callback, pattern="^summarize")
    migration_handler = MessageHandler(filters.StatusUpdate.MIGRATE, handle_chat_migration)
    
    application.add_handler(start_handler)
    application.add_handler(stats_handler)
    application.add_handler(migration_handler)  # Add migration handler before message handler
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
