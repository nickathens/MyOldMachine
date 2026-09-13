"""The engine picker: two named, pre-tuned choices a non-admin user may pick.

An "engine" is a (provider, model, effort) triple with a name and a colour,
offered per Telegram user. It exists because the three knobs this repo
already has are global and admin-only: ``LLM_PROVIDER``, ``LLM_MODEL`` and
``LLM_EFFORT`` live in .env, one value each for the whole install, and only
an admin may write them. On a machine several people share, that means one
person's choice of model is everybody's.

What this module deliberately is NOT:

* It is not a second model catalog. ``install/wizard.PROVIDER_MODELS`` still
  owns which models exist, and ``core.model_efforts`` still owns which effort
  levels each one accepts. Every engine below is checked against BOTH by
  ``tests/test_engine_picker.py``; a level this repo has not read for a model
  can never ship in an engine row.
* Ordinary users on a CLI installation default to Opus at Max when its
  subscription login is available. API/local installs retain the machine
  default unless a user explicitly picks an engine.
* An administrator never runs on a per-user engine. They own the three .env
  knobs, and a stored engine would silently outrank the model and effort they
  just set, so the pair above is offered to everybody except them. What they
  are offered instead is the MACHINE catalog at the foot of this file: every
  model either subscription CLI here can run, where picking one writes the
  same LLM_PROVIDER and LLM_MODEL the Provider and Model rows write. One
  setting with every option in it, rather than a second setting with two.

Availability is PROBED, never assumed. Both engines are subprocess CLIs that
may not be installed, may not be logged in, and (for Astra) may be too old:
Codex answers every single turn for an unknown model with "The 'gpt-6-astra'
model is not supported when using Codex with a ChatGPT account", which reads
like an account problem rather than an out-of-date binary. Offering a button
that cannot work is worse than offering no button.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Optional

from core.model_efforts import efforts_for, model_needs_newer_cli

# Each engine names its own colour. The Mini App maps the accent to a CSS
# class; Telegram has no colours at all and prints the label instead.
ENGINES: tuple[dict, ...] = (
    {
        "id": "opus",
        "label": "Opus",
        "sub": "Claude Opus 5, max effort",
        # "claude-cli", never bare "claude": create_provider maps the bare
        # name to the HTTP API provider the moment LLM_API_KEY is set, and
        # this button is a Claude Code subscription choice. On an install
        # that has both, the bare name would quietly bill the API key.
        "provider": "claude-cli",
        "model": "claude-opus-5",
        "effort": "max",
        "accent": "default",
        "is_default": True,
        "cli": "claude",
    },
    {
        "id": "astra",
        "label": "Astra",
        "sub": "GPT-6 Astra, extra-high effort",
        "provider": "codex",
        "model": "gpt-6-astra",
        "effort": "xhigh",
        "accent": "astra",
        "is_default": False,
        "cli": "codex",
    },
)

ENGINE_IDS = tuple(e["id"] for e in ENGINES)

# Said to an administrator by every surface that offers the picker, so the
# Mini App, /engine and the API cannot drift into three different stories.
ADMIN_KEEPS_MACHINE_SETTING = (
    "You set the machine's provider, model and effort, and your own messages "
    "run on those. The engine picker is for the other users."
)

# A probe answer is kept this long. Short enough that installing the CLI, or
# logging in, takes effect without a restart; long enough that a picker open
# does not shell out twice.
_PROBE_TTL = 300.0
_probe_cache: dict[str, tuple[float, tuple[bool, str]]] = {}


def get_engine(engine_id: Optional[str]) -> Optional[dict]:
    """The engine row for ``engine_id``, or None when there is no such row."""
    if not engine_id:
        return None
    for engine in ENGINES:
        if engine["id"] == engine_id:
            return engine
    return None


def default_engine() -> dict:
    """The engine presented as the recommended one."""
    for engine in ENGINES:
        if engine.get("is_default"):
            return engine
    return ENGINES[0]


def _cli_version_text(binary: str) -> Optional[str]:
    """``<binary> --version`` output, or None when it cannot be run.

    None is "no such CLI here", which is a hard unavailable. An empty string
    would be a CLI that ran and said nothing, which is a different answer.
    """
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=20,
        )
    except (FileNotFoundError, PermissionError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "") + (result.stderr or "")


def _login_status(engine: dict, binary: str) -> tuple[bool, str]:
    """Check local subscription login, without claiming remote model access.

    Keyed on the CLI, never on the engine id: the machine catalog below names
    ten engines across the same two binaries, and an id test would have sent
    nine of them to the Codex checker.
    """
    from core.llm import ClaudeCLIProvider, CodexCLIProvider
    claude = engine["cli"] == "claude"
    cls = ClaudeCLIProvider if claude else CodexCLIProvider
    provider = cls(engine["model"])
    args = ["auth", "status", "--json"] if claude else ["login", "status"]
    try:
        result = subprocess.run([binary, *args], env=provider._get_cli_env(None),
                                capture_output=True, text=True, timeout=10)
        if claude:
            info = json.loads(result.stdout)
            logged_in = (isinstance(info, dict) and info.get("loggedIn") is True
                         and info.get("authMethod") in ("claude.ai", "oauth_token"))
        else:
            text = (result.stdout or "") + (result.stderr or "")
            logged_in = "logged in using chatgpt" in text.lower()
        if result.returncode == 0 and logged_in:
            return True, ""
        return False, f"{engine['cli']} needs a subscription login on this machine"
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return False, f"Could not verify {engine['cli']} subscription login"


def _probe(engine: dict) -> tuple[bool, str]:
    from core.llm import _find_cli_binary
    binary = _find_cli_binary(engine["cli"])
    version_text = _cli_version_text(binary)
    if version_text is None:
        return False, f"{engine['cli']} CLI is not installed on this machine"
    too_old = model_needs_newer_cli(engine["model"], version_text)
    if too_old:
        return False, too_old
    return _login_status(engine, binary)


def engine_available(engine: dict, *, refresh: bool = False) -> tuple[bool, str]:
    """(available, reason). Reason is "" when available.

    Cached for ``_PROBE_TTL`` seconds per engine, because both the Mini App
    and the Telegram command ask on every render and the answer costs a
    subprocess. ``refresh`` forces a re-probe (used right before a switch is
    stored, so the answer behind a stored choice is never older than the
    press that made it).
    """
    # Keyed on what the probe actually asks about (this binary, this model),
    # not on the engine id: the per-user picker and the machine catalog name
    # the same models twice, and the answer for a pair cannot differ between
    # them. One subprocess serves both lists.
    key = f"{engine['cli']}:{engine['model']}"
    now = time.monotonic()
    if not refresh:
        cached = _probe_cache.get(key)
        if cached is not None and (now - cached[0]) < _PROBE_TTL:
            return cached[1]
    answer = _probe(engine)
    _probe_cache[key] = (now, answer)
    return answer


def probe_cache_clear() -> None:
    """Drop every cached availability answer (tests, and after an update)."""
    _probe_cache.clear()


def available_engines(*, refresh: bool = False) -> list[dict]:
    """Every engine row, each with ``available`` and ``reason`` filled in."""
    rows = []
    for engine in ENGINES:
        available, reason = engine_available(engine, refresh=refresh)
        row = dict(engine)
        row["available"] = available
        row["reason"] = reason
        rows.append(row)
    return rows


def engine_effort(engine: dict) -> str:
    """The engine's effort, or "" if the model does not accept it.

    The table in core.model_efforts is the authority on what a model takes,
    so an engine row that drifts away from it sends no override at all
    rather than a level the CLI will ignore with a stderr warning nobody
    reads. A test locks the two together, so "" here means the tables were
    edited apart.
    """
    effort = engine.get("effort", "")
    return effort if effort in efforts_for(engine["provider"], engine["model"]) else ""


# ─── Per-user selection ──────────────────────────────────────────────


def user_engine_id(user_id: int) -> str:
    """The engine id this user picked, or "" when they never picked one."""
    from core.user_prefs import get_pref
    value = get_pref(user_id, "engine", "")
    return value if value in ENGINE_IDS else ""


def set_user_engine(user_id: int, engine_id: str) -> tuple[bool, str]:
    """Store this user's engine choice. ``""`` clears it back to the default.

    Availability is re-probed here rather than trusted from the picker's
    render: the CLI could have been removed between the two, and a stored
    choice that cannot run would fail every later turn with a CLI error
    instead of a sentence.

    An administrator is refused: their turns follow LLM_PROVIDER, LLM_MODEL
    and LLM_EFFORT, so a stored engine here would be a second, invisible
    setting that beats the visible one. Clearing is always allowed, so a
    pick made before this rule can be removed by the person who made it.
    """
    from core.config import is_admin
    from core.user_prefs import clear_pref, set_pref
    if not engine_id:
        if not clear_pref(user_id, "engine"):
            return False, "Could not save your choice (the preferences file did not write)."
        return True, "Cleared. Your default engine applies from the next message."
    if is_admin(user_id):
        return False, ADMIN_KEEPS_MACHINE_SETTING
    engine = get_engine(engine_id)
    if engine is None:
        return False, f"No such engine: {engine_id}"
    available, reason = engine_available(engine, refresh=True)
    if not available:
        return False, reason
    if not set_pref(user_id, "engine", engine_id):
        return False, "Could not save your choice (the preferences file did not write)."
    return True, f"{engine['label']} it is: {engine['sub']}."


def resolve_engine(user_id: int, *, default_provider: str | None = None,
                   admin: bool | None = None) -> Optional[dict]:
    """Resolve a saved choice or the ordinary CLI user's Opus default.

    An administrator always resolves to None, the machine setting, even with
    a choice stored from before ``set_user_engine`` began refusing them: two
    settings that disagree are worse than one, and the one they can see in
    the settings panel is the one that must win.

    No available engine means the machine provider remains in use. A stored
    preference survives an unavailable login, and both UIs expose fallback.
    """
    from core.config import get_llm_provider, get_llm_api_key, is_admin
    if admin is None:
        admin = is_admin(user_id)
    if admin:
        return None
    engine = get_engine(user_engine_id(user_id))
    if engine is None:
        if default_provider is None:
            default_provider = get_llm_provider()
        if default_provider == "claude" and get_llm_api_key():
            default_provider = "claude-api"
        if default_provider not in ("claude", "claude-cli", "codex", "codex-cli"):
            return None
        engine = default_engine()
    available, _reason = engine_available(engine)
    return engine if available else None


# ─── The machine's own engines (what an administrator picks from) ────
#
# The two rows at the top of this file are a curated pair, offered to people
# who cannot touch .env. An administrator is not one of those people, and
# giving them the same pair said this machine had two engines. It does not:
# ``install/wizard.PROVIDER_MODELS`` already ships ten models across the two
# subscription CLIs, and every one of them is a machine this repo can run.
#
# So the administrator's picker is the MACHINE setting, with every option in
# it. Not a second store: picking here writes LLM_PROVIDER and LLM_MODEL, the
# same two values the Provider and Model rows write, which is why both
# surfaces re-read after a switch. One setting, two views of it, every choice.
#
# Only the subscription CLIs are listed, and LLM_API_KEY is the reason: there
# is exactly one key and it belongs to whichever provider is configured, so
# "run Gemini" is not a one-tap fact on a machine with no Gemini key. Those
# providers keep the Provider row, which asks for the key first. What is
# listed here is what this machine can run right now, probed, plus whatever
# it is running already even when that is neither CLI.
MACHINE_CLI_PROVIDERS: tuple[tuple[str, str, str], ...] = (
    # (.env provider id, CLI binary, accent)
    ("claude-cli", "claude", "default"),
    ("codex", "codex", "astra"),
)

# Short spellings for the Telegram command, so /engine sonnet works next to
# /engine claude-sonnet-5. The model id is always the real id; these are
# aliases onto it, and a model with no alias is picked by its own id.
MACHINE_ALIASES: dict[str, str] = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "fable": "claude-fable-5-1",
    "astra": "gpt-6-astra",
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-5.6-luna",
    "spark": "gpt-5.3-codex-spark",
    "mini": "gpt-5.4-mini",
}

MACHINE_PICKER_NOTE = (
    "This is the machine setting, the same one the Provider and Model rows "
    "hold. Your messages use this setting. Ordinary users keep their own "
    "engine, defaulting to Opus at Max on CLI installations when available. "
    "This setting also supplies their fallback when no personal engine is available."
)


def _catalog_split(description: str) -> tuple[str, str]:
    """A wizard catalog line as (name, what it is).

    The catalog writes "Claude Opus 5 — complex agentic coding ..., 1M ctx".
    The name is a button label; the rest is a sentence written for a terminal
    and far too long for one, so the sub-line keeps its first clauses only.
    Derived rather than re-typed: a model added to the wizard appears here
    with no edit, which is the drift this repo keeps paying for elsewhere.
    """
    name, _, rest = description.partition(" — ")
    if not name:
        name = description
    name = name.strip()
    clauses = [c.strip() for c in re.split(r"[,;]", rest) if c.strip()]
    sub = ""
    for clause in clauses:
        candidate = f"{sub}, {clause}" if sub else clause
        if len(candidate) > 52:
            break
        sub = candidate
    return name, sub


def _machine_catalog() -> list[dict]:
    """Every (provider, model) pair a subscription CLI on this machine runs."""
    from install.wizard import PROVIDER_MODELS
    reverse = {model: alias for alias, model in MACHINE_ALIASES.items()}
    rows: list[dict] = []
    for provider, cli, accent in MACHINE_CLI_PROVIDERS:
        for model, description in PROVIDER_MODELS.get(cli, ()):
            label, sub = _catalog_split(description)
            rows.append({
                "id": model,
                "alias": reverse.get(model, ""),
                "label": label,
                "sub": sub,
                "provider": provider,
                "model": model,
                "cli": cli,
                "accent": accent,
            })
    return rows


def machine_engines(current_provider: str | None = None,
                    current_model: str | None = None,
                    *, refresh: bool = False, api_key: str | None = None) -> list[dict]:
    """The administrator's list: every runnable engine, current one marked.

    ``current_provider``/``current_model`` are passed in rather than read
    here, because the two callers read .env differently: the bot has it in
    its environment, the Mini App reads the file on every request precisely
    because another process rewrites it.

    A pair that is running but is not on either CLI (an API provider, Ollama)
    is added at the front. A list of things to switch to that cannot show
    what is switched on is the "which one is live" question all over again.
    """
    from core.config import get_llm_api_key, get_llm_model, get_llm_provider
    if current_provider is None:
        current_provider = get_llm_provider()
    if current_model is None:
        current_model = get_llm_model()
    if api_key is None:
        api_key = get_llm_api_key()
    # Bare claude selects the API when a key exists. Match the factory,
    # otherwise the page calls a billed API turn a subscription turn.
    canonical = {"claude": "claude-api" if api_key else "claude-cli",
                 "codex-cli": "codex"}
    current_provider = canonical.get(current_provider, current_provider)

    rows = []
    for row in _machine_catalog():
        available, reason = engine_available(row, refresh=refresh)
        out = dict(row)
        out["available"] = available
        out["reason"] = reason
        out["current"] = (row["provider"] == current_provider
                          and row["model"] == current_model)
        rows.append(out)

    if not any(row["current"] for row in rows):
        rows.insert(0, {
            "id": f"current:{current_provider}:{current_model}",
            "alias": "",
            "label": current_model or current_provider,
            "sub": f"set on {current_provider}",
            "provider": current_provider,
            "model": current_model,
            "cli": "",
            "accent": "default",
            "available": True,
            "reason": "",
            "current": True,
        })
    return rows


def machine_engine(engine_id: str, rows: list[dict] | None = None) -> Optional[dict]:
    """One machine engine by model id or short alias, or None."""
    if not engine_id:
        return None
    wanted = MACHINE_ALIASES.get(engine_id.strip().lower(), engine_id.strip())
    for row in (rows if rows is not None else machine_engines()):
        if row["id"] == wanted or (row["alias"] and row["alias"] == engine_id.strip().lower()):
            return row
    return None
