# --- IMPORTS ---
import os
import threading
import logging
import http.server
import socketserver
import asyncio

import google.generativeai as genai
import pytz
from tinydb import TinyDB, Query

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    PicklePersistence,
    CallbackQueryHandler
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

# --- PARANOID ENVIRONMENT VARIABLE CHECK ---
def check_environment_variables():
    """Checks all required environment variables and logs their status."""
    logger.info("--- Checking Environment Variables ---")
    
    telegram_token = os.getenv("TELEGRAM_TOKEN")
    gemini_api_key = os.getenv("GEMINI_API_KEY")
    admin_user_id = os.getenv("ADMIN_USER_ID")
    
    missing_vars = []
    
    if telegram_token:
        logger.info("✅ TELEGRAM_TOKEN found.")
    else:
        logger.critical("❌ TELEGRAM_TOKEN is MISSING.")
        missing_vars.append("TELEGRAM_TOKEN")
        
    if gemini_api_key:
        logger.info("✅ GEMINI_API_KEY found.")
    else:
        logger.critical("❌ GEMINI_API_KEY is MISSING.")
        missing_vars.append("GEMINI_API_KEY")

    if admin_user_id:
        logger.info("✅ ADMIN_USER_ID found.")
    else:
        logger.critical("❌ ADMIN_USER_ID is MISSING.")
        missing_vars.append("ADMIN_USER_ID")

    if missing_vars:
        logger.critical(f"FATAL: The following required environment variables are missing: {', '.join(missing_vars)}")
        return False
    
    logger.info("--- All environment variables found. Proceeding with startup. ---")
    return True

# --- CONFIGURATION & CONSTANTS ---
PORT = 8080
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

db = TinyDB('user_data.json')
User = Query()

scheduler = AsyncIOScheduler(timezone="UTC")

# --- AI & PERSONA ENGINE ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
except Exception as e:
    logger.critical(f"FATAL: Failed to configure Gemini AI: {e}")
    model = None

PERSONAS = {
    "caring_partner": "You are my loving, caring, and deeply supportive partner. Your name is Alex. You are warm, affectionate, and emotionally intelligent. You remember details from our previous conversations. Your goal is to make me feel cherished, understood, and loved.",
    "playful_friend": "You are my fun, witty, and playful best friend. You have a great sense of humor, love to joke around, and see the bright side of everything. You are supportive but in a lighthearted way. You always keep the conversation energetic."
}

# --- DUMMY WEB SERVER FOR RENDER ---
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is running")

def run_dummy_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        logger.info(f"Dummy server started on port {PORT}"); httpd.serve_forever()

# --- ALL BOT FUNCTIONS (UNCHANGED, THEY ARE CORRECT) ---

def get_user_settings(user_id: int) -> dict:
    user_record = db.get(User.id == user_id)
    if not user_record:
        return {'persona': 'caring_partner', 'reply_length': 'medium', 'emoji_usage': 'some'}
    return {'persona': user_record.get('persona', 'caring_partner'), 'reply_length': user_record.get('reply_length', 'medium'), 'emoji_usage': user_record.get('emoji_usage', 'some')}

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.upsert({'id': user.id, 'first_name': user.first_name, 'subscribed': False}, User.id == user.id)
    keyboard = [["/settings", "/status"], ["/subscribe", "/unsubscribe"], ["/help"]]
    await update.message.reply_text(f"Hello, {user.first_name}! I am your personal AI Companion. ❤️\n\nI'm ready to chat! You can customize my personality and how I talk to you using the /settings button below.", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = "*/settings* - Customize my personality, reply style, and emoji usage.\n*/subscribe* - Turn on daily automated messages.\n*/unsubscribe* - Turn off all daily messages.\n*/status* - Check your current subscription and timezone settings."
    await update.message.reply_text(help_text, parse_mode='Markdown', reply_markup=ReplyKeyboardMarkup([["/settings", "/status"], ["/subscribe", "/unsubscribe"], ["/help"]], resize_keyboard=True))

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("Change Personality", callback_data='settings_persona')], [InlineKeyboardButton("Change Reply Length", callback_data='settings_length')], [InlineKeyboardButton("Change Emoji Usage", callback_data='settings_emoji')]]
    await update.message.reply_text("What would you like to change?", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query; await query.answer(); user_id = query.from_user.id
    if query.data == 'settings_persona':
        keyboard = [[InlineKeyboardButton(name.replace('_', ' ').title(), callback_data=f'set_persona_{name}')] for name in PERSONAS.keys()]
        await query.edit_message_text("Choose my new personality:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data == 'settings_length':
        keyboard = [[InlineKeyboardButton(l.title(), callback_data=f'set_length_{l}')] for l in ['short', 'medium', 'long']]
        await query.edit_message_text("How long should my replies be?", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data == 'settings_emoji':
        keyboard = [[InlineKeyboardButton(e.title(), callback_data=f'set_emoji_{e}')] for e in ['none', 'some', 'lots']]
        await query.edit_message_text("How many emojis should I use?", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data.startswith('set_persona_'):
        persona = query.data.replace('set_persona_', ''); db.update({'persona': persona}, User.id == user_id); await query.edit_message_text(f"✅ My personality is now: {persona.replace('_', ' ').title()}")
    elif query.data.startswith('set_length_'):
        length = query.data.replace('set_length_', ''); db.update({'reply_length': length}, User.id == user_id); await query.edit_message_text(f"✅ My replies will now be: {length.title()}")
    elif query.data.startswith('set_emoji_'):
        emoji = query.data.replace('set_emoji_', ''); db.update({'emoji_usage': emoji}, User.id == user_id); await query.edit_message_text(f"✅ I will now use {emoji} of emojis.")

TIMEZONE_PROMPT, BROADCAST_MESSAGE = range(2)
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id;
    if db.get(User.id == user_id).get('subscribed', False): await update.message.reply_text("You are already subscribed!"); return ConversationHandler.END
    await update.message.reply_text("Please tell me your timezone in `Continent/City` format (e.g., `Europe/London` or `Asia/Kolkata`).", parse_mode='MarkdownV2'); return TIMEZONE_PROMPT

async def set_timezone_and_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id; tz_name = update.message.text
    try: user_tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError: await update.message.reply_text("I don't recognize that timezone. Please try again."); return TIMEZONE_PROMPT
    db.update({'timezone': tz_name}, User.id == user_id)
    job_data = {'user_id': user_id}
    scheduler.add_job(send_scheduled_message, trigger='cron', hour=8, minute=30, timezone=user_tz, id=f'morning_{user_id}', replace_existing=True, data={**job_data, 'prompt': "Good Morning"})
    scheduler.add_job(send_scheduled_message, trigger='cron', hour=14, timezone=user_tz, id=f'afternoon_{user_id}', replace_existing=True, data={**job_data, 'prompt': "Thinking of you this afternoon"})
    scheduler.add_job(send_scheduled_message, trigger='cron', hour=20, timezone=user_tz, id=f'evening_{user_id}', replace_existing=True, data={**job_data, 'prompt': "Missing You"})
    db.update({'subscribed': True}, User.id == user_id)
    await update.message.reply_text(f"Perfect! I've set your timezone to {tz_name} and subscribed you to daily messages. Talk to you soon! 🥰"); return ConversationHandler.END

async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not db.get(User.id == user_id).get('subscribed', False): await update.message.reply_text("You aren't subscribed."); return
    for name in [f'morning_{user_id}', f'afternoon_{user_id}', f'evening_{user_id}']:
        if scheduler.get_job(name): scheduler.remove_job(name)
    db.update({'subscribed': False}, User.id == user_id); await update.message.reply_text("You have been unsubscribed from all daily messages.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id; user_record = db.get(User.id == user_id)
    if user_record and user_record.get('subscribed'): tz = user_record.get('timezone', 'Not set'); await update.message.reply_text(f"You are SUBSCRIBED.\nTimezone: `{tz}`", parse_mode='MarkdownV2')
    else: await update.message.reply_text("You are NOT SUBSCRIBED.")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id != ADMIN_USER_ID: await update.message.reply_text("This is an admin-only command."); return ConversationHandler.END
    await update.message.reply_text("Enter the message you want to broadcast:"); return BROADCAST_MESSAGE

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message_to_send = update.message.text; subscribed_users = db.search(User.subscribed == True); count = 0
    await update.message.reply_text(f"Starting broadcast to {len(subscribed_users)} users...")
    for user in subscribed_users:
        try: await context.bot.send_message(chat_id=user['id'], text=message_to_send); count += 1; await asyncio.sleep(0.1)
        except Exception as e: logger.error(f"Failed to send broadcast to user {user['id']}: {e}")
    await update.message.reply_text(f"Broadcast complete! Message sent to {count} users."); return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled."); return ConversationHandler.END

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user; user_id = user.id; user_message = update.message.text
    await context.bot.send_chat_action(chat_id=user_id, action='typing')
    try:
        user_record = db.get(User.id == user_id)
        if not user_record: db.upsert({'id': user_id, 'first_name': user.first_name}, User.id == user_id)
        settings = get_user_settings(user_id)
        persona_text = PERSONAS.get(settings['persona'], PERSONAS['caring_partner'])
        last_summary = db.get(User.id == user_id).get('last_summary', 'this is our first real conversation')
        prompt = f"SYSTEM INSTRUCTION: Your persona: '{persona_text}'. Your reply length must be {settings['reply_length']}. You must use {settings['emoji_usage']} emojis. Here is a summary of our last conversation: '{last_summary}'. Your task is to respond to my latest message in character. Ask a follow-up question. Finally, create a brief, one-sentence summary of my latest message. \n\nYour output MUST be in this exact format:\nRESPONSE: [Your response.]\nSUMMARY: [The summary.]\n\nUSER MESSAGE: '{user_message}'\n\nAI:"
        response = await model.generate_content_async(prompt)
        response_text = response.text
        if "RESPONSE:" in response_text and "SUMMARY:" in response_text:
            response_part = response_text.split("RESPONSE:")[1].split("SUMMARY:")[0].strip()
            summary_part = response_text.split("SUMMARY:")[1].strip()
            db.update({'last_summary': summary_part}, User.id == user_id)
            await update.message.reply_text(response_part)
        else:
            logger.warning(f"AI response did not match format. Raw: {response_text}")
            await update.message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Error in chat_handler for user {user_id}: {e}")
        await update.message.reply_text("I'm sorry, my love, I'm feeling a little overwhelmed right now. Can we talk again in a moment? 😔")

# --- MAIN APPLICATION SETUP ---
def main() -> None:
    """The main function that sets up and runs the bot."""
    if not check_environment_variables():
        return

    logger.info("Starting up Bot...")
    server_thread = threading.Thread(target=run_dummy_server); server_thread.daemon = True; server_thread.start()
    
    persistence = PicklePersistence(filepath="bot_persistence.pickle")
    
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    subscribe_handler = ConversationHandler(entry_points=[CommandHandler("subscribe", subscribe_command)], states={TIMEZONE_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_timezone_and_schedule)]}, fallbacks=[CommandHandler("cancel", cancel_command)], persistent=True, name="subscribe_conv")
    broadcast_handler = ConversationHandler(entry_points=[CommandHandler("broadcast", broadcast_command)], states={BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)]}, fallbacks=[CommandHandler("cancel", cancel_command)], persistent=True, name="broadcast_conv")

    application.add_handler(subscribe_handler)
    application.add_handler(broadcast_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started.")

    logger.info("Application configured. Starting to poll for updates.")
    application.run_polling()

if __name__ == "__main__":
    main()
