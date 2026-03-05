#!/usr/bin/env python3
"""
MyOldMachine — LLM-Powered Telegram Bot

A self-hosted, provider-agnostic Telegram bot with a modular skill system.
Supports Claude, OpenAI, Gemini, Ollama, and OpenRouter.
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

from core.config import (
    BOT_DIR, DATA_DIR, USERS_DIR, MEMORY_DIR, SKILLS_DIR, SCHEDULER_DIR,
    get_telegram_token, get_telegram_api_base, get_allowed_users,
    get_bot_name, get_llm_provider, get_llm_model, get_llm_api_key,
    get_ollama_base_url, get_webhook_port, get_user_profile, is_admin,
    LOG_DIR,
)
from core.llm import create_provider, Message, LLMResponse
from core.skill_loader import SkillManager
from core.session import SessionManager
from core.scheduler import Scheduler, parse_natural_time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Per-user locks
_user_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


# Duplicate message protection
_processed_ids: set[int] = set()
_MAX_PROCESSED = 200

# Media group buffering
_media_group_buffers: dict[str, dict] = {}
_MEDIA_GROUP_WAIT = 1.5

# Globals initialized in main()
_llm_provider = None
_skill_manager = None
_scheduler = None


def get_user_dir(user_id: int) -> Path:
    d = USERS_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_attachments_dir(user_id: int) -> Path:
    d = get_user_dir(user_id) / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_session(user_id: int) -> SessionManager:
    return SessionManager(get_user_dir(user_id))


# --- System prompt builder ---

def build_system_prompt(user_id: int) -> str:
    """Build the system prompt with user context, skills, and memories."""
    profile = get_user_profile(user_id)
    user_name = profile.get("name", "User")
    user_role = profile.get("role", "user")
    blocked_skills = profile.get("blocked_skills", [])

    parts = []

    # Bot identity
    bot_name = get_bot_name()
    parts.append(f"You are {bot_name}, a helpful AI assistant running as a Telegram bot.")
    parts.append(f"The user's name is {user_name}. Their role is: {user_role}.")
    parts.append(f"User's Telegram ID: {user_id}")
    parts.append("")

    # File sending instructions
    parts.append("### Sending Files to User:")
    parts.append(f"  python {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --photo /path/to/image.png")
    parts.append(f"  python {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --video /path/to/video.mp4")
    parts.append(f"  python {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --document /path/to/file.pdf")
    parts.append("")

    # Attachments path
    parts.append(f"User attachments directory: {get_attachments_dir(user_id)}")
    parts.append("")

    # Scheduler instructions
    parts.append("### Reminders:")
    parts.append("When the user asks you to set a reminder, use the scheduler CLI:")
    parts.append(f"  python {BOT_DIR}/utils/scheduler_cli.py add --user {user_id} --at \"YYYY-MM-DD HH:MM\" --message \"text\"")
    parts.append(f"  python {BOT_DIR}/utils/scheduler_cli.py list --user {user_id}")
    parts.append(f"  python {BOT_DIR}/utils/scheduler_cli.py remove --id <job_id> --user {user_id}")
    parts.append("")

    # Custom instructions file
    instructions_file = DATA_DIR / "instructions.md"
    if instructions_file.exists():
        parts.append("### Custom Instructions:")
        parts.append(instructions_file.read_text())
        parts.append("")

    # Skills
    if _skill_manager:
        skills_ctx = _skill_manager.build_context(exclude=blocked_skills)
        if skills_ctx:
            parts.append(skills_ctx)

    # Memory
    session = get_session(user_id)
    memories = session.load_memories()
    if memories:
        parts.append("### Persistent memories about this user:")
        for mem in memories[-20:]:
            parts.append(f"- {mem['content']}")
        parts.append("")

    # Summary of older conversations
    summary = session.load_summary()
    if summary:
        parts.append("### Long-term context (summary of older conversations):")
        parts.append(summary)
        parts.append("")

    return "\n".join(parts)


def build_messages(user_id: int, new_message: str) -> list[Message]:
    """Build the message list from conversation history + new message."""
    session = get_session(user_id)
    history = session.load_conversation()

    messages = []
    for msg in history:
        messages.append(Message(role=msg["role"], content=msg["content"]))
    messages.append(Message(role="user", content=new_message))
    return messages


def sanitize_response(content: str) -> str:
    """Remove hallucinated conversation continuations."""
    patterns = [
        r"\n\s*Human\s*:.*", r"\n\s*USER\s*:.*", r"\n\s*User\s*:.*",
        r"\n\s*<user>.*", r"\n\s*<human>.*",
    ]
    for p in patterns:
        content = re.sub(p, "", content, flags=re.IGNORECASE | re.DOTALL)
    return content.strip()


async def call_llm(user_id: int, message: str, chat=None) -> str:
    """Call the configured LLM provider and return the response text."""
    typing_task = None

    async def send_typing():
        while True:
            try:
                if chat:
                    await chat.send_action("typing")
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(3)

    try:
        if chat:
            typing_task = asyncio.create_task(send_typing())

        system_prompt = build_system_prompt(user_id)
        messages = build_messages(user_id, message)

        response: LLMResponse = await _llm_provider.complete(
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=8192,
            temperature=0.7,
        )

        if response.error:
            logger.error(f"LLM error for user {user_id}: {response.error}")
            return f"Error from {response.provider}: {response.error}"

        if not response.text:
            return "No response generated. Try again or rephrase your message."

        text = sanitize_response(response.text)
        logger.info(
            f"LLM response for {user_id}: {len(text)} chars, "
            f"{response.input_tokens}+{response.output_tokens} tokens "
            f"({response.provider}/{response.model})"
        )
        return text

    except Exception as e:
        logger.exception(f"Failed to call LLM for user {user_id}")
        return f"Error: {e}"
    finally:
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass


def split_message(text: str, max_length: int = 4000) -> list[str]:
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks


# --- Telegram handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_name = get_bot_name()
    await update.message.reply_text(
        f"Connected to {bot_name}.\n\n"
        f"Provider: {get_llm_provider()} / {get_llm_model()}\n\n"
        "/clear - Reset conversation\n"
        "/status - Bot status\n"
        "/remember <fact> - Save a memory\n"
        "/memories - Show memories\n"
        "/forget <n> - Delete memory by number\n"
        "/remind <time> <message> - Set a reminder\n"
        "/reminders - Show reminders\n"
        "/cancel <id> - Cancel a reminder"
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    if session.conversation_file.exists():
        archive = session.user_dir / f"conversation_{datetime.now():%Y%m%d_%H%M%S}.json"
        session.conversation_file.rename(archive)
    await update.message.reply_text("Conversation cleared.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    history = session.load_conversation()
    memories = session.load_memories()
    summary = session.load_summary()
    skills_count = len(_skill_manager.get_enabled_skills()) if _skill_manager else 0
    await update.message.reply_text(
        f"Status: Online\n"
        f"Provider: {get_llm_provider()} / {get_llm_model()}\n"
        f"Messages in context: {len(history)}\n"
        f"Persistent memories: {len(memories)}\n"
        f"Has summary: {'Yes' if summary else 'No'}\n"
        f"Skills loaded: {skills_count}"
    )


async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.replace("/remember", "").strip()
    if not text:
        await update.message.reply_text("Usage: /remember <fact to remember>")
        return
    session = get_session(user_id)
    session.add_memory(text)
    await update.message.reply_text(f"Saved: {text}")


async def memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    memories = get_session(user_id).load_memories()
    if not memories:
        await update.message.reply_text("No memories saved. Use /remember to add some.")
        return
    text = "Memories:\n\n"
    for i, mem in enumerate(memories, 1):
        text += f"{i}. {mem['content']}\n"
    for chunk in split_message(text):
        await update.message.reply_text(chunk)


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.replace("/forget", "").strip()
    if not text.isdigit():
        await update.message.reply_text("Usage: /forget <number>")
        return
    idx = int(text) - 1
    session = get_session(user_id)
    memories = session.load_memories()
    if 0 <= idx < len(memories):
        removed = memories.pop(idx)
        session.save_memories(memories)
        await update.message.reply_text(f"Forgot: {removed['content']}")
    else:
        await update.message.reply_text("Invalid memory number.")


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.replace("/remind", "").strip()
    if not text:
        await update.message.reply_text(
            "Usage: /remind <time> <message>\n"
            "Examples:\n"
            "  /remind in 30 minutes Check the oven\n"
            "  /remind tomorrow at 9am Meeting\n"
            "  /remind at 3pm Call mom"
        )
        return
    # Parse time
    words = text.split()
    time_part = message_part = None
    for i in range(min(6, len(words)), 0, -1):
        parsed = parse_natural_time(" ".join(words[:i]))
        if parsed:
            time_part = parsed
            message_part = " ".join(words[i:])
            break
    if not time_part:
        await update.message.reply_text("Couldn't understand the time.")
        return
    if not message_part:
        await update.message.reply_text("Please include a message.")
        return
    if _scheduler:
        job = _scheduler.add_job(user_id, message_part, time_part)
        await update.message.reply_text(
            f"Reminder set for {time_part:%Y-%m-%d %H:%M}\n"
            f"Message: {message_part}\n"
            f"ID: {job.job_id}"
        )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _scheduler:
        await update.message.reply_text("Scheduler not available.")
        return
    jobs = _scheduler.get_user_jobs(user_id)
    if not jobs:
        await update.message.reply_text("No reminders. Use /remind to set one.")
        return
    text = "Reminders:\n\n"
    for j in jobs:
        repeat_tag = f" [{j.repeat}]" if j.repeat else ""
        text += f"- {j.run_at:%Y-%m-%d %H:%M}{repeat_tag}: {j.message[:50]}\n  ID: {j.job_id}\n\n"
    text += "Use /cancel <id> to remove."
    for chunk in split_message(text):
        await update.message.reply_text(chunk)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.replace("/cancel", "").strip()
    if not text:
        await update.message.reply_text("Usage: /cancel <reminder_id>")
        return
    if not _scheduler:
        await update.message.reply_text("Scheduler not available.")
        return
    job = _scheduler.get_job(text)
    if not job:
        await update.message.reply_text(f"Reminder '{text}' not found.")
        return
    if job.user_id != user_id:
        await update.message.reply_text("You can only cancel your own reminders.")
        return
    _scheduler.remove_job(text)
    await update.message.reply_text(f"Reminder '{text}' cancelled.")


async def download_attachments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> list[tuple[Path, str]]:
    """Download all attachments and return (path, type) pairs."""
    user_id = update.effective_user.id
    attachments_dir = get_attachments_dir(user_id)
    message = update.message
    downloaded = []

    async def save_file(file_obj, file_type, ext, original_name=None):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = f"{ts}_{original_name}" if original_name else f"{ts}_{file_type}{ext}"
        path = attachments_dir / name
        try:
            await file_obj.download_to_drive(path)
            if path.exists() and path.stat().st_size > 0:
                return path
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.error(f"Download failed for {file_type}: {e}")
        return None

    if message.photo:
        f = await message.photo[-1].get_file()
        p = await save_file(f, "image", ".jpg")
        if p:
            downloaded.append((p, "image"))
    if message.document:
        f = await message.document.get_file()
        name = message.document.file_name
        ext = Path(name).suffix if name else ""
        p = await save_file(f, "document", ext, name)
        if p:
            downloaded.append((p, "document"))
    if message.audio:
        f = await message.audio.get_file()
        name = message.audio.file_name
        ext = Path(name).suffix if name else ".mp3"
        p = await save_file(f, "audio", ext, name)
        if p:
            downloaded.append((p, "audio"))
    if message.voice:
        f = await message.voice.get_file()
        p = await save_file(f, "voice", ".ogg")
        if p:
            downloaded.append((p, "voice"))
    if message.video:
        f = await message.video.get_file()
        name = message.video.file_name
        ext = Path(name).suffix if name else ".mp4"
        p = await save_file(f, "video", ext, name)
        if p:
            downloaded.append((p, "video"))

    return downloaded


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Auth check
    allowed = get_allowed_users()
    if allowed and user_id not in allowed:
        await update.message.reply_text("Unauthorized. Your ID is not in ALLOWED_USERS.")
        return

    # Duplicate protection
    msg_id = update.message.message_id
    if msg_id in _processed_ids:
        return
    _processed_ids.add(msg_id)
    if len(_processed_ids) > _MAX_PROCESSED:
        oldest = sorted(_processed_ids)[:len(_processed_ids) - _MAX_PROCESSED]
        for oid in oldest:
            _processed_ids.discard(oid)

    # Media group handling
    media_group_id = update.message.media_group_id
    if media_group_id:
        if media_group_id not in _media_group_buffers:
            _media_group_buffers[media_group_id] = {
                "updates": [], "timer": None, "user_id": user_id,
            }
        buf = _media_group_buffers[media_group_id]
        buf["updates"].append(update)
        if buf["timer"]:
            buf["timer"].cancel()
        buf["timer"] = asyncio.create_task(
            _process_media_group(media_group_id, context)
        )
        return

    await _process_single(update, context)


async def _process_media_group(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(_MEDIA_GROUP_WAIT)
    buf = _media_group_buffers.pop(media_group_id, None)
    if not buf:
        return
    user_id = buf["user_id"]
    updates = buf["updates"]

    lock = _get_user_lock(user_id)
    async with lock:
        all_attachments = []
        caption = ""
        for upd in updates:
            attachments = await download_attachments(upd, context)
            all_attachments.extend(attachments)
            if not caption:
                caption = upd.message.text or upd.message.caption or ""

        user_message = caption
        if all_attachments:
            info = "\n\n[Attachments:]"
            for path, ftype in all_attachments:
                info += f"\n- {ftype}: {path}"
            user_message = (user_message + info) if user_message else f"[Sent {len(all_attachments)} file(s)]{info}"

        if not user_message:
            return

        chat = updates[0].message.chat
        response = await call_llm(user_id, user_message, chat=chat)
        response = sanitize_response(response)

        session = get_session(user_id)
        history = session.load_conversation()
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": response})
        session.save_conversation(history)

        for chunk in split_message(response):
            await chat.send_message(chunk)


async def _process_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lock = _get_user_lock(user_id)

    if lock.locked():
        await update.message.reply_text("Still working on your previous request.")

    async with lock:
        attachments = await download_attachments(update, context)
        user_message = update.message.text or update.message.caption or ""

        if attachments:
            info = "\n\n[Attachments:]"
            for path, ftype in attachments:
                info += f"\n- {ftype}: {path}"
                if ftype == "image":
                    info += " (viewable with Read tool)"
            user_message = (user_message + info) if user_message else f"[Sent file(s)]{info}"

        if not user_message:
            await update.message.reply_text("Please send a message or attachment.")
            return

        logger.info(f"From {user_id}: {user_message[:100]}...")
        try:
            await update.message.chat.send_action("typing")
        except Exception:
            pass

        response = await call_llm(user_id, user_message, chat=update.message.chat)
        response = sanitize_response(response)

        session = get_session(user_id)
        history = session.load_conversation()
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": response})
        session.save_conversation(history)

        for chunk in split_message(response):
            await update.message.reply_text(chunk)

        logger.info(f"Responded to {user_id}: {len(response)} chars")


def main():
    global _llm_provider, _skill_manager, _scheduler

    token = get_telegram_token()

    # Initialize LLM provider
    provider_name = get_llm_provider()
    model = get_llm_model()
    api_key = get_llm_api_key()
    kwargs = {}
    if provider_name == "ollama":
        kwargs["base_url"] = get_ollama_base_url()
    _llm_provider = create_provider(provider_name, model, api_key, **kwargs)
    logger.info(f"LLM provider: {_llm_provider.provider_name} / {model}")

    # Initialize skills
    _skill_manager = SkillManager(SKILLS_DIR)
    logger.info(f"Loaded {len(_skill_manager.skills)} skills")

    # Build Telegram app
    api_base = get_telegram_api_base()
    builder = Application.builder().token(token)
    if api_base:
        builder = builder.base_url(f"{api_base}/bot").base_file_url(f"{api_base}/file/bot")
    builder = builder.concurrent_updates(True)
    app = builder.build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("memories", memories_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Message handler
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Document.ALL |
         filters.AUDIO | filters.VIDEO | filters.VOICE) & ~filters.COMMAND,
        handle_message,
    ))

    # Initialize scheduler
    async def post_init(application):
        global _scheduler

        async def send_reminder(user_id: int, text: str):
            try:
                await application.bot.send_message(chat_id=user_id, text=text)
            except Exception as e:
                logger.error(f"Failed to send to {user_id}: {e}")

        _scheduler = Scheduler(SCHEDULER_DIR / "scheduler.db", send_fn=send_reminder)
        _scheduler.start()
        logger.info("Scheduler started")

    async def post_shutdown(application):
        if _scheduler:
            _scheduler.stop()

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    logger.info(f"Starting {get_bot_name()}...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
