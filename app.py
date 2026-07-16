"""
bot.py — Main entry point for the Telegram AI Assistant Bot
Multi-user, persistent, NLP-powered with Gemini AI
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

from telegram import Update, BotCommand
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from database import (
    init_db, get_or_create_user, get_chat_history,
    save_chat_message, get_user_memories, upsert_memory
)
from gemini_handler import detect_intent, chat_with_memory, extract_memories
from handlers.reminder import handle_reminder, load_pending_reminders
from handlers.image_handler import (
    handle_photo_upload, handle_image_search,
    handle_list_images, handle_image_name_reply
)
from handlers.notes_handler import (
    handle_save_note, handle_list_notes,
    handle_note_callback, handle_password_input
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


# ─────────────────────────────────────────────────────────────
# /start command
# ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    name = user.first_name or "there"
    await update.message.reply_text(
        f"👋 Hey {name}! I'm your personal AI assistant.\n\n"
        "Here's what I can do:\n"
        "⏰ *Reminders* — 'Remind me to call mom at 3 PM'\n"
        "📸 *Images* — Send a photo and I'll name & tag it\n"
        "🔍 *Image Search* — 'Find my morning selfie'\n"
        "📝 *Notes* — 'Save a note: Buy groceries'\n"
        "🔐 *Secret Notes* — 'Save a secret note with password 1234'\n"
        "💬 *Chat* — Just talk to me!\n\n"
        "I remember things about you, so the more we talk, the better I get! 🧠",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────
# /help command
# ─────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Commands & Examples:*\n\n"
        "*Reminders:*\n"
        "• 'Remind me at 6 PM to take medicine'\n"
        "• 'Set alarm for tomorrow 9 AM — meeting with boss'\n\n"
        "*Images:*\n"
        "• Send any photo → I'll ask for a name\n"
        "• 'Find my birthday party photo'\n"
        "• 'Show images from last week'\n"
        "• 'List my images'\n\n"
        "*Notes:*\n"
        "• 'Save a note: Meeting agenda ...'\n"
        "• 'Save a secret note with password mypass123: Bank PIN is ...'\n"
        "• 'Show my notes'\n"
        "• 'Delete note 5'\n\n"
        "*Chat:*\n"
        "• Anything! I'm here to chat 😊\n\n"
        "/start — Introduction\n"
        "/notes — View your notes\n"
        "/images — List saved images\n"
        "/memory — See what I remember about you\n"
        "/forget — Clear my memory about you",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────────────────────
# /memory command
# ─────────────────────────────────────────────────────────────

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_or_create_user(telegram_id=user.id)
    memories = get_user_memories(db_user.id)

    if not memories:
        await update.message.reply_text("🧠 I don't have any memories about you yet. Let's chat!")
        return

    lines = ["🧠 *What I remember about you:*\n"]
    for k, v in memories.items():
        lines.append(f"• *{k}*: {v}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# /forget command
# ─────────────────────────────────────────────────────────────

async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from database import Session, Memory
    user = update.effective_user
    db_user = get_or_create_user(telegram_id=user.id)
    session = Session()
    session.query(Memory).filter_by(user_id=db_user.id).delete()
    session.commit()
    await update.message.reply_text("🗑️ Done! I've cleared all memories about you. Fresh start! 🌱")


# ─────────────────────────────────────────────────────────────
# /notes command shortcut
# ─────────────────────────────────────────────────────────────

async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_or_create_user(telegram_id=user.id, username=user.username, first_name=user.first_name)
    await handle_list_notes(update, db_user)


# ─────────────────────────────────────────────────────────────
# /images command shortcut
# ─────────────────────────────────────────────────────────────

async def cmd_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_or_create_user(telegram_id=user.id, username=user.username, first_name=user.first_name)
    await handle_list_images(update, db_user)


# ─────────────────────────────────────────────────────────────
# Main message handler (NLP dispatcher)
# ─────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""

    # Register / fetch user
    db_user = get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )

    # 1. Check if user is unlocking a note (password flow)
    if await handle_password_input(update, db_user):
        return

    # 2. Check if user is naming a pending image
    if await handle_image_name_reply(update, db_user):
        return

    # 3. NLP intent detection
    history = get_chat_history(db_user.id, limit=20)
    intent = detect_intent(text, history)
    intent_type = intent.get("intent", "chat")

    logger.info(f"User {user.id} intent: {intent_type}")

    # ── Reminder ──────────────────────────────────────────
    if intent_type == "reminder":
        save_chat_message(db_user.id, "user", text)
        reply = await handle_reminder(update, context, intent, db_user)
        save_chat_message(db_user.id, "model", reply)
        await update.message.reply_text(reply, parse_mode="Markdown")
        return

    # ── Save Note ─────────────────────────────────────────
    if intent_type == "save_note":
        save_chat_message(db_user.id, "user", text)
        reply = await handle_save_note(update, db_user, intent)
        save_chat_message(db_user.id, "model", reply)
        await update.message.reply_text(reply, parse_mode="Markdown")
        return

    # ── List Notes ────────────────────────────────────────
    if intent_type == "read_note":
        save_chat_message(db_user.id, "user", text)
        await handle_list_notes(update, db_user)
        return

    # ── Delete Note ───────────────────────────────────────
    if intent_type == "delete_note":
        note_id = intent.get("note_id")
        if note_id:
            from database import get_note_by_id, delete_note
            note = get_note_by_id(int(note_id), db_user.id)
            if note:
                if note.is_protected:
                    from handlers.notes_handler import UNLOCK_STATE
                    UNLOCK_STATE[db_user.telegram_id] = f"delete:{note.id}"
                    await update.message.reply_text(
                        f"🔐 Note *\"{note.title}\"* is password protected.\nSend the password to delete it:",
                        parse_mode="Markdown"
                    )
                else:
                    delete_note(note.id, db_user.id)
                    await update.message.reply_text(f"🗑️ Note *\"{note.title}\"* deleted!", parse_mode="Markdown")
            else:
                await update.message.reply_text("⚠️ Note not found.")
        else:
            await handle_list_notes(update, db_user)
        return

    # ── Search Image ──────────────────────────────────────
    if intent_type == "search_image":
        save_chat_message(db_user.id, "user", text)
        query = intent.get("image_query") or text
        await handle_image_search(update, query, db_user)
        return

    # ── List Images ───────────────────────────────────────
    if intent_type == "list_images":
        save_chat_message(db_user.id, "user", text)
        await handle_list_images(update, db_user)
        return

    # ── Casual Chat ───────────────────────────────────────
    memories = get_user_memories(db_user.id)
    save_chat_message(db_user.id, "user", text)

    # Send typing indicator
    await update.message.chat.send_action("typing")

    reply = chat_with_memory(
        user_message=text,
        history=history,
        memories=memories,
        user_name=user.first_name or "there"
    )
    save_chat_message(db_user.id, "model", reply)
    await update.message.reply_text(reply)

    # Background memory extraction (non-blocking pattern via simple call)
    new_memories = extract_memories(text, reply)
    for k, v in new_memories.items():
        upsert_memory(db_user.id, k, v)


# ─────────────────────────────────────────────────────────────
# Photo handler
# ─────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_or_create_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    await handle_photo_upload(update, context, db_user)


# ─────────────────────────────────────────────────────────────
# Callback query handler (note buttons)
# ─────────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_or_create_user(telegram_id=user.id)
    await handle_note_callback(update, context, db_user)


# ─────────────────────────────────────────────────────────────
# Error handler
# ─────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env!")
    if not os.getenv("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY is not set in .env!")

    # Initialize database
    init_db()
    logger.info("Database initialized.")

    # Build application with job queue enabled
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    # Load pending reminders from DB into job queue
    load_pending_reminders(application.job_queue)

    # Register commands
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("memory", cmd_memory))
    application.add_handler(CommandHandler("forget", cmd_forget))
    application.add_handler(CommandHandler("notes", cmd_notes))
    application.add_handler(CommandHandler("images", cmd_images))

    # Register message handlers
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Callback query (inline buttons)
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Error handler
    application.add_error_handler(error_handler)

    logger.info("🤖 Bot is starting...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
