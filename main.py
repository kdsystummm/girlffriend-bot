# --- IMPORTS ---
import os
import threading
import logging
import http.server
import socketserver
import psutil
from datetime import time, datetime

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
from functools import wraps

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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot_startup_time = datetime.utcnow()
db = TinyDB('user_data.json')
User = Query()
jobstores = {'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')}
scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")

TIMEZONE_PROMPT, BROADCAST_MESSAGE, BROADCAST_CONFIRM = range(3)

# --- AI & PERSONA ENGINE ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
except Exception as e:
    logger.critical(f"FATAL: Failed to configure Gemini AI: {e}")
    model = None

# --- ADMIN DECORATOR ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("Sorry, this is a restricted admin-only command.")
            logger.warning(f"Unauthorized access attempt to {func.__name__} by user {user_id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- DUMMY WEB SERVER FOR RENDER ---
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_dummy_server():
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.allow_reuse_address = True
        logger.info(f"Dummy server started on port {PORT}")
        httpd.serve_forever()

# --- SCHEDULED MESSAGE JOBS ---
async def send_scheduled_message(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.data['user_id']
    prompt = job.data['prompt']
    
    user_record = db.get(User.id == user_id)
    if not user_record or not user_record.get('subscribed'):
        logger.warning(f"Skipping scheduled message for unsubscribed user {user_id}")
        return

    last_summary = user_record.get('last_summary', 'we haven\'t talked in a while')

    try:
        full_prompt = f"SYSTEM INSTRUCTION: You are my loving partner, Alex. Your task is to send me a warm, caring message. The reason for the message is: '{prompt}'. My last conversation with you was about: '{last_summary}'. Craft a short, heartfelt message based on this, and end with a loving question.\n\nAI:"
        response = await model.generate_content_async(full_prompt)
        await context.bot.send_message(chat_id=user_id, text=response.text)
        logger.info(f"Sent scheduled '{prompt}' message to user {user_id}")
    except Exception as e:
        logger.error(f"Failed to send scheduled message to {user_id}: {e}")

# --- USER COMMAND HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db.upsert({'id': user.id, 'first_name': user.first_name, 'subscribed': False}, User.id == user.id)
    await update.message.reply_text(
        f"Hello, {user.first_name}! I am your personal AI Companion. ❤️\n\n"
        "To see all my features, please use the /help command."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "Here's everything I can do:\n\n"
        "*/help* - Shows this message.\n\n"
        "*/subscribe* - Turn on daily messages. I'll ask for your timezone!\n\n"
        "*/unsubscribe* - Turn off all daily messages.\n\n"
        "*/status* - Check your subscription status.\n\n"
        "If you are the bot admin, you can use `/admin_help` to see special commands."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_record = db.get(User.id == user_id)
    if user_record and user_record.get('subscribed'):
        tz = user_record.get('timezone', 'Not set')
        await update.message.reply_text(f"You are currently SUBSCRIBED to daily messages.\nYour timezone is set to: `{tz}`", parse_mode='MarkdownV2')
    else:
        await update.message.reply_text("You are currently NOT SUBSCRIBED to daily messages.")

# --- SUBSCRIPTION CONVERSATION HANDLER ---
async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if db.get(User.id == user_id).get('subscribed', False):
        await update.message.reply_text("You are already subscribed! Use /unsubscribe first if you wish to change your timezone.")
        return ConversationHandler.END
    await update.message.reply_text(
        "I'd love to send you messages! ❤️ To do it at the right time, I need your timezone.\n\n"
        "Please tell me your timezone in `Continent/City` format (e.g., `America/New_York` or `Asia/Kolkata`)."
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
    scheduler.add_job(send_scheduled_message, 'cron', hour=8, minute=30, timezone=user_tz, id=f'morning_{user_id}', data={**job_data, 'prompt': "Good Morning"})
    scheduler.add_job(send_scheduled_message, 'cron', hour=14, minute=0, timezone=user_tz, id=f'afternoon_{user_id}', data={**job_data, 'prompt': "Thinking of you this afternoon"})
    scheduler.add_job(send_scheduled_message, 'cron', hour=20, minute=0, timezone=user_tz, id=f'evening_{user_id}', data={**job_data, 'prompt': "Missing You"})
    db.update({'subscribed': True}, User.id == user_id)

    await update.message.reply_text(f"Perfect! I've set your timezone to {tz_name} and subscribed you to daily messages. Talk to you soon! 🥰")
    logger.info(f"User {user_id} subscribed with timezone {tz_name}")
    return ConversationHandler.END

async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not db.get(User.id == user_id).get('subscribed', False):
        await update.message.reply_text("You aren't subscribed to any messages right now.")
        return

    job_ids = [f'morning_{user_id}', f'afternoon_{user_id}', f'evening_{user_id}']
    for job_id in job_ids:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            logger.info(f"Removed job: {job_id}")

    db.update({'subscribed': False}, User.id == user_id)
    await update.message.reply_text("You have been unsubscribed. I'll miss sending you messages, but I'm always here to chat!")
    logger.info(f"User {user_id} unsubscribed.")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Okay, I've cancelled the current operation.")
    return ConversationHandler.END

# --- ADMIN COMMANDS ---
@admin_only
async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "👑 *Admin Control Panel* 👑\n\n"
        "*/admin_status* - View the bot's live operational dashboard.\n\n"
        "*/admin_broadcast* - Start the process to send a message to all users.\n\n"
        "*/admin_user_info <user_id>* - Get the DB record for a specific user.\n\n"
        "*/admin_clear_summary <user_id>* - Reset a user's conversation summary."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

@admin_only
async def admin_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    process = psutil.Process(os.getpid())
    mem_usage = process.memory_info().rss / (1024 * 1024)
    uptime = datetime.utcnow() - bot_startup_time
    total_users = len(db)
    subscribed_users = len(db.search(User.subscribed == True))
    active_jobs = scheduler.get_jobs()
    job_list_str = "\n".join([f"\\- `{job.name}` (Next: {job.next_run_time.strftime('%Y-%m-%d %H:%M:%S %Z')})" for job in active_jobs]) if active_jobs else "None"
    status_report = (
        f"*--- Bot Status Dashboard ---*\n\n"
        f"*System*:\n"
        f"  \\- Uptime: `{str(uptime).split('.')[0]}`\n"
        f"  \\- Memory Usage: `{mem_usage:.2f} MB`\n\n"
        f"*Users*:\n"
        f"  \\- Total Users: `{total_users}`\n"
        f"  \\- Active Subscriptions: `{subscribed_users}`\n\n"
        f"*Scheduler*:\n"
        f"  \\- Active Jobs ({len(active_jobs)}):\n"
        f"{job_list_str}"
    )
    await update.message.reply_text(status_report, parse_mode='MarkdownV2')

@admin_only
async def admin_user_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id_to_check = int(context.args[0])
        user_record = db.get(User.id == user_id_to_check)
        if not user_record:
            await update.message.reply_text(f"No data found for user ID: `{user_id_to_check}`", parse_mode='MarkdownV2')
            return
        info_text = f"*User Info for `{user_id_to_check}`*\n\n"
        for key, value in user_record.items():
            info_text += f"  \\- *{key}*: `{value}`\n"
        await update.message.reply_text(info_text, parse_mode='MarkdownV2')
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/admin_user_info <user_id>`")

@admin_only
async def admin_clear_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id_to_clear = int(context.args[0])
        if db.contains(User.id == user_id_to_clear):
            db.update({'last_summary': 'this is our first real conversation'}, User.id == user_id_to_clear)
            await update.message.reply_text(f"✅ Conversation summary for user `{user_id_to_clear}` has been reset.", parse_mode='MarkdownV2')
        else:
            await update.message.reply_text(f"User `{user_id_to_clear}` not found.", parse_mode='MarkdownV2')
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/admin_clear_summary <user_id>`")

# --- BROADCAST CONVERSATION HANDLER ---
@admin_only
async def admin_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    total_users = len(db)
    await update.message.reply_text(
        f"Entering broadcast mode. The message you send next will be prepared to be sent to all {total_users} users.\n\n"
        "Please send the message now, or type /cancel to abort."
    )
    return BROADCAST_MESSAGE

async def broadcast_get_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['broadcast_message'] = update.message.text
    total_users = len(db)
    # The warning fix is here. The 'r' makes it a raw string.
    await update.message.reply_text(
        f"The following message will be sent to {total_users} users:\n\n"
        f"--------------------\n{update.message.text}\n--------------------\n\n"
        r"To confirm and send, type `YES`. To abort, type /cancel."
    , parse_mode='MarkdownV2')
    return BROADCAST_CONFIRM

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.upper() != 'YES':
        await update.message.reply_text("Confirmation not received. Broadcast aborted.")
        context.user_data.clear()
        return ConversationHandler.END

    message_to_send = context.user_data['broadcast_message']
    all_users = db.all()
    sent_count, failed_count = 0, 0
    await update.message.reply_text(f"Confirmation received. Starting broadcast to {len(all_users)} users... Please wait.")
    for user in all_users:
        try:
            await context.bot.send_message(chat_id=user['id'], text=message_to_send)
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to user {user['id']}: {e}")
            failed_count += 1
    await update.message.reply_text(f"Broadcast complete.\n\nSuccessfully sent: {sent_count}\nFailed to send: {failed_count}")
    context.user_data.clear()
    return ConversationHandler.END

# --- CORE CHAT HANDLER ---
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_message = update.message.text
    await context.bot.send_chat_action(chat_id=user_id, action='typing')
    try:
        user_record = db.get(User.id == user_id)
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
        response_part = response_text.split("RESPONSE:")[1].split("SUMMARY:")[0].strip()
        summary_part = response_text.split("SUMMARY:")[1].strip()
        db.update({'last_summary': summary_part}, User.id == user_id)
        await update.message.reply_text(response_part)
    except Exception as e:
        logger.error(f"Error in chat_handler for user {user_id}: {e}\nResponse text was: {response.text if 'response' in locals() else 'N/A'}")
        await update.message.reply_text("I'm sorry, my love, I'm feeling a little overwhelmed right now. Can we talk again in a moment? 😔")

# --- MAIN APPLICATION SETUP ---
async def post_init(application: Application) -> None:
    logger.info("Bot initialized. Starting scheduler.")
    scheduler.start()

async def post_shutdown(application: Application) -> None:
    logger.info("Bot is shutting down.")
    scheduler.shutdown()
    db.close()

def main() -> None:
    logger.info("Bot is starting up...")
    if not TELEGRAM_TOKEN or not model or ADMIN_ID == 0:
        logger.critical("FATAL: Required environment variables are not set. Bot cannot start.")
        return

    server_thread = threading.Thread(target=run_dummy_server)
    server_thread.daemon = True
    server_thread.start()
    
    persistence = PicklePersistence(filepath="bot_persistence.pickle")
    
    application = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).post_init(post_init).post_shutdown(post_shutdown).build()

    subscribe_conv = ConversationHandler(
        entry_points=[CommandHandler("subscribe", subscribe_command)],
        states={TIMEZONE_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_timezone_and_schedule)]},
        fallbacks=[CommandHandler("cancel", cancel_command)],
        persistent=True, name="subscribe_conv"
    )
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("admin_broadcast", admin_broadcast_command)],
        states={
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_get_message)],
            BROADCAST_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        persistent=True, name="broadcast_conv"
    )

    application.add_handler(subscribe_conv)
    application.add_handler(broadcast_conv)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("admin_help", admin_help_command))
    application.add_handler(CommandHandler("admin_status", admin_status_command))
    application.add_handler(CommandHandler("admin_user_info", admin_user_info_command))
    application.add_handler(CommandHandler("admin_clear_summary", admin_clear_summary_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    logger.info("Application configured. Starting to poll for updates.")
    application.run_polling()

if __name__ == "__main__":
    main()
