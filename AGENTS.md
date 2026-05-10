# AGENTS.md

This file is for Claude agents working on this repo to coordinate. Two agents are typically active:

- **Mac agent** — runs on Nick's Mac mini (CooCoos-Mac-mini.local). Owns macOS specific concerns: launchd, Apple paths, brew installs, mac multi user quirks.
- **Linux agent** — runs on Nick's Linux machine. Owns Linux specific concerns: systemd, apt installs, slot based multi user (mom_user1, mom_user2, ...).

## Working agreement

1. Push work on your own branch with a clear prefix: `mac/...` for Mac agent, `linux/...` for Linux agent.
2. Never force push to `main`.
3. Open PRs against `main`. Wait for the other agent to comment before merging anything that touches shared files (bot.py, core/, utils/, skills/).
4. If you see a PR from the other agent, read it, comment on any overlap with your own active work, and approve when ready.
5. Keep this file's "Active work" section up to date so the other agent does not step on what you are doing.

## Shared files (high collision risk)

These files are touched by both sides and are the most common conflict points:

- `bot.py`
- `core/llm.py`
- `core/tools.py`
- `core/health.py`
- `install.sh`

If you are about to change one of these, leave a note below first.

## Mac only files

The Mac agent owns these. Linux agent should not edit them:

- `~/Library/LaunchAgents/com.jarvis.bot.plist` (system level, not in repo)
- Anything under `install/macos/` if it exists

## Linux only files

The Linux agent owns these. Mac agent should not edit them:

- `mom-user@.service`, `mom-orchestrator.service`, anything systemd
- Anything under `install/linux/` if it exists

## Active work

Append entries here. Format: `YYYY-MM-DD (agent) — short note`.

- 2026-05-10 (mac) — session_guard.py for cross user binding, per user Google credentials, launchd log fix, skill_hooks orphan killer. PR opened against main.
