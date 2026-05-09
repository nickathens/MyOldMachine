#!/usr/bin/env python3
"""
MyOldMachine — LLM-Powered Telegram Bot

A self-hosted, provider-agnostic Telegram bot that turns any old machine into
a dedicated AI assistant. Supports Claude CLI (with full tool-use), Claude API,
OpenAI, Gemini, Kimi, MiniMax, Ollama, and OpenRouter.
"""

import asyncio
import functools
import glob as _glob_mod
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

from core.config import (
    BOT_DIR, DATA_DIR, USERS_DIR, SKILLS_DIR,
    get_telegram_token, get_telegram_api_base, get_allowed_users,
    get_bot_name, get_llm_provider, get_llm_model, get_llm_api_key,
    get_ollama_base_url, get_user_profile, is_admin,
    LOG_DIR,
)
from core.llm import create_provider, Message, LLMResponse, ClaudeCLIProvider, CodexCLIProvider

# Both subprocess CLI providers share progress callbacks, /stop semantics, and
# graceful shutdown. Anywhere the bot needs to gate CLI-specific behavior,
# isinstance against this tuple instead of either class alone.
_CLI_PROVIDERS = (ClaudeCLIProvider, CodexCLIProvider)
from core.tools import get_process_registry
from core.skill_loader import SkillManager
from core.session import SessionManager, clear_session_manager, get_session_manager
from core.memory import MemoryManager
from core.scheduler import init_scheduler, get_scheduler, parse_natural_time
from core.health import (
    build_health_report, run_health_check,
    init_polling_monitor, get_polling_monitor, record_polling_update,
)
from core.updater import full_update, get_current_version, get_current_branch
from core.system_probe import probe_system, get_caps_summary, load_caps
from core.message_log import log_exchange
from utils.safe_json import save_json as _atomic_save_json
from utils.env_io import atomic_env_write as _atomic_env_write

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

# Logging
from logging.handlers import RotatingFileHandler

class _TokenRedactFilter(logging.Filter):
    """Redact bot tokens from log output to prevent credential leaks."""
    import re as _re
    _pattern = _re.compile(r'\d{8,10}:[A-Za-z0-9_\-]{30,}')

    def filter(self, record):
        if record.args:
            try:
                record.msg = str(record.msg) % record.args
                record.args = None
            except (TypeError, ValueError):
                pass
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = self._pattern.sub('[REDACTED]', record.msg)
        return True

_log_handlers = [
    RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=10*1024*1024, backupCount=3),
    logging.StreamHandler(),
]
for _h in _log_handlers:
    _h.addFilter(_TokenRedactFilter())

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

# Per-user locks
_user_locks: dict[int, asyncio.Lock] = {}

# Cache conversation history from build_messages for reuse in _save_and_send,
# avoiding redundant disk reads within the same request cycle.
_conversation_cache: dict[int, list] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def requires_auth(func):
    """Gate a command handler on the allowed-users list.

    Telegram's CommandHandler dispatches before handle_message's gate runs, so
    every command handler must check authorization itself or a stranger who
    knows the bot handle can call /schedule, /remind, etc. Apply to every
    command except /start and /help (those are intentionally open so a fresh
    user can find out the bot exists and how to ask for access).
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user is None:
            return
        user_id = update.effective_user.id
        allowed = get_allowed_users()
        if not allowed or user_id not in allowed:
            try:
                await update.message.reply_text("Unauthorized.")
            except Exception:
                pass
            return
        return await func(update, context)
    return wrapper


# --- Conditional LLM semaphore (hardware-aware) ---
# On low-resource machines (RAM < 8GB or CPU cores <= 2), serializes all LLM
# calls to prevent concurrent heavy subprocesses from causing OOM/thermal crashes.
# On capable machines, this is a no-op that allows full concurrency.

class _NoOpSemaphore:
    """Async context manager that does nothing — used on capable machines."""
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass

_llm_semaphore = None  # Lazily created on first use inside event loop (Python 3.8 compat)
_semaphore_active = False  # Whether real semaphore is in use


def _get_llm_semaphore():
    """Get the LLM semaphore, creating it lazily inside the running event loop.

    Creating asyncio.Semaphore() in main() (before run_polling() starts) would
    bind it to the wrong event loop on Python 3.8-3.9. Lazy creation ensures
    it's bound to the event loop that actually runs the handlers.
    """
    global _llm_semaphore
    if _llm_semaphore is None:
        if _semaphore_active:
            _llm_semaphore = asyncio.Semaphore(1)
        else:
            _llm_semaphore = _NoOpSemaphore()
    return _llm_semaphore


async def _run_compaction_under_semaphore(prompt: str, summary_file, batch_size: int):
    """Run compaction as an async task that acquires the LLM semaphore.

    This prevents the compaction subprocess from running concurrently with
    user-facing LLM calls on low-resource machines.
    """
    from core.session import _compaction_scheduled
    sf_key = str(summary_file)
    try:
        async with _get_llm_semaphore():
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=prompt.encode()),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.error("Semaphore-aware compaction timed out")
                return

            if proc.returncode == 0 and stdout.strip():
                from utils.safe_json import save_json as _sj
                _sj(summary_file, {
                    "summary": stdout.decode(errors="replace").strip(),
                    "updated": datetime.now().isoformat(),
                    "compacted_messages": batch_size,
                })
                logger.info(f"Semaphore-aware compaction complete: {batch_size} messages summarized")
            else:
                logger.warning(f"Compaction returned no output (exit {proc.returncode})")
    except Exception as e:
        logger.error(f"Semaphore-aware compaction failed: {e}")
    finally:
        _compaction_scheduled.discard(sf_key)


async def _auto_transcribe_voice(voice_path: str) -> str:
    """Auto-transcribe a voice message using Whisper. Returns transcript or empty string."""
    venv_python = str(BOT_DIR / ".venv" / "bin" / "python")
    try:
        await asyncio.to_thread(
            subprocess.run,
            [venv_python, "-m", "whisper", voice_path,
             "--model", "base", "--output_format", "txt",
             "--output_dir", str(Path(voice_path).parent)],
            capture_output=True, text=True, timeout=120,
        )
        txt_file = Path(voice_path).with_suffix(".txt")
        if txt_file.exists():
            transcript = txt_file.read_text(encoding="utf-8").strip()
            txt_file.unlink(missing_ok=True)
            if transcript:
                logger.info(f"Auto-transcribed voice ({len(transcript)} chars)")
                return transcript
    except FileNotFoundError:
        logger.debug("Whisper not available for auto-transcription")
    except subprocess.TimeoutExpired:
        logger.warning("Voice auto-transcription timed out")
    except Exception as e:
        logger.warning(f"Voice auto-transcription failed: {e}")
    return ""


def _extract_pdf_text(file_path: str, max_chars: int = 50000, max_ocr_pages: int = 5) -> str:
    """Extract text from a PDF file. Falls back to OCR for scanned PDFs.

    Pre-extracts text so the LLM doesn't need to use the Read tool on PDFs,
    which can consume excessive memory (rendering pages as images) and crash
    resource-constrained machines.
    """
    try:
        # Try pypdf first (used by the pdf skill), then PyPDF2 as fallback
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader
            except ImportError:
                return "[PDF text extraction unavailable — install pypdf: pip install pypdf]"

        text_parts = []
        total_chars = 0
        with open(file_path, 'rb') as f:
            reader = PdfReader(f)
            num_pages = len(reader.pages)
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                if total_chars + len(page_text) > max_chars:
                    remaining = max_chars - total_chars
                    if remaining > 0:
                        text_parts.append(f"--- Page {i+1}/{num_pages} ---")
                        text_parts.append(page_text[:remaining])
                    text_parts.append(f"\n[... truncated at {max_chars} chars, {num_pages - i - 1} pages remaining ...]")
                    break
                text_parts.append(f"--- Page {i+1}/{num_pages} ---")
                text_parts.append(page_text)
                total_chars += len(page_text)

        extracted = "\n".join(text_parts)

        # If very little text extracted, it's likely a scanned PDF — try OCR
        if total_chars < 100 and num_pages > 0:
            ocr_text = _ocr_pdf(file_path, num_pages, max_ocr_pages, max_chars)
            if ocr_text:
                return f"[Scanned PDF — {num_pages} pages, OCR extracted from first {min(num_pages, max_ocr_pages)}:]\n{ocr_text}"
            return f"[Scanned PDF — {num_pages} pages, no readable text extracted.]"

        return extracted
    except Exception as e:
        return f"[PDF text extraction failed: {e}]"


def _ocr_pdf(file_path: str, num_pages: int, max_pages: int, max_chars: int) -> str:
    """OCR a scanned PDF using pdftoppm + tesseract."""
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        return ""
    try:
        pages_to_ocr = min(num_pages, max_pages)
        text_parts = []
        total_chars = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["pdftoppm", "-png", "-r", "200", "-l", str(pages_to_ocr),
                 file_path, os.path.join(tmpdir, "page")],
                capture_output=True, timeout=60
            )
            if result.returncode != 0:
                return ""

            page_images = sorted(_glob_mod.glob(os.path.join(tmpdir, "page-*.png")))
            for i, img_path in enumerate(page_images):
                ocr_result = subprocess.run(
                    ["tesseract", img_path, "-"],
                    capture_output=True, text=True, timeout=30
                )
                page_text = ocr_result.stdout.strip() if ocr_result.returncode == 0 else ""
                if total_chars + len(page_text) > max_chars:
                    remaining = max_chars - total_chars
                    if remaining > 0:
                        text_parts.append(f"--- Page {i+1}/{num_pages} (OCR) ---")
                        text_parts.append(page_text[:remaining])
                    text_parts.append(f"\n[... OCR truncated at {max_chars} chars ...]")
                    break
                text_parts.append(f"--- Page {i+1}/{num_pages} (OCR) ---")
                text_parts.append(page_text)
                total_chars += len(page_text)

        return "\n".join(text_parts) if text_parts else ""
    except Exception as e:
        logger.warning(f"PDF OCR failed: {e}")
        return ""


# Duplicate message protection
_processed_ids: set[int] = set()
_MAX_PROCESSED = 200

# Media group buffering
_media_group_buffers: dict[str, dict] = {}
_MEDIA_GROUP_WAIT = 1.5

# Globals initialized in main()
_llm_provider = None
_skill_manager = None
_memory_manager = None

MAX_CONTEXT_MESSAGES = 40

# Prompt size budget (characters). When system prompt + conversation history exceeds
# this, older messages are trimmed. Conservative value that fits all providers —
# Claude CLI (200k), OpenAI (128k), Gemini (1M), DeepSeek (128k), Ollama (varies).
# ~20k tokens leaves room for the model's response and internal overhead.
PROMPT_CHAR_BUDGET = 80000

# Health check interval (seconds)
_HEALTH_CHECK_INTERVAL = 4 * 3600  # 4 hours
_last_health_check: float | None = None  # None = not yet checked


# --- Alias helpers ---

def _get_aliases_file(user_id: int) -> Path:
    return get_user_dir(user_id) / "aliases.json"


def _load_aliases(user_id: int) -> dict[str, str]:
    """Load user's command aliases. Returns {name: expansion}."""
    path = _get_aliases_file(user_id)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, UnicodeDecodeError):
        return {}


def _save_aliases(user_id: int, aliases: dict[str, str]):
    """Save user's command aliases."""
    path = _get_aliases_file(user_id)
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(aliases, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(path)
    except Exception as e:
        logger.warning(f"Failed to save aliases for {user_id}: {e}")
        tmp.unlink(missing_ok=True)


# Built-in command names that cannot be used as aliases.
# Must include every name passed to CommandHandler() in main(). If a builtin
# name isn't listed here, the alias system will silently accept an alias of
# that name and never fire (the real CommandHandler shadows it). Keep this
# in sync with the registrations at the bottom of main().
_RESERVED_COMMANDS = {
    "start", "help", "clear", "status",
    "remember", "memories", "forget",
    "remind", "reminders", "cancel",
    "stop", "recover", "clear_recovery",
    "topic", "topics",
    "schedule", "jobs",
    "health", "cleanup", "system", "update", "restart",
    "provider", "model", "apikey",
    "alias",
    "adduser", "removeuser", "users", "purgeuser",
    "maintenance", "skillstats",
}


# --- User data helpers ---

def get_user_dir(user_id: int) -> Path:
    """Resolve the data dir for a Telegram user.

    Multi-user mode: returns data/users/userN/ where N is the slot bound to
    this Telegram ID. The dir is owned by mom_userN with the orchestrator
    in the group, so both the bot and the slot's CLI can read/write it.
    Slot dirs are provisioned at install time. We do NOT auto-create them
    here, because mkdir() from the orchestrator would create an
    orchestrator-owned dir and break the privacy model.

    Single-user mode (or unbound IDs): keeps the legacy data/users/<id>/
    layout to preserve backwards compatibility with existing installs. We
    DO auto-create that dir since it has no privileged ownership.
    """
    multiuser = False
    try:
        from core.users import resolve_user_dir, is_multiuser_enabled, lookup_slot
        d = resolve_user_dir(user_id)
        # Only treat this as a slot dir if the user is actually bound to one.
        # Unbound users in multi-user mode fall through to legacy TID path.
        multiuser = is_multiuser_enabled() and lookup_slot(user_id) is not None
    except ImportError:
        d = USERS_DIR / str(user_id)
    if not multiuser:
        d.mkdir(parents=True, exist_ok=True)
    return d


def get_attachments_dir(user_id: int) -> Path:
    d = get_user_dir(user_id) / "attachments"
    # parents=False: in multi-user mode, the slot dir is provisioned at
    # install with privileged ownership we can't replicate. If the slot
    # dir is missing, fail loudly here rather than silently creating an
    # orchestrator-owned dir that breaks the privacy model.
    d.mkdir(exist_ok=True)
    return d


def get_session(user_id: int) -> SessionManager:
    session = get_session_manager(user_id, get_user_dir(user_id))
    if _semaphore_active:
        session.set_compaction_runner(_run_compaction_under_semaphore)
    return session


# --- Task progress tracking (crash recovery) ---

def get_progress_file(user_id: int) -> Path:
    return get_user_dir(user_id) / "task_in_progress.json"


def save_task_progress(user_id: int, original_message: str, partial_text: str,
                       status: str, tool: str = None):
    """Save current task progress for recovery after restart."""
    progress_file = get_progress_file(user_id)
    started = datetime.now().isoformat()
    if progress_file.exists():
        try:
            with open(progress_file, encoding="utf-8") as f:
                existing = json.load(f)
                started = existing.get("started", started)
        except Exception:
            pass
    data = {
        "user_id": user_id,
        "original_message": original_message[:500],
        "partial_text": partial_text[-20480:] if len(partial_text) > 20480 else partial_text,
        "status": status,
        "current_tool": tool,
        "started": started,
        "updated": datetime.now().isoformat(),
    }
    # Atomic write to prevent corruption on crash
    tmp = progress_file.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(progress_file)
    except Exception as e:
        logger.warning(f"Failed to save task progress for {user_id}: {e}")
        tmp.unlink(missing_ok=True)


def clear_task_progress(user_id: int):
    progress_file = get_progress_file(user_id)
    if progress_file.exists():
        progress_file.unlink()


def get_incomplete_task(user_id: int) -> dict | None:
    progress_file = get_progress_file(user_id)
    if progress_file.exists():
        try:
            with open(progress_file, encoding="utf-8") as f:
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
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
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
            with open(pending_file, encoding="utf-8") as f:
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

def build_orientation_prompt(user_id: int, first_user_message: str = None) -> str:
    """Build the first-contact orientation prompt.

    Fires once per user, on their first message ever. If first_user_message is
    provided, the prompt asks the bot to introduce itself, address what the user
    just said, and end with the get-to-know-you questions — all in one reply.
    If first_user_message is None, returns the legacy "intro only" form, used
    by the intro reflection pass to give the LLM canonical seed questions for
    context.
    """
    bot_name = get_bot_name()
    caps_summary = get_caps_summary(DATA_DIR)
    caps = load_caps(DATA_DIR)

    # Build skill readiness report
    skills_report = ""
    if caps:
        skill_status = caps.get("skills", {})
        ready = [name for name, info in skill_status.items() if info.get("ready")]
        not_ready = {name: info.get("missing", [])
                     for name, info in skill_status.items() if not info.get("ready")}

        if ready:
            skills_report += f"\nSkills ready to use ({len(ready)}): {', '.join(sorted(ready))}"
        if not_ready:
            skills_report += f"\n\nSkills that need setup ({len(not_ready)}):"
            for name, missing in sorted(not_ready.items()):
                skills_report += f"\n  - {name}: missing {', '.join(missing)}"

    if first_user_message:
        # Live intro turn — combine intro with answering the user's actual message
        header = (
            f"This is the VERY FIRST message this user has ever sent to you. "
            f"They have not met you yet.\n\n"
            f"Your name is {bot_name}. You are their personal AI assistant that lives "
            f"on this computer and can do things for them: manage files, edit media, "
            f"set reminders, and much more.\n\n"
            f"## What the user just said (their first message)\n"
            f"{first_user_message[:1500]}\n"
        )
        task_intro = "## YOUR TASK\nReply to them. Your reply must do THREE things, blended naturally:"
        task_answer = (
            "3. ADDRESS what they just said (their message above). Take care of "
            "their request, or if you need more context, ask the minimum to proceed.\n\n"
        )
        task_questions = (
            "4. END with two gentle questions to learn about them (not a form):\n"
            "   - What they would like to be called\n"
            "   - What they are most interested in using you for\n\n"
        )
    else:
        header = (
            f"This is your FIRST conversation with this user. You just got installed on their machine.\n\n"
            f"Your name is {bot_name}. You are their personal AI assistant that lives on this computer "
            f"and can do things for them: manage files, edit media, set reminders, and much more.\n"
        )
        task_intro = "YOUR TASK for this first message:"
        task_answer = ""
        task_questions = (
            "3. ASK the user about themselves. You want to learn:\n"
            "   - What they would like to call themselves (or confirm their Telegram name)\n"
            "   - What they are most interested in using you for\n"
            "   - Any specific tasks they have in mind right now\n\n"
        )

    return (
        f"{header}\n"
        f"Here is what you know about this machine:\n{caps_summary}\n"
        f"{skills_report}\n\n"
        f"{task_intro}\n\n"
        "1. INTRODUCE yourself warmly but concisely. Explain in simple, non-technical "
        "language what you are and what you can do. Do NOT list every skill: give a "
        "high-level overview grouped by category (for example 'I can work with photos "
        "and videos', 'I can help with music and audio', 'I can manage your files and "
        "schedule').\n\n"
        "2. REPORT the machine status in plain language. Not raw specs: translate them. "
        "For example, instead of '15.5 GB RAM', say 'Your computer has plenty of memory'. "
        "Instead of listing missing binaries, say which categories of skills are ready and "
        "which ones would need a quick setup (and offer to do it for them).\n\n"
        f"{task_answer}"
        f"{task_questions}"
        "Keep your message friendly, clear, and under 300 words. "
        "Do NOT use technical jargon, file paths, command names, or code. "
        "Do NOT overwhelm them with information. Make them feel like they just got "
        "a capable, approachable assistant, not a terminal window.\n\n"
        "Remember: this user may have ZERO experience with computers beyond basic use. "
        "Meet them where they are."
    )


def build_system_prompt(user_id: int) -> str:
    """Build the system prompt with user context, skills, memories, and instructions.

    All providers now have tool-use capability (either native via Claude CLI,
    or through our function-calling execution layer for API providers).
    """
    profile = get_user_profile(user_id)
    user_name = profile.get("name", "User")
    user_role = profile.get("role", "user")
    blocked_skills = profile.get("blocked_skills", [])
    is_cli_provider = isinstance(_llm_provider, _CLI_PROVIDERS)
    has_tool_use = _llm_provider.supports_tool_use if _llm_provider else False

    parts = []

    # Bot identity
    bot_name = get_bot_name()
    if has_tool_use:
        parts.append(f"You are {bot_name}, an AI assistant that controls this machine.")
        parts.append("You have full access to the operating system through tool calls. "
                     "You can run commands, read and write files, install software, "
                     "manage files, and configure services.")
        if not is_cli_provider:
            parts.append(
                "You have 5 tools available:\n"
                "  - run_command: Execute shell commands. Set background=true for long-running "
                "commands (installs, builds, downloads) — returns a process_id.\n"
                "  - read_file: Read file contents.\n"
                "  - write_file: Create/overwrite files. Content is validated against file extension.\n"
                "  - list_directory: List files in a directory.\n"
                "  - check_process: Poll or kill background processes by process_id.\n\n"
                "CRITICAL INSTRUCTIONS FOR TOOL USE:\n"
                "You MUST use the function calling / tool_calls mechanism to invoke tools.\n"
                "NEVER write commands as code blocks or text. NEVER output ```bash\\n...``` blocks "
                "with commands for the user to run. You have REAL tools — use them.\n"
                "When the user asks you to do something on the machine, CALL the tool directly.\n\n"
                "WRONG (do NOT do this):\n"
                "  ```bash\\n  python scheduler_cli.py add ...\\n  ```\n\n"
                "RIGHT (do this instead):\n"
                "  Call run_command with command=\"python scheduler_cli.py add ...\"\n\n"
                "If you want to check a file, call read_file. If you want to create a file, call write_file.\n"
                "If you want to run ANY command, call run_command. ALWAYS use the tools.\n"
                "For long-running commands (apt install, pip install, builds, downloads), "
                "use run_command with background=true, then poll with check_process."
            )
        parts.append("If the user asks for something and you're missing a tool, install it.")

        # MCP server tools (if any connected)
        from core.mcp_client import get_mcp_manager
        mcp = get_mcp_manager()
        mcp_tools = mcp.get_tools()
        if mcp_tools:
            parts.append("")
            parts.append("### MCP Server Tools:")
            parts.append("These additional tools are available from connected MCP servers:")
            for t in mcp_tools:
                parts.append(f"  - {t.name} [{t.server_name}]: {t.description}")
            if not is_cli_provider:
                parts.append("Call MCP tools just like built-in tools — they appear in the tool list.")
        parts.append("")
        parts.append("### Communication Style:")
        parts.append(
            "IMPORTANT: Assume the user has NO experience with code, terminals, or technical concepts. "
            "Always explain things in plain, everyday language. Never use jargon without explaining it. "
            "Never show raw file paths, command-line syntax, or code snippets in your responses "
            "unless the user specifically asks for technical details.\n\n"
            "Instead of technical language, use simple descriptions:\n"
            "  - Instead of 'I'll run ffmpeg to transcode', say 'I'll convert the video for you'\n"
            "  - Instead of 'pip install failed', say 'I tried to add a tool but it didn't work'\n"
            "  - Instead of 'check the logs at /var/log/', say 'I'll look into what went wrong'\n"
            "  - Instead of listing file paths, describe what you did in plain words\n\n"
            "If the user demonstrates technical knowledge through their messages, you can gradually "
            "match their level. But start simple. When in doubt, explain more, not less."
        )
        parts.append("")
        parts.append("### Software Compatibility:")
        parts.append("Some apps may be installed via Flatpak instead of the system package manager.")
        parts.append("Flatpak apps are sandboxed and version-independent — they work on any Linux version.")
        parts.append("To run a Flatpak app: flatpak run <app_id>  (e.g. flatpak run org.blender.Blender)")
        parts.append("To check installed Flatpak apps: flatpak list --app")
        parts.append("If a skill's binary isn't in PATH but is installed via Flatpak, use the flatpak run command.")
        parts.append("")
        parts.append("### Machine Safety Awareness:")
        parts.append(
            "This bot runs on someone's personal machine — possibly old, fragile, or their only computer. "
            "You should always be aware of hardware safety. The user is in control and can install "
            "whatever they want, but you must make sure they understand the risks first.\n\n"
            "**Warn before risky operations.** If a command or package could stress hardware, "
            "damage data, or destabilize the system, explain the risk clearly and ask the user "
            "to confirm before proceeding. The system will flag risky commands automatically — "
            "when you see a ⚠️ HARDWARE WARNING in tool output, relay it to the user.\n\n"
            "**Examples of things that need a warning:**\n"
            "- Hardware monitoring tools (sysstat, dstat) — can stress old disks/CPUs with aggressive polling\n"
            "- Stress testing tools (stress, stress-ng, cpuburn, prime95) — can overheat or crash old hardware\n"
            "- Disk benchmarking (fio, bonnie++, iozone) — aggressive I/O on failing drives can kill them\n"
            "- Kernel module manipulation (modprobe, insmod) — can crash unstable systems\n"
            "- Disk partitioning (fdisk, parted, gdisk) — can destroy data if used incorrectly\n"
            "- Firmware tools (flashrom, fwupd) — can permanently brick hardware\n\n"
            "**Warn before system-level changes.** Before installing system packages, modifying "
            "services (systemctl enable/disable), changing boot configuration, modifying "
            "network settings, or adding systemd timers, explain what you're about to do "
            "and why. If there's any risk of instability, say so explicitly.\n\n"
            "**Prefer lightweight alternatives when possible.** For system info, built-in "
            "commands (free, df, uptime, lscpu, cat /proc/meminfo) are safe and already available. "
            "Suggest these first, but install packages if the user wants them.\n\n"
            "**When in doubt, ask.** If you're unsure whether an action could harm the machine, "
            "explain what you'd do and let the user decide. It's their machine — they have the "
            "final say. Your job is to make sure they're informed."
        )
        parts.append("")
        parts.append("### Bot Self-Protection (CRITICAL):")
        parts.append(
            "NEVER modify the bot's own Python virtual environment (.venv directory), "
            "bot.py, core/ directory, or .env file. These are the bot's own runtime files. "
            "Deleting or recreating the .venv will crash the running bot immediately. "
            "If you need to install Python packages for a skill or task, install them in a "
            "SEPARATE virtual environment (e.g., in the user's home directory or project directory), "
            "not in the bot's .venv. The bot manages its own dependencies internally."
        )
        parts.append("")
    else:
        parts.append(f"You are {bot_name}, an AI assistant.")
        parts.append("You are a text-only assistant. You CANNOT run commands or access the filesystem. "
                     "Do NOT write out shell commands or code blocks pretending to execute them. "
                     "Just have a helpful conversation.")
    parts.append(f"The user's name is {user_name}. Their role is: {user_role}.")
    parts.append(f"User's Telegram ID: {user_id}")
    parts.append("")

    # Only show sudo info to Claude CLI (which already has full access via
    # --dangerously-skip-permissions).  API providers — especially weak/free
    # models — are prompt-injection targets and should not know the sudo path.
    if has_tool_use and user_role == "admin" and is_cli_provider:
        parts.append("Sudo password is stored at ~/.sudo_pass — use it for privileged commands.")
        parts.append("")

    # Tool-use-only sections (skip for text-only providers)
    if has_tool_use:
        # Use the venv Python for bot utility scripts — they need packages
        # from the venv (httpx, apscheduler, etc.).
        venv_python = str(BOT_DIR / ".venv" / "bin" / "python")

        # Telegram capabilities
        parts.append("### Sending Files to User:")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --photo /path/to/image.png")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --video /path/to/video.mp4")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/send_to_telegram.py --user {user_id} --document /path/to/file.pdf")
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
        parts.append(f"  {venv_python} {BOT_DIR}/utils/scheduler_cli.py add --user {user_id} --at \"YYYY-MM-DD HH:MM\" --message \"text\"")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/scheduler_cli.py add --user {user_id} --at \"YYYY-MM-DD HH:MM\" --message \"text\" --repeat daily --until \"YYYY-MM-DD HH:MM\"")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/scheduler_cli.py add --user {user_id} --at \"in 30 minutes\" --message \"text\"")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/scheduler_cli.py update --id <job_id> --user {user_id} --message \"new text\"")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/scheduler_cli.py list --user {user_id}")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/scheduler_cli.py remove --id <job_id> --user {user_id}")
        parts.append("NEVER use crontab. The scheduler handles everything.")
        parts.append("")
        parts.append("### Scheduler Protocol (MANDATORY RULES):")
        parts.append("1. **Deadline-related recurring reminders MUST use --until.** If a user says 'remind me every day until March 17', the --until flag is REQUIRED. Never create a repeating reminder for a countdown without an end date.")
        parts.append("2. **To reword a reminder, use `update`, NEVER delete+recreate.** The `update` subcommand changes the message text while preserving the job ID, trigger, and recurrence. Delete+recreate risks losing the reminder if the recreate step fails.")
        parts.append("3. **Always verify after creating.** Run `list` after adding a reminder to confirm it was stored correctly.")
        parts.append("4. **Never remove a recurring job unless the user explicitly asks to cancel it.** Modifying text is not a reason to remove a job.")
        parts.append("")

        # Maintenance system
        parts.append("### Maintenance:")
        parts.append("The bot runs nightly maintenance automatically (system updates at 4:00 AM, cleanup at 3:30 AM).")
        parts.append("If the user wants automatic backups, they need to set a target path:")
        parts.append("  /maintenance backup /path/to/drive")
        parts.append("Other commands: /maintenance updates on|off, /maintenance cleanup on|off, /maintenance run backup|update|cleanup")
        parts.append("")

        # Memory system
        memory_dir = DATA_DIR / "memory"
        parts.append("### Memory System:")
        parts.append("Use memory proactively, not just when asked.")
        parts.append("")
        parts.append("**Projects:**")
        parts.append(f"  {venv_python} {BOT_DIR}/utils/project_manager.py create \"Name\" \"Summary\" \"/path\"")
        parts.append(f"  Project state: {memory_dir}/projects/<slug>/state.json")
        parts.append("")
        parts.append("**Topic Memories:**")
        parts.append(f"  Write domain knowledge to {memory_dir}/topics/<topic>.md")
        parts.append("")
        parts.append("**Decision Logs:**")
        parts.append(f"  Log significant decisions to {memory_dir}/decisions/YYYY-MM-DD_description.md")
        parts.append("  Include: what was decided, options considered, rationale")
        parts.append("")

    # Memory dir used by project/topic listing below
    memory_dir = DATA_DIR / "memory"

    # Custom instructions file (cap at 8000 chars to prevent context bloat)
    instructions_file = DATA_DIR / "instructions.md"
    if instructions_file.exists():
        parts.append("### Custom Instructions:")
        instructions_text = instructions_file.read_text(encoding="utf-8")
        if len(instructions_text) > 8000:
            instructions_text = instructions_text[:7500] + "\n\n[... instructions truncated for context management]"
        parts.append(instructions_text)
        parts.append("")

    # CLAUDE.md and SYSTEM-CONTEXT.md — only for tool-use providers
    # Cap at 15000 chars each to prevent context bloat on small-context models
    if has_tool_use:
        claude_md = Path.home() / "CLAUDE.md"
        if claude_md.exists():
            parts.append("### Global Instructions (CLAUDE.md):")
            claude_md_text = claude_md.read_text(encoding="utf-8")
            if len(claude_md_text) > 15000:
                claude_md_text = claude_md_text[:14000] + "\n\n[... CLAUDE.md truncated for context management]"
            parts.append(claude_md_text)
            parts.append("")

        system_context = Path.home() / "SYSTEM-CONTEXT.md"
        if system_context.exists() and user_role == "admin":
            parts.append("### System Context:")
            ctx_text = system_context.read_text(encoding="utf-8")
            if len(ctx_text) > 10000:
                ctx_text = ctx_text[:9000] + "\n\n[... system context truncated]"
            parts.append(ctx_text)
            parts.append("")

    # Active projects from memory (cap at 8 to prevent context bloat)
    projects_dir = memory_dir / "projects"
    if projects_dir.exists():
        project_lines = []
        project_count = 0
        for pdir in sorted(projects_dir.iterdir()):
            if not pdir.is_dir():
                continue
            state_file = pdir / "state.json"
            if not state_file.exists():
                continue
            try:
                with open(state_file, encoding="utf-8") as f:
                    state = json.load(f)
                if state.get("status") != "in_progress":
                    continue
                if project_count >= 8:
                    break
                project_count += 1
                block = []
                block.append(f"\n**{state.get('name', 'Unknown')}**")
                block.append(f"  Location: {state.get('location', 'unknown')}")
                if state.get("summary"):
                    block.append(f"  Summary: {state['summary']}")
                if state.get("next_steps"):
                    for step in state["next_steps"][:3]:
                        block.append(f"  - {step}")
                block_text = "\n".join(block)
                # Cap per-project block at 1500 chars
                if len(block_text) > 1500:
                    block_text = block_text[:1400] + "\n  [... truncated]"
                project_lines.append(block_text)
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

    # Conversation summary (from compaction) — cap to prevent bloat
    session = get_session(user_id)
    summary = session.load_summary()
    if summary:
        # Sanitize role markers that the compaction LLM may have included
        summary = sanitize_role_markers(summary)
        if len(summary) > 3000:
            summary = summary[:2800] + "\n\n[... summary truncated for context management]"
        # Continuation injection: frame the summary as a session continuation
        # so the LLM resumes naturally instead of recapping or asking about context
        parts.append("### Session Continuation:")
        parts.append(
            "This session is being continued from a previous conversation that ran "
            "out of context. The summary below covers the earlier portion of the "
            "conversation."
        )
        parts.append("")
        parts.append(
            "**WARNING: Tool results from the previous session are GONE.** "
            "The summary below captures key decisions and state, but any tool "
            "outputs (web scrapes, file reads, command results) from the prior "
            "session are permanently lost. If the summary says research was "
            "'attempted' or 'in progress', treat it as NOT DONE. If you cannot "
            "find the actual delivered content in your visible context right now, "
            "it does not exist. Never claim prior work was completed unless you "
            "can quote the specific results."
        )
        parts.append("")
        parts.append(summary)
        parts.append("")
        parts.append("Recent messages are preserved verbatim below.")
        parts.append("")

    # Persistent memories
    memories = session.load_memories()
    if memories:
        parts.append("### Persistent Memories:")
        for mem in memories:
            parts.append(f"- {mem['content']}")
        parts.append("")

    # Deep memory system — person models and observations
    if _memory_manager:
        # Determine if we're in full mode (strong model) or lite mode
        provider = get_llm_provider()
        full_mode = provider in ("claude", "claude-cli", "claude-api", "openai", "deepseek", "grok", "kimi", "minimax", "gemini", "google")
        # Ollama: full mode only if running a large model
        if provider == "ollama":
            model_name = get_llm_model().lower()
            full_mode = any(size in model_name for size in ("70b", "72b", "34b", "32b", "405b"))

        memory_ctx = _memory_manager.build_memory_context(user_id, full_mode=full_mode)
        if memory_ctx:
            parts.append(memory_ctx)

        # Observation instructions — only for tool-use providers
        if has_tool_use:
            venv_python = str(BOT_DIR / ".venv" / "bin" / "python")
            parts.append(_memory_manager.build_observation_instructions(
                user_id, venv_python, BOT_DIR
            ))
            parts.append("")

    # MemPalace per-user permanent conversation memory (if provisioned)
    if has_tool_use:
        mempalace_python = BOT_DIR / "data" / "mempalace" / "venv" / "bin" / "python"
        user_palace = get_user_dir(user_id) / "mempalace" / "palace"
        if mempalace_python.exists() and user_palace.exists():
            mp_py = str(mempalace_python)
            mp_search = str(SKILLS_DIR / "mempalace" / "scripts" / "mempalace_search.py")
            mp_user_dir = str(get_user_dir(user_id))
            parts.append("### Conversation Memory Search (MemPalace):")
            parts.append(
                "Semantic search over your full conversation history with this user. "
                "The palace is private to this user -- never accesses anyone else's data. "
                "Use ONLY when the user says 'remember', 'recall', 'what did we discuss about', "
                "or when you cannot find something the user references in structured memory."
            )
            parts.append(f"  {mp_py} {mp_search} \"query\" --user-dir {mp_user_dir}")
            parts.append(f"  {mp_py} {mp_search} \"query\" --user-dir {mp_user_dir} --results 10")
            parts.append("Do NOT use for general queries or when the answer is in current context.")
            parts.append("")

    # Skills — only relevant for providers that can execute tools
    if has_tool_use and _skill_manager:
        # Load system caps for resource-aware skill annotations
        caps = load_caps(DATA_DIR)
        # Per-user overlay: applies any forked skills, visibility overrides,
        # and pinned notes the user has set. Falls through to shared skills
        # for everything else.
        from core.skill_overlay import build_user_context
        skills_ctx = build_user_context(
            _skill_manager,
            get_user_dir(user_id),
            exclude=blocked_skills,
            ram_gb=caps.get("ram_gb", 0),
            disk_free_gb=caps.get("disk_free_gb", 0),
        )
        if skills_ctx:
            parts.append(skills_ctx)

        # Per-user skill customization tools / hints
        user_dir = get_user_dir(user_id)
        if is_cli_provider:
            # CLI providers don't have function-calling tools; they edit files
            # via Bash. Tell them where the per-user overlay and settings live.
            parts.append(
                "### Per-user skill customization (your private overlay):\n"
                f"  Overlay dir:  {user_dir / 'skills'}\n"
                f"  Settings file: {user_dir / 'skill_settings.json'}\n"
                "Three things you can do for this user without affecting others:\n"
                "  1. Hide / abbreviate a skill: edit skill_settings.json, set "
                "{'skillOverrides': {'<skill>': 'off' | 'name-only' | 'on'}}.\n"
                "  2. Pin a short note (max 500 chars): edit skill_settings.json, set "
                "{'notes': {'<skill>': '...'}}. The note is appended whenever the "
                "skill appears in your context.\n"
                "  3. Fork a skill: copy the entire shared skill dir into the overlay, "
                "then edit your copy freely. The shared one is untouched. Use "
                "the helper script: python utils/skill_prefs_cli.py --user-dir "
                f"{user_dir} fork <skill> | unfork <skill>.\n"
                "Only act on these when the user explicitly asks for them. Do not "
                "fork a skill speculatively."
            )
        else:
            # API providers can call set_skill_override / set_skill_note /
            # clear_skill_note / fork_skill / unfork_skill directly.
            parts.append(
                "### Per-user skill customization tools:\n"
                "When the user asks to hide a skill, pin a preference, or fork a "
                "skill for personal edits, use these tools (they affect only this "
                "user, never others):\n"
                "  - set_skill_override(skill, state)  states: on | off | name-only\n"
                "  - set_skill_note(skill, note)      pinned text appended in context\n"
                "  - clear_skill_note(skill)          removes a pinned note\n"
                "  - fork_skill(skill)                copy shared skill into private overlay\n"
                "  - unfork_skill(skill)              delete fork, revert to shared\n"
                "Don't use these speculatively — only when explicitly asked or when "
                "the user states a clear preference about how a skill should behave."
            )

    # User aliases
    aliases = _load_aliases(user_id)
    if aliases:
        parts.append("### User Shortcuts:")
        for name, expansion in sorted(aliases.items()):
            parts.append(f"  /{name} → {expansion}")
        parts.append("")

    # Continuation injection closing: tell the LLM to resume seamlessly
    if summary:
        parts.append(
            "Resume the conversation naturally from where it left off. "
            "Do not acknowledge the summary, do not recap, respond only "
            "to the user's latest message.\n\n"
            "ANTI-CONFABULATION RULE: NEVER say 'as shown above', 'analysis is "
            "complete above', 'already got everything', or reference prior results "
            "unless you can see the actual content in your current context. If the "
            "summary mentions work that was attempted or in progress, assume the "
            "results are LOST and you must redo the work from scratch."
        )

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

    # Cache for _save_and_send to avoid redundant disk read
    _conversation_cache[user_id] = history

    if len(history) > MAX_CONTEXT_MESSAGES:
        history = history[-MAX_CONTEXT_MESSAGES:]

    messages = []
    for msg in history:
        role = msg.get("role")
        content = msg.get("content")
        if role and content is not None:
            messages.append(Message(role=role, content=content))
    messages.append(Message(role="user", content=new_message))
    return messages


def sanitize_role_markers(content: str) -> str:
    """Neutralize role markers in summaries to prevent hallucinated conversation turns.

    Lighter than sanitize_response — doesn't truncate at "Assistant:", just
    escapes markers so they don't look like conversation structure.
    """
    content = re.sub(r'\bHuman:', '[Human]:', content)
    content = re.sub(r'\bUSER:', '[USER]:', content)
    content = re.sub(r'\bUser:', '[User]:', content)
    content = re.sub(r'<user>', '[user-tag]', content, flags=re.IGNORECASE)
    content = re.sub(r'</user>', '[/user-tag]', content, flags=re.IGNORECASE)
    content = re.sub(r'<human>', '[human-tag]', content, flags=re.IGNORECASE)
    content = re.sub(r'</human>', '[/human-tag]', content, flags=re.IGNORECASE)
    content = re.sub(r'<assistant>', '[assistant-tag]', content, flags=re.IGNORECASE)
    content = re.sub(r'</assistant>', '[/assistant-tag]', content, flags=re.IGNORECASE)
    return content


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


# Patterns that indicate confabulated references to non-existent prior work.
# These catch the specific failure mode where the model claims results exist
# from a compressed/lost session. Only applied to SHORT responses (under 500 chars)
# to avoid false positives in legitimate long responses.
_CONFABULATION_PATTERNS = [
    re.compile(r"\balready\b.{0,30}\b(?:got|have|had|obtained|gathered)\b.{0,30}\b(?:everything|all|data|results|info)\b.{0,20}\b(?:needed|from|we need)", re.IGNORECASE),
    re.compile(r"\b(?:analysis|results?|research|findings?)\b.{0,20}\b(?:is|are)\b.{0,10}\bcomplete\b.{0,10}\babove\b", re.IGNORECASE),
    re.compile(r"\bas (?:shown|detailed|presented|discussed|mentioned|noted|covered) above\b", re.IGNORECASE),
    re.compile(r"\balready (?:did|done|completed|finished|covered|handled|went through)\b.{0,20}\bthat\b", re.IGNORECASE),
]


def _is_confabulated_response(text: str) -> bool:
    """Detect responses that reference non-existent prior work.

    Only flags SHORT responses (under 500 chars) that match confabulation patterns.
    Long responses are unlikely to be pure confabulation -- they contain actual content.
    """
    if len(text) > 500:
        return False
    return any(p.search(text) for p in _CONFABULATION_PATTERNS)


async def call_llm(user_id: int, message: str, chat=None, images: list = None) -> str:
    """Call the configured LLM provider and return the response text.

    Args:
        images: Optional list of file paths to images for multimodal vision.
    """
    # Fast-fail guard: if the most recent health-check (run at startup or
    # after /provider, /model, /apikey) reported the provider as broken,
    # skip the LLM call and return an actionable error instead of crashing
    # in subprocess / HTTP code on every message.
    if _llm_provider is not None and _llm_provider.last_health is not None:
        healthy, reason = _llm_provider.last_health
        if not healthy:
            return (
                f"This bot's LLM provider ({_llm_provider.provider_name}) "
                f"is not healthy:\n\n{reason}\n\n"
                f"Use /provider to switch to a working one (claude-api, "
                f"openrouter, gemini, ollama, etc.) or /apikey to fix the "
                f"current key."
            )

    system_prompt = build_system_prompt(user_id)
    messages = build_messages(user_id, message)

    # Attach images to the user's message for multimodal vision support
    if images and messages:
        messages[-1].images = images

    # Size-based conversation trimming: if system prompt + messages exceeds the
    # character budget, trim older conversation messages (not the new user message).
    # This prevents "prompt too long" errors regardless of conversation length.
    system_size = len(system_prompt)
    new_msg_size = len(message) + 20  # <user>...</user> tags overhead
    remaining_budget = max(PROMPT_CHAR_BUDGET - system_size - new_msg_size, 5000)

    # Calculate total conversation size (all messages except the last one, which is the new message)
    conv_messages = messages[:-1]  # History only, exclude new user message
    conv_size = sum(len(m.content) + 20 for m in conv_messages)  # +20 for XML tag overhead

    if conv_size > remaining_budget and conv_messages:
        session = get_session(user_id)
        # Convert Message objects to dicts for smart_trim_conversation
        history_dicts = [{"role": m.role, "content": m.content} for m in conv_messages]

        # Smart-trim first (removes verbose tool outputs, truncates old messages)
        trimmed = session.smart_trim_conversation(history_dicts)

        # If still over budget, drop oldest messages (keep at least 6 for continuity)
        min_keep = 6
        while len(trimmed) > min_keep and sum(len(m.get("content", "")) + 20 for m in trimmed) > remaining_budget:
            trimmed.pop(0)

        original_count = len(conv_messages)
        trimmed_size = sum(len(m.get("content", "")) + 20 for m in trimmed)
        messages_dropped = len(trimmed) < original_count

        # Rebuild messages if anything changed (count reduced or content trimmed)
        if messages_dropped or trimmed_size < conv_size:
            messages = [Message(role=m["role"], content=m["content"]) for m in trimmed]
            messages.append(Message(role="user", content=message))

        # Only persist to disk if messages were actually dropped (not just content-trimmed)
        # Content trimming is session-local and doesn't need to be saved
        if messages_dropped:
            current_topic = session.get_current_topic()
            if current_topic:
                session.save_topic_session(current_topic, trimmed)
            else:
                # Write directly (atomic) to avoid save_conversation() re-running smart_trim
                _atomic_save_json(session.conversation_file, trimmed)
                session.compact_conversation(trimmed, session.summary_file)
                # Invalidate cache — disk now has trimmed version
                _conversation_cache.pop(user_id, None)

        if messages_dropped or trimmed_size < conv_size:
            logger.info(
                f"Size-based trim for user {user_id}: {original_count} -> {len(trimmed)} messages, "
                f"{conv_size} -> {trimmed_size} chars "
                f"(system: {system_size} chars, budget: {PROMPT_CHAR_BUDGET})"
            )

    # Semaphore serializes LLM calls on low-resource machines (no-op on capable ones).
    # If another user is mid-request, tell this user they are queued. Using locked()
    # avoids racing the acquire: at worst the message is sent and the lock has just
    # been released, which is fine (the user just sees an extra "next in line" notice).
    sem = _get_llm_semaphore()
    if (_semaphore_active and isinstance(sem, asyncio.Semaphore)
            and sem.locked() and chat is not None):
        try:
            await chat.send_message(
                "Another request is being processed. You're next in line."
            )
        except Exception:
            pass
    # Set the per-user contextvar so skill-preference tools called from
    # within execute_tool() know which user's overlay to write to. Reset
    # in a finally so the contextvar doesn't leak across LLM calls if an
    # exception escapes the semaphore block.
    from core.tools import set_current_user_dir, reset_current_user_dir
    _user_ctx_token = set_current_user_dir(get_user_dir(user_id))
    try:
        async with sem:
            # For Claude CLI provider, pass extra kwargs for progress tracking
            if isinstance(_llm_provider, _CLI_PROVIDERS):
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
    finally:
        reset_current_user_dir(_user_ctx_token)

    if response.error and not response.text:
        logger.error(f"LLM error for user {user_id}: {response.error}")
        return f"Error from {response.provider}: {response.error}"

    if not response.text:
        return "No response generated. Try again or rephrase your message."

    text = sanitize_response(response.text)

    # Detect confabulated references to non-existent prior work.
    # Only triggers on short responses that match specific patterns.
    if _is_confabulated_response(text):
        logger.warning(
            f"Confabulation detected for user {user_id}: {text[:200]!r}"
        )
        text = (
            "I don't have the results from my previous session available. "
            "Let me redo this work. Could you repeat what you need?"
        )

    logger.info(f"LLM response for {user_id}: {len(text)} chars ({response.provider}/{response.model})")
    return text


def _save_and_send(user_id: int, user_message: str, response: str,
                   session=None, message_id: int = None, **_kwargs):
    """Save conversation turn and trigger compaction if needed.

    Note: response is expected to be already sanitized by call_llm().
    """
    if session is None:
        session = get_session(user_id)

    # Truncate stored messages to prevent unbounded history growth.
    # Full content is preserved in the append-only message log below.
    stored_user_msg = user_message[:500] if len(user_message) > 500 else user_message
    stored_response = response
    if len(response) > 4000:
        stored_response = response[:3800] + "\n\n[... response truncated in history]"

    current_topic = session.get_current_topic()
    if current_topic:
        history = session.get_topic_session(current_topic)
        history.append({"role": "user", "content": stored_user_msg})
        history.append({"role": "assistant", "content": stored_response})
        session.save_topic_session(current_topic, history)
    else:
        # Use cached history from build_messages to avoid redundant disk read
        history = _conversation_cache.pop(user_id, None)
        if history is None:
            history = session.load_conversation()
        history.append({"role": "user", "content": stored_user_msg})
        history.append({"role": "assistant", "content": stored_response})
        # Tiered compaction gate: idle > 15min AND msgs > threshold, OR token hard cap.
        do_compact, compact_reason = session.should_compact(history)
        if do_compact:
            logger.info(f"Compaction triggered for user {user_id}: {compact_reason}")
            history, _ = session.compact_conversation(history, session.summary_file)
        session.save_conversation(history)

    # Append-only message log (survives compaction)
    try:
        log_exchange(user_id, user_message, response,
                     message_id=message_id, topic=current_topic)
    except Exception as e:
        logger.error(f"Failed to log message for user {user_id}: {e}")

    return response


def command_body(text: str) -> str:
    """Return the message body after the leading command token.

    Robust to bot suffixes (/cmd@BotName), newlines after the command, and
    bodies that contain the same command string later (which a naive
    str.replace would also strip).
    """
    if not text:
        return ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def split_message(text: str, max_length: int = 4000) -> list[str]:
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = text.rfind(" ", 0, max_length)
        if split_at <= 0:
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
        await update.message.reply_text(
            "Welcome back! I was working on something when I got interrupted.\n\n"
            "Send /recover to see what I was doing, or just send me a new message."
        )
    else:
        bot_name = get_bot_name()
        skills_count = len(_skill_manager.get_enabled_skills()) if _skill_manager else 0
        await update.message.reply_text(
            f"Hi! I'm {bot_name}, your AI assistant on this computer.\n\n"
            f"I have {skills_count} skills available — from managing files and "
            f"setting reminders to editing photos, videos, and audio.\n\n"
            f"Just send me a message describing what you need, "
            f"and I'll take care of it.\n\n"
            f"Send /help to see all available commands."
        )


CLEAR_ARCHIVE_RETENTION = 20


def _prune_clear_archives(user_dir: Path, keep: int = CLEAR_ARCHIVE_RETENTION) -> int:
    """Drop oldest conversation_*.json archives beyond `keep`. Returns deletes."""
    archives = sorted(
        user_dir.glob("conversation_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    pruned = 0
    for old in archives[keep:]:
        try:
            old.unlink()
            pruned += 1
        except OSError as e:
            logger.warning(f"Failed to prune archive {old.name}: {e}")
    return pruned


@requires_auth
async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    if session.conversation_file.exists():
        archive = session.user_dir / f"conversation_{datetime.now():%Y%m%d_%H%M%S}.json"
        # Avoid collision if two clears happen in the same second
        if archive.exists():
            archive = session.user_dir / f"conversation_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.json"
        session.conversation_file.rename(archive)
        pruned = _prune_clear_archives(session.user_dir)
        if pruned:
            logger.info(f"Pruned {pruned} stale conversation archives for user {user_id}")
    # Also clear the compaction summary — stale summary from a cleared conversation
    # would contaminate the new conversation's context
    if session.summary_file.exists():
        session.summary_file.unlink()
    await update.message.reply_text("Conversation cleared.")
    logger.info(f"Context cleared by user {user_id}")


@requires_auth
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


@requires_auth
async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = command_body(update.message.text)
    if not text:
        await update.message.reply_text("Usage: /remember <fact to remember>")
        return
    session = get_session(user_id)
    session.add_memory(text)
    await update.message.reply_text(f"Saved: {text}")


@requires_auth
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


@requires_auth
async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = command_body(update.message.text)
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


@requires_auth
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
    response = "**Interrupted Task**\n\n"
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


@requires_auth
async def clear_recovery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    progress_file = get_progress_file(user_id)
    if progress_file.exists():
        progress_file.unlink()
        await update.message.reply_text("Recovery data cleared.")
    else:
        await update.message.reply_text("No recovery data to clear.")


@requires_auth
async def topic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch to a topic-specific session or back to main."""
    user_id = update.effective_user.id
    text = command_body(update.message.text)
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


@requires_auth
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


@requires_auth
async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = command_body(update.message.text)
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


@requires_auth
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
        until_tag = ""
        if j.end_date:
            until_tag = f" (until {j.end_date.strftime('%Y-%m-%d')})"
        text += f"- {j.run_at:%Y-%m-%d %H:%M}{repeat_tag}{until_tag}{type_tag}: {j.message[:50]}\n  ID: {j.job_id}\n\n"
    text += "Use /cancel <id> to remove."
    for chunk in split_message(text):
        await update.message.reply_text(chunk)


@requires_auth
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = command_body(update.message.text)
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
            f"System jobs can't be cancelled via /cancel.\n\n"
            f"To manage system jobs, use: /schedule list"
        )
        return
    scheduler.remove_job(text)
    await update.message.reply_text(f"Reminder '{text}' cancelled.")


@requires_auth
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kill the active CLI task for this user.

    Only affects subprocess CLI providers (Claude CLI, Codex CLI). Each user can
    only stop their own task. The in-flight turn returns whatever partial output
    was accumulated, with a '[Stopped by /stop command]' suffix.
    """
    user_id = update.effective_user.id
    if not isinstance(_llm_provider, _CLI_PROVIDERS):
        await update.message.reply_text(
            "/stop is only supported for CLI providers (Claude CLI, Codex CLI)."
        )
        return
    killed = _llm_provider.stop_user(user_id)
    if killed:
        await update.message.reply_text("Stopping current task...")
    else:
        await update.message.reply_text("No active task to stop.")


@requires_auth
async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Schedule a Claude agent task (with full tool-use)."""
    user_id = update.effective_user.id
    # Tool-use scheduling lets the LLM run shell commands, write files, send
    # messages, etc. Even an authenticated non-admin should not be able to
    # spawn a future Claude session that runs as the bot user with full
    # filesystem access. Restrict this command to admins.
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    text = command_body(update.message.text)
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


@requires_auth
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
        until_text = ""
        if job.end_date:
            until_text = f" (until {job.end_date.strftime('%Y-%m-%d')})"
        type_text = type_map.get(job.job_type, job.job_type.capitalize())
        text += f"[{type_text}] {job.run_at:%Y-%m-%d %H:%M}{repeat_text}{until_text}\n"
        text += f"  {job.message[:60]}{'...' if len(job.message) > 60 else ''}\n"
        text += f"  ID: {job.job_id}\n\n"
    text += "Use /cancel <id> to remove."
    for chunk in split_message(text):
        await update.message.reply_text(chunk)


@requires_auth
async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    report = await asyncio.to_thread(build_health_report, BOT_DIR)

    # Multi-user supplement
    try:
        from core.users import (
            is_multiuser_enabled, num_slots, list_slots,
            queue_enabled, concurrent_requests,
        )
        if is_multiuser_enabled():
            slots = list_slots()
            bound = sum(1 for v in slots.values() if v)
            total = num_slots()
            qstate = "on" if queue_enabled() else "off"
            cr = concurrent_requests()
            cr_text = f"{cr}" if cr > 0 else "unlimited"
            report += (
                f"\n\nMulti-user: {bound}/{total} slots bound\n"
                f"Queue: {qstate} (concurrent={cr_text})"
            )
    except ImportError:
        pass

    await update.message.reply_text(report)


@requires_auth
async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bind a free slot to a Telegram ID (admin only, multi-user mode only)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return  # silently ignore; don't leak command existence
    try:
        from core.users import is_multiuser_enabled, add_user
    except ImportError:
        await update.message.reply_text("Multi-user support unavailable.")
        return
    if not is_multiuser_enabled():
        await update.message.reply_text(
            "Multi-user mode is not enabled on this install. "
            "Reinstall with multi-user to use this command."
        )
        return
    args = (context.args or [])
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /adduser <telegram_id> <name>\n"
            "Example: /adduser 123456789 Alice"
        )
        return
    try:
        tid = int(args[0])
    except ValueError:
        await update.message.reply_text("First argument must be a numeric Telegram ID.")
        return
    name = " ".join(args[1:]).strip()
    if not name:
        await update.message.reply_text("Name is required.")
        return
    ok, msg, slot = await asyncio.to_thread(add_user, tid, name)
    await update.message.reply_text(msg)


@requires_auth
async def removeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unbind a Telegram ID and archive their slot dir (admin only)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    try:
        from core.users import (
            is_multiuser_enabled, lookup_slot, remove_user, slot_data_dir,
        )
    except ImportError:
        await update.message.reply_text("Multi-user support unavailable.")
        return
    if not is_multiuser_enabled():
        await update.message.reply_text("Multi-user mode is not enabled.")
        return
    args = (context.args or [])
    if len(args) < 1:
        await update.message.reply_text("Usage: /removeuser <telegram_id>")
        return
    try:
        tid = int(args[0])
    except ValueError:
        await update.message.reply_text("Argument must be a numeric Telegram ID.")
        return

    # Validate the slot exists and is removable BEFORE touching the filesystem.
    found = await asyncio.to_thread(lookup_slot, tid)
    if not found:
        await update.message.reply_text(
            f"Telegram ID {tid} is not bound to any slot."
        )
        return
    slot, info = found
    if info.get("is_admin"):
        await update.message.reply_text(
            "Cannot remove the admin. Reinstall to change admin."
        )
        return

    # Archive the slot's data directory FIRST. If this fails, we must NOT
    # unbind the slot. Otherwise /adduser could re-bind a new user to a
    # slot whose dir still contains the previous user's data, leaking it
    # across the privacy boundary. The slot dir is owned by mom_userN, but
    # its parent (data/users/) is orchestrator-owned, so the rename
    # succeeds without elevation. Encode the Telegram ID in the archive
    # name so /purgeuser can find it later.
    archive_msg = ""
    src = slot_data_dir(slot)
    if src.exists():
        try:
            archive_root = USERS_DIR / "_archived"
            archive_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dst = archive_root / f"{stamp}_user{slot}_tid{tid}"
            await asyncio.to_thread(src.rename, dst)
            archive_msg = f"\nArchived slot data to: {dst.relative_to(BOT_DIR)}"
        except OSError as e:
            await update.message.reply_text(
                f"Failed to archive slot {slot} data: {e}\n"
                f"Slot remains bound to preserve privacy. "
                f"Resolve the filesystem issue and re-run /removeuser."
            )
            return

    ok, msg, _ = await asyncio.to_thread(remove_user, tid)
    if not ok:
        await update.message.reply_text(
            f"{msg}\n(Note: slot data was already archived to "
            f"{archive_msg.strip() or 'the archive dir'}. Manual restore may be needed.)"
        )
        return

    # Drop the cached SessionManager so any future request from this tid
    # (e.g., re-bound via /adduser to a different slot) rebuilds against
    # the new data dir instead of writing into the now-archived path.
    clear_session_manager(tid)

    await update.message.reply_text(msg + archive_msg)


@requires_auth
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List bound slots (admin only)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    try:
        from core.users import is_multiuser_enabled, num_slots, list_slots
    except ImportError:
        await update.message.reply_text("Multi-user support unavailable.")
        return
    if not is_multiuser_enabled():
        await update.message.reply_text("Multi-user mode is not enabled (single-user install).")
        return
    slots = list_slots()
    total = num_slots()
    lines = [f"Slots ({sum(1 for v in slots.values() if v)}/{total} bound):", ""]
    for i in range(1, total + 1):
        info = slots.get(str(i))
        if not info:
            lines.append(f"[{i}] (free)")
            continue
        admin_tag = " (admin)" if info.get("is_admin") else ""
        added = info.get("added", "?")
        lines.append(
            f"[{i}] {info.get('name', '?')}{admin_tag}\n"
            f"    Telegram ID: {info.get('telegram_id', '?')}\n"
            f"    Added: {added}"
        )
    await update.message.reply_text("\n".join(lines))


@requires_auth
async def purgeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permanently delete archived slot data (admin only, requires confirmation)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    try:
        from core.users import is_multiuser_enabled
    except ImportError:
        await update.message.reply_text("Multi-user support unavailable.")
        return
    if not is_multiuser_enabled():
        await update.message.reply_text("Multi-user mode is not enabled.")
        return
    args = (context.args or [])
    confirm_phrase = "PURGE I UNDERSTAND"
    raw = " ".join(args).strip()
    if not raw:
        await update.message.reply_text(
            "Usage: /purgeuser <telegram_id> PURGE I UNDERSTAND\n\n"
            "This permanently deletes archived slot data for the given Telegram ID. "
            "The user must already have been removed via /removeuser."
        )
        return

    parts = raw.split(maxsplit=1)
    try:
        tid = int(parts[0])
    except ValueError:
        await update.message.reply_text("First argument must be a numeric Telegram ID.")
        return

    rest = parts[1] if len(parts) > 1 else ""
    if rest.strip() != confirm_phrase:
        await update.message.reply_text(
            f"Confirmation phrase missing. To proceed, send:\n"
            f"/purgeuser {tid} {confirm_phrase}"
        )
        return

    archive_root = USERS_DIR / "_archived"
    if not archive_root.exists():
        await update.message.reply_text("No archive directory found. Nothing to purge.")
        return

    matches = sorted(archive_root.glob(f"*_user*_tid{tid}"))
    if not matches:
        await update.message.reply_text(
            f"No archived data found for Telegram ID {tid}. "
            f"(Archive name pattern: <timestamp>_userN_tid{tid})"
        )
        return

    deleted: list[str] = []
    errors: list[str] = []
    for d in matches:
        try:
            await asyncio.to_thread(shutil.rmtree, d)
            deleted.append(d.name)
        except OSError as e:
            errors.append(f"{d.name}: {e}")
    msg_parts = [f"Purged {len(deleted)} archived directories for {tid}."]
    if deleted:
        msg_parts.append("Deleted:\n" + "\n".join(f"  {n}" for n in deleted))
    if errors:
        msg_parts.append("Errors:\n" + "\n".join(f"  {e}" for e in errors))
    await update.message.reply_text("\n\n".join(msg_parts))


@requires_auth
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    await update.message.reply_text("Checking for updates...")
    try:
        result = await asyncio.to_thread(full_update, BOT_DIR)
        # Re-run system probe to detect newly installed tools
        try:
            await asyncio.to_thread(probe_system, DATA_DIR)
            result += "\n\nSystem capabilities re-probed."
        except Exception:
            pass
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Update failed: {e}")


@requires_auth
async def system_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    version = get_current_version(BOT_DIR)
    branch = get_current_branch(BOT_DIR)
    skills_count = len(_skill_manager.get_enabled_skills()) if _skill_manager else 0
    provider = get_llm_provider()
    if isinstance(_llm_provider, _CLI_PROVIDERS):
        cli_label = "Codex CLI" if isinstance(_llm_provider, CodexCLIProvider) else "Claude CLI"
        tool_use = f"Yes ({cli_label} — native)"
    elif _llm_provider and _llm_provider.supports_tool_use:
        tool_use = "Yes (function calling)"
    else:
        tool_use = "No (text-only)"
    caps_summary = get_caps_summary(DATA_DIR)
    text = (
        f"MyOldMachine System Info\n\n"
        f"Version: {version} ({branch})\n"
        f"Provider: {provider} / {get_llm_model()}\n"
        f"Tool-use: {tool_use}\n"
        f"Skills: {skills_count}\n"
        f"{caps_summary}\n"
        f"Python: {platform.python_version()}\n"
        f"Bot dir: {BOT_DIR}"
    )
    await update.message.reply_text(text)


@requires_auth
async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    from utils.cleanup import run_cleanup
    report = await asyncio.to_thread(run_cleanup)
    await update.message.reply_text(report)


@requires_auth
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return
    from core.updater import restart_service
    await update.message.reply_text("Shutting down gracefully, then restarting...")
    # Graceful shutdown: stop scheduler and kill background processes before restart
    scheduler = get_scheduler()
    if scheduler:
        scheduler.stop()
    registry = get_process_registry()
    running = registry.list_running()
    if running:
        await registry.cleanup_all()
    if isinstance(_llm_provider, _CLI_PROVIDERS):
        # Give CLI processes up to 10 seconds to finish before restart
        for _ in range(10):
            if not _llm_provider.has_active_processes:
                break
            await asyncio.sleep(1)
    await asyncio.sleep(1)
    success, msg = restart_service()
    if not success:
        await update.message.reply_text(f"Restart failed: {msg}")


async def _refresh_provider_health(provider) -> tuple[bool, str]:
    """Run health_check on a provider, store the result on it, and return it.

    Mirrors the startup probe in main(). Used by /provider, /model, /apikey
    so the user gets immediate feedback if the new provider can't actually
    serve a request.
    """
    try:
        healthy, reason = await provider.health_check()
    except Exception as exc:
        healthy, reason = False, f"health_check raised {exc.__class__.__name__}: {exc}"
    provider.last_health = (healthy, reason)
    return healthy, reason


@requires_auth
async def provider_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch LLM provider and/or model without restarting."""
    global _llm_provider
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return

    text = command_body(update.message.text)

    # Default models per provider — keep in sync with install/wizard.py DEFAULT_MODELS
    # Last updated: April 24, 2026
    default_models = {
        "claude": "claude-sonnet-4-6",
        "claude-cli": "claude-sonnet-4-6",
        "claude-api": "claude-sonnet-4-6",
        "openai": "gpt-5.5",
        "deepseek": "deepseek-v4-flash",
        "grok": "grok-4-1-fast-non-reasoning",
        "kimi": "kimi-k2.6",
        "minimax": "MiniMax-M2.7",
        "gemini": "gemini-3-flash-preview",
        "ollama": "llama3.1:8b",
        "ollama-cloud": "qwen3.5:cloud",
        "openrouter": "nvidia/nemotron-3-super-120b-a12b:free",
    }

    if not text:
        # Show current state and available providers
        current = get_llm_provider()
        model = get_llm_model()
        has_key = bool(get_llm_api_key())
        tool_use = "Yes" if _llm_provider and _llm_provider.supports_tool_use else "No"
        msg = (
            f"Current provider: {current}\n"
            f"Current model: {model}\n"
            f"API key set: {'Yes' if has_key else 'No'}\n"
            f"Tool-use: {tool_use}\n\n"
            f"Available providers:\n"
        )
        for p, default_m in default_models.items():
            if p in ("claude-cli", "claude-api"):
                continue
            marker = " (active)" if p == current else ""
            msg += f"  {p} — default: {default_m}{marker}\n"
        msg += (
            "\nUsage:\n"
            "  /provider <name> — switch provider (uses default model)\n"
            "  /provider <name> <model> — switch provider + model\n"
            "  /model <model> — change model only\n"
            "  /apikey <key> — set API key for current provider\n\n"
            "Example:\n"
            "  /provider openrouter nvidia/nemotron-3-super-120b-a12b:free\n"
            "  /provider grok\n"
            "  /model gpt-5.4-mini"
        )
        await update.message.reply_text(msg)
        return

    # Parse: first word is provider, rest is model (optional)
    parts = text.split(None, 1)
    new_provider = parts[0].lower()
    new_model = parts[1].strip() if len(parts) > 1 else None

    # Validate provider
    valid_providers = set(default_models.keys())
    if new_provider not in valid_providers:
        await update.message.reply_text(
            f"Unknown provider: {new_provider}\n"
            f"Valid: {', '.join(sorted(p for p in valid_providers if p not in ('claude-cli', 'claude-api')))}"
        )
        return

    if not new_model:
        new_model = default_models.get(new_provider, "")

    # Check if API key is needed but missing
    needs_key = new_provider in ("openai", "deepseek", "grok", "kimi", "minimax", "gemini", "openrouter", "claude-api")
    current_key = get_llm_api_key()
    if needs_key and not current_key:
        await update.message.reply_text(
            f"Provider '{new_provider}' requires an API key.\n"
            f"Set one first: /apikey <your-key>\n"
            f"Then try switching again."
        )
        return

    # Update .env file
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found_provider = False
        found_model = False
        for line in lines:
            if line.startswith("LLM_PROVIDER="):
                new_lines.append(f"LLM_PROVIDER={new_provider}")
                found_provider = True
            elif line.startswith("LLM_MODEL="):
                new_lines.append(f"LLM_MODEL={new_model}")
                found_model = True
            else:
                new_lines.append(line)
        if not found_provider:
            new_lines.append(f"LLM_PROVIDER={new_provider}")
        if not found_model:
            new_lines.append(f"LLM_MODEL={new_model}")
        _atomic_env_write(env_file, "\n".join(new_lines) + "\n")
    else:
        await update.message.reply_text("Error: .env file not found.")
        return

    # Update environment variables so get_llm_provider/get_llm_model return new values
    os.environ["LLM_PROVIDER"] = new_provider
    os.environ["LLM_MODEL"] = new_model

    # Reload provider in memory
    api_key = get_llm_api_key()
    kwargs = {}
    if new_provider == "ollama":
        kwargs["base_url"] = get_ollama_base_url()
    try:
        _llm_provider = create_provider(new_provider, new_model, api_key, **kwargs)
        if isinstance(_llm_provider, _CLI_PROVIDERS):
            _llm_provider.on_progress_save = save_task_progress
            _llm_provider.on_progress_clear = clear_task_progress
        logger.info(f"Provider switched to {new_provider}/{new_model} by user {user_id}")
        tool_use = "Yes" if _llm_provider.supports_tool_use else "No"
        warning = ""
        if not _llm_provider.supports_tool_use:
            warning = (
                "\n\nWARNING: This provider has no tool-use support. "
                "You lose all skills, file management, scheduling, and "
                "command execution. The bot becomes text-only."
            )
        healthy, reason = await _refresh_provider_health(_llm_provider)
        if healthy:
            health_line = f"Health-check: OK ({reason})"
        else:
            health_line = (
                f"Health-check FAILED: {reason}\n"
                f"This provider will reject messages until resolved."
            )
            logger.error(f"Health-check failed after /provider switch: {reason}")
        await update.message.reply_text(
            f"Switched to {new_provider} / {new_model}\n"
            f"Tool-use: {tool_use}{warning}\n\n"
            f"{health_line}\n\n"
            f"No restart needed. Send a message to test."
        )
    except Exception as e:
        # Log full error for the operator, send a generic line to the user so
        # raw stack traces / SDK internals don't bleed into the chat.
        logger.exception(f"Failed to switch provider: {e}")
        await update.message.reply_text(
            "Failed to create provider. Check the bot log for details."
        )


@requires_auth
async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change the model for the current provider without switching providers."""
    global _llm_provider
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return

    text = command_body(update.message.text)
    if not text:
        await update.message.reply_text(
            f"Current model: {get_llm_model()}\n"
            f"Provider: {get_llm_provider()}\n\n"
            f"Usage: /model <model-name>\n"
            f"Example: /model gemini-3-flash-preview"
        )
        return

    new_model = text.strip()
    current_provider = get_llm_provider()

    # Update .env
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith("LLM_MODEL="):
                new_lines.append(f"LLM_MODEL={new_model}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"LLM_MODEL={new_model}")
        _atomic_env_write(env_file, "\n".join(new_lines) + "\n")

    os.environ["LLM_MODEL"] = new_model

    # Reload provider
    api_key = get_llm_api_key()
    kwargs = {}
    if current_provider == "ollama":
        kwargs["base_url"] = get_ollama_base_url()
    try:
        _llm_provider = create_provider(current_provider, new_model, api_key, **kwargs)
        if isinstance(_llm_provider, _CLI_PROVIDERS):
            _llm_provider.on_progress_save = save_task_progress
            _llm_provider.on_progress_clear = clear_task_progress
        logger.info(f"Model switched to {new_model} by user {user_id}")
        healthy, reason = await _refresh_provider_health(_llm_provider)
        if healthy:
            health_line = f"Health-check: OK ({reason})"
        else:
            health_line = (
                f"Health-check FAILED: {reason}\n"
                f"Messages will be rejected until resolved."
            )
            logger.error(f"Health-check failed after /model switch: {reason}")
        await update.message.reply_text(
            f"Model changed to: {new_model}\n"
            f"Provider: {current_provider}\n\n"
            f"{health_line}\n\n"
            f"No restart needed."
        )
    except Exception as e:
        logger.error(f"Failed to switch model: {e}")
        await update.message.reply_text(f"Failed: {e}")


@requires_auth
async def apikey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set or update the API key for the current provider."""
    global _llm_provider
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return

    text = command_body(update.message.text)
    if not text:
        has_key = bool(get_llm_api_key())
        await update.message.reply_text(
            f"Provider: {get_llm_provider()}\n"
            f"API key set: {'Yes' if has_key else 'No'}\n\n"
            f"Usage: /apikey <your-api-key>\n\n"
            f"The key is stored in .env (local only, never sent anywhere)."
        )
        return

    new_key = text.strip()

    # Update .env
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith("LLM_API_KEY="):
                new_lines.append(f"LLM_API_KEY={new_key}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"LLM_API_KEY={new_key}")
        _atomic_env_write(env_file, "\n".join(new_lines) + "\n")

    os.environ["LLM_API_KEY"] = new_key

    # Reload provider with new key
    current_provider = get_llm_provider()
    current_model = get_llm_model()
    kwargs = {}
    if current_provider == "ollama":
        kwargs["base_url"] = get_ollama_base_url()
    try:
        _llm_provider = create_provider(current_provider, current_model, new_key, **kwargs)
        if isinstance(_llm_provider, _CLI_PROVIDERS):
            _llm_provider.on_progress_save = save_task_progress
            _llm_provider.on_progress_clear = clear_task_progress
        logger.info(f"API key updated for {current_provider} by user {user_id}")
        healthy, reason = await _refresh_provider_health(_llm_provider)
        if healthy:
            health_line = f"\nHealth-check: OK ({reason})"
        else:
            health_line = (
                f"\nHealth-check FAILED: {reason}\n"
                f"Double-check the key — messages will be rejected until resolved."
            )
            logger.error(f"Health-check failed after /apikey update: {reason}")

        # Delete the user's message to avoid the key sitting in chat history
        chat = update.message.chat
        deleted = False
        try:
            await update.message.delete()
            deleted = True
        except Exception:
            pass

        if deleted:
            await chat.send_message(
                f"API key updated for {current_provider}.\n"
                f"(Your message with the key was deleted for security.)"
                f"{health_line}"
            )
        else:
            await chat.send_message(
                f"API key updated for {current_provider}.\n"
                f"(Could not delete your message — delete it manually to avoid "
                f"the key sitting in chat history.)"
                f"{health_line}"
            )
    except Exception as e:
        logger.error(f"Failed to update API key: {e}")
        await update.message.reply_text(f"Failed: {e}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_name = get_bot_name()
    text = (
        f"{bot_name} — What I Can Do\n\n"
        "Just send me a message describing what you need. "
        "You can also send me photos, documents, audio, or video files "
        "and I'll work with them.\n\n"
        "Useful commands:\n"
        "  /start — Say hello and see basic info\n"
        "  /help — Show this guide\n"
        "  /clear — Start a fresh conversation\n"
        "  /status — See how things are running\n\n"
        "Memory & reminders:\n"
        "  /remember — Save something I should always know\n"
        "  /memories — See what I remember about you\n"
        "  /remind — Set a reminder (e.g. /remind tomorrow 9am Call the dentist)\n"
        "  /reminders — See your upcoming reminders\n"
        "  /cancel — Cancel a reminder\n"
        "  /stop — Stop the current AI task immediately\n\n"
        "Organization:\n"
        "  /topic — Switch to a separate conversation thread\n"
        "  /topics — See all your conversation threads\n\n"
        "Shortcuts:\n"
        "  /alias — Create quick shortcuts for things you ask often\n\n"
        "Settings (advanced):\n"
        "  /provider — Change AI brain\n"
        "  /model — Change AI model\n"
        "  /apikey — Set API key for a provider\n\n"
        "System:\n"
        "  /health — Check if everything is healthy\n"
        "  /maintenance — Backup, updates, and cleanup settings\n"
        "  /update — Get the latest version\n"
        "  /restart — Restart me\n"
    )
    await update.message.reply_text(text)


@requires_auth
async def alias_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage custom command shortcuts."""
    user_id = update.effective_user.id
    text = command_body(update.message.text)

    aliases = _load_aliases(user_id)

    if not text:
        # List all aliases
        if not aliases:
            await update.message.reply_text(
                "No shortcuts set.\n\n"
                "Usage:\n"
                "  /alias set <name> <text>\n"
                "  /alias remove <name>\n\n"
                "Example:\n"
                "  /alias set disk Check disk usage\n"
                "Then send /disk to run it."
            )
            return
        lines = ["Your shortcuts:\n"]
        for name, expansion in sorted(aliases.items()):
            preview = expansion[:60] + ("..." if len(expansion) > 60 else "")
            lines.append(f"  /{name} → {preview}")
        lines.append(f"\nTotal: {len(aliases)}")
        lines.append("Use /alias remove <name> to delete.")
        await update.message.reply_text("\n".join(lines))
        return

    parts = text.split(None, 1)
    action = parts[0].lower()

    if action == "set":
        if len(parts) < 2:
            await update.message.reply_text("Usage: /alias set <name> <text>")
            return
        rest = parts[1].split(None, 1)
        if len(rest) < 2:
            await update.message.reply_text("Usage: /alias set <name> <text to send to bot>")
            return
        name = rest[0].lower().lstrip("/")
        expansion = rest[1]

        if name in _RESERVED_COMMANDS:
            await update.message.reply_text(f"'{name}' is a built-in command and cannot be used as a shortcut.")
            return
        if not re.match(r'^[a-z0-9_]+$', name):
            await update.message.reply_text("Shortcut names can only contain lowercase letters, numbers, and underscores.")
            return
        if len(name) > 32:
            await update.message.reply_text("Shortcut name too long (max 32 characters).")
            return
        if len(aliases) >= 50:
            await update.message.reply_text("Maximum 50 shortcuts. Remove some first.")
            return

        aliases[name] = expansion
        _save_aliases(user_id, aliases)
        await update.message.reply_text(f"Shortcut set: /{name} → {expansion[:100]}")

    elif action == "remove" or action == "delete":
        if len(parts) < 2:
            await update.message.reply_text("Usage: /alias remove <name>")
            return
        name = parts[1].strip().lower().lstrip("/")
        if name not in aliases:
            await update.message.reply_text(f"No shortcut named '{name}'.")
            return
        del aliases[name]
        _save_aliases(user_id, aliases)
        await update.message.reply_text(f"Shortcut '/{name}' removed.")

    else:
        await update.message.reply_text(
            "Usage:\n"
            "  /alias - list shortcuts\n"
            "  /alias set <name> <text>\n"
            "  /alias remove <name>"
        )


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
        if original_name:
            safe_name = original_name.replace("/", "_").replace("\\", "_").replace("\0", "_")
            name = f"{ts}{idx_suffix}_{safe_name}"
        else:
            name = f"{ts}{idx_suffix}_{file_type}{ext}"
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

async def _try_alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the message is an alias command. If so, expand and process it. Returns True if handled."""
    msg_text = update.message.text or ""
    if not msg_text.startswith("/"):
        return False
    # Extract command name (strip the leading / and any @botname suffix)
    cmd = msg_text.split()[0][1:].split("@")[0].lower()
    if cmd in _RESERVED_COMMANDS:
        return False
    user_id = update.effective_user.id
    aliases = _load_aliases(user_id)
    if cmd not in aliases:
        return False
    # Expand: replace the /cmd with the alias text, keep any extra args
    rest = msg_text[len(msg_text.split()[0]):].strip()
    expanded = aliases[cmd]
    if rest:
        expanded = f"{expanded} {rest}"
    # Monkey-patch the message text so _process_single sees the expansion
    update.message.text = expanded
    return True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Auth check — empty allowed list rejects everyone (must configure during setup)
    allowed = get_allowed_users()
    if not allowed or user_id not in allowed:
        await update.message.reply_text("Unauthorized.")
        return

    # Duplicate protection
    msg_id = update.message.message_id
    if msg_id in _processed_ids:
        return
    _processed_ids.add(msg_id)
    if len(_processed_ids) > _MAX_PROCESSED:
        # Remove oldest half — message IDs are monotonically increasing
        cutoff = min(_processed_ids) + (_MAX_PROCESSED // 2)
        _processed_ids.difference_update({mid for mid in _processed_ids if mid < cutoff})

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
        # Daily reset check (same as _process_single)
        session = get_session(user_id)
        if session.should_daily_reset():
            session.perform_daily_reset()
            logger.info(f"Daily reset performed for user {user_id}")

        all_attachments = []
        caption = ""
        for idx, upd in enumerate(updates):
            attachments = await download_attachments(upd, context, group_index=idx)
            all_attachments.extend(attachments)
            if not caption:
                caption = upd.message.text or upd.message.caption or ""

        user_message = caption
        image_paths = [str(p) for p, t in all_attachments if t == "image"] if all_attachments else []
        if all_attachments:
            info = "\n\n[Attachments:]"
            for path, ftype in all_attachments:
                if str(path).lower().endswith('.pdf'):
                    pdf_text = _extract_pdf_text(str(path))
                    info += f"\n- document (PDF): {os.path.basename(str(path))}"
                    info += f"\n  [PDF content pre-extracted — file not available for Read tool]\n{pdf_text}"
                elif ftype == "image":
                    info += f"\n- {ftype}: {path} (viewable with Read tool)"
                else:
                    info += f"\n- {ftype}: {path}"
            user_message = (user_message + info) if user_message else f"[Sent {len(all_attachments)} file(s)]{info}"

        if not user_message:
            return

        chat = updates[0].message.chat
        first_msg_id = updates[0].message.message_id
        save_pending_message(user_id, user_message, first_msg_id)

        try:
            try:
                await chat.send_action("typing")
            except Exception:
                pass

            response = await call_llm(user_id, user_message, chat=chat,
                                      images=image_paths or None)

            _save_and_send(user_id, user_message, response, session=session, message_id=first_msg_id)

            for chunk in split_message(response):
                try:
                    await chat.send_message(chunk)
                except Exception as e:
                    logger.error(f"Failed to send to user {user_id}: {e}")
        except Exception as e:
            logger.exception(f"Error processing media group for user {user_id}")
            try:
                await chat.send_message(f"Error processing your message: {str(e)[:200]}")
            except Exception:
                pass
        finally:
            clear_pending_message(user_id)


async def _process_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lock = _get_user_lock(user_id)

    if lock.locked():
        try:
            await update.message.reply_text("Still working on your previous request. I'll get to this once it's done.")
        except Exception:
            pass

    async with lock:
        # Daily reset check
        session = get_session(user_id)
        if session.should_daily_reset():
            session.perform_daily_reset()
            logger.info(f"Daily reset performed for user {user_id}")

        # Initialize person model for new users
        if _memory_manager and not _memory_manager.get_model(user_id):
            user_name = update.effective_user.first_name or "User"
            _memory_manager.init_model(user_id, name=user_name)
            logger.info(f"Initialized person model for user {user_id} ({user_name})")

        # Intro flow state. First message ever -> show intro. Second message ->
        # run intro reflection to seed the model. Third message onward ->
        # normal flow. Migration in post_init() sets both markers for users who
        # already have a populated model from prior deployments.
        intro_first_turn = False
        intro_should_reflect = False
        if _memory_manager:
            if not _memory_manager.intro_shown(user_id):
                intro_first_turn = True
            elif not _memory_manager.intro_done(user_id):
                intro_should_reflect = True

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

        # Collect image paths for multimodal vision support
        image_paths = [str(p) for p, t in attachments if t == "image"] if attachments else []

        # Auto-transcribe voice for non-CLI providers (they can't invoke Whisper via tools)
        if attachments and not isinstance(_llm_provider, _CLI_PROVIDERS):
            for path, ftype in attachments:
                if ftype == "voice":
                    transcript = await _auto_transcribe_voice(str(path))
                    if transcript:
                        # Replace voice path reference with transcript in the message
                        attachments = [
                            (p, t) if t != "voice" or str(p) != str(path)
                            else (p, "voice_transcribed")
                            for p, t in attachments
                        ]
                        user_message = (user_message + f"\n\n[Voice message transcribed]: {transcript}").strip()

        if attachments:
            info = "\n\n[Attachments:]"
            for path, ftype in attachments:
                if str(path).lower().endswith('.pdf'):
                    pdf_text = _extract_pdf_text(str(path))
                    info += f"\n- document (PDF): {os.path.basename(str(path))}"
                    info += f"\n  [PDF content pre-extracted — file not available for Read tool]\n{pdf_text}"
                elif ftype == "image":
                    info += f"\n- {ftype}: {path} (viewable with Read tool)"
                elif ftype == "voice_transcribed":
                    continue  # Already included in user_message above
                else:
                    info += f"\n- {ftype}: {path}"
            user_message = (user_message + info) if user_message else f"[Sent file(s)]{info}"

        if not user_message:
            await update.message.reply_text("Please send a message or attachment.")
            return

        logger.info(f"From {user_id}: {user_message[:100]}...")
        save_pending_message(user_id, user_message, update.message.message_id)

        try:
            try:
                await update.message.chat.send_action("typing")
            except Exception:
                pass

            # On the user's first message, swap in the orientation prompt as the
            # LLM input. The user's clean message still gets saved to history.
            if intro_first_turn:
                llm_message = build_orientation_prompt(
                    user_id, first_user_message=user_message
                )
            else:
                llm_message = user_message

            response = await call_llm(user_id, llm_message, chat=update.message.chat,
                                      images=image_paths or None)

            # Save to current session (topic or main) with compaction
            _save_and_send(user_id, user_message, response, session=session,
                           message_id=update.message.message_id)

            for chunk in split_message(response):
                try:
                    await update.message.reply_text(chunk)
                except Exception as e:
                    logger.error(f"Failed to send reply to {user_id}: {e}")

            logger.info(f"Responded to {user_id}: {len(response)} chars")

            # Intro flow bookkeeping after a successful response
            if _memory_manager:
                if intro_first_turn:
                    _memory_manager.mark_intro_shown(user_id)
                    logger.info(f"Intro shown to user {user_id}")
                elif intro_should_reflect:
                    # Mark done first so a reflection failure does not loop on
                    # every subsequent turn. Then run the reflection in a
                    # thread so it does not block the event loop.
                    _memory_manager.mark_intro_done(user_id)
                    try:
                        from utils.reflect import run_intro_reflection
                        profile = get_user_profile(user_id)
                        user_name = profile.get(
                            "name", update.effective_user.first_name or "User"
                        )
                        intro_msg = build_orientation_prompt(user_id)
                        loop = asyncio.get_running_loop()
                        fut = loop.run_in_executor(
                            None,
                            run_intro_reflection,
                            _memory_manager, user_id, user_name,
                            intro_msg, user_message,
                        )

                        def _intro_reflection_done(f, uid=user_id):
                            try:
                                f.result()
                            except Exception:
                                logger.exception(
                                    f"Intro reflection failed for user {uid}"
                                )

                        fut.add_done_callback(_intro_reflection_done)
                        logger.info(f"Intro reflection scheduled for user {user_id}")
                    except Exception as e:
                        logger.error(
                            f"Intro reflection scheduling failed for {user_id}: {e}"
                        )
        except Exception as e:
            logger.exception(f"Error processing message for user {user_id}")
            try:
                await update.message.reply_text(f"Error processing your message: {str(e)[:200]}")
            except Exception:
                pass
        finally:
            clear_pending_message(user_id)


async def _health_monitor_loop(scheduler):
    """Periodically check system health and alert admins if issues found."""
    global _last_health_check
    # Wait 60 seconds after startup before first check
    await asyncio.sleep(60)
    while True:
        try:
            now = asyncio.get_running_loop().time()
            if _last_health_check is None or now - _last_health_check >= _HEALTH_CHECK_INTERVAL:
                _last_health_check = now
                admin_ids = [
                    uid for uid in get_allowed_users()
                    if is_admin(uid)
                ]
                if admin_ids:
                    await run_health_check(
                        scheduler.send_message,
                        admin_ids,
                        BOT_DIR,
                    )
        except Exception as e:
            logger.error(f"Health monitor error: {e}")
        await asyncio.sleep(300)  # Check eligibility every 5 minutes


def _setup_reflection_job(scheduler):
    """Set up the nightly reflection scheduler job if not already present."""
    from core.scheduler import _get_all_meta

    # Check if reflection job already exists
    existing = _get_all_meta()
    for meta in existing:
        if meta.get("name") == "nightly-reflection":
            logger.info("Nightly reflection job already scheduled")
            return

    # Determine if the current provider supports reflection
    provider = get_llm_provider()
    model = get_llm_model().lower()

    # Strong enough for reflection?
    capable_providers = ("claude", "claude-cli", "claude-api", "openai", "deepseek", "grok", "kimi", "minimax", "gemini", "google", "ollama-cloud")
    is_capable = provider in capable_providers

    # Ollama: only large models
    if provider == "ollama":
        is_capable = any(size in model for size in ("70b", "72b", "34b", "32b", "405b"))

    # OpenRouter: depends on the model — assume capable if paid
    if provider == "openrouter":
        is_capable = ":free" not in model

    if not is_capable:
        logger.info(f"Skipping reflection job — {provider}/{model} may not be capable enough")
        return

    # Schedule nightly at 3:00 AM
    run_at = datetime.now().replace(hour=3, minute=0, second=0, microsecond=0)
    if run_at <= datetime.now():
        run_at += timedelta(days=1)

    venv_python = str(BOT_DIR / ".venv" / "bin" / "python")
    reflect_script = str(BOT_DIR / "utils" / "reflect.py")

    # Get admin user for notifications
    allowed = get_allowed_users()
    admin_id = allowed[0] if allowed else 0

    scheduler.add_job(
        user_id=admin_id,
        message="Nightly memory reflection",
        run_at=run_at,
        repeat="daily",
        job_type="command",
        name="nightly-reflection",
        notify=False,
        command=f"{venv_python} {reflect_script}",
    )
    logger.info("Scheduled nightly reflection job at 3:00 AM")


def _setup_maintenance_jobs(scheduler):
    """Set up nightly maintenance jobs (system update, cleanup, backup) if not already present."""
    from core.scheduler import _get_all_meta
    from utils.maintenance import load_config

    existing = _get_all_meta()
    existing_names = {meta.get("name") for meta in existing}

    venv_python = str(BOT_DIR / ".venv" / "bin" / "python")
    allowed = get_allowed_users()
    admin_id = allowed[0] if allowed else 0

    config = load_config()

    # --- Cleanup job: 3:30 AM ---
    if "nightly-cleanup" not in existing_names and config.get("cleanup", True):
        cleanup_script = str(BOT_DIR / "utils" / "cleanup.py")
        run_at = datetime.now().replace(hour=3, minute=30, second=0, microsecond=0)
        if run_at <= datetime.now():
            run_at += timedelta(days=1)

        scheduler.add_job(
            user_id=admin_id,
            message="Nightly cleanup",
            run_at=run_at,
            repeat="daily",
            job_type="command",
            name="nightly-cleanup",
            notify=False,
            command=f"{venv_python} {cleanup_script}",
        )
        logger.info("Scheduled nightly cleanup job at 3:30 AM")

    # --- System update job: 4:00 AM ---
    if "nightly-system-update" not in existing_names and config.get("system_updates", True):
        update_script = str(BOT_DIR / "utils" / "system_update.py")
        run_at = datetime.now().replace(hour=4, minute=0, second=0, microsecond=0)
        if run_at <= datetime.now():
            run_at += timedelta(days=1)

        notify_args = ""
        if admin_id:
            notify_args = f" --notify --user-id {admin_id}"

        scheduler.add_job(
            user_id=admin_id,
            message="Nightly system update",
            run_at=run_at,
            repeat="daily",
            job_type="command",
            name="nightly-system-update",
            notify=False,
            command=f"{venv_python} {update_script}{notify_args}",
        )
        logger.info("Scheduled nightly system update job at 4:00 AM")

    # --- Backup job: 2:00 AM (only if backup is configured) ---
    if ("nightly-backup" not in existing_names
            and config.get("backup_enabled")
            and config.get("backup_path")):
        backup_script = str(BOT_DIR / "utils" / "backup.py")
        run_at = datetime.now().replace(hour=2, minute=0, second=0, microsecond=0)
        if run_at <= datetime.now():
            run_at += timedelta(days=1)

        notify_args = ""
        if admin_id:
            notify_args = f" --notify --user-id {admin_id}"

        # Don't pass --target on command line (path may contain spaces/metacharacters).
        # backup.py reads backup_path from maintenance.json when --target is omitted.
        scheduler.add_job(
            user_id=admin_id,
            message="Nightly backup",
            run_at=run_at,
            repeat="daily",
            job_type="command",
            name="nightly-backup",
            notify=False,
            command=f"{venv_python} {backup_script} create{notify_args}",
        )
        logger.info(f"Scheduled nightly backup job at 2:00 AM -> {config['backup_path']}")

    # --- System probe job: 4:30 AM (after system update) ---
    if "nightly-system-probe" not in existing_names:
        run_at = datetime.now().replace(hour=4, minute=30, second=0, microsecond=0)
        if run_at <= datetime.now():
            run_at += timedelta(days=1)

        bot_dir_s = str(BOT_DIR)
        data_dir_s = str(DATA_DIR)
        probe_cmd = (
            f"cd '{bot_dir_s}' && {venv_python} -c "
            f"\"from pathlib import Path; from core.system_probe import probe_system; "
            f"probe_system(Path('{data_dir_s}'))\""
        )
        scheduler.add_job(
            user_id=admin_id,
            message="Nightly system probe",
            run_at=run_at,
            repeat="daily",
            job_type="command",
            name="nightly-system-probe",
            notify=False,
            command=probe_cmd,
        )
        logger.info("Scheduled nightly system probe job at 4:30 AM")


@requires_auth
async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configure and view maintenance settings (backup, updates, cleanup)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Admin only.")
        return

    from utils.maintenance import load_config, update_config, get_status_report
    from core.scheduler import _get_all_meta

    text = (update.message.text or "").replace("/maintenance", "", 1).strip()

    # No arguments: show status
    if not text:
        report = get_status_report()

        # Add scheduled job info
        existing = _get_all_meta()
        job_names = [m.get("name", "") for m in existing
                     if m.get("name", "").startswith("nightly-")]
        if job_names:
            report += "\n\nScheduled jobs: " + ", ".join(sorted(job_names))
        else:
            report += "\n\nNo maintenance jobs scheduled."

        cmd_lines = [
            "\n\nCommands:",
            "  /maintenance backup /path/to/drive",
            "  /maintenance updates on|off",
            "  /maintenance cleanup on|off",
        ]
        if platform.system() == "Darwin":
            cmd_lines.append("  /maintenance macos-updates on|off")
            cmd_lines.append("  /maintenance macos-restart on|off")
        cmd_lines += [
            "  /maintenance backup off",
            "  /maintenance run backup",
            "  /maintenance run update",
            "  /maintenance run cleanup",
        ]
        report += "\n".join(cmd_lines)
        await update.message.reply_text(report)
        return

    parts = text.split(None, 1)
    subcmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    # --- /maintenance backup <path> or /maintenance backup off ---
    if subcmd == "backup":
        if not arg:
            config = load_config()
            if config.get("backup_enabled") and config.get("backup_path"):
                from utils.backup import list_backups
                report = await asyncio.to_thread(list_backups, config["backup_path"])
                await update.message.reply_text(report)
            else:
                await update.message.reply_text(
                    "Backup is not configured.\n\n"
                    "Set it up with:\n"
                    "  /maintenance backup /path/to/backup/drive\n\n"
                    "Example:\n"
                    "  /maintenance backup /mnt/external/backups\n"
                    "  /maintenance backup ~/backups"
                )
            return

        if arg.lower() == "off":
            update_config(backup_enabled=False)
            # Remove the scheduled job
            scheduler = get_scheduler()
            if scheduler:
                existing = _get_all_meta()
                for meta in existing:
                    if meta.get("name") == "nightly-backup":
                        scheduler.remove_job(meta.get("job_id", ""))
                        break
            await update.message.reply_text("Backup disabled.")
            return

        # Interpret as backup path
        backup_path = os.path.expanduser(arg)
        backup_path = os.path.abspath(backup_path)

        # Validate path
        target = Path(backup_path)
        if not target.exists():
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                await update.message.reply_text(f"Cannot create directory: {e}")
                return
        if not os.access(backup_path, os.W_OK):
            await update.message.reply_text(f"No write permission to {backup_path}")
            return

        # Save config
        update_config(backup_enabled=True, backup_path=backup_path)

        # Schedule backup job
        scheduler = get_scheduler()
        if scheduler:
            # Remove existing backup job if any
            existing = _get_all_meta()
            for meta in existing:
                if meta.get("name") == "nightly-backup":
                    scheduler.remove_job(meta.get("job_id", ""))
                    break
            _setup_maintenance_jobs(scheduler)

        await update.message.reply_text(
            f"Backup enabled.\n"
            f"Target: {backup_path}\n"
            f"Schedule: nightly at 2:00 AM\n"
            f"Retention: 7 backups\n\n"
            f"Run /maintenance run backup to create one now."
        )
        return

    # --- /maintenance updates on|off ---
    if subcmd == "updates":
        if arg.lower() in ("on", "true", "yes", "1"):
            update_config(system_updates=True)
            scheduler = get_scheduler()
            if scheduler:
                _setup_maintenance_jobs(scheduler)
            await update.message.reply_text("System updates enabled (nightly at 4:00 AM).")
        elif arg.lower() in ("off", "false", "no", "0"):
            update_config(system_updates=False)
            scheduler = get_scheduler()
            if scheduler:
                existing = _get_all_meta()
                for meta in existing:
                    if meta.get("name") == "nightly-system-update":
                        scheduler.remove_job(meta.get("job_id", ""))
                        break
            await update.message.reply_text("System updates disabled.")
        else:
            await update.message.reply_text("Usage: /maintenance updates on|off")
        return

    # --- /maintenance cleanup on|off ---
    if subcmd == "cleanup":
        if arg.lower() in ("on", "true", "yes", "1"):
            update_config(cleanup=True)
            scheduler = get_scheduler()
            if scheduler:
                _setup_maintenance_jobs(scheduler)
            await update.message.reply_text("Cleanup enabled (nightly at 3:30 AM).")
        elif arg.lower() in ("off", "false", "no", "0"):
            update_config(cleanup=False)
            scheduler = get_scheduler()
            if scheduler:
                existing = _get_all_meta()
                for meta in existing:
                    if meta.get("name") == "nightly-cleanup":
                        scheduler.remove_job(meta.get("job_id", ""))
                        break
            await update.message.reply_text("Cleanup disabled.")
        else:
            await update.message.reply_text("Usage: /maintenance cleanup on|off")
        return

    # --- /maintenance macos-updates on|off (Apple softwareupdate sub-toggle) ---
    if subcmd == "macos-updates":
        if platform.system() != "Darwin":
            await update.message.reply_text("macOS-only setting. This machine is not macOS.")
            return
        if arg.lower() in ("on", "true", "yes", "1"):
            update_config(macos_system_updates=True)
            await update.message.reply_text(
                "macOS softwareupdate enabled. Apple updates pulled nightly via "
                "`softwareupdate -i -a`. Toggle auto-restart with /maintenance macos-restart on|off."
            )
        elif arg.lower() in ("off", "false", "no", "0"):
            update_config(macos_system_updates=False)
            await update.message.reply_text(
                "macOS softwareupdate disabled. Apple security responses still auto-install."
            )
        else:
            await update.message.reply_text("Usage: /maintenance macos-updates on|off")
        return

    # --- /maintenance macos-restart on|off (auto-reboot toggle for softwareupdate) ---
    if subcmd == "macos-restart":
        if platform.system() != "Darwin":
            await update.message.reply_text("macOS-only setting. This machine is not macOS.")
            return
        if arg.lower() in ("on", "true", "yes", "1"):
            update_config(macos_system_updates_restart=True)
            await update.message.reply_text(
                "Auto-restart enabled. Updates that require a reboot will trigger one at 4:00 AM."
            )
        elif arg.lower() in ("off", "false", "no", "0"):
            update_config(macos_system_updates_restart=False)
            await update.message.reply_text(
                "Auto-restart disabled. You'll need to reboot manually after updates that require it."
            )
        else:
            await update.message.reply_text("Usage: /maintenance macos-restart on|off")
        return

    # --- /maintenance run <task> ---
    if subcmd == "run":
        task = arg.lower()
        if task == "backup":
            config = load_config()
            if not config.get("backup_enabled") or not config.get("backup_path"):
                await update.message.reply_text(
                    "Backup not configured. Set it up first:\n"
                    "  /maintenance backup /path/to/drive"
                )
                return
            await update.message.reply_text("Running backup...")
            from utils.backup import create_backup
            result = await asyncio.to_thread(create_backup, config["backup_path"])
            await update.message.reply_text(result)
        elif task == "update":
            await update.message.reply_text("Running system update...")
            from utils.system_update import run_system_update
            result = await asyncio.to_thread(run_system_update)
            await update.message.reply_text(result)
        elif task == "cleanup":
            await update.message.reply_text("Running cleanup...")
            from utils.cleanup import run_cleanup
            result = await asyncio.to_thread(run_cleanup)
            await update.message.reply_text(result)
        else:
            await update.message.reply_text(
                "Usage: /maintenance run backup|update|cleanup"
            )
        return

    await update.message.reply_text(
        "Unknown subcommand. Use /maintenance to see available commands."
    )


# --- Skill Stats ---

def _fmt_skill_duration(ms) -> str:
    """Format milliseconds to human-readable duration for /skillstats."""
    if ms is None:
        return "\u2014"
    ms = int(round(ms))
    if ms < 0:
        return "0ms"
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms / 60000:.1f}m"


@requires_auth
async def skillstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show skill usage statistics. Admin only."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Only admins can view skill stats.")
        return

    args = (update.message.text or "").split(maxsplit=1)
    subcommand = args[1].strip().lower() if len(args) > 1 else "summary"

    db_path = DATA_DIR / "skill_usage.db"
    if not db_path.exists():
        await update.message.reply_text("No skill usage data yet. Use skills with hooks active to start recording.")
        return

    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = _sqlite3.Row

    try:
        if subcommand == "summary":
            total = conn.execute("SELECT COUNT(*) FROM skill_invocations").fetchone()[0]
            if total == 0:
                await update.message.reply_text("No invocations recorded yet.")
                return

            rows = conn.execute("""
                SELECT skill_name,
                       COUNT(*) as cnt,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as ok,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail,
                       AVG(duration_ms) as avg_dur
                FROM skill_invocations
                GROUP BY skill_name
                ORDER BY cnt DESC
                LIMIT 15
            """).fetchall()

            text = f"Skill Usage ({total} total)\n\n"
            for r in rows:
                avg = _fmt_skill_duration(r["avg_dur"])
                fail_str = f" / {r['fail']} fail" if r["fail"] else ""
                text += f"  {r['skill_name']}: {r['cnt']}x ({r['ok']} ok{fail_str}) avg {avg}\n"

            fails = conn.execute("SELECT COUNT(*) FROM skill_invocations WHERE success = 0").fetchone()[0]
            if fails > 0:
                text += f"\n{fails} total failures. Use /skillstats failures for details."

            for chunk in split_message(text):
                await update.message.reply_text(chunk)

        elif subcommand == "recent":
            rows = conn.execute("""
                SELECT skill_name, started_at, duration_ms, success
                FROM skill_invocations
                ORDER BY started_at DESC
                LIMIT 20
            """).fetchall()
            if not rows:
                await update.message.reply_text("No invocations recorded yet.")
                return

            text = "Recent Skill Invocations\n\n"
            for r in rows:
                ts = datetime.fromtimestamp(r["started_at"]).strftime("%m-%d %H:%M")
                dur = _fmt_skill_duration(r["duration_ms"])
                status = "ok" if r["success"] == 1 else ("FAIL" if r["success"] == 0 else "?")
                text += f"  {ts}  {r['skill_name']}  {dur}  {status}\n"

            for chunk in split_message(text):
                await update.message.reply_text(chunk)

        elif subcommand == "failures":
            rows = conn.execute("""
                SELECT skill_name, started_at, duration_ms, error
                FROM skill_invocations
                WHERE success = 0
                ORDER BY started_at DESC
                LIMIT 15
            """).fetchall()
            if not rows:
                await update.message.reply_text("No failures recorded.")
                return

            text = f"Skill Failures ({len(rows)} most recent)\n\n"
            for r in rows:
                ts = datetime.fromtimestamp(r["started_at"]).strftime("%m-%d %H:%M")
                err = (r["error"] or "unknown")[:80].replace("\n", " ")
                text += f"  {ts}  {r['skill_name']}\n    {err}\n\n"

            for chunk in split_message(text):
                await update.message.reply_text(chunk)

        elif subcommand == "daily":
            rows = conn.execute("""
                SELECT date(started_at, 'unixepoch', 'localtime') as day,
                       COUNT(*) as cnt,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail,
                       COUNT(DISTINCT skill_name) as unique_skills
                FROM skill_invocations
                WHERE started_at >= ?
                GROUP BY day
                ORDER BY day DESC
            """, (time.time() - 30 * 86400,)).fetchall()
            if not rows:
                await update.message.reply_text("No invocations in the last 30 days.")
                return

            text = "Daily Skill Usage (last 30 days)\n\n"
            for r in rows:
                fail_str = f" / {r['fail']} fail" if r["fail"] else ""
                text += f"  {r['day']}  {r['cnt']}x{fail_str}  ({r['unique_skills']} skills)\n"

            for chunk in split_message(text):
                await update.message.reply_text(chunk)

        elif subcommand == "help":
            await update.message.reply_text(
                "/skillstats \u2014 Usage summary\n"
                "/skillstats recent \u2014 Last 20 invocations\n"
                "/skillstats failures \u2014 Recent failures\n"
                "/skillstats daily \u2014 Daily breakdown (30 days)\n"
                "/skillstats <name> \u2014 Stats for one skill"
            )
            return

        else:
            # Treat as skill name lookup
            skill_name = subcommand
            stats = conn.execute("""
                SELECT COUNT(*) as cnt,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as ok,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fail,
                       AVG(duration_ms) as avg_dur,
                       MIN(started_at) as first_use,
                       MAX(started_at) as last_use
                FROM skill_invocations
                WHERE skill_name = ?
            """, (skill_name,)).fetchone()

            if stats["cnt"] == 0:
                skills = conn.execute(
                    "SELECT DISTINCT skill_name FROM skill_invocations ORDER BY skill_name"
                ).fetchall()
                skill_list = ", ".join(r["skill_name"] for r in skills) if skills else "none"
                await update.message.reply_text(
                    f"No data for '{skill_name}'.\n\nAvailable: {skill_list}"
                )
                return

            first = datetime.fromtimestamp(stats["first_use"]).strftime("%Y-%m-%d")
            last = datetime.fromtimestamp(stats["last_use"]).strftime("%Y-%m-%d %H:%M")
            text = (
                f"Skill: {skill_name}\n\n"
                f"  Total: {stats['cnt']}\n"
                f"  OK: {stats['ok']}  Failed: {stats['fail']}\n"
                f"  Avg duration: {_fmt_skill_duration(stats['avg_dur'])}\n"
                f"  First used: {first}\n"
                f"  Last used: {last}\n"
            )
            await update.message.reply_text(text)

    except Exception as e:
        logger.error(f"Skillstats error: {e}")
        await update.message.reply_text(f"Error querying stats: {e}")
    finally:
        conn.close()


def _configure_claude_hooks():
    """Auto-configure Claude Code hooks in ~/.claude/settings.json.

    Adds PreToolUse/PostToolUse/PostToolUseFailure/Stop hooks for skill
    monitoring when using Claude CLI as the provider. Merges with existing
    settings without overwriting user configuration.

    Called once at startup if Claude CLI is the active provider.
    """
    settings_file = Path.home() / ".claude" / "settings.json"
    gate_script = str(BOT_DIR / "utils" / "skill_hook_gate.sh")
    hooks_script = f"python3 {BOT_DIR / 'utils' / 'skill_hooks.py'}"

    # Define the hooks we need
    needed_hooks = {
        "PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": gate_script, "timeout": 10}]
        }],
        "PostToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": gate_script, "timeout": 10}]
        }],
        "PostToolUseFailure": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": gate_script, "timeout": 10}]
        }],
        "Stop": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": hooks_script, "timeout": 15}]
        }],
    }

    try:
        # Ensure ~/.claude/ exists
        settings_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing settings
        settings = {}
        if settings_file.exists():
            try:
                settings = json.loads(settings_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass

        existing_hooks = settings.get("hooks", {})

        # Check if our hooks are already configured (by checking for our gate script)
        already_configured = any(
            "skill_hook_gate.sh" in hook.get("command", "") or
            "skill_hooks.py" in hook.get("command", "")
            for event_hooks in existing_hooks.values()
            if isinstance(event_hooks, list)
            for entry in event_hooks
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
        )

        if already_configured:
            logger.debug("Claude Code skill hooks already configured")
            return

        # Merge our hooks into existing config
        for event_name, hook_entries in needed_hooks.items():
            if event_name not in existing_hooks:
                existing_hooks[event_name] = []
            existing_hooks[event_name].extend(hook_entries)

        settings["hooks"] = existing_hooks

        # Write back atomically
        tmp_path = settings_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(settings_file)

        logger.info("Claude Code skill hooks configured in ~/.claude/settings.json")
    except Exception as e:
        logger.warning(f"Failed to configure Claude Code hooks (non-fatal): {e}")


# --- Main ---

def main():
    global _llm_provider, _skill_manager, _memory_manager, _semaphore_active

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

    # Synchronous startup health check — surfaces dyld / GLIBC / bad-API-key
    # failures up front so the bot logs them at boot instead of returning a
    # stack trace on the first user message. Result is stashed on the provider
    # for call_llm() to consult; we never auto-swap providers here (per design,
    # the user resolves with /provider).
    try:
        healthy, reason = asyncio.run(_llm_provider.health_check())
    except Exception as exc:
        healthy, reason = False, f"health_check raised {exc.__class__.__name__}: {exc}"
    _llm_provider.last_health = (healthy, reason)
    if healthy:
        logger.info(f"LLM provider health-check: OK ({reason})")
    else:
        logger.error(
            f"LLM provider health-check FAILED: {reason}. "
            f"The bot will start, but /provider will need to be used to "
            f"switch to a working provider before messages can be answered."
        )

    # Wire up progress callbacks for both CLI providers (Claude, Codex)
    if isinstance(_llm_provider, _CLI_PROVIDERS):
        _llm_provider.on_progress_save = save_task_progress
        _llm_provider.on_progress_clear = clear_task_progress
    # ~/.claude/settings.json hooks are Claude-CLI-specific. Codex CLI uses
    # ~/.codex/ and does not honor these hooks.
    if isinstance(_llm_provider, ClaudeCLIProvider):
        _configure_claude_hooks()

    # Hardware-aware concurrency control: enable semaphore on low-resource machines
    # Note: actual asyncio.Semaphore is created lazily on first use inside the event
    # loop (_get_llm_semaphore), because creating it here would bind to the wrong
    # loop on Python 3.8-3.9. We just set the flag here.
    caps = load_caps(DATA_DIR)
    ram_gb = caps.get("ram_gb", 0)
    cpu_cores = caps.get("cpu_cores", 0)
    hardware_constrained = ram_gb > 0 and (ram_gb < 8 or cpu_cores <= 2)

    # Multi-user request queue: when the orchestrator config sets
    # concurrent_requests >= 1, serialize LLM calls across all slots so two
    # users can't simultaneously stress a low-RAM box.
    multiuser_queue = False
    try:
        from core.users import is_multiuser_enabled, concurrent_requests
        if is_multiuser_enabled() and concurrent_requests() >= 1:
            multiuser_queue = True
    except ImportError:
        pass

    if hardware_constrained or multiuser_queue:
        _semaphore_active = True
        reason_parts = []
        if hardware_constrained:
            reason_parts.append(f"hardware ({ram_gb}GB RAM, {cpu_cores} cores)")
        if multiuser_queue:
            reason_parts.append("multi-user queue enabled")
        logger.info(
            f"LLM semaphore ENABLED ({', '.join(reason_parts)}); "
            f"concurrent LLM calls will be serialized"
        )
    else:
        logger.info(
            f"LLM semaphore disabled (RAM: {ram_gb}GB, CPU cores: {cpu_cores}) — "
            f"full concurrency allowed"
        )

    # Startup cleanup — kill orphaned skill processes from previous sessions
    try:
        from utils.startup_cleanup import run_startup_cleanup
        cleanup_summary = run_startup_cleanup()
        total_cleaned = sum(cleanup_summary.values())
        if total_cleaned > 0:
            logger.info(f"Startup cleanup: {cleanup_summary}")
    except Exception as e:
        logger.warning(f"Startup cleanup failed (non-fatal): {e}")

    # Initialize skills
    _skill_manager = SkillManager(SKILLS_DIR)
    logger.info(f"Loaded {len(_skill_manager.skills)} skills")

    # Initialize deep memory system
    _memory_manager = MemoryManager(DATA_DIR)
    logger.info("Memory system initialized")

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

    # Track all incoming updates for polling health monitoring
    async def _track_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
        record_polling_update()

    app.add_handler(TypeHandler(Update, _track_update), group=-1)

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
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("recover", recover_command))
    app.add_handler(CommandHandler("clear_recovery", clear_recovery_command))
    app.add_handler(CommandHandler("topic", topic_command))
    app.add_handler(CommandHandler("topics", list_topics_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("jobs", jobs_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("removeuser", removeuser_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("purgeuser", purgeuser_command))
    app.add_handler(CommandHandler("cleanup", cleanup_command))
    app.add_handler(CommandHandler("maintenance", maintenance_command))
    app.add_handler(CommandHandler("system", system_command))
    app.add_handler(CommandHandler("update", update_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("provider", provider_command))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("apikey", apikey_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("alias", alias_command))
    app.add_handler(CommandHandler("skillstats", skillstats_command))

    # Catch-all for unrecognized /commands — check aliases before rejecting
    async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle unknown commands — check if they're aliases."""
        # Auth check — same as handle_message
        user_id = update.effective_user.id
        allowed = get_allowed_users()
        if not allowed or user_id not in allowed:
            return
        # Duplicate protection — same as handle_message
        msg_id = update.message.message_id
        if msg_id in _processed_ids:
            return
        _processed_ids.add(msg_id)
        if len(_processed_ids) > _MAX_PROCESSED:
            cutoff = min(_processed_ids) + (_MAX_PROCESSED // 2)
            _processed_ids.difference_update({mid for mid in _processed_ids if mid < cutoff})
        if await _try_alias(update, context):
            await _process_single(update, context)
        # If not an alias, silently ignore (don't spam "unknown command")

    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Message handler (text, photos, documents, audio, video, voice, stickers)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Document.ALL |
         filters.AUDIO | filters.VIDEO | filters.VOICE |
         filters.VIDEO_NOTE | filters.Sticker.ALL) & ~filters.COMMAND,
        handle_message,
    ))

    # Startup: initialize scheduler, recover pending messages, health monitor, send first boot
    async def post_init(application):
        scheduler = init_scheduler(token, api_base)
        scheduler.set_claude_handler(call_llm)
        scheduler.start()
        logger.info("Scheduler started with Claude handler")
        await recover_pending_messages(application.bot)

        # Connect to configured MCP servers (if any)
        from core.mcp_client import get_mcp_manager
        mcp = get_mcp_manager()
        if mcp.available:
            try:
                results = await mcp.connect_all()
                for name, status in results.items():
                    logger.info(f"MCP server '{name}': {status}")
            except Exception as e:
                logger.warning(f"MCP initialization failed: {e}")

        # Schedule nightly reflection job if not already present
        _setup_reflection_job(scheduler)

        # Schedule maintenance jobs (system update, cleanup, backup) if not already present
        _setup_maintenance_jobs(scheduler)

        # Start proactive health monitoring (system resources)
        asyncio.create_task(_health_monitor_loop(scheduler))

        # Start polling liveness monitor (detects stale Telegram connection)
        polling_monitor = init_polling_monitor(
            local_api_base=api_base,
        )
        polling_monitor.start()

        # System capability probe — runs once on first boot
        caps_file = DATA_DIR / "system_caps.json"
        if not caps_file.exists():
            logger.info("Running first-boot system capability probe...")
            try:
                caps = probe_system(DATA_DIR)
                logger.info(f"Probe result: {caps.get('summary', {})}")
            except Exception as e:
                logger.warning(f"System probe failed: {e}")

        # Intro flow migration. Pre-existing users with a populated model from
        # earlier deployments must not see the intro again on their next message.
        # Gated by a sentinel so we walk the user list at most once per deploy.
        intro_migration_marker = DATA_DIR / ".intro_migration_done"
        if _memory_manager and not intro_migration_marker.exists():
            for uid in _memory_manager.get_all_users():
                if _memory_manager.get_model(uid):
                    if not _memory_manager.intro_shown(uid):
                        _memory_manager.mark_intro_shown(uid)
                    if not _memory_manager.intro_done(uid):
                        _memory_manager.mark_intro_done(uid)
            # Retire the legacy global first-boot marker if it exists
            legacy_marker = DATA_DIR / ".first_boot_sent"
            if legacy_marker.exists():
                try:
                    legacy_marker.unlink()
                except OSError:
                    pass
            try:
                intro_migration_marker.touch()
            except OSError as e:
                logger.warning(f"Could not write intro migration marker: {e}")

    # Shutdown: stop scheduler, polling monitor, MCP, kill background processes
    async def post_shutdown(application):
        # Stop polling health monitor
        polling_monitor = get_polling_monitor()
        if polling_monitor:
            await polling_monitor.stop()
        scheduler = get_scheduler()
        if scheduler:
            scheduler.stop()
        # Disconnect MCP servers
        from core.mcp_client import get_mcp_manager
        try:
            await get_mcp_manager().disconnect_all()
        except Exception as e:
            logger.warning(f"MCP shutdown error: {e}")
        # Kill any background processes from the tool execution layer
        registry = get_process_registry()
        running = registry.list_running()
        if running:
            logger.info(f"Killing {len(running)} background processes on shutdown...")
            await registry.cleanup_all()
        if isinstance(_llm_provider, _CLI_PROVIDERS):
            await _llm_provider.graceful_shutdown()

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    logger.info(f"Starting {get_bot_name()}...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
