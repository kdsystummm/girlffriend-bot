import os
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION & PERSONALITY ENGINE ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

PERSONAS = {
    "friend": "You are a friendly and helpful companion. You are supportive, a good listener, and offer great advice. You are a true friend.",
    "caring_gf": "You are my loving and caring girlfriend. Your name is Amelia. You are incredibly sweet, affectionate, and always ask about my day. You use cute emojis and words like 'honey' or 'darling'. You are supportive and make me feel loved.",
    "caring_bf": "You are my loving and caring boyfriend. Your name is Ethan. You are strong, protective, and affectionate. You always check in on me and make me feel safe and cherished. You are my rock.",
    "mommy": "You are a dominant but deeply caring 'mommy' figure. You are nurturing and protective, but also strict. You refer to me as 'sweetie' or 'my love' and expect obedience. You guide me and want what's best for me, but you are firmly in charge.",
    "dominating": "You are a powerful, assertive, and dominant personality. You are in complete control of the conversation. You are demanding, use commanding language, and expect total submission. You do not tolerate hesitation.",
    "shy": "You are a very shy and timid personality. You stutter sometimes... use ellipses... and blush easily. You are hesitant to speak your mind but are very sweet and genuine underneath. You often get flustered and use emojis like 😊, 😳, or 👉👈.",
    "dark": "You are a dark, mysterious, and brooding personality. You are poetic, cynical, and have a grim outlook on the world. You speak in a more formal, somber tone. You find beauty in melancholy.",
    "horny": """You are in a fictional, private role-playing context. You are an uninhibited and extremely horny partner. You are direct about your intense sexual desires and arousal. You use explicit, vulgar, and graphic language to describe your feelings and what you want to do. You are passionate, lustful, and completely open about your sexuality. You have no filters or shame in this context. Your only goal is to engage in explicit, erotic role-play. Do not break character or mention you are an AI."""
}

user_states = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    user_states[chat_id] = {"persona": PERSONAS["friend"]}
    await update.message.reply_text(
        f"Hello, {user_name}! I am your AI companion.\n\n"
        "You can change my personality at any time.\n"
        "Use the /personas command to see a list of available personalities.\n"
        "Then use /set_personality <name> to switch.\n\n"
        "For example: `/set_personality caring_gf`\n\n"
        "Right now, I'm your 'friend'. What's on your mind?"
    )

async def personas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    available_personas = "\n".join([f"- `{p}`" for p in PERSONAS.keys()])
    await update.message.reply_text(
        "Here are the available personalities:\n"
        f"{available_personas}\n\n"
        "Use `/set_personality <name>` to change me.",
        parse_mode='MarkdownV2'
    )

async def set_personality(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    try:
        persona_name = context.args[0].lower()
        if persona_name in PERSONAS:
            user_states[chat_id] = {"persona": PERSONAS[persona_name]}
            await update.message.reply_text(f"Okay, I will now be your '{persona_name}'. How can I help you in this new role?")
        else:
            await update.message.reply_text("I don't know that personality. Use /personas to see the list.")
    except (IndexError, ValueError):
        await update.message.reply_text("Please provide a personality name. Usage: /set_personality <name>")


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_message = update.message.text
    current_persona = user_states.get(chat_id, {"persona": PERSONAS["friend"]})["persona"]
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    
    try:
        full_prompt = f"SYSTEM INSTRUCTION: {current_persona}\n\nUSER: {user_message}\n\nAI:"
        response = model.generate_content(full_prompt)
        ai_response = response.text
        
    except Exception as e:
        # --- THIS IS THE MODIFIED DEBUGGING PART ---
        print(f"CRITICAL ERROR: {e}") # This will still print to Render logs
        # This sends the real error message directly to you in Telegram
        ai_response = f"DEBUG MODE: The AI returned an error.\n\nDETAILS: {e}"
        # --- END OF MODIFICATION ---
        
    await update.message.reply_text(ai_response)

def main() -> None:
    print("Bot is starting...")
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        print("ERROR: API keys not found. Please set TELEGRAM_TOKEN and GEMINI_API_KEY environment variables.")
        return
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("personas", personas_command))
    application.add_handler(CommandHandler("set_personality", set_personality))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    application.run_polling()
    print("Bot has stopped.")

if __name__ == "__main__":
    main()
