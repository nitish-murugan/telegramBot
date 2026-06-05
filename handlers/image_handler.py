"""
handlers/image_handler.py — Image save, search, and retrieval
"""
import os
import json
import logging
from pathlib import Path
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from database import save_image_record, search_images, get_or_create_user
from gemini_handler import describe_image

logger = logging.getLogger(__name__)

IMAGE_DIR = os.getenv("IMAGE_STORAGE_PATH", "./images")
Path(IMAGE_DIR).mkdir(parents=True, exist_ok=True)

# Per-user state: waiting for a name after image upload
PENDING_IMAGE_STATE = {}   # telegram_id -> {"file_path": ..., "file_id": ...}


async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, db_user):
    """
    Called when user sends a photo. 
    Downloads image, generates AI description, stores in pending state,
    and asks user for a name.
    """
    photo = update.message.photo[-1]  # largest resolution
    file = await context.bot.get_file(photo.file_id)

    # Build save path
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{db_user.telegram_id}_{timestamp}.jpg"
    save_path = os.path.join(IMAGE_DIR, filename)

    await file.download_to_drive(save_path)

    # AI description (runs synchronously — acceptable for Telegram handler context)
    await update.message.reply_text("🔍 Analysing your image... hang on!")
    ai_data = describe_image(save_path)

    # Store in pending state
    PENDING_IMAGE_STATE[db_user.telegram_id] = {
        "file_path": save_path,
        "file_id": photo.file_id,
        "description": ai_data["description"],
        "tags": ai_data["tags"],
        "user_id": db_user.id,
    }

    caption = update.message.caption or ""
    if caption:
        # If caption is already a name, use it directly
        await _save_image_with_name(update, db_user, caption.strip())
    else:
        await update.message.reply_text(
            "📸 Got your image! What would you like to name it?\n"
            "_(You can also add context like 'morning coffee selfie' or 'birthday party')_",
            parse_mode="Markdown"
        )


async def _save_image_with_name(update: Update, db_user, name: str):
    pending = PENDING_IMAGE_STATE.pop(db_user.telegram_id, None)
    if not pending:
        return False

    save_image_record(
        user_id=pending["user_id"],
        name=name,
        file_path=pending["file_path"],
        description=pending["description"],
        tags=pending["tags"],
    )

    tags_list = json.loads(pending["tags"]) if pending["tags"] else []
    tags_preview = ", ".join(tags_list[:5])
    await update.message.reply_text(
        f"✅ Image saved as *\"{name}\"*!\n"
        f"🏷️ Tags detected: `{tags_preview}`\n\n"
        f"You can search for it later with: *find image {name}*",
        parse_mode="Markdown"
    )
    return True


async def handle_image_name_reply(update: Update, db_user) -> bool:
    """
    Called when user sends text and we check if they're naming a pending image.
    Returns True if handled, False otherwise.
    """
    if db_user.telegram_id not in PENDING_IMAGE_STATE:
        return False

    name = update.message.text.strip()
    await _save_image_with_name(update, db_user, name)
    return True


async def handle_image_search(update: Update, query: str, db_user):
    """Search images by name/tags/description and send the best match."""
    results = search_images(db_user.id, query)

    if not results:
        await update.message.reply_text(
            f"🔍 No images found for *\"{query}\"*.\n"
            "Try different keywords — name, tags, or time hints like 'morning' or 'birthday'.",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        f"🖼️ Found *{len(results)}* image(s) matching *\"{query}\"*:",
        parse_mode="Markdown"
    )

    for img in results[:5]:  # Send up to 5 results
        try:
            with open(img.file_path, "rb") as f:
                tags_list = json.loads(img.tags) if img.tags else []
                caption = (
                    f"📸 *{img.name}*\n"
                    f"📅 {img.uploaded_at.strftime('%b %d, %Y %H:%M')} UTC\n"
                    f"🏷️ {', '.join(tags_list[:4]) if tags_list else 'No tags'}"
                )
                await update.message.reply_photo(photo=f, caption=caption, parse_mode="Markdown")
        except FileNotFoundError:
            await update.message.reply_text(f"⚠️ Image file for *{img.name}* is missing.", parse_mode="Markdown")


async def handle_list_images(update: Update, db_user):
    """List all images the user has saved."""
    from database import Session, UserImage
    session = Session()
    images = session.query(UserImage).filter_by(user_id=db_user.id).order_by(UserImage.uploaded_at.desc()).all()

    if not images:
        await update.message.reply_text("📂 You haven't saved any images yet!")
        return

    lines = ["📸 *Your saved images:*\n"]
    for i, img in enumerate(images, 1):
        lines.append(f"{i}. *{img.name}* — {img.uploaded_at.strftime('%b %d, %Y')}")

    lines.append("\n_Search with: 'find image <name or hint>'_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
