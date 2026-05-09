# Multi-user mode

Share one machine with up to 8 people while keeping each person's data, conversations, memories, and skill state fully private. The kernel enforces the boundaries.

> Supports **Linux** and **macOS**. Existing single-user installs continue to work unchanged.
>
> **WARNING:** macOS multi-user support is untested on real hardware. The code paths exist but have not been verified. Use at your own risk until this notice is removed.

## How it works

When you choose 2-8 users at install time, the wizard provisions:

- One **orchestrator** system user (`mom_orchestrator`) that runs the bot process. On Linux this is created via `useradd`; on macOS via `sysadminctl -addUser ... -roleAccount`. This account reads `.env`, the slot table, and shared resources. It cannot read any user's private data.
- One **slot** system user per allowed user (`mom_user1`, `mom_user2`, ...). Each slot owns its own data directory and is the identity the CLI subprocess runs as when that user sends a message.
- A **sudoers fragment** at `/etc/sudoers.d/myoldmachine` granting the orchestrator the right to spawn only the configured CLI binaries (`claude`, `codex`) as only the slot accounts. No shells, no other commands, no other targets. Works identically on Linux and macOS.

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

### Scope of the kernel-enforced isolation

Kernel-enforced privacy applies to **CLI providers** (Claude CLI, Codex CLI). Each user's CLI subprocess runs as their slot system user, so its tool calls (`run_command`, `read_file`, etc.) inherit that slot's UID and cannot reach another slot's directory.

**API providers (OpenAI, Gemini, Claude API, Ollama, etc.) currently do NOT get the same isolation.** The bot calls these APIs in-process and executes their tool requests as `mom_orchestrator`, which has group read access to every slot directory by design. If you run an API provider in multi-user mode, treat the LLM as if it could read any slot's data: the boundary is enforced only by application logic and the LLM's own behavior, not the kernel.

If kernel-enforced privacy matters to you, configure each slot to use a CLI provider. If you are the only user or you trust everyone on the box, the in-process API path is fine.

## Choosing the right number of users

Each slot consumes RAM proportional to the LLM you run. As a rough guide:

| RAM per user | Recommendation |
|---|---|
| < 4 GB | Strongly recommend universal queue (one LLM call at a time across all users) |
| 4-8 GB | Universal queue is a good safety net but not required |
| ≥ 8 GB | Per-user queue is fine; users can run requests in parallel |

## Queue mode

There is **always** a queue. Each user has their own per-user queue: one in-flight LLM request at a time per user, regardless of mode. The wizard asks about the queue *scope*:

- **`universal`**: all users share one queue. The bot processes one LLM request at a time across the whole machine. Other users get a "next in line" message and wait. Best for tight RAM/CPU budgets.
- **`per_user`**: each user has their own queue. Two users can have requests running in parallel, but each user's own messages still serialize. Best when the machine can comfortably run multiple concurrent LLM calls.

Hardware-constrained machines auto-promote to `universal` at startup regardless of the wizard answer (RAM < 8 GB or CPU cores ≤ 2). Look for the `Queue mode:` line in `data/logs/bot.log` to confirm what's active.

You can change the mode later by editing `data/orchestrator/users.json`:

```json
{
  "queue_mode": "universal",        // or "per_user"
  "queue_enabled": true,            // legacy mirror, kept in sync
  "concurrent_requests": 1          // legacy mirror: 1 universal, 0 per_user
}
```

Restart the bot for the change to take effect.

## Install walkthrough

```bash
git clone https://github.com/nickathens/MyOldMachine.git
cd MyOldMachine
./install.sh
```

At step 5, you'll see:

```
Number of users (1-8) [1]: 3
The first user (you) is the admin. The admin can:
  - Add and remove users from Telegram (/adduser, /removeuser)
  - See system health (/health)
  - Restart the bot (/restart)
The admin CANNOT read other users' messages, memories, or files.

This machine has 12 GB RAM (4.0 GB per user).

Queue mode (always on; choose the scope)
Each user always has their own queue: one in-flight request at a time per
user, regardless of mode. The choice is what happens when two users send
a message at the same instant.

  universal  All users share one queue. The bot processes one LLM
             request at a time across all users.
  per-user   Each user has their own queue. Two users can run
             requests in parallel, but each user's own messages
             still serialize.

Queue mode [universal/per-user] [universal]: universal
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

The wizard handles this for you on Linux: `claude auth login` runs as the install user once, then `propagate_claude_credentials` copies `~/.claude/.credentials.json` to each slot's home. If you ever rotate your Anthropic credentials and need to force re-propagate, re-run the installer — the multi-user step is idempotent.

If you bypass the wizard or want to authenticate slots independently:

```bash
sudo -u mom_user1 claude auth login   # opens browser
sudo -u mom_user2 claude auth login
sudo -u mom_user3 claude auth login
# (or codex login, depending on which CLI you configured)
```

Until a slot user has authenticated, the bot's first call for that slot will fail with an auth error. This is a **feature**, not a bug: it means one user's API quota and identity are never reused for another user's requests. If you'd rather everyone share one token, switch to single-user mode and stop here.

## Operational notes

- **Restarts:** `/restart` reloads the bot but does not touch the slot table or system users. Adding or removing users does not require a restart.
- **Bot updates:** `/update` (or running the installer again) preserves the slot table. The wizard detects an existing multi-user setup and skips re-provisioning.
- **Recovery:** The slot table at `data/orchestrator/users.json` is the source of truth. If it's lost, the wizard can rebuild it, but bound Telegram IDs need to be re-added by the admin.
- **Re-binding a slot after `/removeuser`:** the archive step renames the slot dir into `_archived/`, leaving no slot dir behind. The orchestrator cannot recreate it with the correct ownership at runtime (it would need root). Before binding a new user to a previously-removed slot, re-run `./install.sh` once to re-provision the empty slot dir. Until then, CLI calls for that slot will fail with a logged error and the new user's first message will surface the missing-dir state.
- **Backups:** Each slot's data lives at `data/users/userN/`. Standard tar/rsync of `data/users/` (run as root) captures everything. `data/users/_archived/` contains removed users' historical data and is safe to back up too.
- **Removing multi-user (Linux):** There is no automatic teardown. Manually delete `/etc/sudoers.d/myoldmachine`, run `userdel mom_orchestrator mom_user1 mom_user2 ...`, and remove `data/orchestrator/`. The bot then runs in single-user mode again.
- **Removing multi-user (macOS):** Unload the daemon with `sudo launchctl unload /Library/LaunchDaemons/com.myoldmachine.bot.plist`, delete the plist, remove `/etc/sudoers.d/myoldmachine`, run `sudo sysadminctl -deleteUser mom_orchestrator` (and each `mom_userN`), and remove `data/orchestrator/`.

## macOS-specific notes

> **WARNING:** This section is untested on real hardware. The LaunchDaemon, sysadminctl, and sudoers paths have been written from documentation but not verified on a live Mac. Test thoroughly before relying on it.

On macOS, the multi-user service runs as a **LaunchDaemon** (`/Library/LaunchDaemons/com.myoldmachine.bot.plist`) instead of a per-user LaunchAgent. Key differences from single-user mode:

- The daemon starts at boot, before any user logs in. No login session is needed.
- Installation requires `sudo` (the wizard handles this during setup).
- The `UserName` key in the plist runs the bot as `mom_orchestrator`.
- System users are created as macOS "role accounts" via `sysadminctl`. They have no login shell and cannot be used interactively.
- `sudoers` works identically to Linux. The same fragment at `/etc/sudoers.d/myoldmachine` grants the orchestrator CLI dispatch rights.

Service management commands on macOS:

```bash
# Check if the daemon is running
sudo launchctl list | grep myoldmachine

# Stop the daemon
sudo launchctl unload /Library/LaunchDaemons/com.myoldmachine.bot.plist

# Start the daemon
sudo launchctl load -w /Library/LaunchDaemons/com.myoldmachine.bot.plist

# View logs
tail -f <repo_dir>/data/logs/bot.log
```

## Why this design

We considered three alternatives and rejected each:

1. **Bubblewrap or per-user containers.** Heavier dependency, harder to reason about, no real privacy gain over OS users on a trusted single-host setup.
2. **Single bot user with logical paths.** The bot itself becomes the trust boundary. Any code-execution bug or skill misuse can read every user's data. We didn't want a single Python bug to compromise everyone.
3. **Full Docker per user.** Image management, networking, and persistence become a separate operations problem. Overkill for a 1-8 user machine that's meant to feel like a personal appliance.

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

**macOS-specific (run these on macOS installs)**

- [ ] `sudo launchctl list | grep myoldmachine` shows the daemon is loaded and running (PID column is nonzero).
- [ ] `/Library/LaunchDaemons/com.myoldmachine.bot.plist` exists with mode 0644 and owner `root:wheel`.
- [ ] `ps aux | grep bot.py` shows the process running as `mom_orchestrator` (not the install user).
- [ ] `id mom_orchestrator` succeeds (role account exists).
- [ ] `id mom_user1` succeeds (slot account exists).
- [ ] After reboot (no user login), the bot starts automatically and responds on Telegram.

**Universal queue (`queue_mode=universal` or hardware-constrained)**

- [ ] Two users sending long-running CLI commands at the same time: the second one waits, both complete without OOM. Watch `data/logs/bot.log` for `Queue mode: UNIVERSAL` on startup.

**Per-user queue (`queue_mode=per_user` on roomy hardware)**

- [ ] Two users sending requests at the same time: both run concurrently. Each user's own follow-up messages still wait for their previous turn. Watch `data/logs/bot.log` for `Queue mode: PER-USER`.
