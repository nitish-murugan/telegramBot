"""
handlers/reminder.py — Reminder scheduling logic
"""
import logging
from datetime import datetime, timezone

from database import (
    get_or_create_user, add_reminder, get_pending_reminders, mark_reminder_sent
)
from gemini_handler import detect_intent, confirm_reminder_text

logger = logging.getLogger(__name__)


async def handle_reminder(update, context, intent: dict, db_user) -> str:
    """Parse intent and schedule a reminder; returns confirmation text."""
    time_str = intent.get("reminder_time")
    message = intent.get("reminder_message")
    if not message:
        message = "Your reminder!"

    if not time_str:
        return "🤔 I couldn't figure out the time for that reminder. Could you be more specific? (e.g. 'Remind me at 3 PM to call mom')"

    try:
        # Parse ISO datetime
        remind_at = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        # Make it timezone-aware (IST) if naive
        if remind_at.tzinfo is None:
            ist = timezone(timedelta(hours=5, minutes=30))
            remind_at = remind_at.replace(tzinfo=ist)
        remind_at_utc = remind_at.astimezone(timezone.utc)

        if remind_at_utc <= datetime.now(timezone.utc):
            return "⚠️ That time is already in the past! Please give me a future time."

        # Save to DB
        reminder = add_reminder(
            user_id=db_user.id,
            telegram_id=update.effective_user.id,
            message=message,
            remind_at=remind_at_utc.replace(tzinfo=None)  # Store as UTC naive
        )

        # Schedule in job queue
        job_queue = context.application.job_queue
        if job_queue:
            job_queue.run_once(
                send_reminder_job,
                when=remind_at_utc,
                data={"reminder_id": reminder.id, "chat_id": update.effective_chat.id, "message": message},
                name=f"reminder_{reminder.id}"
            )

        return confirm_reminder_text(message, remind_at_utc)

    except ValueError as e:
        logger.error(f"Reminder time parse error: {e}")
        return "⚠️ I had trouble understanding that time. Try something like 'tomorrow at 9 AM' or 'June 25 at 3 PM'."


async def send_reminder_job(context):
    """Job callback to send a reminder message."""
    data = context.job.data
    reminder_id = data["reminder_id"]
    chat_id = data["chat_id"]
    message = data["message"]

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ *Reminder!*\n\n{message}",
            parse_mode="Markdown"
        )
        mark_reminder_sent(reminder_id)
    except Exception as e:
        logger.error(f"Failed to send reminder {reminder_id}: {e}")


def load_pending_reminders(job_queue):
    """On startup, reload all unsent reminders from DB into the job queue."""
    if not job_queue:
        logger.warning("JobQueue not available — reminders won't fire!")
        return

    now_utc = datetime.utcnow()
    pending = get_pending_reminders()
    loaded = 0

    for reminder in pending:
        remind_at = reminder.remind_at  # naive UTC
        if remind_at <= now_utc:
            # Already past — mark as sent (missed)
            mark_reminder_sent(reminder.id)
            continue

        remind_at_aware = remind_at.replace(tzinfo=timezone.utc)
        job_queue.run_once(
            send_reminder_job,
            when=remind_at_aware,
            data={
                "reminder_id": reminder.id,
                "chat_id": reminder.telegram_id,
                "message": reminder.message
            },
            name=f"reminder_{reminder.id}"
        )
        loaded += 1

    logger.info(f"Loaded {loaded} pending reminders from DB.")
