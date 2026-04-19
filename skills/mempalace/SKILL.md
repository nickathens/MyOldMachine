# MemPalace -- Conversation Memory Search

Semantic search over conversation history. Finds past discussions by meaning, not just keywords.

## When to Use

- When the user says "remember", "recall", "what did we discuss about"
- When you can't find something the user references in structured memory
- When the user asks about a past conversation topic

Do NOT use for general queries or when the answer is in current context.

## Setup

If not yet installed, run the setup script with the bot's Python:

```
<BOT_VENV_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_setup.py
```

This creates an isolated Python environment, installs MemPalace, and mines existing
conversation history. Takes 2-10 minutes depending on history size and hardware.

**Requirements:** 4 GB RAM minimum, 2 GB free disk. The setup downloads ~500 MB
(ChromaDB + embedding model).

After setup, set up a daily sync job to keep the palace current:

```
<BOT_VENV_PYTHON> <SCHEDULER_CLI> add --user <ADMIN_USER_ID> --at "03:30" --type command --command "<MEMPALACE_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_sync.py" --repeat daily --name "MemPalace daily sync" --no-notify
```

## Searching

Run with the **mempalace venv Python** (not the bot's Python):

```
<MEMPALACE_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_search.py "search query" --wing user_<USER_ID>
```

Options:
- `--results N` -- Number of results (default: 5)
- `--wing WING` -- Required. User's wing (user_<telegram_id>)
- `--room ROOM` -- Filter by room (technical, architecture, planning, decisions, problems, general)
- `--json` -- Raw JSON output

The mempalace venv Python is at: `<BOT_DIR>/data/mempalace/venv/bin/python`

## Manual Sync

To manually sync new messages into the palace:

```
<MEMPALACE_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_sync.py
```

Options:
- `--force-today` -- Re-mine today's messages (normally only complete days are mined)
- `--dry-run` -- Preview what would be mined without filing
- `--user USER_ID` -- Sync specific user only (default: all users)

## Data Location

- Palace (vector store): `<BOT_DIR>/data/mempalace/palace/`
- Session exports: `<BOT_DIR>/data/mempalace/convos/<user_id>/`
- Isolated venv: `<BOT_DIR>/data/mempalace/venv/`

## Important

- Each user has their own wing (`user_<telegram_id>`). Never search another user's wing.
- MemPalace searches text content only. No attachments or files are stored.
- The embedding model runs locally. No API calls required for search.
- Strong for concrete topic recall (bugs, features, technical discussions). Weak for abstract/emotional queries.
