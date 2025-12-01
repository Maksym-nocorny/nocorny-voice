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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message."""
    await update.message.reply_text(
        "Hi! I'm a transcription bot. Send me a voice message or a video note, and I'll transcribe it for you using Gemini."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles voice and video note messages."""
    
    status_message = await update.message.reply_text("Downloading media...")
    
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
             file_ext = ".mp3" # Defaulting to mp3, but could be others
             mime_type = update.message.audio.mime_type or "audio/mpeg"
        elif update.message.video:
             file_id = update.message.video.file_id
             file_ext = ".mp4"
             mime_type = update.message.video.mime_type or "video/mp4"
        else:
            await status_message.edit_text("Unsupported media type.")
            return

        new_file = await context.bot.get_file(file_id)
        
        # Create a temporary file to save the media
        with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as temp_file:
            temp_path = temp_file.name
            await new_file.download_to_drive(temp_path)
        
        await status_message.edit_text("Transcribing with Gemini...")

        # Upload to Gemini
        gemini_file = genai.upload_file(path=temp_path, mime_type=mime_type)
        
        # Wait for processing to complete (essential for video)
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = genai.get_file(gemini_file.name)
            
        if gemini_file.state.name == "FAILED":
            raise ValueError("Gemini failed to process the media file.")

        # Generate content
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content(["Transcribe this audio/video exactly as spoken.", gemini_file])
        
        # Cleanup local file
        os.remove(temp_path)
        
        # Store transcription
        chat_id = update.effective_chat.id
        last_transcriptions[chat_id] = response.text

        # Get user's language for button text
        user_lang = update.effective_user.language_code or 'en'
        
        # Button text translations
        button_texts = {
            'en': '📝 Summarize',
            'uk': '📝 Підсумувати',
            'ru': '📝 Резюмировать',
            'es': '📝 Resumir',
            'de': '📝 Zusammenfassen',
            'fr': '📝 Résumer',
            'it': '📝 Riassumere',
            'pl': '📝 Podsumuj',
        }
        button_text = button_texts.get(user_lang, '📝 Summarize')  # Default to English

        # Create keyboard
        keyboard = [[InlineKeyboardButton(button_text, callback_data="summarize")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await status_message.edit_text(f"**Transcription:**\n\n{response.text}", parse_mode='Markdown', reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await status_message.edit_text(f"An error occurred: {str(e)}")

async def handle_summary_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the summarize button click."""
    query = update.callback_query
    await query.answer() # Acknowledge the callback

    chat_id = update.effective_chat.id
    original_text = last_transcriptions.get(chat_id)

    if not original_text:
        await query.edit_message_text(text="Session expired or no text found. Please resend the media.")
        return

    await query.edit_message_text(text=f"**Transcription:**\n\n{original_text}\n\n_Summarizing..._", parse_mode='Markdown')

    # Get user's language code (e.g., 'en', 'ru', 'uk')
    user_lang = update.effective_user.language_code or 'en'

    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        # Prompt Gemini to summarize in the specific language
        prompt = f"Summarize the following text concisely. The summary MUST be in the language '{user_lang}':\n\n{original_text}"
        response = model.generate_content([prompt])
        
        # Send summary as a new message so the original transcription is preserved (or append it)
        # User requested "option for the bot to propose to summarize... Another option should be hidden"
        # Since we edited the message to remove the button, let's append the summary or send new.
        # Let's send a new message for the summary to keep things clean.
        
        # First restore original text without button (already done by edit_message_text above effectively, but let's clean it up)
        await query.edit_message_text(text=f"**Transcription:**\n\n{original_text}", parse_mode='Markdown')
        
        # Send summary
        await context.bot.send_message(chat_id=chat_id, text=f"**Summary:**\n\n{response.text}", parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error summarizing: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"Error generating summary: {e}")

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
