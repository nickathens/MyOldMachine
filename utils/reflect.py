#!/usr/bin/env python3
"""
Nightly Reflection Script — Overnight Learning for the Memory System

Runs as a scheduled job (typically 3:00 AM). Reads recent observations for
each user, analyzes patterns via LLM, and updates person models.

Architecture:
    Phase 0: Parse & Route — deterministic Python, no LLM
        - Parse observations into structured records
        - Route project-scoped observations to project state.json 'lessons' arrays
        - Sum importance scores; skip reflection if below threshold
        - Filter out project-scoped observations (they've been routed)
    Phase 1: Generate Questions — LLM call #1 (short, capable providers only)
        - Given observations, generate 3 key questions to focus the update
    Phase 2: Update Model — LLM call #2 (full)
        - Answer the questions, produce targeted model update with strict quality rules

Tier-aware:
- Capable providers (Claude CLI, paid APIs): Two-phase reflection (better quality)
- Weaker providers (Ollama, free tier, OpenRouter): Single-call reflection (saves tokens)
- No capable model: Skips reflection entirely

Usage:
    python reflect.py                          # Full reflection for all users
    python reflect.py --dry                    # Preview without writing
    python reflect.py --user 12345             # Reflect on one user only
    python reflect.py --threshold 20           # Custom importance threshold
"""

import argparse
try:
    import fcntl
except ImportError:
    fcntl = None
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from shutil import which

from dotenv import load_dotenv

# Add parent directory to path
BOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BOT_DIR))

# Load .env before importing config (scheduler strips env vars)
load_dotenv(BOT_DIR / ".env")

from core.memory import MemoryManager
from core.config import DATA_DIR, get_llm_provider, get_llm_model, get_llm_api_key, get_ollama_base_url

logger = logging.getLogger(__name__)
LOG_FILE = DATA_DIR / "logs" / "reflection.log"

# Importance threshold: sum of importance scores must exceed this to trigger reflection
DEFAULT_THRESHOLD = 16

# Phase 2 dual-budget retry caps. Two failure modes get distinct budgets:
# - 0-byte returns: transient model burp, deserve generous retries with backoff
# - Non-empty unparseable returns: real format issue, fewer retries before giving up
PHASE2_MAX_EMPTY_RETRIES = 4
PHASE2_MAX_FORMAT_RETRIES = 2
PHASE2_RETRY_BACKOFF_SEC = 30


def log(msg: str):
    """Append to reflection log."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} | REFLECT | {msg}"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry + "\n")
    except Exception:
        pass
    print(msg)


# ─── Observation Parsing ───────────────────────────────────────────

def parse_observation(line: str) -> dict:
    """Parse a single observation line into a structured record.

    Handles both old format:
        [2026-03-09 15:57] (correction) Content here
    And new format:
        [2026-03-09 15:57] (correction) [importance:8] [project:my-app] Content here
    """
    record = {
        "raw": line,
        "timestamp": "",
        "type": "",
        "importance": 5,  # default for old-format observations
        "project": None,
        "seen": 1,  # corroboration count: how many times this pattern was observed
        "content": "",
    }

    # Extract timestamp
    ts_match = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]', line)
    if not ts_match:
        return None
    record["timestamp"] = ts_match.group(1)

    # Extract type (supports hyphenated types like self-eval)
    type_match = re.search(r'\(([\w-]+)\)', line)
    if not type_match:
        return None
    record["type"] = type_match.group(1)

    # Get everything after the type
    after_type = line[type_match.end():].strip()

    # Extract metadata tags (new format)
    while after_type.startswith("["):
        # Skip [reflected] marker — it's not a key:value tag
        reflected_match = re.match(r'\[reflected\]', after_type)
        if reflected_match:
            after_type = after_type[reflected_match.end():].strip()
            continue
        tag_match = re.match(r'\[(\w+):([^\]]+)\]', after_type)
        if not tag_match:
            break
        key, value = tag_match.group(1), tag_match.group(2)
        if key == "importance":
            try:
                record["importance"] = int(value)
            except ValueError:
                pass
        elif key == "project":
            record["project"] = value
        elif key == "seen":
            try:
                record["seen"] = int(value)
            except ValueError:
                pass
        after_type = after_type[tag_match.end():].strip()

    record["content"] = after_type
    return record


def get_recent_observations(user_id: int, mm: MemoryManager, days: int = 7) -> list:
    """Get unprocessed observations from the last N days.

    Observations marked with [reflected] are skipped to prevent reprocessing.
    """
    obs_file = mm._observations_file(user_id)
    if not obs_file.exists():
        return []

    content = obs_file.read_text(encoding="utf-8")
    lines = [line for line in content.split("\n") if line.startswith("[")]

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    records = []
    for line in lines:
        # Skip already-reflected observations
        if "[reflected]" in line:
            continue
        try:
            date_str = line[1:11]
            if date_str >= cutoff:
                record = parse_observation(line)
                if record:
                    records.append(record)
        except (IndexError, ValueError):
            continue

    return records


def compute_importance_sum(records: list) -> int:
    """Sum the importance scores of all observation records."""
    return sum(r["importance"] for r in records)


# ─── Phase 0: Route & Filter (deterministic, no LLM) ──────────────

def route_project_observations(records: list, mm: MemoryManager, user_id: int) -> tuple:
    """Route project-scoped observations to project state files.

    Returns:
        (routed_records, remaining_records)
    """
    routed = []
    remaining = []

    # Check if projects dir exists for this user
    projects_dir = mm._user_dir(user_id) / "projects"

    for record in records:
        if record["project"]:
            project_slug = record["project"]
            # Validate slug: no path traversal, alphanumeric + hyphens only
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', project_slug):
                log(f"  WARNING: Invalid project slug '{project_slug}', skipping")
                remaining.append(record)
                continue
            state_file = projects_dir / project_slug / "state.json"

            if state_file.exists():
                _append_lesson_to_project(state_file, record)
                routed.append(record)
                log(f"  Routed observation to project '{project_slug}': {record['content'][:80]}")
            else:
                # Project doesn't exist — keep in model reflection
                log(f"  WARNING: Project '{project_slug}' not found, keeping in model pool")
                remaining.append(record)
        else:
            remaining.append(record)

    return routed, remaining


def _append_lesson_to_project(state_file: Path, record: dict):
    """Append a lesson entry to a project's state.json."""
    try:
        with open(state_file, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return

    if "lessons" not in data:
        data["lessons"] = []

    lesson = {
        "date": record["timestamp"][:10],
        "type": record["type"],
        "content": record["content"],
    }

    # Avoid duplicate lessons (same date + same content)
    for existing in data["lessons"]:
        if existing.get("date") == lesson["date"] and existing.get("content") == lesson["content"]:
            return

    data["lessons"].append(lesson)

    # Keep only last 20 lessons per project
    if len(data["lessons"]) > 20:
        data["lessons"] = data["lessons"][-20:]

    data["updated"] = datetime.now().strftime("%Y-%m-%d")

    # Atomic write: temp file + fsync + rename to prevent corruption
    tmp = state_file.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.rename(state_file)


# ─── Mark Observations as Reflected ───────────────────────────────

def _mark_observations_reflected(user_id: int, mm: MemoryManager, records: list):
    """Mark processed observations as reflected so they aren't reprocessed.

    Adds [reflected] tag after the type marker. This prevents the same
    observations from being fed into reflection every night for 7 days.
    """
    obs_file = mm._observations_file(user_id)
    if not obs_file.exists():
        return

    # Hold exclusive lock for the entire read-modify-write cycle to prevent
    # add_observation() from appending between read and rename (data loss).
    lock_fd = os.open(str(obs_file), os.O_RDONLY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        content = obs_file.read_text(encoding="utf-8")

        # Build a set of raw lines to match
        raw_lines = {r["raw"] for r in records}

        new_lines = []
        marked = 0
        for line in content.split("\n"):
            if line in raw_lines and "[reflected]" not in line:
                # Insert [reflected] tag after the type marker
                type_match = re.search(r'\(([\w-]+)\)', line)
                if type_match:
                    insert_pos = type_match.end()
                    line = line[:insert_pos] + " [reflected]" + line[insert_pos:]
                    marked += 1
            new_lines.append(line)

        if marked > 0:
            tmp = obs_file.with_suffix(".md.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(new_lines))
                f.flush()
                os.fsync(f.fileno())
            tmp.rename(obs_file)
            log(f"  Marked {marked} observations as reflected for user {user_id}")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


# ─── LLM Calls ────────────────────────────────────────────────────

# Cap detection: a capped Claude CLI prints a short usage/spend-limit notice to
# stdout and exits 0, so a naive "exit 0 + non-empty stdout" check would return
# that notice as if it were reflection output. That poisons the person model and
# burns the retry budget on a CLI that cannot answer. Ported from the production
# bot's account_pool.is_cap_message: require the "hit your ... limit" anchor AND
# one corroborating clause (reset time, reset duration, or spend marker), bounded
# by length, so a long response that merely mentions the phrase never trips it.
_CAP_ANCHOR = re.compile(r"you'?ve\s+hit\s+your\s+(?:\S+\s+){0,3}limit", re.IGNORECASE)
_CAP_RESET = re.compile(r"resets?(?:\s+at)?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?", re.IGNORECASE)
_CAP_RESET_DURATION = re.compile(r"resets?\s+in\s+\d", re.IGNORECASE)
_CAP_SPEND = re.compile(r"settings/usage|raise it at|spend limit", re.IGNORECASE)
_MAX_CAP_MESSAGE_LEN = 400


def _is_cap_message(text: str) -> bool:
    """True if `text` is a Claude CLI usage/spend-cap notice, not real output."""
    if not text or not isinstance(text, str) or len(text) > _MAX_CAP_MESSAGE_LEN:
        return False
    if not _CAP_ANCHOR.search(text):
        return False
    return bool(
        _CAP_RESET.search(text)
        or _CAP_RESET_DURATION.search(text)
        or _CAP_SPEND.search(text)
    )


# Why a provider call failed, kept so the admin alert can name a cause. The
# providers report failures as an empty return value, so without capturing the
# detail here it is gone by the time the alert is written. Thread-local because
# intro reflections run on executor threads in the bot process, and two users
# onboarding at once must not have their failures cross-attributed.
_provider_error = threading.local()


def _record_provider_error(detail: str) -> str:
    """Remember why a provider call failed. Returns the detail, so call sites can
    wrap their existing log() call instead of growing an extra line."""
    _provider_error.detail = (detail or "").strip()
    return _provider_error.detail


def _recorded_provider_error() -> str:
    return getattr(_provider_error, "detail", "")


# Failures that mean "a human must log in on the machine" — the Claude CLI's
# OAuth session is dead. Matched only against Claude CLI failures: the same
# words in an API provider's error body mean a bad API key, and a `claude`
# login cannot fix that.
_CLI_LOGIN_FAILURE_RE = re.compile(
    r"oauth .{0,40}expired|could not be refreshed|not authenticated|"
    r"failed to authenticate|re-authenticate|please run .?claude",
    re.IGNORECASE,
)

# Failures that mean "the provider rejected the configured API key". The fix is
# that provider's key in .env, not the Claude CLI login.
_API_KEY_FAILURE_RE = re.compile(
    r"invalid[ _]api[ _]key|api key not valid|authentication_error|"
    r"invalid x-api-key|unauthorized|\b401\b",
    re.IGNORECASE,
)


def _describe_no_llm_failure() -> str:
    """Explain a no_llm outcome in terms the admin can act on.

    This used to be the fixed string "No capable model available", which points
    at model configuration whatever the real cause was. The recorded provider
    detail decides the wording, and the two humanly-fixable causes each name
    their own fix. Which advice applies depends on which provider failed, not
    just on the words in the error: `authentication_error` from the CLI means
    the OAuth login died (re-login fixes it), while the same token from an API
    provider means the configured key is bad (re-login fixes nothing). Every
    detail recorded by the CLI paths starts with "Claude CLI", which is what
    the gate keys on.
    """
    detail = _recorded_provider_error()
    if not detail:
        return "No capable model available (providers returned no output and no error)"
    from_cli = detail.startswith("Claude CLI")
    if from_cli and _CLI_LOGIN_FAILURE_RE.search(detail):
        return ("Claude login expired — run `claude` in Terminal on the machine to log "
                f"in again. Provider said: {detail[:200]}")
    if not from_cli and _API_KEY_FAILURE_RE.search(detail):
        return ("API key rejected — check that provider's API key in .env; a `claude` "
                f"login will not fix this. Provider said: {detail[:200]}")
    return f"No usable model output. Provider said: {detail[:200]}"


def _call_claude_cli(prompt: str) -> str:
    """Call Claude CLI for reflection. Returns output text or empty string."""
    if not which("claude"):
        # Recorded but not logged: on an API-only install this is the normal
        # fallback path on every call, not an event worth a log line.
        _record_provider_error("Claude CLI not installed (claude not on PATH)")
        return ""
    try:
        # Use the configured model if it's a Claude model, otherwise use default
        configured_model = get_llm_model()
        if configured_model.startswith("claude-"):
            cli_model = configured_model
        else:
            cli_model = "claude-sonnet-5"
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", cli_model],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            out = result.stdout.strip()
            if _is_cap_message(out):
                log(_record_provider_error(
                    "Claude CLI is capped (usage or spend limit); treating as "
                    "unavailable so reflection fails over to the API provider"))
                return ""
            return out
        # The CLI reports *why* it failed on stdout — expired login, unknown
        # model, usage cap — and leaves stderr for unrelated warnings. Logging
        # stderr alone printed a bare "stderr=" on the night the login expired,
        # which is exactly when the reason mattered most.
        detail = (result.stdout or "").strip() or (result.stderr or "").strip() or "no output"
        log(_record_provider_error(
            f"Claude CLI failed: exit={result.returncode}: {detail[:300]}"))
    except subprocess.TimeoutExpired:
        log(_record_provider_error("Claude CLI timed out (120s)"))
    except Exception as e:
        log(_record_provider_error(f"Claude CLI error: {e}"))
    return ""


def _call_api(prompt: str) -> str:
    """Call the configured API provider for reflection. Returns output text or empty string."""
    import httpx

    provider = get_llm_provider()
    model = get_llm_model()
    api_key = get_llm_api_key()

    # Ollama (local and cloud) doesn't always need an API key
    if not api_key and provider not in ("ollama", "ollama-cloud"):
        # Only if nothing failed yet this run: _call_api runs after the CLI, and
        # a config note must not overwrite the CLI's real failure (the expired
        # login on a CLI-primary box lands exactly here).
        if not _recorded_provider_error():
            _record_provider_error(f"No API key configured for provider '{provider}'")
        return ""

    # Providers that support reflection (need decent reasoning ability)
    api_configs = {
        "openai": ("https://api.openai.com/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "deepseek": ("https://api.deepseek.com/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "grok": ("https://api.x.ai/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "kimi": ("https://api.moonshot.ai/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "minimax": ("https://api.minimax.io/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "zai": ("https://api.z.ai/api/paas/v4/chat/completions", "Authorization", f"Bearer {api_key}"),
        "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "claude-api": ("https://api.anthropic.com/v1/messages", None, None),
    }

    if provider not in api_configs and provider not in ("gemini", "google", "ollama", "ollama-cloud"):
        if not _recorded_provider_error():
            _record_provider_error(f"Provider '{provider}' has no API fallback for reflection")
        return ""

    try:
        if provider in ("ollama", "ollama-cloud"):
            base_url = get_ollama_base_url()
            url = f"{base_url}/v1/chat/completions"
            headers = {"Content-Type": "application/json"}
            # Ollama cloud needs auth
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            body = {
                "model": model,
                "max_tokens": 4096,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": "You are analyzing behavioral observations to update a person model."},
                    {"role": "user", "content": prompt},
                ],
            }
            with httpx.Client(timeout=300.0) as client:
                resp = client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message") or {}
                        return msg.get("content", "")
                else:
                    log(_record_provider_error(
                        f"Ollama API returned {resp.status_code}: {resp.text[:200]}"))
            return ""

        elif provider in ("gemini", "google"):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 4096, "temperature": 0.3},
            }
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(url, headers={"x-goog-api-key": api_key}, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates") or [{}]
                    content = candidates[0].get("content") or {}
                    parts = content.get("parts") or []
                    return "".join(p.get("text", "") for p in parts)
                else:
                    log(_record_provider_error(
                        f"Gemini API returned {resp.status_code}: {resp.text[:200]}"))
            return ""

        elif provider == "claude-api":
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": 4096,
                        "temperature": 0.3,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return "".join(
                        b["text"] for b in data.get("content", []) if b.get("type") == "text"
                    )
                else:
                    log(_record_provider_error(
                        f"Claude API returned {resp.status_code}: {resp.text[:200]}"))
            return ""

        else:
            url, auth_header, auth_value = api_configs[provider]
            headers = {"Content-Type": "application/json"}
            if auth_header:
                headers[auth_header] = auth_value

            # Determine token parameter name and temperature support
            is_openai_reasoning = provider == "openai" and (
                model.startswith("gpt-5") or model.startswith("o1")
                or model.startswith("o3") or model.startswith("o4")
            )
            is_grok_reasoning = provider == "grok" and (
                ("reasoning" in model and "non-reasoning" not in model)
                or (model.startswith("grok-4") and "fast" not in model)
                or "mini" in model
            )
            is_deepseek_reasoner = provider == "deepseek" and "reasoner" in model

            uses_completion_tokens = is_openai_reasoning or is_grok_reasoning or provider == "openrouter"
            rejects_temperature = is_openai_reasoning or is_grok_reasoning or is_deepseek_reasoner

            token_key = "max_completion_tokens" if uses_completion_tokens else "max_tokens"
            body = {
                "model": model,
                token_key: 4096,
                "messages": [
                    {"role": "system", "content": "You are analyzing behavioral observations to update a person model."},
                    {"role": "user", "content": prompt},
                ],
            }
            if not rejects_temperature:
                body["temperature"] = 0.3

            with httpx.Client(timeout=120.0) as client:
                resp = client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message") or {}
                        return msg.get("content", "")
                else:
                    log(_record_provider_error(
                        f"{provider} API returned {resp.status_code}: {resp.text[:200]}"))
            return ""

    except Exception as e:
        log(_record_provider_error(f"API reflection failed ({provider}): {e}"))
        return ""


def _call_llm_for_phase2(prompt: str) -> str:
    """Run one Phase 2 call attempt: try Claude CLI, then API fallback.

    Returns the raw output text, or empty string if both providers returned nothing.
    """
    output = _call_claude_cli(prompt)
    if not output:
        output = _call_api(prompt)
    return output


def _run_phase2_with_retry(prompt: str, mm) -> tuple:
    """Phase 2 (model update) with dual-budget retry on transient failures.

    Two independent budgets:
        empty_count: 0-byte returns from both providers (transient model burp)
        format_count: non-empty returns whose markers can't be parsed (real format issue)

    The CLI 0-byte burp surfaces as 'subprocess returns exit 0 with empty stdout'.
    A single hard failure on those used to land the whole reflection in parse_error.
    The retry budget catches the burp without infinitely looping on real format bugs.

    Returns:
        (model_content, summary, failure_status)
            On success: (model_content, summary, None)
            On all-empty exhaustion: ("", "", "no_llm")
            On format exhaustion: ("", "", "parse_error")
    """
    empty_count = 0
    format_count = 0
    attempt = 0

    # Clear per-run, so a failure recorded while reflecting on one user is never
    # reported as the cause of the next user's failure.
    _record_provider_error("")

    while empty_count < PHASE2_MAX_EMPTY_RETRIES and format_count < PHASE2_MAX_FORMAT_RETRIES:
        attempt += 1
        output = _call_llm_for_phase2(prompt)

        if not output:
            empty_count += 1
            log(f"  Phase 2 attempt {attempt}: both providers returned 0 bytes "
                f"(empty {empty_count}/{PHASE2_MAX_EMPTY_RETRIES}, "
                f"format {format_count}/{PHASE2_MAX_FORMAT_RETRIES})")
            if empty_count < PHASE2_MAX_EMPTY_RETRIES:
                log(f"    transient burp — backing off {PHASE2_RETRY_BACKOFF_SEC}s before retry...")
                time.sleep(PHASE2_RETRY_BACKOFF_SEC)
            continue

        model_content, summary = mm.parse_reflection_output(output)

        if model_content:
            if attempt > 1:
                log(f"  Phase 2 parsed on attempt {attempt} "
                    f"(empty={empty_count}, format={format_count})")
            return model_content, summary, None

        format_count += 1
        log(f"  Phase 2 attempt {attempt}: call returned {len(output)}B "
            f"but parser found no model markers "
            f"(empty {empty_count}/{PHASE2_MAX_EMPTY_RETRIES}, "
            f"format {format_count}/{PHASE2_MAX_FORMAT_RETRIES})")
        log(f"    output[:500]: {output[:500]}")

        if format_count < PHASE2_MAX_FORMAT_RETRIES:
            log(f"    backing off {PHASE2_RETRY_BACKOFF_SEC}s before retry...")
            time.sleep(PHASE2_RETRY_BACKOFF_SEC)

    if empty_count >= PHASE2_MAX_EMPTY_RETRIES:
        log(f"  Phase 2 gave up: {empty_count} total 0-byte returns from providers")
        return "", "", "no_llm"
    log(f"  Phase 2 gave up: {format_count} total unparseable returns from providers")
    return "", "", "parse_error"


# ─── Provider Capability Detection ────────────────────────────────

# Providers capable enough for two-phase reflection (question generation + model update).
# These are paid APIs or strong local models where the extra call is worth the quality gain.
_CAPABLE_PROVIDERS = {"claude", "claude-cli", "claude-api", "openai", "deepseek", "grok", "kimi", "minimax", "zai"}

# Large Ollama models that can handle two-phase reflection
_CAPABLE_OLLAMA_SIZES = {"70b", "72b", "34b", "32b", "405b"}


def _is_capable_provider() -> bool:
    """Determine if the current provider is capable enough for two-phase reflection.

    Two-phase uses 2 LLM calls (questions + update) for better quality.
    Only worth the extra cost/latency on strong models.
    """
    # Claude CLI is always capable
    if which("claude"):
        return True

    provider = get_llm_provider()
    if provider in _CAPABLE_PROVIDERS:
        return True

    # Gemini Pro models are capable, but free-tier Gemini may not be
    if provider in ("gemini", "google"):
        model = get_llm_model().lower()
        return "pro" in model or "ultra" in model

    # Large Ollama models only
    if provider in ("ollama", "ollama-cloud"):
        model = get_llm_model().lower()
        return any(size in model for size in _CAPABLE_OLLAMA_SIZES)

    return False


# ─── Phase 1: Generate Questions (capable providers only) ────────

def _generate_questions(observations_text: str) -> str:
    """Generate 3 key questions from observations using a short LLM call.

    Returns the raw question text, or empty string on failure.
    """
    prompt = (
        "You are analyzing behavioral observations about a person from the last 7 days.\n\n"
        f"## Observations\n{observations_text}\n\n"
        "## Task\n"
        "Based on these observations, identify the 3 most important questions about how "
        "this person's behavior, priorities, communication patterns, or relationship with "
        "the bot has changed or evolved.\n\n"
        "These questions should:\n"
        "- Focus on patterns across multiple observations, not individual events\n"
        "- Ask about WHY something is happening, not just WHAT happened\n"
        "- Identify emerging trends or shifts that the observations hint at but don't explicitly state\n"
        "- Be answerable from the observations + existing model\n\n"
        "Output exactly 3 questions, numbered 1-3, one per line. Nothing else."
    )

    # Try Claude CLI first, then API
    output = _call_claude_cli(prompt)
    if not output:
        output = _call_api(prompt)
    return output


# ─── Prompt Builders ──────────────────────────────────────────────

def _build_strict_prompt(current_model: str, observations_text: str,
                         questions: str, routing_note: str) -> str:
    """Build the strict model update prompt with quality guardrails.

    Used for capable providers (two-phase or single-call with quality rules).
    """
    questions_section = ""
    if questions:
        questions_section = (
            f"\n## Key Questions to Address\n"
            f"These questions were generated from analyzing the observations. "
            f"Answer each one as part of your model update.\n"
            f"{questions}\n"
        )

    step1 = ""
    if questions:
        step1 = (
            "### Step 1: Answer the Questions\n"
            "For each question above, write a brief answer (2-3 sentences) based on the "
            "observations and your understanding of this person. These answers should inform "
            "where and how you update the model.\n\n"
        )

    return (
        "You are a behavioral analyst maintaining a working model of a person.\n\n"
        f"## Current Model\n{current_model}\n\n"
        f"## Recent Behavioral Observations (last 7 days)\n"
        f"These are the observations that should inform the model update. Project-specific "
        f"corrections and lessons have already been routed to their project files — they are "
        f"NOT included here.\n"
        f"Some observations carry a [seen:N] tag with N>=2: the same pattern was independently "
        f"observed N times (corroborated). Treat a higher N as a stronger, more stable signal "
        f"and prefer to retain well-corroborated patterns; a single uncorroborated observation "
        f"should stay tentative.\n{observations_text}\n"
        f"{routing_note}"
        f"{questions_section}\n"
        f"## Your Task\n\n"
        f"{step1}"
        f"### {'Step 2' if questions else 'Step 1'}: Write the Updated Model\n\n"
        "Update the model following these rules:\n\n"
        "Structure: Keep these sections:\n"
        "- Identity (who they are — stable facts and core traits)\n"
        "- Behavioral (how they work, communicate, decide — patterns)\n"
        "- State (current priorities, mood, what they're working on — volatile)\n"
        "- Relationship (trust level, expectations, what works — evolving)\n\n"
        "Section stability:\n"
        "- Identity section: Only change if observations directly contradict it. Most stable section.\n"
        "- Behavioral section: Update when you see a new pattern confirmed by 2+ observations. "
        "Single events don't become behavioral rules.\n"
        "- State section: Update freely — this is volatile by nature.\n"
        "- Relationship section: Update trust/expectations when observations show clear shifts.\n\n"
        "Deduplication (critical):\n"
        "- Each insight appears ONCE, in ONE section. If something is in Behavioral, don't repeat in Relationship.\n"
        "- Before writing a sentence, check: is this already said elsewhere in the model? If yes, don't write it.\n"
        "- The \"Recent Observations\" section at the bottom should say "
        "\"[All observations have been integrated into the model above]\".\n\n"
        "Content rules (STRICT):\n"
        "- Do NOT include project-specific details. No project names, tools, file sizes, resolutions, "
        "competition names, or technical specs. The model describes WHO this person IS and HOW they behave, "
        "not WHAT they're working on. The State section can mention broad focus AREAS "
        "(e.g. \"game development, AI systems\") but not specific project names or technical details.\n"
        "- Do NOT include factual profile data (age, address, weight, dietary targets).\n"
        "- Do NOT use bold (**) formatting. Write everything in plain text.\n"
        "- Update the \"Last updated\" date to today.\n"
        "- Preserve everything that's still accurate.\n"
        "- Remove anything outdated or contradicted by observations.\n"
        "- End the model with: ## Recent Observations (last 7 days)\\n"
        "[All observations have been integrated into the model above]\n\n"
        "Length target: 35-50 lines. If the model exceeds 50 lines, you have duplication or "
        "project detail leakage — cut it.\n\n"
        f"### {'Step 3' if questions else 'Step 2'}: Reflection Summary\n"
        "Write a reflection that demonstrates genuine understanding:\n"
        "- What patterns are emerging vs. what's stable\n"
        "- What you consolidated or removed and why\n"
        "- One prediction about what this person will care about next\n\n"
        "## Output Format — use EXACTLY these markers:\n\n"
        "---MODEL_START---\n"
        "[complete updated model.md content]\n"
        "---MODEL_END---\n\n"
        "---SUMMARY_START---\n"
        "[reflection summary]\n"
        "---SUMMARY_END---\n"
    )


def _build_simple_prompt(current_model: str, observations_text: str,
                         routing_note: str) -> str:
    """Build a simpler single-call prompt for weaker providers.

    Still includes the key quality rules but in a more compact form
    that smaller models can follow reliably.
    """
    return (
        "You are analyzing behavioral observations about a person to update their working model.\n\n"
        f"## Current Model\n{current_model}\n\n"
        f"## Recent Observations (last 7 days)\n"
        f"A [seen:N] tag with N>=2 means the pattern recurred N times; treat higher N as a "
        f"stronger, more stable signal.\n{observations_text}\n"
        f"{routing_note}\n"
        "## Task\n"
        "Write a COMPLETE updated model.md file based on the observations.\n\n"
        "Rules:\n"
        "- Keep these sections: Identity, Behavioral, State, Relationship\n"
        "- Identity: only change if contradicted. Behavioral: new patterns from 2+ observations. "
        "State: update freely. Relationship: update on clear trust shifts.\n"
        "- Each insight appears ONCE in ONE section. No duplicates.\n"
        "- No project names, tool names, file paths, or technical specs. "
        "Describe who they are and how they behave, not what they're working on.\n"
        "- No bold formatting. Plain text only.\n"
        "- No factual profile data (age, weight, address).\n"
        "- Update the \"Last updated\" date to today.\n"
        "- Target 35-50 lines. Cut if over 50.\n"
        "- End with: ## Recent Observations (last 7 days)\\n"
        "[All observations have been integrated into the model above]\n\n"
        "Also write a 2-3 sentence reflection summary.\n\n"
        "Output format — use EXACTLY these markers:\n\n"
        "---MODEL_START---\n"
        "[complete updated model.md content]\n"
        "---MODEL_END---\n\n"
        "---SUMMARY_START---\n"
        "[2-3 sentence reflection summary]\n"
        "---SUMMARY_END---\n"
    )


# ─── Intro Reflection ─────────────────────────────────────────────

def _build_intro_prompt(user_name: str, intro_message: str, user_reply: str) -> str:
    """Build the seed-model prompt for the intro reflection.

    Fired once per user, after they reply to the bot's introduction. Distills
    whatever they shared (name, focus areas, comm style hints) into a small
    starter model. The full model fills in via nightly reflection later.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        "You are seeding a working model for a brand-new user.\n\n"
        "## Bot's introduction (sent to the user)\n"
        f"{intro_message[:2000]}\n\n"
        "## User's first reply\n"
        f"{user_reply[:2000]}\n\n"
        "## Task\n"
        "Extract whatever the user shared and write a concise seed model.\n"
        "If the user said 'skip', declined, or shared nothing useful, write a\n"
        "minimal placeholder. Do not invent details.\n\n"
        "Write a model.md with this exact structure:\n\n"
        f"# {{Name}} — Working Model\n\n"
        f"Last updated: {today}\n\n"
        "## Identity\n"
        f"- Name: {{name they gave, or fall back to: {user_name}}}\n"
        "- (concrete facts they shared, one per line)\n\n"
        "## Preferences\n"
        "(communication style if hinted; language preference if visible. "
        "Otherwise: \"To be discovered\")\n\n"
        "## Current State\n"
        "(focus areas they mentioned; immediate task if any. "
        "Otherwise: \"Just starting\")\n\n"
        "## Relationship\n"
        "- Trust level: New user\n"
        "- Expectations: To be established as we work together\n\n"
        "Rules:\n"
        "- 15 lines max. Do not pad. Do not use bold formatting.\n"
        "- No project-specific details, no file paths, no technical specs.\n"
        "- No invented facts. If they said nothing about a section, keep its placeholder.\n"
        "- Plain text only.\n\n"
        "## Output Format — use EXACTLY these markers:\n\n"
        "---MODEL_START---\n"
        "[seed model content]\n"
        "---MODEL_END---\n"
    )


def run_intro_reflection(mm: MemoryManager, user_id: int, user_name: str,
                          intro_message: str, user_reply: str) -> dict:
    """Distill the user's first reply into a seed person model.

    Called once per user, right after they reply to the introduction. Writes
    whatever the LLM extracts (name, focus, comm style) to model.md so the
    bot has useful context from turn three onward instead of waiting for the
    nightly reflection to populate the model.

    Returns a status dict. On any failure (no LLM available, parse error)
    the caller should still mark intro_done so we don't retry on every turn.
    """
    log(f"Intro reflection for user {user_id}: seeding model from first reply")

    prompt = _build_intro_prompt(user_name, intro_message, user_reply)

    # Clear per-run, same as _run_phase2_with_retry: a failure recorded during an
    # earlier run on this thread must never be reported as this user's cause.
    _record_provider_error("")

    output = _call_claude_cli(prompt)
    if not output:
        output = _call_api(prompt)
    if not output:
        reason = _describe_no_llm_failure()
        log(f"  No LLM available for intro reflection on user {user_id}: {reason}")
        return {"user_id": user_id, "status": "no_llm", "reason": reason}

    model_content, _summary = mm.parse_reflection_output(output)
    if not model_content:
        log(f"  Could not parse intro model for user {user_id}")
        return {"user_id": user_id, "status": "parse_error",
                "output_preview": output[:200]}

    mm.set_model(user_id, model_content)
    mm.log_reflection(user_id, 0,
                      "Intro reflection: seeded model from user's first reply.")
    log(f"  Intro reflection complete for user {user_id} ({len(model_content)} chars)")
    return {"user_id": user_id, "status": "updated", "size": len(model_content)}


# ─── Reflection Pipeline ──────────────────────────────────────────

def run_reflection(mm: MemoryManager, user_id: int, dry_run: bool = False,
                   threshold: int = DEFAULT_THRESHOLD) -> dict:
    """Run the full reflection pipeline for one user."""
    # Get recent unprocessed observations as structured records
    records = get_recent_observations(user_id, mm)
    if not records:
        log(f"No recent observations for user {user_id}, skipping")
        return {"user_id": user_id, "status": "skipped", "reason": "no observations"}

    model = mm.get_model(user_id)
    if not model:
        log(f"No model found for user {user_id}, skipping")
        return {"user_id": user_id, "status": "skipped", "reason": "no model"}

    log(f"Reflecting on user {user_id}: {len(records)} unprocessed observations from last 7 days")

    # ── Phase 0: Route project-scoped observations ──
    routed, remaining = route_project_observations(records, mm, user_id)
    log(f"  Phase 0: {len(routed)} routed to projects, {len(remaining)} for model reflection")

    # Build a summary of what was routed (for LLM context)
    routed_summary = ""
    if routed:
        routed_lines = []
        for r in routed:
            routed_lines.append(f"- [{r['project']}] ({r['type']}) {r['content'][:100]}")
        routed_summary = "\n".join(routed_lines)

    # ── Threshold check ──
    importance_sum = compute_importance_sum(remaining)
    log(f"  Importance sum: {importance_sum} (threshold: {threshold})")

    corroborated = [r for r in remaining if r.get("seen", 1) >= 2]
    if corroborated:
        log(f"  {len(corroborated)} corroborated observation(s) (seen>=2) "
            f"weighted as higher-confidence")

    if importance_sum < threshold:
        log(f"  Below threshold ({importance_sum} < {threshold}), skipping model reflection")
        # Still mark routed observations as reflected so they don't get re-routed
        if not dry_run and routed:
            _mark_observations_reflected(user_id, mm, routed)
        # Write a minimal reflection log entry
        if not dry_run:
            mm.log_reflection(user_id, len(records),
                              f"Skipped: importance sum {importance_sum} < threshold {threshold}. "
                              f"{len(routed)} observations routed to projects.")
        return {
            "user_id": user_id,
            "status": "below_threshold",
            "importance_sum": importance_sum,
            "threshold": threshold,
            "routed": len(routed),
        }

    # Build observations text for LLM (only non-routed observations)
    observations_text = "\n".join(r["raw"] for r in remaining)
    obs_count = len(records)

    if dry_run:
        log(f"DRY RUN: Would reflect on user {user_id} ({len(remaining)} observations, importance={importance_sum})")
        return {"user_id": user_id, "status": "dry_run", "obs_count": obs_count,
                "routed": len(routed), "importance_sum": importance_sum}

    # ── Build routing note for LLM context ──
    routing_note = ""
    if routed_summary:
        routing_note = (
            f"\n## Project-Routed Observations (already saved to project files — DO NOT add to model)\n"
            f"{routed_summary}\n"
        )

    # ── Determine reflection mode ──
    capable = _is_capable_provider()
    questions = ""

    if capable:
        # ── Phase 1: Generate questions (capable providers only) ──
        log(f"  Phase 1: Generating questions from {len(remaining)} observations...")
        questions = _generate_questions(observations_text)
        if not questions:
            log("  Phase 1 produced no questions, using fallback questions")
            questions = (
                "1. What behavioral patterns have changed or strengthened?\n"
                "2. How has the relationship dynamic shifted?\n"
                "3. What emerging priorities or concerns are visible?"
            )
        else:
            log(f"  Phase 1 complete: {questions[:150]}...")

        # ── Phase 2: Update model with strict prompt ──
        log("  Phase 2: Updating model (strict prompt)...")
        prompt = _build_strict_prompt(model, observations_text, questions, routing_note)
    else:
        # ── Single-call mode for weaker providers ──
        log("  Single-call mode (provider not capable of two-phase)...")
        prompt = _build_simple_prompt(model, observations_text, routing_note)

    # Phase 2 with dual-budget retry: handles transient 0-byte returns separately
    # from real parser failures so a single Sonnet burp doesn't kill the reflection.
    model_content, summary, failure = _run_phase2_with_retry(prompt, mm)

    if failure == "no_llm":
        reason = _describe_no_llm_failure()
        log(f"No LLM available for reflection on user {user_id} after retries: {reason}")
        return {"user_id": user_id, "status": "no_llm", "reason": reason}

    if failure == "parse_error" or not model_content:
        log(f"Could not parse model update for user {user_id} after retries")
        return {"user_id": user_id, "status": "parse_error", "reason": "no model content in output"}

    # Update the person model
    mm.set_model(user_id, model_content)
    log(f"Updated user {user_id}'s model ({len(model_content)} chars)")

    # Mark ALL processed observations as reflected (both routed and remaining)
    _mark_observations_reflected(user_id, mm, records)

    # Write reflection log
    reflection_text = summary or ""
    if routed:
        reflection_text += f"\n\nProject routing: {len(routed)} observations routed to project state files."
    if capable and questions:
        reflection_text += "\n\nReflection mode: two-phase (questions + strict update)"
    else:
        reflection_text += "\n\nReflection mode: single-call (simple prompt)"
    mm.log_reflection(user_id, obs_count, reflection_text)

    # Archive old observations
    mm.archive_old_observations(user_id)

    return {
        "user_id": user_id,
        "status": "updated",
        "summary": summary,
        "obs_count": obs_count,
        "routed": len(routed),
        "reflected": len(remaining),
        "importance_sum": importance_sum,
        "two_phase": capable,
    }


def _send_alert(message: str):
    """Send a Telegram alert to admin about reflection issues."""
    send_script = BOT_DIR / "utils" / "send_to_telegram.py"
    if not send_script.exists():
        return
    # Find admin user from env
    allowed = os.environ.get("ALLOWED_USERS", "").strip()
    if not allowed:
        return
    admin_id = allowed.split(",")[0].strip()
    if not admin_id:
        return
    try:
        # Use the running interpreter so send_to_telegram.py sees the same
        # site-packages as the bot. BOT_DIR/.venv was a legacy single-user
        # convention that no longer holds in multi-user deployments.
        subprocess.run(
            [sys.executable, str(send_script), "--user", admin_id, "--message", message],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


def _warn_if_login_expiring(dry: bool = False):
    """Warn the admin before the Claude login lapses.

    Every LLM-backed job on this machine fails closed when the login dies, and
    only a human at the keyboard can renew it. The expiry is knowable weeks
    ahead, so there is no reason for the first signal to be a dead nightly run —
    this rides along with the job that already runs every night.

    Checks BOTH credential chains, not just the shared file. This job is one of
    the callers that rides the keychain chain, so a file-only check could report
    healthy from inside an outage that had already killed it.

    Never raises: a problem reading the credential file must not stop the
    reflection that follows it.
    """
    try:
        from utils.claude_login_check import read_machine_login_state, warning_message
        state = read_machine_login_state()
        log(f"Login check: {state['detail']}")
        message = warning_message(state)
        if message and not dry:
            _send_alert(message)
    except Exception as e:
        log(f"Login check skipped: {e}")


def main():
    parser = argparse.ArgumentParser(description="Nightly reflection for the memory system")
    parser.add_argument("--dry", action="store_true", help="Preview without writing")
    parser.add_argument("--user", type=int, help="Reflect on one user only")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help=f"Importance threshold to trigger reflection (default: {DEFAULT_THRESHOLD})")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    mm = MemoryManager(DATA_DIR)

    log("=== Nightly Reflection Started ===")

    _warn_if_login_expiring(dry=args.dry)

    results = []

    if args.user:
        users = [args.user]
    else:
        users = mm.get_all_users()

    for user_id in sorted(users):
        result = run_reflection(mm, user_id, args.dry, args.threshold)
        results.append(result)

    log("=== Nightly Reflection Complete ===")

    updated = [r for r in results if r.get("status") == "updated"]
    skipped = [r for r in results if r.get("status") == "skipped"]
    below_threshold = [r for r in results if r.get("status") == "below_threshold"]
    errors = [r for r in results if r.get("status") in ("no_llm", "parse_error")]

    print(f"\nResults: {len(updated)} updated, {len(skipped)} skipped, "
          f"{len(below_threshold)} below threshold, {len(errors)} errors")
    for r in updated:
        routed_str = f" ({r.get('routed', 0)} routed to projects)" if r.get('routed') else ""
        print(f"  User {r['user_id']}: updated{routed_str} — {r.get('summary', '')[:150]}")
    for r in below_threshold:
        print(f"  User {r['user_id']}: below threshold (importance {r['importance_sum']} < {r['threshold']})")
    for r in errors:
        print(f"  User {r['user_id']}: ERROR - {r.get('reason', 'unknown')}")

    # Alert admin on failures
    if errors:
        error_details = "\n".join(f"- User {r['user_id']}: {r.get('reason', 'unknown')}" for r in errors)
        _send_alert(f"Reflection failed for {len(errors)} user(s):\n{error_details}")


if __name__ == "__main__":
    main()
