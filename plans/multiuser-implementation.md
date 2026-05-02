# Multi-User Implementation Plan

**Status:** Planning. No code yet. Awaiting plan sign-off.
**Decision log:** `~/memory/nick/decisions/2026-05-02_myoldmachine-multiuser-architecture.md`
**Locked decisions:** Slot-based (4 max), Linux users + filesystem perms, shared LLM provider (v1), admin-bound at install, others via `/adduser`, no migration for existing single-user installs.

---

## Architectural Summary

```
+-----------------------------------------------------+
| Telegram → mom_orchestrator (bot.py service)        |
|   ├── users.json: slot ↔ telegram_id mapping        |
|   ├── identifies user, looks up slot                |
|   └── spawn: sudo -u mom_userN claude ...           |
+-----------------------------------------------------+
                        |
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
   mom_user1       mom_user2        mom_user3 (open)
   data/users/     data/users/      data/users/
   user1/  0700    user2/  0700     user3/  0700
   (private)       (private)        (private)
```

Kernel enforces every cross-user file access. Bot's only job: correct dispatch.

---

## Phases

### Phase 1: OS abstraction layer
Build cross-platform primitives so every OS-specific call lives in one file.

**Files:** `install/multiuser.py` (new)

**Functions:**
- `create_system_user(name, *, nologin=True)`: Linux: `useradd -r -s /usr/sbin/nologin <name>`. macOS: `sysadminctl -addUser <name> -roleAccount`.
- `delete_system_user(name)`: for uninstall, optional rollback
- `set_owner(path, user)`: wraps `chown` with sudo when needed
- `set_perms(path, mode)`: wraps `chmod`
- `grant_sudo(orchestrator, target_users, binaries)`: write `/etc/sudoers.d/myoldmachine` (single fragment, not per-user). Pattern: `mom_orchestrator ALL=(mom_user1,mom_user2,mom_user3,mom_user4) NOPASSWD: /full/path/to/claude, /full/path/to/codex`
- `revoke_sudo()`: uninstall path, removes the sudoers fragment
- `validate_sudoers_fragment(fragment_path)`: runs `visudo -cf` before installing

**Tests:** Manual on Linux + macOS VM. Verify file ownership, sudoers parsing, can spawn child as target user.

**Done when:** Can call `create_system_user('mom_test')`, run `sudo -u mom_test whoami`, get `mom_test` back, and `delete_system_user('mom_test')` cleans up.

---

### Phase 2: Install wizard updates
Add the multi-user prompt and provisioning to `install/wizard.py`.

**Wizard flow addition (after provider selection, before service registration):**
```
Step X: Multi-user setup
  How many people will use this machine? (1-4) [default: 1]
  > 2

  You picked 2 users. One must be the admin.
  • The admin can add/remove other users, restart the bot, view system health.
  • The admin cannot read other users' data: privacy is enforced by the OS.

  Admin name (just a label for you): _____
  Admin Telegram ID (the number you'll send messages from): _____

  Slots 2 will be left open. Once installed, you (the admin) can add a user
  from Telegram with: /adduser <telegram_id> <name>

  RAM check: 1.0 GB total, 2 users → 0.5 GB per user.
  Recommend: enable request queue (only 1 user runs at a time, others wait).
  Enable queue? [Y/n]
```

**Provisioning steps (when N>1):**
1. `create_system_user('mom_orchestrator')` (if not already created on prior install)
2. For slot in 1..N: `create_system_user(f'mom_user{slot}')`
3. Create `data/users/userN/` directories owned by `mom_userN`, mode 0700
4. Create `data/orchestrator/` owned by `mom_orchestrator`, mode 0700
5. Create `data/shared/` group-writable to a `mom` group containing all slot users
6. Write `/etc/sudoers.d/myoldmachine` granting orchestrator → spawn CLI as slots
7. Write initial `users.json` with admin binding
8. Set up systemd/launchd service to run as `mom_orchestrator`

**Edge cases:**
- N=1: skip everything multi-user, behave like today
- Reinstall: detect existing slots, ask whether to wipe or preserve

**Done when:** Fresh install on clean VM with N=2 produces correct file ownership, sudoers, and a runnable service.

---

### Phase 3: Orchestrator dispatch layer
Modify `bot.py` and `core/llm.py` to dispatch CLI subprocesses as the right user.

**Changes:**
- `data/orchestrator/users.json` schema:
  ```json
  {
    "slots": {
      "1": {"telegram_id": 123456789, "name": "Admin", "is_admin": true, "added": "2026-05-02"},
      "2": {"telegram_id": 987654321, "name": "User Two", "is_admin": false, "added": "2026-05-02"},
      "3": null,
      "4": null
    },
    "max_users": 4
  }
  ```
- `core/users.py` (new): `lookup_slot_by_telegram_id(tid) -> (slot, user_dict)`, `is_admin(tid) -> bool`, `add_user(tid, name) -> slot`, `remove_user(tid) -> archived_slot`
- `core/llm.py`: `ClaudeCLIProvider` and `CodexCLIProvider` accept a `slot` parameter. When set, spawn subprocess with `['sudo', '-u', f'mom_user{slot}', cli_path, ...]` instead of direct exec
- `bot.py` message router: identify telegram_id → resolve slot → pass slot to LLM provider
- Per-slot data directory resolution: `data/users/user{slot}/` instead of single shared dir

**Critical detail:** When orchestrator runs as `mom_orchestrator`, plain `whoami` returns `mom_orchestrator`. The CLI subprocess running as `mom_userN` cannot accidentally write to `data/users/user{other}/` because of 0700 perms. Test this.

**Done when:** Two users on the same machine send messages, each sees only their conversation history, neither's CLI subprocess can read the other's `data/users/` dir (verified by attempting and getting EACCES).

---

### Phase 4: Admin commands
Add admin-only Telegram commands.

**Files:** `bot.py` (handlers), `core/users.py` (logic)

**Commands:**
- `/adduser <telegram_id> <name>`: admin-only. Bind a free slot to telegram_id. Immediate, no confirmation. Errors if no free slots or telegram_id already bound.
- `/removeuser <telegram_id>`: admin-only. Archive `data/users/userN/` to `data/users/_archived/<timestamp>_userN/`. Free the slot. Errors if telegram_id is the admin.
- `/users`: admin-only. List bound slots with name, telegram_id, last activity, queue position.
- `/health`: admin-only. System stats (RAM, disk, CPU, uptime, active users, queue depth).
- `/purgeuser <telegram_id>`: admin-only, requires confirmation phrase. Permanently delete archived data.

**Authorization:** All admin commands check `is_admin(message.from_user.id)`. Non-admin gets generic "command not recognized" (don't leak the command's existence).

**Done when:** Admin can bind/remove/list users from Telegram. Removed user's data is archived (not deleted). Non-admin attempts to call admin commands get rejected.

---

### Phase 5: Request queue
Port the `_claude_semaphore` mechanism from `claude-telegram-bot/bot.py`.

**Files:** `bot.py`, `core/config.py`

**Changes:**
- Config flag: `concurrent_requests: int = 0` (0 = unlimited, >0 = serial cap)
- On message receive: if queue enabled and another request running → tell user "Another user's request is being processed. You're next in line." Then await semaphore.
- Wizard sets `concurrent_requests = 1` automatically when `ram_gb / max_users < 2`.

**Done when:** With queue enabled and concurrency=1, two simultaneous users → one waits with a clear message, no OOM.

---

### Phase 6: Backward compatibility
Detect old installs and run in legacy mode.

**Detection:** if `data/orchestrator/users.json` does not exist → legacy mode. Skip slot resolution, run CLI directly as the install user (today's behavior).

**Files touched:** `bot.py`, `core/llm.py`

**Done when:** Existing single-user install runs unchanged after pulling the new version. No data migrations, no permission errors.

---

### Phase 7: Documentation
Update README and write multi-user guide.

**Files:**
- `README.md`: add "Multi-user setup" section with brief overview, link to detailed guide
- `docs/MULTIUSER.md` (new): full walkthrough: install with N>1, admin commands, recovery procedures, threat model, what privacy guarantees you actually get
- `CONTEXT.md`: note that multi-user is opt-in at install, not retrofittable

---

### Phase 8: Testing
Verification checklist before declaring done.

- [ ] Fresh Linux install, N=1 → identical to today (no system users, no sudo, no orchestrator dir)
- [ ] Fresh Linux install, N=2 → both users isolated, admin commands work, non-admin commands don't leak
- [ ] Fresh macOS install, N=2 → same isolation works on macOS via sysadminctl + sudoers
- [ ] Privacy test: User B sends message, Claude tries `Read /home/ntouri/myoldmachine/data/users/user1/conversation.json` → permission denied at kernel level
- [ ] Privacy test: User B sends message, Claude tries `Bash: cat /home/ntouri/myoldmachine/data/users/user1/.env` → permission denied
- [ ] Recovery test: Install user runs `sudo claude` → can read everything
- [ ] Recovery test: Install user runs plain `claude` → cannot read user data dirs
- [ ] Queue test: 2 users send simultaneously with concurrency=1 → second user gets "next in line", no OOM
- [ ] Stress test: 4 users on Pi 4 (2GB RAM) with queue enabled → stable, no crashes
- [ ] Admin command test: `/adduser`, `/removeuser`, `/users`, `/health`, `/purgeuser` all work and are admin-gated
- [ ] Service test: kill bot mid-request → systemd restarts → state intact, queue resets
- [ ] Uninstall test: removing the package cleans up sudoers fragment, leaves slot users in place (data preservation)

---

## Out of Scope (v2+)

- Per-user LLM provider configs (.env per slot, different providers per user)
- `/share-context-with-admin` opt-in for support debugging
- Live migration from single-user to multi-user
- Invite-code flow for adding users without admin pre-knowing telegram_id
- Web UI for admin (currently Telegram-only)

---

## Risks

1. **Sudoers misconfiguration** could grant too much or break `sudo -u`. Mitigation: `validate_sudoers_fragment()` runs `visudo -cf` before install. Rollback path on validation failure.
2. **macOS sysadminctl variations** across macOS versions. Mitigation: explicit version check, fallback to `dscl` if needed, document tested versions.
3. **Forgotten file ownership in skills.** If a skill writes to a hardcoded path outside `data/users/userN/`, isolation breaks. Mitigation: skills MUST write to a path the bot tells them, never hardcoded. Audit existing skills.
4. **APScheduler shared DB** has all users' jobs. Mitigation: include `user_id` field on every job, filter by user when listing, never let user A see/modify user B's jobs.
5. **Existing 166 single-user installs.** Mitigation: backwards-compat detection (Phase 6) so they keep running unchanged.
