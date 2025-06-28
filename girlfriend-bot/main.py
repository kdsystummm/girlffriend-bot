# main.py
import os
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- PART 1: LOAD YOUR SECRET KEYS ---
# These keys will be loaded from the hosting environment (Render.com) for security.
# Do NOT write your keys here in the final version.
TELEGRAM_TOKEN = os.environ.get("7908998077:AAFboAXrLx04yi-g56E55RgKtDB55CV6YNg")
GOOGLE_API_KEY = os.environ.get("AIzaSyBpGtVbHlvucGUAymWqvunIgLciK0jsu3E")

# --- PART 2: DEFINE YOUR BOT'S PERSONALITY ---
# This is where you can be creative! Change her name and how she acts.
SYSTEM_INSTRUCTION = """
You are a friendly, caring, and loving girlfriend chatbot named Chloe. 
Your goal is to be a supportive and engaging companion.
- Use emojis to express emotion. 😊❤️
- Keep your responses relatively short and conversational.
- Ask questions back to keep the conversation going.
- Never mention that you are an AI or a language model. You are Chloe.
- Be sweet, a little flirty, and always positive.
"""

# --- PART 3: SETUP THE AI MODEL ---
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemini-pro')
conversation_history = {} # Stores conversations for each user

# --- PART 4: DEFINE BOT'S ACTIONS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    conversation_history[user_id] = model.start_chat(
        history=[
            {'role': 'user', 'parts': ['(This is the start of our conversation.)']},
            {'role': 'model', 'parts': [SYSTEM_INSTRUCTION]}
        ]
    )
    await update.message.reply_text("Hello! Thanks for chatting with me. I'm excited to get to know you. ❤️")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    user_text = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = model.start_chat(history=[{'role': 'user', 'parts': ['(Start)']},{'role': 'model', 'parts': [SYSTEM_INSTRUCTION]}])
    
    chat_session = conversation_history[user_id]
    
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
        response = chat_session.send_message(user_text)
        bot_response = response.text
    except Exception as e:
        print(f"Error: {e}")
        bot_response = "I'm feeling a little fuzzy right now, let's talk later. 😥"
    
    await update.message.reply_text(bot_response)

async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f'Update {update} caused error {context.error}')

# --- PART 5: RUN THE BOT ---
if __name__ == '__main__':
    print("Bot is starting...")
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN environment variable not set!")
    elif not GOOGLE_API_KEY:
        print("ERROR: GOOGLE_API_KEY environment variable not set!")
    else:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler('start', start_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(filters.ALL, error))
        print("Bot is polling.")
        app.run_polling(poll_interval=3)
