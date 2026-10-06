"""One home for the per-model facts more than one place has to agree on:
which reasoning-effort levels a model accepts, how new a CLI has to be to
know the model at all, and which request fields a model's API refuses.

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
repo's default effort has always been ``max`` and its default Codex model was
``gpt-5.5`` until 2026-10-06, so wiring effort into the Codex provider against
one shared list would have started sending every default install a level its
own model does not support.

Source for the Codex rows: OpenAI's model catalog, which Codex CLI 0.153.4
fetched on 2026-09-06 and cached at ``~/.codex/models_cache.json``. Each row
is that model's ``supported_reasoning_levels`` and ``default_reasoning_level``
verbatim, and every row still matched the catalog 0.158.0 fetched on
2026-09-29 and the one 0.160.0 fetched on 2026-10-05. A Codex model absent
from the table gets an EMPTY set, which means "do not offer a row and send no
override" — the CLI then applies the model's own default. Guessing a set for
a model we have not read is how a level that does not exist reaches the API.

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
# The three GPT-6 workhorse rows were read from the catalog Codex 0.160.0
# fetched on 2026-10-05, verbatim, like the rest. gpt-5.5 has no row any more:
# Codex retires it on 2026-10-14 and the picker no longer offers it, and a
# model without a row gets no override at all, which is the right answer for
# an install still on it until then (the CLI applies the model's default).
_MODEL_EFFORTS = {
    "gpt-6.1-sol": _SIX,
    "gpt-6-astra": _SIX,
    "gpt-6-sol": _SIX,
    "gpt-6-luna": _FIVE,
    "gpt-5.6-sol": _SIX,
    "gpt-5.6-terra": _SIX,
    "gpt-5.6-luna": _FIVE,
}

# default_reasoning_level from the same catalog rows.
_MODEL_DEFAULT_EFFORT = {
    "gpt-6.1-sol": "low",
    "gpt-6-astra": "medium",
    "gpt-6-sol": "medium",
    "gpt-6-luna": "medium",
    "gpt-5.6-sol": "low",
    "gpt-5.6-terra": "medium",
    "gpt-5.6-luna": "medium",
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
#
# Claude Code has the same shape of floor. Measured 2026-09-22, the day
# Opus 5.5 shipped: 2.1.278 answers every claude-opus-5-5 turn with "API
# Error: 400 Claude Code 2.1.278 does not support this model; version 2.1.280
# or newer is required" before the API is contacted, and 2.1.280 (whose
# changelog adds the model) answers on claude-opus-5-5. A picker button on
# the older build would fail every press, and the Opus engine is the default
# for every ordinary user, so the floor is read here before it is offered.
#
# gpt-6.1-sol, the Codex default since 2026-10-06, has a floor the release
# notes do not give. They credit 0.159.1 ("Added GPT-6.1 Sol as the default
# model in the bundled catalog"), but that is the bundled picker. Measured the
# same day with one `codex exec -m gpt-6.1-sol` per published build, the bot's
# env and sign-in: 0.156.1, 0.157.0, 0.157.1 and 0.158.0 each answered "The
# 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT
# account." (HTTP 400, turn.failed), and 0.159.0 and 0.159.1 completed. So the
# floor is 0.159.0. gpt-6-sol and gpt-6-luna completed on 0.156.1, a build
# older than the 0.157.0 notes that announce them, so they have none here.
# claude-sonnet-5-5 has none either: Claude Code 2.1.278 and 2.1.283 (the
# changelog adds the model in 2.1.284) both ran it, with the bot's argv and
# --effort max, warning "[claude-code:unrecognized_model]" on stderr only.
MODEL_MIN_CLI = {
    "gpt-6-astra": (0, 153, 1),
    "gpt-6.1-sol": (0, 159, 0),
    "claude-opus-5-5": (2, 1, 280),
}

# Which CLI each floor is measured against. Keyed by the same ids as
# MODEL_MIN_CLI (tests pin the two key sets equal), and explicit rather than
# a prefix rule on the id, for the same reason the engine rows carry a `cli`.
MODEL_CLI = {
    "gpt-6-astra": "codex",
    "gpt-6.1-sol": "codex",
    "claude-opus-5-5": "claude",
}

# What the refusal calls each CLI, and how that CLI updates itself.
_CLI_UPDATE_HINT = {
    "codex": ("Codex CLI", "`npm i -g @openai/codex`"),
    "claude": ("Claude Code", "`claude update`"),
}


def parse_cli_version(text: str) -> tuple | None:
    """(major, minor, patch) out of a `--version` line, or None.

    Codex 0.153.4 prints "codex-cli 0.153.4"; Claude Code prints
    "2.1.280 (Claude Code)". Anything without a dotted triple returns None,
    and an unknown version is never treated as too old:
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
    name, update = _CLI_UPDATE_HINT[MODEL_CLI[model]]
    return (
        f"{name} {have} is too old for {model}, which needs {want} or "
        f"newer. Update with {update}, or pick another model with /model."
    )


# ─── What a model's API refuses ──────────────────────────────────────

# Three places build a request body by hand: the providers in core/llm.py,
# the reflection API fallback in utils/reflect.py and the email triage call
# in utils/email_triage.py. The last two used to copy the first one's rules,
# and the copies were what each new model family slipped past: until
# 2026-10-06 both still sent temperature to every Claude model (HTTP 400
# from Sonnet 5 on) and max_tokens to anything called gpt-6. Stdlib-only for
# the same reason as the rest of this file.

def model_version(model: str, family: str) -> tuple[int, int] | None:
    """(major, minor) of an id such as "gpt-6.1-sol" or "kimi-k2.7-code".

    `family` is the text in front of the number ("gpt-", "grok-", "kimi-k",
    "gemini-"). A dash right after the major ends it, so "grok-4-1-fast" is
    (4, 0). An id outside the family answers None. The rules below compare
    versions rather than prefixes, because a prefix list is exactly what each
    new major walked straight past.
    """
    match = re.match(re.escape(family) + r"(\d+)(?:\.(\d+))?", (model or "").lower())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


# Anthropic models that still accept a non-default ``temperature``. Sonnet 5
# and 5.5, Fable/Mythos 5, and Opus 4.7+ return HTTP 400 if sampling params
# are set, while omitting the field is accepted by every model, so it is sent
# only to legacy models known to take it.
CLAUDE_SAMPLING_OK = ("sonnet-4-", "opus-4-1", "opus-4-5", "opus-4-6", "haiku")


def claude_accepts_temperature(model: str) -> bool:
    m = (model or "").lower()
    return any(tag in m for tag in CLAUDE_SAMPLING_OK)


def openai_is_reasoning(model: str) -> bool:
    """GPT-5 and every later major, and the o-series.

    They take max_completion_tokens, not max_tokens, and refuse a
    temperature. Read by number rather than by prefix: the "gpt-5" prefix
    this replaced sent max_tokens and a temperature to all of GPT-6.
    """
    if (model or "").lower().startswith(("o1", "o3", "o4")):
        return True
    version = model_version(model, "gpt-")
    return version is not None and version >= (5, 0)


# OpenAI models whose Chat Completions endpoint takes function tools only
# with reasoning switched off. Both pages say, as of 2026-10-06: "Chat
# Completions supports function calling only with reasoning_effort set to
# none." Every request this repo sends to the OpenAI provider carries tools.
# gpt-6.1-sol is worse off and is not offered at all: its page says Chat
# Completions is "supported without tool calling".
OPENAI_CHAT_TOOLS_NEED_NO_REASONING = frozenset({"gpt-6-sol", "gpt-6-luna"})


def gemini_accepts_temperature(model: str) -> bool:
    """Only models before Gemini 3.

    Google deprecated temperature, top_p and top_k on 2026-07-21, and its
    Gemini 3 guide asks for the default of 1.0: "Changing the temperature
    (setting it below 1.0) may lead to unexpected behavior, such as looping
    or degraded performance". This repo sent 0.7 on every turn.
    """
    version = model_version(model, "gemini-")
    return version is None or version < (3, 0)


# Every model Kimi still serves (kimi-k3, the kimi-k2.7-code pair, kimi-k2.6)
# fixes its sampling, and the parameter reference says what passing one does:
# "'Fixed' means the parameter cannot be modified: passing any other value
# returns an error, so do not pass it explicitly." The models that took a
# temperature (kimi-k2.5, the moonshot-v1 series) were discontinued
# 2026-08-31, so Kimi gets no temperature at all.
KIMI_ACCEPTS_TEMPERATURE = False
