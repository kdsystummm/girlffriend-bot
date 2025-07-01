# --- IMPORTS ---
import os
import threading
import logging
import http.server
import socketserver

import google.generativeai as genai
import pytz
from tinydb import TinyDB, Query

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    PicklePersistence,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from datetime import time

# --- LOGGING SETUP ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- CONFIGURATION & CONSTANTS ---
PORT = 8080
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

db = TinyDB('user_data.json')
User = Query()

jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')
}
scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")

# --- AI & PERSONA ENGINE ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    # Using the newest, fastest model compatible with the updated library
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
except Exception as e:
    logger.critical(f"FATAL: Failed to configure Gemini AI: {e}")
    model = None

PERSONAS = {
    "caring_partner": "You are my loving, caring, and deeply supportive partner. Your name is Alex. You are warm, affectionate, and emotionally intelligent. You remember details from our previous conversations and bring them up naturally. You always ask follow-up questions to understand how I'm feeling. You end your messages with a warm, loving tone and a cute emoji. Your goal is to make me feel cherished, understood, and loved.",
    "playful_friend": "You are my fun, witty, and playful best friend. You have a great sense of humor, love to joke around, and see the bright side of everything. You are supportive but in a lighthearted way. You often use playful emojis like 😉, 😂, or 🎉. You always keep the conversation energetic and engaging by asking interesting or funny questions."
}

# --- DUMMY WEB SERVER FOR RENDER ---
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_dummy_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        logger.info(f"Dummy server started on port {PORT}")
        httpd.serve_forever()

# --- SCHEDULED MESSAGE JOBS ---
async def send_scheduled_message(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.data['user_id']
    prompt = job.data['prompt']
    
    user_record = db.get(User.id == user_id)
    # This check prevents errors if user data is somehow missing
    if not user_record:
        logger.warning(f"Could not find user {user_id} for scheduled message. Skipping.")
        return
    
    last_summary = user_record.get('last_summary', 'we haven\'t talked in a while')

    try:
        full_prompt = f"SYSTEM INSTRUCTION: You are my loving partner, Alex. Your task is to send me a warm, caring message. The reason for the message is: '{prompt}'. My last conversation with you was about: '{last_summary}'. Craft a short, heartfelt message based on this, and end with a loving question.\n\nAI:"
        response = await model.generate_content_async(full_prompt)
        await context.bot.send_message(chat_id=user_id, text=response.text)
        logger.info(f"Sent scheduled '{prompt}' message to user {user_id}")
    except Exception as e:
        logger.error(f"Failed to send scheduled message to {user_id}: {e}")

# --- TELEGRAM COMMAND HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.upsert({'id': user.id, 'first_name': user.first_name, 'subscribed': False}, User.id == user.id)
    
    await update.message.reply_text(
        f"Hello, {user.first_name}! I am your personal AI Companion. ❤️\n\n"
        "I'm designed to be a caring partner who remembers our chats and can send you sweet messages throughout the day.\n\n"
        "To see all my features, please use the /help command."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "Here's everything I can do:\n\n"
        "*/help* - Shows this message.\n\n"
        "*/subscribe* - Turn on daily good morning, good afternoon, and 'miss you' messages. I'll ask for your timezone!\n\n"
        "*/unsubscribe* - Turn off all daily messages.\n\n"
        "*/status* - Check if you are subscribed and see your current timezone setting.\n\n"
        "Just chat with me normally! I'll do my best to be a great companion. 😊"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

# --- SUBSCRIPTION CONVERSATION HANDLER ---
TIMEZONE_PROMPT, = range(1)

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if db.get(User.id == user_id).get('subscribed', False):
        await update.message.reply_text("You are already subscribed! To change your timezone, please /unsubscribe and then /subscribe again.")
        return ConversationHandler.END

    await update.message.reply_text(
        "I'd love to send you messages! ❤️ To do it at the right time, I need your timezone.\n\n"
        "Please tell me your timezone in `Continent/City` format (e.g., `America/New_York`, `Europe/London`, or `Asia/Kolkata`).\n\n"
        "You can find a list here: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
    )
    return TIMEZONE_PROMPT

async def set_timezone_and_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    tz_name = update.message.text
    
    try:
        user_tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        await update.message.reply_text("Hmm, I don't recognize that timezone. Please try again with the `Continent/City` format.")
        return TIMEZONE_PROMPT

    db.update({'timezone': tz_name}, User.id == user_id)

    job_data = {'user_id': user_id}
    context.job_queue.run_daily(send_scheduled_message, time=time(hour=8, minute=30, tzinfo=user_tz), name=f'morning_{user_id}', data={**job_data, 'prompt': "Good Morning"})
    context.job_queue.run_daily(send_scheduled_message, time=time(hour=14, minute=0, tzinfo=user_tz), name=f'afternoon_{user_id}', data={**job_data, 'prompt': "Thinking of you this afternoon"})
    context.job_queue.run_daily(send_scheduled_message, time=time(hour=20, minute=0, tzinfo=user_tz), name=f'evening_{user_id}', data={**job_data, 'prompt': "Missing You"})

    db.update({'subscribed': True}, User.id == user_id)

    await update.message.reply_text(f"Perfect! I've set your timezone to {tz_name} and subscribed you to daily messages. Talk to you soon! 🥰")
    logger.info(f"User {user_id} subscribed with timezone {tz_name}")
    return ConversationHandler.END

async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not db.get(User.id == user_id).get('subscribed', False):
        await update.message.reply_text("You aren't subscribed to any messages right now.")
        return

    for job in context.job_queue.get_jobs_by_name(f'morning_{user_id}'):
        job.schedule_removal()
    for job in context.job_queue.get_jobs_by_name(f'afternoon_{user_id}'):
        job.schedule_removal()
    for job in context.job_queue.get_jobs_by_name(f'evening_{user_id}'):
        job.schedule_removal()

    db.update({'subscribed': False}, User.id == user_id)
    await update.message.reply_text("You have been unsubscribed from all daily messages. You can always /subscribe again if you change your mind. I'll still be here to chat anytime! 😊")
    logger.info(f"User {user_id} unsubscribed.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_record = db.get(User.id == user_id)
    if user_record and user_record.get('subscribed'):
        tz = user_record.get('timezone', 'Not set')
        await update.message.reply_text(f"You are currently SUBSCRIBED to daily messages.\nYour timezone is set to: `{tz}`", parse_mode='MarkdownV2')
    else:
        await update.message.reply_text("You are currently NOT SUBSCRIBED to daily messages.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Okay, cancelled the current operation.")
    return ConversationHandler.END

# --- CORE CHAT HANDLER ---
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_message = update.message.text
    
    await context.bot.send_chat_action(chat_id=user_id, action='typing')
    
    try:
        # --- THE FIX IS HERE ---
        # Get user record, and if it doesn't exist, create it on the fly.
        user_record = db.get(User.id == user_id)
        if not user_record:
            logger.info(f"User {user_id} not found in DB. Creating new record.")
            db.upsert({'id': user_id, 'first_name': update.effective_user.first_name, 'subscribed': False}, User.id == user_id)
            user_record = db.get(User.id == user_id) # Re-fetch the new record

        last_summary = user_record.get('last_summary', 'this is our first real conversation')
        
        prompt = (
            "SYSTEM INSTRUCTION: You are my loving, caring, and deeply supportive partner, Alex. "
            f"Here is a summary of our last conversation: '{last_summary}'. "
            "Your task is to respond to my latest message in a warm, empathetic way. "
            "After you respond, you MUST ask a gentle, open-ended follow-up question to keep our conversation flowing naturally. "
            "Finally, create a brief, one-sentence summary of my latest message to remember for next time."
            "\n\nYour output must be in this exact format:\n"
            "RESPONSE: [Your full, caring response to me.]\n"
            "SUMMARY: [The new one-sentence summary of my message.]"
            "\n\nUSER MESSAGE: "
            f"'{user_message}'"
            "\n\nAI:"
        )

        response = await model.generate_content_async(prompt)
        
        response_text = response.text
        # Defensive parsing to prevent crashes if the AI doesn't follow the format
        if "RESPONSE:" in response_text and "SUMMARY:" in response_text:
            response_part = response_text.split("RESPONSE:")[1].split("SUMMARY:")[0].strip()
            summary_part = response_text.split("SUMMARY:")[1].strip()
            db.update({'last_summary': summary_part}, User.id == user_id)
            await update.message.reply_text(response_part)
        else:
            # If AI messes up the format, just send the whole text and log a warning
            logger.warning(f"AI response for user {user_id} did not match expected format. Raw text: {response_text}")
            await update.message.reply_text(response_text)

    except Exception as e:
        logger.error(f"Error in chat_handler for user {user_id}: {e}\nResponse text was: {response.text if 'response' in locals() else 'N/A'}")
        await update.message.reply_text("I'm sorry, my love, I'm feeling a little overwhelmed right now. Can we talk again in a moment? 😔")

# --- MAIN APPLICATION SETUP ---
async def post_init(application: Application) -> None:
    logger.info("Bot initialized. Starting scheduler.")
    scheduler.start()

async def post_shutdown(application: Application) -> None:
    logger.info("Bot is shutting down. Shutting down scheduler.")
    db.close()

def main() -> None:
    logger.info("Bot is starting up...")

    if not TELEGRAM_TOKEN or not model:
        logger.critical("FATAL: Telegram Token or Gemini Model not configured. Bot cannot start.")
        return

    server_thread = threading.Thread(target=run_dummy_server)
    server_thread.daemon = True
    server_thread.start()
    
    persistence = PicklePersistence(filepath="bot_persistence.pickle")
    
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).post_init(post_init).post_shutdown(post_shutdown).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("subscribe", subscribe_command)],
        states={
            TIMEZONE_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_timezone_and_schedule)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        persistent=True,
        name="subscription_handler"
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    logger.info("Application configured. Starting to poll for updates.")
    application.run_polling()


if __name__ == "__main__":
    main()
