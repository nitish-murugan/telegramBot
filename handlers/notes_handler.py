"""
handlers/notes_handler.py — Note creation, retrieval, and deletion with optional password protection
"""
import hashlib
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import add_note, get_notes, get_note_by_id, delete_note

logger = logging.getLogger(__name__)

# Per-user unlock state: {telegram_id: note_id being unlocked}
UNLOCK_STATE = {}


def _hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


async def handle_save_note(update: Update, db_user, intent: dict) -> str:
    """Save a note, optionally with password protection."""
    title = intent.get("note_title") or "Untitled Note"
    content = intent.get("note_content") or update.message.text
    password = intent.get("note_password")
    is_protected = bool(intent.get("note_protected")) or bool(password)

    pw_hash = _hash_password(password) if password else None

    note = add_note(
        user_id=db_user.id,
        title=title,
        content=content,
        is_protected=is_protected,
        password_hash=pw_hash
    )

    lock_icon = "🔐" if is_protected else "📝"
    protection_note = " (password protected)" if is_protected else ""
    return (
        f"{lock_icon} Note saved!{protection_note}\n\n"
        f"*Title:* {note.title}\n"
        f"*ID:* `{note.id}`\n\n"
        f"You can read it with: _'show my notes'_"
    )


async def handle_list_notes(update: Update, db_user):
    """List all notes with inline buttons for locked ones."""
    notes = get_notes(db_user.id)

    if not notes:
        await update.message.reply_text("📂 You have no notes yet! Start by saying *'save a note: ...'*", parse_mode="Markdown")
        return

    text = "📋 *Your Notes:*\n\n"
    keyboard = []

    for note in notes:
        icon = "🔐" if note.is_protected else "📝"
        text += f"{icon} [{note.id}] *{note.title}* — {note.created_at.strftime('%b %d')}\n"
        keyboard.append([
            InlineKeyboardButton(
                f"{icon} Read: {note.title[:25]}",
                callback_data=f"read_note:{note.id}"
            ),
            InlineKeyboardButton("🗑️", callback_data=f"delete_note:{note.id}")
        ])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_note_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_user):
    """Handle inline button callbacks for reading/deleting notes."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("read_note:"):
        note_id = int(data.split(":")[1])
        note = get_note_by_id(note_id, db_user.id)
        if not note:
            await query.edit_message_text("⚠️ Note not found.")
            return

        if note.is_protected:
            UNLOCK_STATE[db_user.telegram_id] = note_id
            await query.message.reply_text(
                f"🔐 Note *\"{note.title}\"* is password protected.\nPlease send the password:",
                parse_mode="Markdown"
            )
        else:
            await _send_note(query.message, note)

    elif data.startswith("delete_note:"):
        note_id = int(data.split(":")[1])
        note = get_note_by_id(note_id, db_user.id)
        if not note:
            await query.edit_message_text("⚠️ Note not found.")
            return

        if note.is_protected:
            UNLOCK_STATE[db_user.telegram_id] = f"delete:{note_id}"
            await query.message.reply_text(
                f"🔐 Note *\"{note.title}\"* is password protected.\nSend the password to delete it:",
                parse_mode="Markdown"
            )
        else:
            delete_note(note_id, db_user.id)
            await query.edit_message_text(f"🗑️ Note *\"{note.title}\"* deleted.", parse_mode="Markdown")


async def handle_password_input(update: Update, db_user) -> bool:
    """
    Check if user is in unlock state and validate password.
    Returns True if handled, False otherwise.
    """
    if db_user.telegram_id not in UNLOCK_STATE:
        return False

    state = UNLOCK_STATE[db_user.telegram_id]
    pw_input = update.message.text.strip()
    pw_hash = _hash_password(pw_input)

    if isinstance(state, str) and state.startswith("delete:"):
        note_id = int(state.split(":")[1])
        note = get_note_by_id(note_id, db_user.id)
        if note and note.password_hash == pw_hash:
            delete_note(note_id, db_user.id)
            del UNLOCK_STATE[db_user.telegram_id]
            await update.message.reply_text(f"🗑️ Note *\"{note.title}\"* deleted.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Wrong password! Note not deleted.")
            del UNLOCK_STATE[db_user.telegram_id]
        return True

    # Reading a locked note
    note_id = int(state)
    note = get_note_by_id(note_id, db_user.id)
    if not note:
        del UNLOCK_STATE[db_user.telegram_id]
        return True

    if note.password_hash == pw_hash:
        del UNLOCK_STATE[db_user.telegram_id]
        await _send_note(update.message, note)
    else:
        await update.message.reply_text("❌ Wrong password! Access denied.")
        del UNLOCK_STATE[db_user.telegram_id]

    return True


async def _send_note(message, note):
    icon = "🔐" if note.is_protected else "📝"
    text = (
        f"{icon} *{note.title}*\n"
        f"📅 {note.created_at.strftime('%B %d, %Y %H:%M')} UTC\n"
        f"{'─' * 30}\n"
        f"{note.content}"
    )
    await message.reply_text(text, parse_mode="Markdown")
