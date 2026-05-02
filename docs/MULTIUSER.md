# Multi-user mode

Share one machine with up to 4 people while keeping each person's data, conversations, memories, and skill state fully private. The kernel enforces the boundaries.

> **Linux only in v1.** macOS multi-user is planned. Existing single-user installs continue to work unchanged.

## How it works

When you choose 2-4 users at install time, the wizard provisions:

- One **orchestrator** system user (`mom_orchestrator`) that runs the bot process. This account reads `.env`, the slot table, and shared resources. It cannot read any user's private data.
- One **slot** system user per allowed user (`mom_user1`, `mom_user2`, ...). Each slot owns its own data directory and is the identity the CLI subprocess runs as when that user sends a message.
- A **sudoers fragment** at `/etc/sudoers.d/myoldmachine` granting the orchestrator the right to spawn only the configured CLI binaries (`claude`, `codex`) as only the slot accounts. No shells, no other commands, no other targets.

When a Telegram message arrives, the bot looks up which slot is bound to the sender's Telegram ID, then dispatches the LLM call as `sudo -u mom_userN claude ...` with the working directory set to that slot's data folder. The Linux kernel enforces filesystem permissions: `mom_user2` cannot enter `mom_user1`'s directory, period.

```
       Telegram message
              │
              ▼
   ┌───────────────────────┐
   │   mom_orchestrator    │  ← bot process; reads slot table
   │   (data/orchestrator/) │     dispatches by sender's slot
   └───────────┬───────────┘
               │ sudo -u mom_userN
   ┌───────────┴───────────┐
   ▼                       ▼
mom_user1               mom_user2          ← per-slot CLI subprocess
data/users/user1/      data/users/user2/   ← kernel-enforced isolation
```

## Privacy boundaries

| Boundary | Who can read it | Who can write it |
|---|---|---|
| `data/orchestrator/` (slot table, bot session state) | mom_orchestrator only (mode 0700) | mom_orchestrator only |
| `data/users/userN/` (per-slot dir) | mom_userN, mom_orchestrator (mode 2770, group=mom_orchestrator) | same |
| `data/shared/` | all slots (mode 0775) | mom_orchestrator only |
| `/etc/sudoers.d/myoldmachine` | root only (mode 0440) | root only |

The admin role is a **controller**, not a data viewer. The admin can add or remove users, see system health, and restart the bot: but cannot read another user's conversations, files, or memories. That separation is enforced by the OS, not by application logic.

## Choosing the right number of users

Each slot consumes RAM proportional to the LLM you run. As a rough guide:

| RAM per user | Recommendation |
|---|---|
| < 4 GB | Strongly recommend the request queue (one LLM call at a time) |
| 4-8 GB | Queue is a good safety net but not required |
| ≥ 8 GB | Queue is optional; full concurrency is fine |

The wizard asks about the queue based on your machine's RAM divided by the user count. Turning it on serializes LLM calls across all users so two people typing at the same time can't OOM the machine.

You can change the queue setting later by editing `data/orchestrator/users.json` (`concurrent_requests: 0` for unlimited, `1` for serial) and restarting the bot.

## Install walkthrough

```bash
git clone https://github.com/nickathens/MyOldMachine.git
cd MyOldMachine
./install.sh
```

At step 5, you'll see:

```
Number of users (1-4) [1]: 3
The first user (you) is the admin. The admin can:
  - Add and remove users from Telegram (/adduser, /removeuser)
  - See system health (/health)
  - Restart the bot (/restart)
The admin CANNOT read other users' messages, memories, or files.

This machine has 12 GB RAM (4.0 GB per user).
Enable request queue? Two users hitting the LLM at once on a small box
can OOM. The queue serializes calls. [Y/n]: y
```

The wizard then creates the system users, sets up the directories, installs the sudoers fragment (validated with `visudo -cf` before installation), and writes the orchestrator's slot table.

When the bot starts, you (the admin) get a Telegram message. Send `/users` to see the slot table: slot 1 is bound to your ID, slots 2-N are free.

## Admin commands

All commands are admin-only. Non-admins get no response at all (the command's existence is not leaked).

### `/users`

Lists every slot, who's bound to it, and when they were added.

```
Slots (2/3 bound):

[1] Alice (admin)
    Telegram ID: 111111111
    Added: 2026-05-02T14:23:01
[2] Bob
    Telegram ID: 222222222
    Added: 2026-05-02T14:25:30
[3] (free)
```

### `/adduser <telegram_id> <name>`

Binds a Telegram ID to the lowest-numbered free slot. The bound user can send a message to the bot immediately: no second step required.

```
/adduser 333444555 Carol
→ Bound Carol (333444555) to slot 3.
```

Errors if the Telegram ID is already bound, or if every slot is full.

### `/removeuser <telegram_id>`

Unbinds the user from their slot and archives their data folder to `data/users/_archived/<timestamp>_userN_tid<id>/`. The slot becomes available for the next `/adduser`. The user's data is preserved for recovery: nothing is deleted by this command.

```
/removeuser 222222222
→ Removed user from slot 2 (Bob).
→ Archived slot data to: data/users/_archived/20260502-153045_user2_tid222222222
```

The admin slot cannot be removed by this command. To change the admin, reinstall with multi-user.

### `/purgeuser <telegram_id> PURGE I UNDERSTAND`

Permanently deletes the archived data for a Telegram ID. The confirmation phrase is required exactly as shown: there is no second prompt and no undo.

```
/purgeuser 222222222 PURGE I UNDERSTAND
→ Purged 1 archived directories for 222222222.
```

The user must already have been removed via `/removeuser`. Active slots are never affected by this command.

### `/health`

In multi-user mode, the standard health report includes a multi-user supplement:

```
...
Multi-user: 2/3 slots bound
Queue: on (concurrent=1)
```

## CLI authentication per slot

The Claude CLI and Codex CLI store credentials in the running user's home directory (`~/.claude/`, `~/.codex/`). Each slot user has its own home (`data/users/userN/`) and therefore needs its own authentication.

After install, run the login flow once per slot:

```bash
sudo -u mom_user1 claude login    # opens browser, paste token back
sudo -u mom_user2 claude login
sudo -u mom_user3 claude login
# (or codex login, depending on which CLI you configured)
```

Until a slot user has authenticated, the bot's first call for that slot will fail with an auth error. This is a **feature**, not a bug: it means one user's API quota and identity are never reused for another user's requests. If you'd rather everyone share one token, switch to single-user mode and stop here.

## Operational notes

- **Restarts:** `/restart` reloads the bot but does not touch the slot table or system users. Adding or removing users does not require a restart.
- **Bot updates:** `/update` (or running the installer again) preserves the slot table. The wizard detects an existing multi-user setup and skips re-provisioning.
- **Recovery:** The slot table at `data/orchestrator/users.json` is the source of truth. If it's lost, the wizard can rebuild it, but bound Telegram IDs need to be re-added by the admin.
- **Re-binding a slot after `/removeuser`:** the archive step renames the slot dir into `_archived/`, leaving no slot dir behind. The orchestrator cannot recreate it with the correct ownership at runtime (it would need root). Before binding a new user to a previously-removed slot, re-run `./install.sh` once to re-provision the empty slot dir. Until then, CLI calls for that slot will fail with a logged error and the new user's first message will surface the missing-dir state.
- **Backups:** Each slot's data lives at `data/users/userN/`. Standard tar/rsync of `data/users/` (run as root) captures everything. `data/users/_archived/` contains removed users' historical data and is safe to back up too.
- **Removing multi-user:** There is no automatic teardown. Manually delete `/etc/sudoers.d/myoldmachine`, run `userdel mom_orchestrator mom_user1 mom_user2 ...`, and remove `data/orchestrator/`. The bot then runs in single-user mode again.

## Why this design

We considered three alternatives and rejected each:

1. **Bubblewrap or per-user containers.** Heavier dependency, harder to reason about, no real privacy gain over OS users on a trusted single-host setup.
2. **Single bot user with logical paths.** The bot itself becomes the trust boundary. Any code-execution bug or skill misuse can read every user's data. We didn't want a single Python bug to compromise everyone.
3. **Full Docker per user.** Image management, networking, and persistence become a separate operations problem. Overkill for a 1-4 user machine that's meant to feel like a personal appliance.

The slot model gives the strongest practical privacy guarantee (kernel-enforced) at the lowest operational cost. The orchestrator is the only privileged component and it can do exactly two privileged things: spawn a CLI as a slot user, and that's it.

## Verification checklist

After installing multi-user mode, run these checks on the target host. Anything not green needs to be debugged before relying on the privacy boundary.

**Privacy (kernel-enforced isolation)**

- [ ] `sudo -u mom_user1 ls data/users/user2/` is **denied** with `Permission denied`.
- [ ] `sudo -u mom_user2 cat data/users/user1/<some-file>` is **denied**.
- [ ] `sudo -u mom_orchestrator cat .env` succeeds (orchestrator must read its own config).
- [ ] `sudo -u mom_orchestrator ls data/users/user1/` succeeds (orchestrator is in the slot's group).

**Sudoers correctness**

- [ ] `sudo -u mom_orchestrator sudo -n -u mom_user1 /usr/bin/claude --help` succeeds.
- [ ] `sudo -u mom_orchestrator sudo -n -u mom_user1 /bin/bash` is **denied** (only configured CLI binaries allowed).
- [ ] `sudo -u mom_orchestrator sudo -n -u root /usr/bin/claude --help` is **denied** (root is not a runas target).

**Admin commands (from Telegram)**

- [ ] `/users` lists slot 1 bound to your ID with `(admin)`.
- [ ] `/adduser <secondary_id> Bob` returns `Bound Bob (...) to slot 2.`
- [ ] As Bob's account, send a message: bot responds, and `data/users/user2/` populates.
- [ ] `/users` from Bob's account: no response (silent ignore: admin only).
- [ ] `/removeuser <bob_id>` returns success and creates `data/users/_archived/<timestamp>_user2_tid<bob_id>/`.
- [ ] `/removeuser <admin_id>` is refused.
- [ ] `/purgeuser <bob_id> PURGE I UNDERSTAND` deletes the archived directory.

**Recovery**

- [ ] After `systemctl restart myoldmachine`, the slot table survives and bound users can still talk to the bot.
- [ ] `data/orchestrator/users.json` is mode 0600 owned by `mom_orchestrator:mom_orchestrator`.
- [ ] `/etc/sudoers.d/myoldmachine` is mode 0440 owned by `root:root`.

**Queue (only if `concurrent_requests=1`)**

- [ ] Two users sending long-running CLI commands at the same time: the second one waits, both complete without OOM. Watch `data/logs/bot.log` for the `LLM semaphore ENABLED` line on startup.
