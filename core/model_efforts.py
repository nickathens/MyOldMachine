"""One home for the per-model facts more than one place has to agree on:
which reasoning-effort levels a model accepts, and how new a CLI has to
be to know the model at all.

Deliberately stdlib-only. `install/wizard.py` reads the CLI floor while
provisioning, before this repo's third-party dependencies are guaranteed
to be installed, so importing `core.llm` (httpx, core.tools) from there
would be a new way for an install to die.

The effort half:

Three places used to keep their own copy of the effort list: ``core.config``
(the value handed to ``claude --effort``), ``miniapp/server.py`` (what the
picker offers and what ``/api/effort`` validates) and, by omission, the Codex
provider, which passed no effort at all. They disagreed the moment a second
CLI arrived, because the set is not one list:

* ``claude --help`` documents ``--effort`` as (low, medium, high, xhigh, max).
* ``gpt-6-astra`` carries a sixth, ``ultra``.
* ``gpt-5.5`` carries only FOUR: it has no ``max`` at all.

That last one is why this table is per model rather than per provider. This
repo's default effort has always been ``max`` and its default Codex model is
``gpt-5.5``, so wiring effort into the Codex provider against one shared list
would have started sending every default install a level its own model does
not support.

Source for the Codex rows: OpenAI's model catalog, which Codex CLI 0.153.4
fetched on 2026-09-06 and cached at ``~/.codex/models_cache.json``. Each row
is that model's ``supported_reasoning_levels`` and ``default_reasoning_level``
verbatim. A Codex model absent from the table gets an EMPTY set, which means
"do not offer a row and send no override" — the CLI then applies the model's
own default. Guessing a set for a model we have not read is how a level that
does not exist reaches the API.

Passing ``ultra`` to the claude binary is not an error, which is the other
trap this module exists to close. Measured on claude 2.1.261:

    $ claude -p --effort ultra ...
    Warning: Unknown --effort value 'ultra' - ignoring it and using the
    default effort. Valid values: low, medium, high, xhigh, max.

It warns on stderr and runs the turn anyway. A stored ``ultra`` left over from
an Astra session would therefore downgrade every later Claude turn silently.
``clamp_effort`` is the guard, and it is applied at both ends: when the Mini
App stores a value, and again when a provider builds its argv, because .env is
a file anyone can hand-edit.

``ultra`` is also not merely "more thinking". Measured 2026-09-06 with
``codex debug prompt-input``, which renders the model-visible prompt with no
API call: at ``-c model_reasoning_effort=low`` the prompt carries "Do not
spawn sub-agents unless the user ... explicitly ask", and at ``ultra`` it
flips to "Proactive multi-agent delegation is active". On the small, old
machines this project targets that is a real resource decision, so ultra is
offered but is never any model's default.
"""
from __future__ import annotations

import re

# Every level any supported CLI accepts, weakest to strongest, each exactly
# once. clamp_effort walks it downward, so the order is load bearing.
EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max", "ultra")

EFFORT_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "X-High",
    "max": "Max",
    "ultra": "Ultra",
}

# claude --help, verified 2026-09-06 on CLI 2.1.261.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

_FOUR = ("low", "medium", "high", "xhigh")
_FIVE = ("low", "medium", "high", "xhigh", "max")
_SIX = ("low", "medium", "high", "xhigh", "max", "ultra")

# Providers whose CLI carries a reasoning effort at all. Everything else (the
# direct-API providers, Ollama, OpenRouter) has no such knob, and the Mini App
# hides the whole row for them rather than storing a value nothing reads.
_CLAUDE_PROVIDERS = frozenset({"claude", "claude-cli", "fcc"})
_CODEX_PROVIDERS = frozenset({"codex", "codex-cli"})
EFFORT_PROVIDERS = _CLAUDE_PROVIDERS | _CODEX_PROVIDERS

# Codex models, from the catalog cited above. Keyed by the exact LLM_MODEL
# string. Claude models are not listed: they all take CLAUDE_EFFORTS, so a new
# Anthropic model needs no edit here.
_MODEL_EFFORTS = {
    "gpt-6-astra": _SIX,
    "gpt-5.6-sol": _SIX,
    "gpt-5.6-terra": _SIX,
    "gpt-5.6-luna": _FIVE,
    "gpt-5.5": _FOUR,
    "gpt-5.4-mini": _FOUR,
    "gpt-5.3-codex-spark": _FOUR,
}

# default_reasoning_level from the same catalog rows.
_MODEL_DEFAULT_EFFORT = {
    "gpt-6-astra": "medium",
    "gpt-5.6-sol": "low",
    "gpt-5.6-terra": "medium",
    "gpt-5.6-luna": "medium",
    "gpt-5.5": "medium",
    "gpt-5.4-mini": "medium",
    "gpt-5.3-codex-spark": "high",
}

# What this repo has always sent to `claude --effort`.
_CLAUDE_DEFAULT_EFFORT = "max"


def efforts_for(provider: str, model: str | None = None) -> tuple[str, ...]:
    """The levels this provider/model pair accepts.

    Empty means "unknown, offer nothing and send nothing", and is returned
    ONLY for a Codex model this table has not read. Everything else gets the
    Claude CLI's set, which is what this function has always answered, so
    `--effort` is never handed an empty string and a caller asking about a
    provider that has no effort knob at all still gets the historical answer
    rather than a new empty one. Whether a row is offered is a separate
    question, answered by supports_effort.
    """
    if model and model in _MODEL_EFFORTS:
        return _MODEL_EFFORTS[model]
    if provider in _CODEX_PROVIDERS:
        return ()
    return CLAUDE_EFFORTS


def supports_effort(provider: str, model: str | None = None) -> bool:
    """Whether a row should be offered for this pair at all.

    Two independent questions, both of which must be yes: does the provider
    have the knob, and are this model's levels known.
    """
    return provider_supports_effort(provider) and bool(
        efforts_for(provider, model))


def provider_supports_effort(provider: str) -> bool:
    """Whether the provider has the knob, regardless of the model chosen.

    Used where only the provider is known (the per-provider models endpoint).
    Model-specific answers come from supports_effort.
    """
    return provider in EFFORT_PROVIDERS


def default_effort_for(provider: str, model: str | None = None) -> str:
    if model and model in _MODEL_DEFAULT_EFFORT:
        return _MODEL_DEFAULT_EFFORT[model]
    if provider in _CODEX_PROVIDERS:
        return ""
    return _CLAUDE_DEFAULT_EFFORT


def effort_options(provider: str, model: str | None = None) -> list[dict]:
    """The rows the Mini App renders for this pair. May be empty."""
    return [{"id": e, "label": EFFORT_LABELS[e]}
            for e in efforts_for(provider, model)]


def clamp_effort(provider: str, model: str | None, effort: str | None) -> str:
    """The strongest level this model accepts that is no stronger than `effort`.

    Returns "" when the pair has no known levels, and the caller then omits
    the flag entirely rather than inventing one.

    Switching Astra/ultra back to a Claude model is the case that matters:
    ultra clamps to max, which is what ultra is minus the delegation the
    claude binary has no concept of. A value that is not on EFFORT_ORDER at
    all (a hand-edited .env, a stale key) is not a level to step down from, so
    it falls back to the pair's own default instead.
    """
    allowed = efforts_for(provider, model)
    if not allowed:
        return ""
    if effort in allowed:
        return effort
    if effort not in EFFORT_ORDER:
        return default_effort_for(provider, model)
    ceiling = EFFORT_ORDER.index(effort)
    for candidate in reversed(EFFORT_ORDER[:ceiling + 1]):
        if candidate in allowed:
            return candidate
    return default_effort_for(provider, model)


# ─── How new a CLI has to be ─────────────────────────────────────────

# Models a Codex build has to be new enough to know, and the release that
# introduced each. Read from the openai/codex releases API on 2026-09-07:
# rust-v0.153.1 (2026-09-03) "Added support for configuring GPT-6-Astra
# through the API without changing the default model or showing it in the
# model picker", and rust-v0.153.4 (2026-09-04) "Fixed Astra's visibility in
# the bundled model picker". The bot always passes -m explicitly, so 0.153.1
# is the floor; the picker fix does not matter here.
#
# Without this gate an older install answers every single turn with "The
# 'gpt-6-astra' model is not supported when using Codex with a ChatGPT
# account", which reads like an account problem rather than an out-of-date
# binary. This project installs on machines whose CLI nobody is watching.
MODEL_MIN_CLI = {
    "gpt-6-astra": (0, 153, 1),
}


def parse_cli_version(text: str) -> tuple | None:
    """(major, minor, patch) out of a `--version` line, or None.

    Codex 0.153.4 prints "codex-cli 0.153.4". Anything without a dotted
    triple returns None, and an unknown version is never treated as too old:
    a wrong refusal here would take a working install off the air.
    """
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return None
    return tuple(int(g) for g in match.groups())


def model_needs_newer_cli(model: str, version_text: str) -> str | None:
    """Refusal message when this build is too old for `model`, else None."""
    required = MODEL_MIN_CLI.get(model)
    if not required:
        return None
    found = parse_cli_version(version_text)
    if found is None or found >= required:
        return None
    want = ".".join(str(n) for n in required)
    have = ".".join(str(n) for n in found)
    return (
        f"Codex CLI {have} is too old for {model}, which needs {want} or "
        f"newer. Update with `npm i -g @openai/codex`, or pick another "
        f"model with /model."
    )
