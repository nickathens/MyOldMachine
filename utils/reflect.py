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
import fcntl
import json
import logging
import os
import re
import subprocess
import sys
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
DEFAULT_THRESHOLD = 18


def log(msg: str):
    """Append to reflection log."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} | REFLECT | {msg}"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
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

    content = obs_file.read_text()
    lines = [l for l in content.split("\n") if l.startswith("[")]

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
        with open(state_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
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
    with open(tmp, "w") as f:
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

        content = obs_file.read_text()

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
            with open(tmp, "w") as f:
                f.write("\n".join(new_lines))
                f.flush()
                os.fsync(f.fileno())
            tmp.rename(obs_file)
            log(f"  Marked {marked} observations as reflected for user {user_id}")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


# ─── LLM Calls ────────────────────────────────────────────────────

def _call_claude_cli(prompt: str) -> str:
    """Call Claude CLI for reflection. Returns output text or empty string."""
    if not which("claude"):
        return ""
    try:
        # Use the configured model if it's a Claude model, otherwise use default
        configured_model = get_llm_model()
        if configured_model.startswith("claude-"):
            cli_model = configured_model
        else:
            cli_model = "claude-sonnet-4-6"
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", cli_model],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        log(f"Claude CLI failed: exit={result.returncode}, stderr={result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        log("Claude CLI timed out (120s)")
    except Exception as e:
        log(f"Claude CLI error: {e}")
    return ""


def _call_api(prompt: str) -> str:
    """Call the configured API provider for reflection. Returns output text or empty string."""
    import httpx

    provider = get_llm_provider()
    model = get_llm_model()
    api_key = get_llm_api_key()

    # Ollama (local and cloud) doesn't always need an API key
    if not api_key and provider not in ("ollama", "ollama-cloud"):
        return ""

    # Providers that support reflection (need decent reasoning ability)
    api_configs = {
        "openai": ("https://api.openai.com/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "deepseek": ("https://api.deepseek.com/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "grok": ("https://api.x.ai/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "kimi": ("https://api.moonshot.ai/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "claude-api": ("https://api.anthropic.com/v1/messages", None, None),
    }

    if provider not in api_configs and provider not in ("gemini", "google", "ollama", "ollama-cloud"):
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
                        return choices[0].get("message", {}).get("content", "")
                else:
                    log(f"Ollama API returned {resp.status_code}")
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
                    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    return "".join(p.get("text", "") for p in parts)
                else:
                    log(f"Gemini API returned {resp.status_code}")
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
                    log(f"Claude API returned {resp.status_code}")
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
                        return choices[0].get("message", {}).get("content", "")
                else:
                    log(f"{provider} API returned {resp.status_code}")
            return ""

    except Exception as e:
        log(f"API reflection failed ({provider}): {e}")
        return ""


# ─── Provider Capability Detection ────────────────────────────────

# Providers capable enough for two-phase reflection (question generation + model update).
# These are paid APIs or strong local models where the extra call is worth the quality gain.
_CAPABLE_PROVIDERS = {"claude", "claude-cli", "claude-api", "openai", "deepseek", "grok", "kimi"}

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
        f"NOT included here.\n{observations_text}\n"
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
        f"## Recent Observations (last 7 days)\n{observations_text}\n"
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

    # Try Claude CLI first (best quality), then API
    output = _call_claude_cli(prompt)
    if not output:
        output = _call_api(prompt)
    if not output:
        log(f"No LLM available for reflection on user {user_id}")
        return {"user_id": user_id, "status": "no_llm", "reason": "No capable model available"}

    model_content, summary = mm.parse_reflection_output(output)

    if not model_content:
        log(f"Could not parse model update for user {user_id}")
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
        venv_python = str(BOT_DIR / ".venv" / "bin" / "python")
        python = venv_python if Path(venv_python).exists() else sys.executable
        subprocess.run(
            [python, str(send_script), "--user", admin_id, "--message", message],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


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
