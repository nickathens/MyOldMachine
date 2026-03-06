#!/usr/bin/env python3
"""
MyOldMachine — LLM-Powered Telegram Bot

A self-hosted, provider-agnostic Telegram bot that turns any old machine into
a dedicated AI assistant. Supports Claude CLI (with full tool-use), Claude API,
OpenAI, Gemini, Ollama, and OpenRouter.
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
    BOT_DIR, DATA_DIR, USERS_DIR, MEMORY_DIR, SKILLS_DIR,
    get_telegram_token, get_telegram_api_base, get_allowed_users,
    get_bot_name, get_llm_provider, get_llm_model, get_llm_api_key,
    get_ollama_base_url, get_webhook_port, get_user_profile, is_admin,
    LOG_DIR,
)
from core.llm import create_provider, Message, LLMResponse, ClaudeCLIProvider
from core.skill_loader import SkillManager
from core.session import SessionManager, get_session_manager
from core.scheduler import init_scheduler, get_scheduler, parse_natural_time
from core.health import build_health_report, check_critical
from core.updater import check_for_updates, full_update, get_current_version, get_current_branch

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

MAX_CONTEXT_MESSAGES = 40


# --- User data helpers ---

def get_user_dir(user_id: int) -> Path:
    d = USERS_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_attachments_dir(user_id: int) -> Path:
    d = get_user_dir(user_id) / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_session(user_id: int) -> SessionManager:
    return get_session_manager(user_id, USERS_DIR)


# --- Task progress tracking (crash recovery) ---

def get_progress_file(user_id: int) -> Path:
    return get_user_dir(user_id) / "task_in_progress.json"


def save_task_progress(user_id: int, original_message: str, partial_text: str,
                       status: str, tool: str = None):
    """Save current task progress for recovery after restart."""
    progress_file = get_progress_file(user_id)
    data = {
        "user_id": user_id,
        "original_message": original_message[:500],
        "partial_text": partial_text,
        "status": status,
        "current_tool": tool,
        "started": datetime.now().isoformat() if not progress_file.exists() else None,
        "updated": datetime.now().isoformat(),
    }
    if progress_file.exists():
        try:
            with open(progress_file) as f:
                existing = json.load(f)
                data["started"] = existing.get("started")
        except Exception:
            pass
    with open(progress_file, "w") as f:
        json.dump(data, f, indent=2)


def clear_task_progress(user_id: int):
    progress_file = get_progress_file(user_id)
    if progress_file.exists():
        progress_file.unlink()


def get_incomplete_task(user_id: int) -> dict | None:
    progress_file = get_progress_file(user_id)
    if progress_file.exists():
        try:
            with open(progress_file) as f:
                return json.load(f)
        except Exception:
            return None
    return None


# --- Pending message queue (survives crashes) ---

def _pending_message_path(user_id: int) -> Path:
    return get_user_dir(user_id) / "pending_message.json"


def save_pending_message(user_id: int, message_text: str, message_id: int):
    """Write a marker before processing so we can detect lost messages on restart."""
    try:
        data = {
            "user_id": user_id,
            "message_id": message_id,
            "text": message_text[:500],
            "received": datetime.now().isoformat(),
        }
        target = _pending_message_path(user_id)
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(target)
    except Exception as e:
        logger.warning(f"Failed to save pending message for {user_id}: {e}")


def clear_pending_message(user_id: int):
    """Remove the marker after response is sent."""
    try:
        path = _pending_message_path(user_id)
        if path.exists():
            path.unlink()
    except Exception as e:
        logger.warning(f"Failed to clear pending message for {user_id}: {e}")


async def recover_pending_messages(bot):
    """On startup, notify users whose messages were lost in a crash."""
    if not USERS_DIR.exists():
        return
    for user_dir in USERS_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        pending_file = user_dir / "pending_message.json"
        tmp_file = user_dir / "pending_message.json.tmp"
        # Promote interrupted tmp files
        if tmp_file.exists():
            if not pending_file.exists():
                try:
                    tmp_file.rename(pending_file)
                except Exception:
                    tmp_file.unlink(missing_ok=True)
            else:
                tmp_file.unlink(missing_ok=True)
        if not pending_file.exists():
            continue
        try:
            with open(pending_file) as f:
                data = json.load(f)
            user_id = data.get("user_id")
            text = data.get("text", "")
            received = data.get("received", "unknown")
            if not user_id:
                pending_file.unlink(missing_ok=True)
                continue
            # Only recover messages less than 1 hour old
            try:
                age = (datetime.now() - datetime.fromisoformat(received)).total_seconds()
                if age > 3600:
                    logger.info(f"Pending message for {user_id} too old ({age:.0f}s), discarding")
                    pending_file.unlink(missing_ok=True)
                    continue
            except (ValueError, TypeError):
                pass
            preview = text[:200] + ("..." if len(text) > 200 else "")
            logger.info(f"Recovering lost message for user {user_id}: {preview[:80]}")
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"I restarted while processing your message. "
                         f"Here's what you sent:\n\n\"{preview}\"\n\n"
                         f"Please resend it if you'd like me to continue."
                )
            except Exception as e:
                logger.error(f"Failed to notify user {user_id} about lost message: {e}")
            pending_file.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Error recovering pending message from {pending_file}: {e}")
            pending_file.unlink(missing_ok=True)


# --- System prompt builder ---

def build_system_prompt(user_id: int) -> str:
    """Build the system prompt with user context, skills, memories, and instructions.

    All providers now have tool-use capability (either native via Claude CLI,
    or through our function-calling execution layer for API providers).
    """
    profile = get_user_profile(user_id)
    user_name = profile.get("name", "User")
    user_role = profile.get("role", "user")
    blocked_skills = profile.get("blocked_skills", [])
    is_claude_cli = isinstance(_llm_provider, ClaudeCLIProvider)
    has_tool_use = _llm_provider.supports_tool_use if _llm_provider else False

    parts = []

    # Bot identity
    bot_name = get_bot_name()
    if has_tool_use:
        parts.append(f"You are {bot_name}, an AI assistant that controls this machine.")
        parts.append("You have full access to the operating system through tool calls. "
                     "You can run commands, read and write files, install software, "
                     "manage files, and configure services.")
        if not is_claude_cli:
            parts.append(
                "You have 4 tools available: run_command (execute shell commands), "
                "read_file (read file contents), write_file (create/overwrite files), "
                "and list_directory (list files in a directory). "
                "Use these tools to accomplish tasks. Do NOT write out commands as text — "
                "actually call the tools to execute them."
            )
        parts.append("If the user asks for something and you're missing a tool, install it.")
    else:
        parts.append(f"You are {bot_name}, an AI assistant.")
        parts.append("You are a text-only assistant. You CANNOT run commands or access the filesystem. "
                     "Do NOT write out shell commands or code blocks pretending to execute them. "
                     "Just have a helpful conversation.")
    parts.append(f"The user's name is {user_name}. Their role is: {user_role}.")
    parts.append(f"User's Telegram ID: {user_id}")
    parts.append("")

    # Only show sudo/system info to providers that can use it, and only to admins
    if has_tool_use and user_role == "admin":
        parts.append(f"Sudo password is stored at ~/.sudo_pass — use it for privileged commands.")
        if not is_claude_cli:
            parts.append("For sudo: run_command('cat ~/.sudo_pass | sudo -S <your_command>')")
        parts.append("")

    # Tool-use-only sections (skip for text-only providers)
    if has_tool_use:
        # Telegram capabilities
        parts.append("### Sending Files to User:")
        parts.append(f"  python {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --photo /path/to/image.png")
        parts.append(f"  python {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --video /path/to/video.mp4")
        parts.append(f"  python {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --document /path/to/file.pdf")
        parts.append("  Add --caption 'description' for captions.")
        parts.append("")
        parts.append(f"User attachments directory: {get_attachments_dir(user_id)}")
        parts.append("")

        # Service restart policy (admin)
        if user_role == "admin":
            parts.append("### Service Restart Policy:")
            parts.append("Do NOT restart the bot service directly — it will kill you mid-response.")
            parts.append("If changes require a restart, ask the user to send /restart.")
            parts.append("")

        # Scheduler instructions
        parts.append("### Reminders and Scheduling:")
        parts.append("When the user asks to set a reminder, use the scheduler CLI:")
        parts.append(f"  python {BOT_DIR}/utils/scheduler_cli.py add --user {user_id} --at \"YYYY-MM-DD HH:MM\" --message \"text\"")
        parts.append(f"  python {BOT_DIR}/utils/scheduler_cli.py add --user {user_id} --at \"in 30 minutes\" --message \"text\"")
        parts.append(f"  python {BOT_DIR}/utils/scheduler_cli.py list --user {user_id}")
        parts.append(f"  python {BOT_DIR}/utils/scheduler_cli.py remove --id <job_id> --user {user_id}")
        parts.append("NEVER use crontab. The scheduler handles everything.")
        parts.append("")

        # Memory system
        memory_dir = DATA_DIR / "memory"
        parts.append("### Memory System:")
        parts.append("Use memory proactively, not just when asked.")
        parts.append("")
        parts.append("**Projects:**")
        parts.append(f"  python {BOT_DIR}/utils/project_manager.py create \"Name\" \"Summary\" \"/path\"")
        parts.append(f"  Project state: {memory_dir}/projects/<slug>/state.json")
        parts.append("")
        parts.append("**Topic Memories:**")
        parts.append(f"  Write domain knowledge to {memory_dir}/topics/<topic>.md")
        parts.append("")
        parts.append("**Decision Logs:**")
        parts.append(f"  Log significant decisions to {memory_dir}/decisions/YYYY-MM-DD_description.md")
        parts.append(f"  Include: what was decided, options considered, rationale")
        parts.append("")

    # Memory dir used by project/topic listing below
    memory_dir = DATA_DIR / "memory"

    # Custom instructions file
    instructions_file = DATA_DIR / "instructions.md"
    if instructions_file.exists():
        parts.append("### Custom Instructions:")
        parts.append(instructions_file.read_text())
        parts.append("")

    # CLAUDE.md and SYSTEM-CONTEXT.md — only for tool-use providers
    if has_tool_use:
        claude_md = Path.home() / "CLAUDE.md"
        if claude_md.exists():
            parts.append("### Global Instructions (CLAUDE.md):")
            parts.append(claude_md.read_text())
            parts.append("")

        system_context = Path.home() / "SYSTEM-CONTEXT.md"
        if system_context.exists() and user_role == "admin":
            parts.append("### System Context:")
            parts.append(system_context.read_text())
            parts.append("")

    # Active projects from memory
    projects_dir = memory_dir / "projects"
    if projects_dir.exists():
        project_lines = []
        for pdir in projects_dir.iterdir():
            if not pdir.is_dir():
                continue
            state_file = pdir / "state.json"
            if not state_file.exists():
                continue
            try:
                with open(state_file) as f:
                    state = json.load(f)
                if state.get("status") != "in_progress":
                    continue
                project_lines.append(f"\n**{state.get('name', 'Unknown')}**")
                project_lines.append(f"  Location: {state.get('location', 'unknown')}")
                if state.get("summary"):
                    project_lines.append(f"  Summary: {state['summary']}")
                if state.get("next_steps"):
                    for step in state["next_steps"][:3]:
                        project_lines.append(f"  - {step}")
            except (json.JSONDecodeError, KeyError):
                continue
        if project_lines:
            parts.append("### Active Projects:")
            parts.extend(project_lines)
            parts.append("")

    # Topic memories listing
    topics_dir = memory_dir / "topics"
    if topics_dir.exists():
        topics = [f.stem for f in topics_dir.glob("*.md")]
        if topics:
            parts.append(f"Available topic memories: {', '.join(topics)}")
            parts.append(f"(Read with: {topics_dir}/<topic>.md)")
            parts.append("")

    # Conversation summary (from compaction)
    session = get_session(user_id)
    summary = session.load_summary()
    if summary:
        parts.append("### Conversation Summary (older context):")
        parts.append(summary)
        parts.append("")

    # Persistent memories
    memories = session.load_memories()
    if memories:
        parts.append("### Persistent Memories:")
        for mem in memories:
            parts.append(f"- {mem['content']}")
        parts.append("")

    # Skills — only relevant for providers that can execute tools
    if has_tool_use and _skill_manager:
        skills_ctx = _skill_manager.build_context(exclude=blocked_skills)
        if skills_ctx:
            parts.append(skills_ctx)

    return "\n".join(parts)


def build_messages(user_id: int, new_message: str) -> list[Message]:
    """Build the message list from conversation history + new message."""
    session = get_session(user_id)

    # Check topic
    current_topic = session.get_current_topic()
    if current_topic:
        history = session.get_topic_session(current_topic)
    else:
        history = session.load_conversation()

    if len(history) > MAX_CONTEXT_MESSAGES:
        history = history[-MAX_CONTEXT_MESSAGES:]

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

    # Strip hallucinated assistant continuations
    assistant_patterns = [
        r"\n\s*Assistant\s*:.*", r"\n\s*Claude\s*:.*", r"\n\s*AI\s*:.*",
    ]
    for p in assistant_patterns:
        match = re.search(p, content, flags=re.IGNORECASE)
        if match and match.start() > 50:
            content = content[:match.start()]

    # Neutralize remaining markers
    content = re.sub(r'\bHuman:', '[Human]:', content)
    content = re.sub(r'\bUSER:', '[USER]:', content)
    content = re.sub(r'<user>', '[user-tag]', content, flags=re.IGNORECASE)
    content = re.sub(r'<human>', '[human-tag]', content, flags=re.IGNORECASE)

    return content.strip()


async def call_llm(user_id: int, message: str, chat=None) -> str:
    """Call the configured LLM provider and return the response text."""
    system_prompt = build_system_prompt(user_id)
    messages = build_messages(user_id, message)

    # For Claude CLI provider, pass extra kwargs for progress tracking
    if isinstance(_llm_provider, ClaudeCLIProvider):
        response: LLMResponse = await _llm_provider.complete(
            system_prompt=system_prompt,
            messages=messages,
            chat=chat,
            user_id=user_id,
            original_message=message,
        )
    else:
        # API providers — simple typing indicator
        typing_task = None

        async def send_typing():
            while True:
                try:
                    if chat:
                        await chat.send_action("typing")
                    await asyncio.sleep(4)
                except asyncio.CancelledError:
                    break
                except Exception:
                    await asyncio.sleep(4)

        try:
            if chat:
                typing_task = asyncio.create_task(send_typing())
            response = await _llm_provider.complete(
                system_prompt=system_prompt,
                messages=messages,
            )
        finally:
            if typing_task:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

    if response.error and not response.text:
        logger.error(f"LLM error for user {user_id}: {response.error}")
        return f"Error from {response.provider}: {response.error}"

    if not response.text:
        return "No response generated. Try again or rephrase your message."

    text = sanitize_response(response.text)
    logger.info(f"LLM response for {user_id}: {len(text)} chars ({response.provider}/{response.model})")
    return text


def _save_and_send(user_id: int, user_message: str, response: str, session=None, chat=None):
    """Save conversation turn and trigger compaction if needed."""
    if session is None:
        session = get_session(user_id)

    response = sanitize_response(response)

    current_topic = session.get_current_topic()
    if current_topic:
        history = session.get_topic_session(current_topic)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": response})
        session.save_topic_session(current_topic, history)
    else:
        history = session.load_conversation()
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": response})
        # Trigger compaction if conversation is getting long
        if len(history) > session.config["compaction_threshold"]:
            history, _ = session.compact_conversation(history, session.summary_file)
        session.save_conversation(history)

    return response


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
    user_id = update.effective_user.id
    get_user_dir(user_id)

    incomplete = get_incomplete_task(user_id)
    if incomplete and incomplete.get("partial_text"):
        original = incomplete.get("original_message", "unknown")
        await update.message.reply_text(
            f"Welcome back. There was an interrupted task.\n\n"
            f"Original request: {original[:100]}...\n\n"
            f"Send /recover to see progress, or continue with a new message."
        )
    else:
        bot_name = get_bot_name()
        import platform
        version = get_current_version(BOT_DIR)
        skills_count = len(_skill_manager.get_enabled_skills()) if _skill_manager else 0
        await update.message.reply_text(
            f"Connected to {bot_name}.\n\n"
            f"Provider: {get_llm_provider()} / {get_llm_model()}\n"
            f"OS: {platform.system()} {platform.release()}\n"
            f"Skills: {skills_count}\n"
            f"Version: {version}\n\n"
            "Send /help for all commands.\n"
            "Just send me a message."
        )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    if session.conversation_file.exists():
        archive = session.user_dir / f"conversation_{datetime.now():%Y%m%d_%H%M%S}.json"
        session.conversation_file.rename(archive)
    await update.message.reply_text("Conversation cleared.")
    logger.info(f"Context cleared by user {user_id}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    history = session.load_conversation()
    memories = session.load_memories()
    summary = session.load_summary()
    current_topic = session.get_current_topic()
    topics = session.list_topics()
    skills_count = len(_skill_manager.get_enabled_skills()) if _skill_manager else 0
    meta = session.load_session_meta()
    await update.message.reply_text(
        f"Status: Online\n"
        f"Provider: {get_llm_provider()} / {get_llm_model()}\n"
        f"Messages in context: {len(history)}\n"
        f"Persistent memories: {len(memories)}\n"
        f"Has summary: {'Yes' if summary else 'No'}\n"
        f"Current session: {current_topic or 'main'}\n"
        f"Topic sessions: {len(topics)}\n"
        f"Skills loaded: {skills_count}\n"
        f"Last reset: {meta.get('last_reset', 'never')}"
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


async def recover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show interrupted task progress."""
    user_id = update.effective_user.id
    incomplete = get_incomplete_task(user_id)
    if not incomplete:
        await update.message.reply_text("No interrupted task found.")
        return
    partial = incomplete.get("partial_text", "")
    original = incomplete.get("original_message", "unknown")
    status = incomplete.get("status", "unknown")
    started = incomplete.get("started", "unknown")
    tool = incomplete.get("current_tool")
    response = f"**Interrupted Task**\n\n"
    response += f"Started: {started}\n"
    response += f"Original request: {original}\n"
    response += f"Last status: {status}"
    if tool:
        response += f" (tool: {tool})"
    response += "\n\n"
    if partial:
        response += f"**Partial output:**\n{partial[:3500]}"
        if len(partial) > 3500:
            response += "\n\n[Truncated]"
    else:
        response += "No partial output captured."
    response += "\n\nSend /clear_recovery to delete this saved progress."
    for chunk in split_message(response):
        await update.message.reply_text(chunk)


async def clear_recovery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    progress_file = get_progress_file(user_id)
    if progress_file.exists():
        progress_file.unlink()
        await update.message.reply_text("Recovery data cleared.")
    else:
        await update.message.reply_text("No recovery data to clear.")


async def topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch to a topic-specific session or back to main."""
    user_id = update.effective_user.id
    text = update.message.text.replace("/topic", "").strip()
    session = get_session(user_id)
    if not text:
        current = session.get_current_topic()
        if current:
            await update.message.reply_text(f"Current topic: {current}\nUse /topic <name> to switch or /topic main to return.")
        else:
            await update.message.reply_text("Currently in main session.\nUse /topic <name> to switch to a topic.")
        return
    if text.lower() == "main":
        result = session.switch_topic(None)
    else:
        result = session.switch_topic(text)
    await update.message.reply_text(result)


async def list_topics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    topics = session.list_topics()
    current = session.get_current_topic()
    if not topics:
        await update.message.reply_text(
            "No topic sessions yet.\nUse /topic <name> to create one."
        )
        return
    text = "Topic Sessions:\n\n"
    for topic in topics:
        marker = " (current)" if topic == current else ""
        text += f"- {topic}{marker}\n"
    text += f"\nMain session{' (current)' if not current else ''}"
    await update.message.reply_text(text)


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.replace("/remind", "").strip()
    if not text:
        await update.message.reply_text(
            "Usage: /remind <time> <message>\n\n"
            "Examples:\n"
            "  /remind in 30 minutes Check the oven\n"
            "  /remind tomorrow at 9am Meeting\n"
            "  /remind at 3pm Call mom\n\n"
            "For recurring:\n"
            "  /remind daily at 9am Standup\n"
            "  /remind weekly monday at 10am Review"
        )
        return
    # Check for repeat keywords
    repeat = None
    if text.startswith("daily "):
        repeat = "daily"
        text = text[6:]
    elif text.startswith("weekly "):
        repeat = "weekly"
        text = text[7:]
    elif text.startswith("monthly "):
        repeat = "monthly"
        text = text[8:]
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
    scheduler = get_scheduler()
    if not scheduler:
        await update.message.reply_text("Scheduler not initialized.")
        return
    job = scheduler.add_job(user_id=user_id, message=message_part, run_at=time_part, repeat=repeat)
    repeat_text = f" (repeats {repeat})" if repeat else ""
    await update.message.reply_text(
        f"Reminder set for {time_part:%Y-%m-%d %H:%M}{repeat_text}\n"
        f"Message: {message_part}\nID: {job.job_id}"
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    scheduler = get_scheduler()
    if not scheduler:
        await update.message.reply_text("Scheduler not available.")
        return
    all_jobs = scheduler.get_user_jobs(user_id)
    jobs = [j for j in all_jobs if j.job_type != "command"]
    if not jobs:
        await update.message.reply_text("No reminders. Use /remind to set one.")
        return
    jobs.sort(key=lambda j: j.run_at)
    text = "Reminders:\n\n"
    for j in jobs:
        repeat_tag = f" [{j.repeat}]" if j.repeat else ""
        type_tag = f" [{j.job_type}]" if j.job_type not in ("reminder",) else ""
        text += f"- {j.run_at:%Y-%m-%d %H:%M}{repeat_tag}{type_tag}: {j.message[:50]}\n  ID: {j.job_id}\n\n"
    text += "Use /cancel <id> to remove."
    for chunk in split_message(text):
        await update.message.reply_text(chunk)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.replace("/cancel", "").strip()
    if not text:
        await update.message.reply_text("Usage: /cancel <reminder_id>")
        return
    scheduler = get_scheduler()
    if not scheduler:
        await update.message.reply_text("Scheduler not available.")
        return
    job = scheduler.get_job(text)
    if not job:
        await update.message.reply_text(f"Reminder '{text}' not found.")
        return
    if job.user_id != user_id:
        await update.message.reply_text("You can only cancel your own reminders.")
        return
    if job.job_type == "command":
        await update.message.reply_text(
            f"Job '{text}' is a system command ({job.name}). "
            f"System jobs can't be cancelled via /cancel."
        )
        return
    scheduler.remove_job(text)
    await update.message.reply_text(f"Reminder '{text}' cancelled.")


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Schedule a Claude agent task (with full tool-use)."""
    user_id = update.effective_user.id
    text = update.message.text.replace("/schedule", "").strip()
    if not text:
        await update.message.reply_text(
            "Usage: /schedule <time> | <task>\n\n"
            "Examples:\n"
            "  /schedule daily at 8am | Check disk usage and alert if >90%\n"
            "  /schedule tomorrow at 6pm | Check weather forecast\n"
            "  /schedule in 2 hours | Run backup script\n\n"
            "Runs as a full Claude session with tool access."
        )
        return
    if "|" not in text:
        await update.message.reply_text("Separate time and task with | (pipe).")
        return
    time_part, task_part = text.split("|", 1)
    time_part = time_part.strip()
    task_part = task_part.strip()
    if not task_part:
        await update.message.reply_text("Include a task description after the pipe.")
        return
    repeat = None
    if time_part.startswith("daily "):
        repeat = "daily"
        time_part = time_part[6:]
    elif time_part.startswith("weekly "):
        repeat = "weekly"
        time_part = time_part[7:]
    parsed_time = parse_natural_time(time_part)
    if not parsed_time:
        await update.message.reply_text("Couldn't understand the time.")
        return
    scheduler = get_scheduler()
    if not scheduler:
        await update.message.reply_text("Scheduler not initialized.")
        return
    name = task_part[:50] if len(task_part) <= 50 else task_part[:47] + "..."
    job = scheduler.add_agent_job(
        user_id=user_id, task=task_part, run_at=parsed_time,
        repeat=repeat, name=name, notify=True,
    )
    repeat_text = f" (repeats {repeat})" if repeat else ""
    await update.message.reply_text(
        f"Scheduled for {parsed_time:%Y-%m-%d %H:%M}{repeat_text}\n"
        f"Task: {task_part}\nID: {job.job_id}\n\n"
        f"Runs as a full Claude session with tool access."
    )


async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all scheduled jobs."""
    user_id = update.effective_user.id
    scheduler = get_scheduler()
    if not scheduler:
        await update.message.reply_text("Scheduler not initialized.")
        return
    jobs = scheduler.get_user_jobs(user_id)
    if not jobs:
        await update.message.reply_text(
            "No scheduled jobs.\n\nUse /remind for reminders\nUse /schedule for agent tasks"
        )
        return
    jobs.sort(key=lambda j: j.run_at)
    text = "Scheduled Jobs:\n\n"
    type_map = {"agent": "Agent", "command": "System", "reminder": "Reminder"}
    for job in jobs:
        repeat_text = f" [{job.repeat}]" if job.repeat else ""
        type_text = type_map.get(job.job_type, job.job_type.capitalize())
        text += f"[{type_text}] {job.run_at:%Y-%m-%d %H:%M}{repeat_text}\n"
        text += f"  {job.message[:60]}{'...' if len(job.message) > 60 else ''}\n"
        text += f"  ID: {job.job_id}\n\n"
    text += "Use /cancel <id> to remove."
    for chunk in split_message(text):
        await update.message.reply_text(chunk)


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    report = build_health_report(BOT_DIR)
    await update.message.reply_text(report)


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    await update.message.reply_text("Checking for updates...")
    try:
        result = full_update(BOT_DIR)
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Update failed: {e}")


async def system_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    import platform
    version = get_current_version(BOT_DIR)
    branch = get_current_branch(BOT_DIR)
    skills_count = len(_skill_manager.get_enabled_skills()) if _skill_manager else 0
    provider = get_llm_provider()
    if isinstance(_llm_provider, ClaudeCLIProvider):
        tool_use = "Yes (Claude CLI — native)"
    else:
        tool_use = "Yes (function calling)"
    text = (
        f"MyOldMachine System Info\n\n"
        f"Version: {version} ({branch})\n"
        f"Provider: {provider} / {get_llm_model()}\n"
        f"Tool-use: {tool_use}\n"
        f"Skills: {skills_count}\n"
        f"OS: {platform.system()} {platform.release()}\n"
        f"Python: {platform.python_version()}\n"
        f"Bot dir: {BOT_DIR}"
    )
    await update.message.reply_text(text)


async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    from utils.cleanup import run_cleanup
    report = run_cleanup()
    await update.message.reply_text(report)


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    from core.updater import restart_service
    await update.message.reply_text("Restarting in 2 seconds...")
    await asyncio.sleep(2)
    success, msg = restart_service()
    if not success:
        await update.message.reply_text(f"Restart failed: {msg}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_name = get_bot_name()
    text = (
        f"{bot_name} Commands\n\n"
        "General:\n"
        "  /start - Connect and show info\n"
        "  /help - Show this help\n"
        "  /clear - Reset conversation\n"
        "  /status - Bot status\n\n"
        "Memory:\n"
        "  /remember <fact> - Save a memory\n"
        "  /memories - Show memories\n"
        "  /forget <n> - Delete memory by number\n\n"
        "Sessions:\n"
        "  /topic <name> - Switch to topic session\n"
        "  /topics - List all topics\n\n"
        "Reminders:\n"
        "  /remind <time> <message> - Set a reminder\n"
        "  /reminders - Show reminders\n"
        "  /cancel <id> - Cancel a reminder\n"
        "  /schedule <time> | <task> - Schedule agent task\n"
        "  /jobs - Show all scheduled jobs\n\n"
        "Recovery:\n"
        "  /recover - Show interrupted task\n"
        "  /clear_recovery - Delete recovery data\n\n"
        "Admin:\n"
        "  /health - System health report\n"
        "  /cleanup - Clean old attachments and logs\n"
        "  /system - System info\n"
        "  /update - Update to latest version\n"
        "  /restart - Restart the bot\n\n"
        "Just send a message to chat. Send files for processing."
    )
    await update.message.reply_text(text)


# --- Attachment handling ---

async def download_attachments(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               group_index: int = None) -> list[tuple[Path, str]]:
    """Download all attachments and return (path, type) pairs."""
    user_id = update.effective_user.id
    attachments_dir = get_attachments_dir(user_id)
    message = update.message
    downloaded = []

    async def save_file(file_obj, file_type, ext, original_name=None, index=None):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        idx_suffix = f"_{index}" if index is not None else ""
        name = f"{ts}{idx_suffix}_{original_name}" if original_name else f"{ts}{idx_suffix}_{file_type}{ext}"
        path = attachments_dir / name
        try:
            await file_obj.download_to_drive(path)
            if path.exists() and path.stat().st_size > 0:
                logger.info(f"Downloaded {file_type} for user {user_id}: {path} ({path.stat().st_size} bytes)")
                return path
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.error(f"Download failed for {file_type}: {e}")
            if path.exists():
                path.unlink()
        return None

    if message.photo:
        f = await message.photo[-1].get_file()
        p = await save_file(f, "image", ".jpg", index=group_index)
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
    if message.video_note:
        f = await message.video_note.get_file()
        p = await save_file(f, "video_note", ".mp4")
        if p:
            downloaded.append((p, "video_note"))
    if message.sticker:
        f = await message.sticker.get_file()
        ext = ".webp" if not message.sticker.is_animated else ".tgs"
        p = await save_file(f, "sticker", ext)
        if p:
            downloaded.append((p, "sticker"))

    return downloaded


# --- Message handling ---

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
    if lock.locked():
        try:
            await updates[0].message.chat.send_message(
                "Still working on your previous request. Your message is queued."
            )
        except Exception:
            pass

    async with lock:
        all_attachments = []
        caption = ""
        for idx, upd in enumerate(updates):
            attachments = await download_attachments(upd, context, group_index=idx)
            all_attachments.extend(attachments)
            if not caption:
                caption = upd.message.text or upd.message.caption or ""

        user_message = caption
        if all_attachments:
            info = "\n\n[Attachments:]"
            for path, ftype in all_attachments:
                info += f"\n- {ftype}: {path}"
                if ftype == "image":
                    info += " (viewable with Read tool)"
            user_message = (user_message + info) if user_message else f"[Sent {len(all_attachments)} file(s)]{info}"

        if not user_message:
            return

        chat = updates[0].message.chat
        first_msg_id = updates[0].message.message_id
        save_pending_message(user_id, user_message, first_msg_id)

        try:
            await chat.send_action("typing")
        except Exception:
            pass

        response = await call_llm(user_id, user_message, chat=chat)

        _save_and_send(user_id, user_message, response, session=None, chat=chat)

        for chunk in split_message(response):
            try:
                await chat.send_message(chunk)
            except Exception as e:
                logger.error(f"Failed to send to user {user_id}: {e}")

        clear_pending_message(user_id)


async def _process_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lock = _get_user_lock(user_id)

    if lock.locked():
        await update.message.reply_text("Still working on your previous request. Your message is queued.")

    async with lock:
        # Daily reset check
        session = get_session(user_id)
        if session.should_daily_reset():
            session.perform_daily_reset()
            logger.info(f"Daily reset performed for user {user_id}")

        # Crash-loop protection
        incomplete = get_incomplete_task(user_id)
        if incomplete:
            try:
                started = datetime.fromisoformat(incomplete.get("started", ""))
                age_seconds = (datetime.now() - started).total_seconds()
                if age_seconds < 120 and incomplete.get("original_message", "")[:100] == (update.message.text or "")[:100]:
                    logger.warning(f"Crash-loop detected for user {user_id}")
                    clear_task_progress(user_id)
                    await update.message.reply_text(
                        "Previous attempt was interrupted. Retrying. "
                        "If this keeps happening, try /clear."
                    )
            except (ValueError, TypeError):
                pass

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
        save_pending_message(user_id, user_message, update.message.message_id)

        try:
            await update.message.chat.send_action("typing")
        except Exception:
            pass

        response = await call_llm(user_id, user_message, chat=update.message.chat)

        # Save to current session (topic or main) with compaction
        _save_and_send(user_id, user_message, response, session=session)

        for chunk in split_message(response):
            await update.message.reply_text(chunk)

        clear_pending_message(user_id)
        logger.info(f"Responded to {user_id}: {len(response)} chars")


# --- Main ---

def main():
    global _llm_provider, _skill_manager

    token = get_telegram_token()

    # Initialize LLM provider
    provider_name = get_llm_provider()
    model = get_llm_model()
    api_key = get_llm_api_key()
    kwargs = {}
    if provider_name == "ollama":
        kwargs["base_url"] = get_ollama_base_url()
    _llm_provider = create_provider(provider_name, model, api_key, **kwargs)
    logger.info(f"LLM provider: {_llm_provider.provider_name} / {model} "
                f"(tool-use: {_llm_provider.supports_tool_use})")

    # Wire up progress callbacks for Claude CLI
    if isinstance(_llm_provider, ClaudeCLIProvider):
        _llm_provider.on_progress_save = save_task_progress
        _llm_provider.on_progress_clear = clear_task_progress

    # Initialize skills
    _skill_manager = SkillManager(SKILLS_DIR)
    logger.info(f"Loaded {len(_skill_manager.skills)} skills")

    # Build Telegram app
    api_base = get_telegram_api_base()
    builder = Application.builder().token(token)
    if api_base:
        from telegram.request import HTTPXRequest
        request = HTTPXRequest(
            connect_timeout=30.0,
            read_timeout=300.0,
            write_timeout=300.0,
        )
        builder = (builder
                   .base_url(f"{api_base}/bot")
                   .base_file_url(f"{api_base}/file/bot")
                   .request(request)
                   .get_updates_request(HTTPXRequest(read_timeout=30.0)))
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
    app.add_handler(CommandHandler("recover", recover_command))
    app.add_handler(CommandHandler("clear_recovery", clear_recovery_command))
    app.add_handler(CommandHandler("topic", topic_command))
    app.add_handler(CommandHandler("topics", list_topics_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("cleanup", cleanup_command))
    app.add_handler(CommandHandler("system", system_command))
    app.add_handler(CommandHandler("update", update_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("help", help_command))

    # Message handler (text, photos, documents, audio, video, voice, stickers)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Document.ALL |
         filters.AUDIO | filters.VIDEO | filters.VOICE |
         filters.VIDEO_NOTE | filters.Sticker.ALL) & ~filters.COMMAND,
        handle_message,
    ))

    # Startup: initialize scheduler, recover pending messages, send first boot
    async def post_init(application):
        scheduler = init_scheduler(token, api_base)
        scheduler.set_claude_handler(call_llm)
        scheduler.start()
        logger.info("Scheduler started with Claude handler")
        await recover_pending_messages(application.bot)

        # First boot message — sent once after install
        first_boot_marker = DATA_DIR / ".first_boot_sent"
        if not first_boot_marker.exists():
            allowed = get_allowed_users()
            if allowed:
                import platform
                bot_name = get_bot_name()
                skills_count = len(_skill_manager.get_enabled_skills()) if _skill_manager else 0
                version = get_current_version(BOT_DIR)
                msg = (
                    f"{bot_name} is online.\n\n"
                    f"OS: {platform.system()} {platform.release()}\n"
                    f"Provider: {get_llm_provider()} / {get_llm_model()}\n"
                    f"Skills: {skills_count}\n"
                    f"Version: {version}\n\n"
                    f"Send /help for commands, or just send me a message."
                )
                for uid in allowed:
                    try:
                        await application.bot.send_message(chat_id=uid, text=msg)
                    except Exception as e:
                        logger.warning(f"Failed to send first boot message to {uid}: {e}")
                first_boot_marker.touch()
                logger.info("First boot message sent")

    # Shutdown: stop scheduler, wait for active processes
    async def post_shutdown(application):
        scheduler = get_scheduler()
        if scheduler:
            scheduler.stop()
        if isinstance(_llm_provider, ClaudeCLIProvider):
            await _llm_provider.graceful_shutdown()

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    logger.info(f"Starting {get_bot_name()}...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
