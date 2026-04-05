#!/bin/bash
# skill_hook_gate.sh — Fast filter for PreToolUse/PostToolUse hooks.
#
# Reads JSON from stdin. If the Bash command references a skill script,
# passes the JSON to skill_hooks.py for processing. Otherwise exits
# immediately (exit 0 = pass through, no blocking).
#
# This avoids ~50ms Python startup overhead on every non-skill Bash command.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT=$(cat)

# Quick string match: does the command contain a skill script path?
# Uses printf instead of echo to avoid backslash interpretation (xpg_echo).
if printf '%s' "$INPUT" | grep -qF 'skills/' && printf '%s' "$INPUT" | grep -qF '/scripts/'; then
    printf '%s' "$INPUT" | python3 "${SCRIPT_DIR}/skill_hooks.py"
    exit $?
fi

exit 0
