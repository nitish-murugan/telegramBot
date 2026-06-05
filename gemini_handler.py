"""
gemini_handler.py — All AI interactions using the new google-genai SDK
"""
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Initialise client once
client = genai.Client(api_key=GEMINI_API_KEY)

# Model names
CHAT_MODEL = "gemini-flash-lite-latest"
VISION_MODEL = "gemini-flash-lite-latest"

IST = timezone(timedelta(hours=5, minutes=30))

# ─────────────────────────────────────────────────────────────
# Intent detection
# ─────────────────────────────────────────────────────────────

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a personal assistant Telegram bot.
Classify the user's message into EXACTLY one of these intents and return ONLY valid JSON.

Intents:
- "reminder"  : User wants to set a reminder / alarm / alert
- "save_note" : User wants to save or write a note
- "read_note" : User wants to read, view, list, or search notes
- "delete_note" : User wants to delete a note
- "save_image": User is naming or tagging an image they just uploaded
- "search_image": User wants to find/search/retrieve an image
- "list_images": User wants to list all their images
- "chat"      : Normal casual conversation, questions, etc.

Return JSON:
{{
  "intent": "<intent>",
  "reminder_time": "<ISO datetime or null, only for reminder. Output in IST offset +05:30>",
  "reminder_message": "<message to remind, only for reminder>",
  "note_title": "<title of note or null>",
  "note_content": "<content of note or null>",
  "note_password": "<password or null>",
  "note_protected": <true/false>,
  "note_id": <integer id or null>,
  "image_name": "<name to give image or null>",
  "image_query": "<search query or null>"
}}

Current IST datetime: {now}
"""


def detect_intent(user_message: str, history: list = None) -> dict:
    """Return structured intent dict from user message."""
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    prompt = INTENT_SYSTEM_PROMPT.format(now=now)

    contents = [
        types.Content(role="user", parts=[
            types.Part(text=prompt + "\n\nUser message: " + user_message)
        ])
    ]

    try:
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            )
        )
        raw = response.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Intent detection failed: {e}")
        return {"intent": "chat"}


# ─────────────────────────────────────────────────────────────
# Conversational chat with memory
# ─────────────────────────────────────────────────────────────

CHAT_SYSTEM_PROMPT = """You are a friendly, casual, and intelligent personal assistant bot on Telegram.
You remember things about the user and use that context naturally in conversation.
Be warm, helpful, concise, and use emojis when appropriate.
You have access to the following memory about this user:
{memory_str}

Today's date and time (IST): {now}
"""


def chat_with_memory(user_message: str, history: list, memories: dict, user_name: str = "there") -> str:
    """Generate a conversational reply using chat history and user memories."""
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    memory_str = "\n".join(f"- {k}: {v}" for k, v in memories.items()) if memories else "None yet."

    system_text = CHAT_SYSTEM_PROMPT.format(memory_str=memory_str, now=now)

    contents = []
    # Add history
    for msg in history:
        contents.append(types.Content(
            role=msg.role,
            parts=[types.Part(text=msg.content)]
        ))
    # Add current user message
    contents.append(types.Content(
        role="user",
        parts=[types.Part(text=user_message)]
    ))

    try:
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_text,
                temperature=0.8,
                max_output_tokens=1024,
            )
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        return "Oops, I hit a snag! 😅 Try again in a moment."


# ─────────────────────────────────────────────────────────────
# Memory extraction
# ─────────────────────────────────────────────────────────────

MEMORY_EXTRACTION_PROMPT = """Extract key personal facts about the user from this conversation exchange.
Return ONLY a JSON object where keys are fact-names and values are the fact.
Only extract clear, definite personal facts (name, preferences, location, job, hobbies, etc.).
If nothing to extract, return {{}}.

User said: "{user_message}"
Assistant replied: "{assistant_reply}"

Return JSON only, no explanation."""


def extract_memories(user_message: str, assistant_reply: str) -> dict:
    prompt = MEMORY_EXTRACTION_PROMPT.format(
        user_message=user_message, assistant_reply=assistant_reply
    )
    try:
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            )
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return {k: str(v) for k, v in data.items() if v}
    except Exception as e:
        logger.warning(f"Memory extraction failed: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# Image description for semantic search
# ─────────────────────────────────────────────────────────────

IMAGE_DESCRIPTION_PROMPT = """Describe this image in detail. Then list 10-15 relevant tags (objects, colors, 
scene type, mood, time of day if apparent, etc.). 
Return JSON:
{
  "description": "<detailed description>",
  "tags": ["tag1", "tag2", ...]
}"""


def describe_image(image_path: str) -> dict:
    """Use Gemini Vision to generate a description and tags for an image."""
    try:
        image_bytes = Path(image_path).read_bytes()
        # Detect mime type
        suffix = Path(image_path).suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp"}
        mime_type = mime_map.get(suffix, "image/jpeg")

        response = client.models.generate_content(
            model=VISION_MODEL,
            contents=[
                types.Content(role="user", parts=[
                    types.Part(
                        inline_data=types.Blob(mime_type=mime_type, data=image_bytes)
                    ),
                    types.Part(text=IMAGE_DESCRIPTION_PROMPT),
                ])
            ],
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            )
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return {
            "description": data.get("description", ""),
            "tags": json.dumps(data.get("tags", []))
        }
    except Exception as e:
        logger.error(f"Image description failed: {e}")
        return {"description": "", "tags": "[]"}


# ─────────────────────────────────────────────────────────────
# Reminder time confirmation
# ─────────────────────────────────────────────────────────────

def confirm_reminder_text(message: str, remind_at: datetime) -> str:
    """Generate a friendly confirmation message for a reminder."""
    time_str = remind_at.astimezone(IST).strftime("%A, %B %d at %I:%M %p IST")
    prompt = f"""Write a short, friendly confirmation message (1-2 sentences, with emoji) 
telling the user their reminder is set. 
Reminder: "{message}"
Time: {time_str}"""
    try:
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(temperature=0.7, max_output_tokens=100)
        )
        return response.text.strip()
    except Exception:
        return f"✅ Reminder set for {time_str}!\nI'll remind you: \"{message}\""
