# MemPalace -- Per-user Permanent Conversation Memory

Each user owns a private semantic-search palace over their full conversation
history. Vector store lives inside the user's data directory; per-Telegram-user
separation is at the application level.

## When to Use

- When the user says "remember", "recall", "what did we discuss about"
- When you cannot find something the user references in structured memory
- When the user asks about a past conversation topic

Do NOT use for general queries or when the answer is in current context.

## Storage Layout

```
<BOT_DIR>/data/mempalace/
  venv/                       # SHARED Python venv (one install per machine)

<user_dir>/mempalace/         # PER-USER palace
  palace/                     # ChromaDB vector store
  convos/                     # Per-day session JSON exports
  sync_state.json             # Last sync timestamp + drawer count
```

The shared venv is just the Python interpreter and the `mempalace` library --
it holds no user data. All user data is under each user's own dir.

## Setup

First run installs the shared venv (one-time, ~500 MB) AND provisions one
user's palace:

```
<BOT_VENV_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_setup.py \
    --user-dir <USER_DIR>
```

Add new users later (the venv install will be skipped if already present):

```
<BOT_VENV_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_setup.py \
    --user-dir <ANOTHER_USER_DIR>
```

Install just the venv ahead of time without touching any user:

```
<BOT_VENV_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_setup.py \
    --shared-only
```

**Requirements:** 4 GB RAM, 2 GB free disk for the shared venv. Per-user
palaces grow with conversation history (typically a few MB per user per year).

## Searching

Run with the shared mempalace venv Python:

```
<MEMPALACE_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_search.py \
    "search query" --user-dir <USER_DIR>
```

Options:
- `--results N` -- Number of results (default: 5)
- `--room ROOM` -- Optional category filter (technical, decisions, problems, ...)
- `--json` -- Raw JSON output

The venv Python is at `<BOT_DIR>/data/mempalace/venv/bin/python`.

## Daily Sync

Schedule a daily job per user so each palace stays current:

```
<BOT_VENV_PYTHON> <SCHEDULER_CLI> add --user <USER_TID> --at "03:30" \
    --type command \
    --command "<MEMPALACE_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_sync.py --user-dir <USER_DIR>" \
    --repeat daily --name "MemPalace daily sync" --no-notify
```

Manual sync:

```
<MEMPALACE_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_sync.py \
    --user-dir <USER_DIR>
```

Options:
- `--force-today` -- Re-mine today's session (otherwise only complete days are mined)
- `--dry-run` -- Preview without writing

## Important

- One palace per user. The script never touches another user's directory.
- MemPalace stores text only. No attachments or files.
- The embedding model runs locally. No API calls.
- Strong for concrete topic recall (bugs, features, decisions). Weaker for
  abstract or emotional queries.
- A forked palace cannot pick up upstream conversations -- it IS the user's
  own conversation memory. There is nothing upstream to merge.
