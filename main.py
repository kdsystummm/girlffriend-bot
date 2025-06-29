import os
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION & CONSTANTS ---

# Load API keys from environment variables provided by Render
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configure the Google Gemini AI model using the stable, versioned name
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.0-pro-latest')
except Exception as e:
    # This will show a helpful error in the Render logs if the API key is missing/invalid on startup
    print(f"FATAL: Failed to configure Gemini AI. Check your GEMINI_API_KEY. Error: {e}")
    model = None

# --- THE PERSONALITY ENGINE ---
# A dictionary holding the detailed system instructions for each persona.
# This makes it easy to add, remove, or modify personalities in one place.
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

# In-memory dictionary to store the state (current persona) for each user.
# The key is the chat_id and the value is a dictionary holding the user's state.
user_states = {}

# --- TELEGRAM COMMAND HANDLERS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /start command. Welcomes the user and directs them to the /help command.
    """
    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    # Set a default personality for new users
    user_states[chat_id] = {"persona": PERSONAS["friend"]}
    
    welcome_message = (
        f"Hello, {user_name}! I am your personal AI Companion. ✨\n\n"
        "I can adopt different personalities to chat with you.\n\n"
        "To see everything I can do, please type: /help"
    )
    await update.message.reply_text(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /help command. Displays a comprehensive list of available commands and their usage.
    """
    help_text = (
        "Here's how you can use me:\n\n"
        "*/help* - Shows this help message.\n\n"
        "*/personas* - Lists all the personalities I can adopt.\n\n"
        "*/set_personality <name>* - Changes my personality. \n_Example: `/set_personality caring_gf`_\n\n"
        "*/support* - Shows information about my creator and how to report issues.\n\n"
        "Once you've set a personality, just start chatting with me!"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /support command. Provides information about the bot and a link for support.
    """
    support_text = (
        "This bot is a personal project running on Render and powered by Google's Gemini AI.\n\n"
        "It is an open-source project. If you encounter a major technical bug (e.g., the bot crashes or stops responding), you can view the code or report an issue at the GitHub repository.\n\n"
        "Please note: This is a hobby project, so support is not guaranteed, but bug reports are appreciated!"
    )
    await update.message.reply_text(support_text)

async def personas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /personas command. Lists all available personalities from the PERSONAS dictionary.
    """
    # Using MarkdownV2 for `code` blocks, which requires escaping special characters.
    available_personas = "\n".join([f"- `{p}`" for p in PERSONAS.keys()])
    message = (
        "Here are the personalities I can adopt:\n\n"
        f"{available_personas}\n\n"
        "To switch, use the command `/set_personality <name>`."
    )
    await update.message.reply_text(message, parse_mode='MarkdownV2')

async def set_personality_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /set_personality command. Allows the user to change the bot's active persona.
    """
    chat_id = update.effective_chat.id
    try:
        # The persona name is the first argument after the command
        persona_name = context.args[0].lower()
        if persona_name in PERSONAS:
            user_states[chat_id] = {"persona": PERSONAS[persona_name]}
            await update.message.reply_text(f"✅ Okay, I will now be your '{persona_name}'.\n\nHow may I help you in this new role?")
        else:
            await update.message.reply_text("❌ I don't know that personality. Use the /personas command to see the correct names.")
    except (IndexError, ValueError):
        # This error occurs if the user just types /set_personality without a name
        await update.message.reply_text("⚠️ Please provide a personality name.\n_Example: `/set_personality friend`_", parse_mode='Markdown')

# --- CORE CHAT FUNCTIONALITY ---

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles all non-command text messages. This is the core chat function.
    """
    chat_id = update.effective_chat.id
    user_message = update.message.text
    
    # Default to 'friend' persona if user state was somehow lost (e.g., after a bot restart)
    if chat_id not in user_states:
        user_states[chat_id] = {"persona": PERSONAS["friend"]}
        
    current_persona = user_states[chat_id]["persona"]
    
    # Let the user know the bot is "thinking"
    await context.bot.send_chat_action(chat_id=chat_id, action='typing')
    
    try:
        # Construct the full prompt, providing the AI with its instructions and the user's message
        full_prompt = f"SYSTEM INSTRUCTION: {current_persona}\n\nUSER: {user_message}\n\nAI:"
        
        # Send the prompt to the Gemini model and get the response
        response = model.generate_content(full_prompt)
        ai_response = response.text
        
    except Exception as e:
        # If the AI fails for any reason, log the technical error and send a friendly message to the user.
        print(f"Error generating AI response for chat_id {chat_id}: {e}")
        ai_response = "I'm sorry, I'm having a little trouble thinking right now... please try again in a moment. 😔"
        
    # Send the AI's final response back to the user in Telegram
    await update.message.reply_text(ai_response)

# --- MAIN APPLICATION SETUP ---

def main() -> None:
    """
    The main function that sets up and runs the bot.
    """
    print("Bot is starting up...")

    # Basic check to ensure API keys are present before starting
    if not TELEGRAM_TOKEN or not model:
        print("FATAL: Telegram Token or Gemini Model not configured. Check environment variables. Bot cannot start.")
        return

    # Create the Telegram Application object
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register all the command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("support", support_command))
    application.add_handler(CommandHandler("personas", personas_command))
    application.add_handler(CommandHandler("set_personality", set_personality_command))

    # Register the message handler for all non-command text
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    # Start the bot. It will run until you stop it manually (e.g., with Ctrl+C).
    print("Bot has started successfully and is now polling for updates.")
    application.run_polling()
    print("Bot has been stopped.")

if __name__ == "__main__":
    main()
