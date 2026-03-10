#!/usr/bin/env python3
"""
Nightly Reflection Script — Overnight Learning for the Memory System

Runs as a scheduled job (typically 3:00 AM). Reads recent observations for
each user, analyzes patterns via LLM, and updates person models.

Tier-aware:
- Uses Claude CLI if available (best quality)
- Falls back to the configured LLM provider via API
- Skips reflection entirely if no capable model is available

Usage:
    python reflect.py                          # Full reflection for all users
    python reflect.py --dry                    # Preview without writing
    python reflect.py --user 12345             # Reflect on one user only
"""

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
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

    # Ollama doesn't need an API key
    if not api_key and provider != "ollama":
        return ""

    # Providers that support reflection (need decent reasoning ability)
    api_configs = {
        "openai": ("https://api.openai.com/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "deepseek": ("https://api.deepseek.com/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "grok": ("https://api.x.ai/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "Authorization", f"Bearer {api_key}"),
        "claude-api": ("https://api.anthropic.com/v1/messages", None, None),
    }

    if provider not in api_configs and provider not in ("gemini", "google", "ollama"):
        return ""

    try:
        if provider == "ollama":
            # Ollama uses OpenAI-compatible API, no auth needed
            base_url = get_ollama_base_url()
            url = f"{base_url}/v1/chat/completions"
            body = {
                "model": model,
                "max_tokens": 4096,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": "You are analyzing behavioral observations to update a person model."},
                    {"role": "user", "content": prompt},
                ],
            }
            with httpx.Client(timeout=300.0) as client:  # Longer timeout for local models
                resp = client.post(url, headers={"Content-Type": "application/json"}, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
                else:
                    log(f"Ollama API returned {resp.status_code}")
            return ""

        elif provider in ("gemini", "google"):
            # Gemini has a different API format
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
            # OpenAI: GPT-5.x and o-series need max_completion_tokens, reject temperature
            # Grok: reasoning models (grok-4 non-fast, grok-3-mini) need max_completion_tokens, reject temperature
            # OpenRouter: max_completion_tokens preferred (max_tokens deprecated)
            # DeepSeek: reasoner ignores temperature
            is_openai_reasoning = provider == "openai" and (
                model.startswith("gpt-5") or model.startswith("o1")
                or model.startswith("o3") or model.startswith("o4")
            )
            is_grok_reasoning = provider == "grok" and (
                "reasoning" in model
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


def run_reflection(mm: MemoryManager, user_id: int, dry_run: bool = False) -> dict:
    """Run reflection for one user. Returns results dict."""
    prompt = mm.build_reflection_prompt(user_id)
    if not prompt:
        log(f"No recent observations for user {user_id}, skipping")
        return {"user_id": user_id, "status": "skipped", "reason": "no observations or no model"}

    obs_count = len([l for l in mm.get_recent_observations(user_id).split("\n") if l.strip()])
    log(f"Reflecting on user {user_id}: {obs_count} observations from last 7 days")

    if dry_run:
        log(f"DRY RUN: Would reflect on user {user_id} ({obs_count} observations)")
        return {"user_id": user_id, "status": "dry_run", "obs_count": obs_count}

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

    # Write reflection log
    mm.log_reflection(user_id, obs_count, summary)

    # Archive old observations
    mm.archive_old_observations(user_id)

    return {"user_id": user_id, "status": "updated", "summary": summary, "obs_count": obs_count}


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
        result = run_reflection(mm, user_id, args.dry)
        results.append(result)

    log("=== Nightly Reflection Complete ===")

    updated = [r for r in results if r.get("status") == "updated"]
    skipped = [r for r in results if r.get("status") == "skipped"]
    errors = [r for r in results if r.get("status") in ("no_llm", "parse_error")]

    print(f"\nResults: {len(updated)} updated, {len(skipped)} skipped, {len(errors)} errors")
    for r in updated:
        print(f"  User {r['user_id']}: {r.get('summary', 'updated')}")
    for r in errors:
        print(f"  User {r['user_id']}: ERROR - {r.get('reason', 'unknown')}")

    # Alert admin on failures
    if errors:
        error_details = "\n".join(f"- User {r['user_id']}: {r.get('reason', 'unknown')}" for r in errors)
        _send_alert(f"Reflection failed for {len(errors)} user(s):\n{error_details}")


if __name__ == "__main__":
    main()
