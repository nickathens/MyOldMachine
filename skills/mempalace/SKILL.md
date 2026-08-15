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
  src/                        # SHARED upstream git checkout, pinned to a tag
  venv/                       # SHARED Python venv (one install per machine)
  backups/                    # Palace copies taken before an upgrade

<user_dir>/mempalace/         # PER-USER palace
  palace/                     # ChromaDB vector store
  convos/                     # Per-day session JSON exports
  sync_state.json             # Last sync timestamp + drawer count
```

The shared venv is just the Python interpreter and the `mempalace` library --
it holds no user data. All user data is under each user's own dir.

**`src/` is load-bearing.** The venv installs it editable (`pip install -e`),
so the running library IS that checkout: moving, renaming or deleting `src/`
breaks every palace on the machine until setup is re-run. `data/` is
gitignored, so the checkout is machine state, not part of this repo.

## Setup

First run clones the upstream repo, installs the shared venv from it
(one-time, ~800 MB: ~690 MB venv + ~100 MB checkout) AND provisions one
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

**Requirements:** 4 GB RAM, 3 GB free disk for the checkout plus the venv.
Per-user palaces grow with conversation history (typically a few MB per user
per year).

## Requirements installed, and the ones refused

Upstream declares nine extras. `mempalace_setup.py` installs these:

| Extra | Why |
| --- | --- |
| `extract` | binary-format mining: pdf, docx, pptx, xlsx, rtf |
| `spellcheck` | typo tolerance on search queries |
| `dev` | upstream's own test suite, so an upgrade can be gated before it goes live |
| one accelerator | `coreml` on macOS, `dml` on Windows, none on Linux by default |

and refuses the rest, each for a stated reason (`gpu`/`dml` are the wrong
accelerator for the platform and upstream says install exactly one;
`milvus`/`pgvector` are alternative vector backends this bot does not use, and
pgvector needs a running PostgreSQL server; `multilingual` is an empty
back-compat alias). The refusals are printed during install, and
`tests/test_mempalace_source_install.py` fails if a future upstream extra is
neither installed nor given a reason.

Nothing needs `MEMPALACE_EMBEDDING_DEVICE`: mempalace defaults to `auto` and
picks the first provider compiled into the installed onnxruntime. On this Mac
that resolves to CoreML.

## Upgrading

```
<BOT_VENV_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_setup.py \
    --shared-only --upgrade --ref vX.Y.Z
```

That fetches, moves the checkout to the tag, and reinstalls in place. Then
move `PINNED_REF` in the setup script so a fresh install lands on the same
version. Refuses to run if `src/` has local edits, so nobody's patch is
silently discarded.

Before any upgrade, copy each palace into `data/mempalace/backups/` and record
each user's drawer count. Compare the counts afterwards: equal counts before
and after is the integrity proof that the upgrade did not drop or duplicate
memories. Read a count without reading anyone's content -- palaces are
private per user, including from the agent running the upgrade for someone
else.

## Present but not wired in

The checkout ships more than the bot uses: a `mempalace` CLI, an MCP server
(`mempalace-mcp`) that would let other AI tools read a palace directly,
document mining beyond conversations, and an agent logstream coordination
layer for multi-machine fleets. None of it is connected to the bot. Do not
wire any of it up without asking -- the MCP server in particular would expose
one user's private palace to whatever client connects.

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

Schedule a daily job per user so each palace stays current.

Note: `scheduler_cli.py add` exposes only `--type reminder|agent`. The engine
supports `command` jobs (that is how the nightly maintenance jobs run) but the
CLI has no `--command` flag, so register them the way bot.py does, by writing
job meta directly. The bot's sync loop arms them within ~60s:

```
<BOT_VENV_PYTHON> -c '
import uuid
from datetime import datetime, timedelta
from core.scheduler import _save_meta
run_at = datetime.now().replace(hour=3, minute=15, second=0, microsecond=0)
if run_at <= datetime.now(): run_at += timedelta(days=1)
_save_meta(job_id=uuid.uuid4().hex, user_id=<ADMIN_TID>,
           message="MemPalace daily sync", job_type="command",
           name="mempalace-sync-<who>", notify=False,
           command="<MEMPALACE_PYTHON> <BOT_DIR>/skills/mempalace/scripts/mempalace_sync.py --user-dir <USER_DIR>",
           repeat="daily", run_at=run_at, raw_at="03:15", timeout_seconds=1800)
'
```

One job per user, each pointed at that user's own dir. Own them under the admin
id with `notify=False`, matching the other nightly system jobs, so they do not
appear in a regular user's reminder list. Verify with `scheduler_cli.py list`
and check `apscheduler_jobs` in `data/scheduler/scheduler.db` for the job id.

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
- **The venv's interpreter must not be pinned to a versioned Homebrew path.**
  A venv records the interpreter it was built from. Homebrew's python bump
  deletes the old `Cellar/python@3.12/3.12.13/...` directory, and every venv
  built from it dies instantly with exit 127 -- that is what killed both
  nightly sync jobs on 2026-08-15. `stable_base_interpreter()` builds from
  `/opt/homebrew/opt/python@3.12/...` instead, which survives the bump. If a
  sync job ever reports "No such file or directory" for the venv Python,
  check every venv on the machine, not just the one that reported.
