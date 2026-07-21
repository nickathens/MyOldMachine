"""Per-user Claude CLI workspaces (config dir, auto-memory pool, transcripts).

Why: the bot serves several Telegram users from ONE OS account and ONE
working directory. The Claude CLI keys all of its persistent state -- the
auto-memory pool it loads into every session, plus session transcripts --
by (config dir, project cwd). With one shared config dir, every user's
sessions read and write the SAME memory pool, so facts learned in one
user's private chat resurface in another user's chat. That is exactly the
2026-07-19 incident: an animation built in one user's session was
described to another user as their own work, because the shared pool
carried the project note with no owner attached.

Fix: point CLAUDE_CONFIG_DIR at data/users/<id>/claude for every CLI turn
that runs on behalf of a Telegram user. Memory and transcripts become
private per user; the filesystem, skills, and shared projects remain
shared machine state (the product's soft multi-user model -- see the
README "Trust model" section: this is scoping, not a security boundary).

Migration: the legacy shared pool is split ONCE, by provenance. Every
memory file the CLI writes carries an ``originSessionId`` in frontmatter;
the matching legacy transcript records which Telegram user the session
served, because the bot stamps "User's Telegram ID: <id>" into every
system prompt. Each file is COPIED into its author's private pool; files
whose origin cannot be established fall back to the primary admin. The
legacy pool is never modified beyond one marker file, so it stays intact
as a frozen backup.

Credentials: the CLI's OAuth login lives in the macOS keychain. Each
workspace gets a symlink to ONE shared .credentials.json (exported from
the keychain once, or already present on Linux installs), so every
session rides the same refresh chain. Two things break that link, and
both are folded back by reconcile_shared_credentials after every turn:

1. Refresh detaches the symlink. The CLI rewrites the credential by
   renaming a temp file onto it; rename onto a symlink REPLACES the
   symlink with a private regular file, forking the token per workspace
   and leaving the shared file (and every other workspace) stale.

2. macOS shadows the file with a per-config-dir keychain item. The CLI
   does not read the plain "Claude Code-credentials" item for a custom
   CLAUDE_CONFIG_DIR -- it keys the credential by config dir, as
   "Claude Code-credentials-<sha256(config_dir)[:8]>". Precedence is:
   namespaced item if one exists, else the file. So the file is
   authoritative only until the CLI writes such an item, and from that
   moment every file-based repair in this module runs, logs success, and
   has NO effect on the credential the CLI actually uses.

   Measured on this machine 2026-07-20: a successful turn from a file
   credential does NOT create the item, but a token REFRESH does. So a
   workspace silently detaches from the shared file at its first refresh
   -- which is the 2026-07-20 outage: the item for one user held a
   revoked token, the CLI sent it, the API returned 401, and the file
   machinery reported healthy throughout.

   The naming scheme is undocumented (Anthropic documents only the plain
   service name and the file fallback). It is corroborated by community
   multi-account tooling and verified live here, but a CLI change could
   move it -- hence test_namespace_derivation, which fails loudly rather
   than letting the breakage reach production silently.

If no credential can be provisioned the workspace is refused and the turn
falls back to the shared config dir, because a private-but-unauthenticated
turn would just die.

System turns (user_id None: maintenance, health checks, nightly jobs)
keep the process-default config dir and are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BOT_DIR = Path(__file__).parent.parent.resolve()

# Entries shared from the legacy config dir into every per-user config dir
# via symlink. Hooks and permissions (settings*.json) and plugins are
# machine-wide concerns; .credentials.json is deliberately ONE shared file
# so all workspaces ride a single OAuth refresh chain instead of forking
# the token per user (see _ensure_shared_credentials to provision it and
# reconcile_shared_credentials to keep the chain intact across refreshes).
_SHARED_CONFIG_ENTRIES = (
    "settings.json",
    "settings.local.json",
    "plugins",
    ".credentials.json",
)

_KEYCHAIN_SERVICE = "Claude Code-credentials"

# Attribute lines in `security dump-keychain` output, e.g.
#     "acct"<blob>="coocooai"
# Non-ASCII values come back hex-encoded (0x...) instead of quoted; those
# simply do not match, which is the right outcome -- an account name this
# module cannot read is one it must not act on.
_KEYCHAIN_ATTR_RE = re.compile(r'"(acct|svce)"<blob>="([^"]*)"')

_MIGRATION_MARKER = ".per-user-split-done"


def _namespaced_keychain_service(config_dir: Path) -> str:
    """The keychain service the CLI uses for a non-default config dir.

    "Claude Code-credentials-<sha256(config_dir)[:8]>", hashed over the
    config dir path exactly as given (not resolved -- the CLI hashes the
    string it was handed). Undocumented; see the module docstring.
    """
    digest = hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]
    return f"{_KEYCHAIN_SERVICE}-{digest}"


def _credential_is_usable(raw) -> bool:
    """True if `raw` is a credential the CLI could actually authenticate with.

    Existence is not usability. A hollow credential (accessToken "")
    satisfies a bare .exists() check forever, so the keychain re-export
    never fires and every workspace linked to the file gets a credential
    guaranteed to fail.

    Expiry is deliberately NOT checked: an expired accessToken with a live
    refreshToken is the normal recoverable state, and rejecting it would
    break the very refresh chain this module exists to maintain.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return False
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        # Other shapes (API-key form) pass through unjudged: this function
        # knows the OAuth shape only, and must not veto what it cannot read.
        return bool(data)
    return bool(oauth.get("accessToken")) and bool(oauth.get("refreshToken"))


def _credential_is_hollow(raw) -> bool:
    """True only for a credential that is definitively empty.

    Narrower than ``not _credential_is_usable``: that says "do not trust
    this", which is the right test before USING a secret. This one says
    "this can never be anything to anyone", which is the only safe licence
    to DELETE a secret. Anything unparseable, or in a shape this module
    does not understand, is deliberately not hollow -- unreadable is not
    the same as empty, and the cost of being wrong is destroying the last
    copy of a login.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return False
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return False
    return not oauth.get("accessToken") and not oauth.get("refreshToken")


def _refresh_expiry_ms(raw) -> float:
    """refreshTokenExpiresAt from a credential blob, -inf when absent.

    -inf rather than 0 so a credential that does not record an expiry
    always loses a "which of these lives longest" comparison instead of
    tying with one that expired at the epoch.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return float("-inf")
    try:
        oauth = (json.loads(raw) or {}).get("claudeAiOauth") or {}
    except (TypeError, ValueError, AttributeError):
        return float("-inf")
    at = oauth.get("refreshTokenExpiresAt")
    return float(at) if isinstance(at, (int, float)) else float("-inf")


def _keychain_accounts(service: str) -> list:
    """Every account name stored under `service`, in keychain order.

    `security find-generic-password -s SERVICE -w` returns ONE item and
    never says which. With several items under a single service macOS
    picks by its own ordering -- and on this machine it picks a HOLLOW
    "unknown" account over the real login (measured 2026-07-21: the
    account-less read returns accessToken ""). So every account-less read
    is a coin flip that this machine loses. Enumerate, then choose.

    Metadata only: `dump-keychain` without -d reads no secret material,
    needs no unlock, and raises no access dialog (measured: 15ms, exit 0).
    """
    if sys.platform != "darwin":
        return []
    try:
        proc = subprocess.run(
            ["security", "dump-keychain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("keychain enumeration failed: %s", exc)
        return []
    if proc.returncode != 0:
        return []

    accounts = []
    for block in proc.stdout.split("\nkeychain: "):
        attrs = dict(_KEYCHAIN_ATTR_RE.findall(block))
        if attrs.get("svce") == service:
            acct = attrs.get("acct")
            if acct is not None and acct not in accounts:
                accounts.append(acct)
    return accounts


def _read_keychain_secret(service: str, account: Optional[str]) -> Optional[str]:
    """One raw secret, by service and (optionally) account."""
    cmd = ["security", "find-generic-password", "-s", service, "-w"]
    if account is not None:
        cmd[2:2] = ["-a", account]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("keychain read failed for %s: %s", service, exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _read_keychain_item(service: str) -> Optional[str]:
    """The best secret stored under `service`, or None if there is none.

    "Best" means: a usable item if any exists, and among usable items the
    one whose refresh token lives longest. An unusable item is still
    returned when it is all there is, because callers must be able to tell
    "nothing here" (leave it alone) from "something broken here" (clear
    it) -- collapsing those two would leave a hollow item shadowing the
    file forever.
    """
    if sys.platform != "darwin":
        return None

    secrets = []
    for account in _keychain_accounts(service):
        secret = _read_keychain_secret(service, account)
        if secret:
            secrets.append(secret)
    if not secrets:
        # Enumeration can fail (or be unavailable) while a direct read
        # works, so never let a dump-keychain problem look like "no login".
        secret = _read_keychain_secret(service, None)
        if secret:
            secrets.append(secret)
    if not secrets:
        return None

    usable = [s for s in secrets if _credential_is_usable(s)]
    return max(usable, key=_refresh_expiry_ms) if usable else secrets[0]


def _delete_keychain_item(service: str, account: Optional[str] = None) -> bool:
    """Remove an item so the CLI falls back to the shared file.

    `account` is optional only because the namespaced per-config-dir items
    are unique per service. Pass it whenever several items share a service
    name: an account-less delete removes whichever one macOS picks first,
    which on this machine is not the one you meant.
    """
    if sys.platform != "darwin":
        return False
    cmd = ["security", "delete-generic-password", "-s", service]
    if account is not None:
        cmd += ["-a", account]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("keychain delete failed for %s: %s", service, exc)
        return False
    return proc.returncode == 0


def _purge_hollow_keychain_items(service: str) -> int:
    """Delete every definitively-empty item under `service`; count them.

    Refusing to USE a hollow credential (the 2026-07-20 fix) stopped it
    handing out tokens guaranteed to 401, but left the trap armed: while
    the item exists it still wins account-less lookups and still starves
    the repair path that is supposed to rescue the machine unattended.
    Removing it is what actually disarms it.

    Only ever removes items that parse as an OAuth credential with both
    tokens empty -- see _credential_is_hollow. Never touches an item it
    cannot read or does not recognise.
    """
    if sys.platform != "darwin":
        return 0
    purged = 0
    for account in _keychain_accounts(service):
        secret = _read_keychain_secret(service, account)
        if secret and _credential_is_hollow(secret):
            if _delete_keychain_item(service, account):
                purged += 1
                logger.warning(
                    "removed hollow keychain credential %s (account %s); it was "
                    "shadowing the real login on account-less lookups",
                    service, account,
                )
    return purged


def _write_shared_credential(shared: Path, payload: bytes) -> bool:
    """Atomically publish `payload` as the shared credential file (0600).

    Atomic because other workspaces read this file concurrently: a reader
    must see the whole old credential or the whole new one, never a torn
    half of each.
    """
    try:
        shared.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(shared.parent), prefix=".credentials.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, shared)
            return True
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning("could not update shared credential: %s", exc)
        return False

_ORIGIN_RE = re.compile(r"originSessionId:\s*([A-Za-z0-9][A-Za-z0-9-]{6,})")
_TELEGRAM_ID_RE = re.compile(r"User's Telegram ID: (\d+)")

# How many transcript lines to scan for the Telegram ID stamp. The stamp
# sits in the system prompt of the first user message, always within the
# first few events of a transcript.
_TRANSCRIPT_SCAN_LINES = 80


def _encode_project_dir(path: Path) -> str:
    """Mirror the CLI's project-dir encoding (non-alphanumerics -> '-')."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def default_legacy_root() -> Path:
    """The config dir the CLI used before per-user isolation."""
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(env) if env else Path.home() / ".claude"


def user_claude_dir(user_id: int) -> Path:
    from core.users import resolve_user_dir
    return resolve_user_dir(user_id) / "claude"


def _export_keychain_credentials(dest: Path) -> bool:
    """macOS: copy the CLI's OAuth credential out of the login keychain.

    The CLI stores its login under the plain service name, and does NOT
    consult that item for a custom CLAUDE_CONFIG_DIR: a workspace with no
    credential file and no namespaced item of its own reports "Not logged
    in" (live-proven 2026-07-19). The file form of the credential,
    standard on Linux, is read for any config dir, so the keychain secret
    is exported once to the legacy root and shared from there. 0600, same
    exposure class as the bot token in .env (see the README trust model:
    one OS account, disk is the boundary).

    Refuses to publish an unusable secret: writing a hollow credential
    here would hand every linked workspace a token guaranteed to 401,
    which is worse than leaving the old one in place for the caller to
    report on.
    """
    secret = _read_keychain_item(_KEYCHAIN_SERVICE)
    if not secret:
        return False
    if not _credential_is_usable(secret):
        logger.warning("keychain credential is hollow/malformed; not exporting")
        return False
    return _write_shared_credential(dest, secret.encode("utf-8"))


def _ensure_shared_credentials(legacy_root: Path) -> bool:
    """True if a *usable* CLI credential file exists (or was provisioned).

    Checking only existence made a hollow file count as success forever,
    so the keychain re-export could never fire to repair it. Independent
    of the keychain-shadowing outage, but the same class of bug: treating
    "a file is there" as "auth works".
    """
    cred = legacy_root / ".credentials.json"
    if cred.exists():
        try:
            if _credential_is_usable(cred.read_bytes()):
                return True
        except OSError as exc:
            logger.warning("unreadable shared credential %s: %s", cred, exc)
        logger.warning("shared credential %s unusable; re-exporting from keychain", cred)
    return _export_keychain_credentials(cred)


def _relink(link: Path, shared: Path) -> None:
    """Rejoin the shared chain so the next turn reads the shared file."""
    try:
        link.unlink()
        link.symlink_to(shared)
    except OSError as exc:
        logger.warning("could not restore credential symlink %s: %s", link, exc)


def _reconcile_detached_file(link: Path, shared: Path) -> bool:
    """Publish a refresh-detached workspace credential, then re-link it."""
    try:
        fresh = link.read_bytes()
    except OSError as exc:
        logger.warning("unreadable refreshed credential %s: %s", link, exc)
        return False

    if not _credential_is_usable(fresh):
        # Never overwrite a healthy shared credential with a hollow one.
        # Discard it and rejoin the chain: the shared file is the better
        # bet, and _ensure_shared_credentials repairs it if it is not.
        logger.warning("detached credential %s unusable; discarding and re-linking", link)
        _relink(link, shared)
        return False

    propagated = False
    try:
        current = shared.read_bytes() if shared.exists() else None
    except OSError:
        current = None
    if current != fresh:
        if not _write_shared_credential(shared, fresh):
            return False
        propagated = True

    _relink(link, shared)
    return propagated


def _reconcile_keychain_credential(config_dir: Path, shared: Path) -> bool:
    """Fold a macOS per-config-dir keychain credential back into the file.

    The CLI writes "Claude Code-credentials-<hash>" when it refreshes a
    token in a workspace, and from then on reads that item INSTEAD of the
    file. The workspace silently leaves the shared refresh chain, and
    every file-based repair in this module becomes a no-op for it while
    still logging success. That is the 2026-07-20 outage.

    Restore the invariant "the shared file is the one credential" by
    folding the item back and then removing it, so the next turn reads the
    file again. Same shape as the detached-symlink case below: capture the
    refresh, republish it, rejoin the chain. The item will reappear at the
    next refresh and be folded again -- convergence per turn, not a
    one-time repair.

    Ordering is deliberate: publish first, delete second. Deleting first
    would strand the only fresh copy if the write then failed.

    An item that is present but unusable is deleted WITHOUT publishing: it
    has no recovery value, and while it exists it shadows a shared file
    that may well be healthy. That is precisely the state that took the
    bot down, and precisely what the manual recovery did by hand.

    Safe from races in practice: this runs after the CLI process has
    exited, so nothing is writing the item concurrently.

    Returns True if a credential was propagated to the shared file.
    """
    if sys.platform != "darwin":
        return False

    service = _namespaced_keychain_service(config_dir)
    secret = _read_keychain_item(service)
    if secret is None:
        return False  # nothing shadowing the file

    if not _credential_is_usable(secret):
        logger.warning(
            "keychain item %s is unusable and shadows the shared credential; "
            "removing so %s falls back to the shared file",
            service, config_dir,
        )
        _delete_keychain_item(service)
        return False

    payload = secret.encode("utf-8")
    propagated = False
    try:
        current = shared.read_bytes() if shared.exists() else None
    except OSError:
        current = None
    if current != payload:
        if not _write_shared_credential(shared, payload):
            # Leave the item in place: right now it is the only fresh copy.
            return False
        propagated = True
        logger.info("folded refreshed keychain credential %s into shared file", service)

    if not _delete_keychain_item(service):
        logger.warning(
            "could not remove keychain item %s; that workspace stays detached "
            "from the shared credential", service,
        )
    return propagated


def _chain_is_alive(raw, now_ms: float) -> bool:
    """True if `raw` could still mint a new access token unattended."""
    if not raw or not _credential_is_usable(raw):
        return False
    return _refresh_expiry_ms(raw) > now_ms


def heal_credential_chains(
    legacy_root: Optional[Path] = None,
    *,
    now_ms: Optional[float] = None,
) -> Optional[str]:
    """Converge the machine onto ONE working login, without a human.

    Two stores can hold a Claude login on macOS:

      * the shared .credentials.json file, read by every per-user turn
        (those set CLAUDE_CONFIG_DIR, and the CLI reads the file form for
        any config dir) and, when no keychain item exists, by default-config
        turns too -- proven live 2026-07-21: with the keychain emptied, a
        default-config `claude -p` still authenticated off the file.
      * the plain keychain item, which SHADOWS the file for default-config
        turns whenever it is present: the nightly reflection, the health
        checks, and any user with no workspace of their own.

    So the file is the one store that serves everybody, and the keychain
    item is only ever a bootstrap source or a liability. That asymmetry
    decides both repairs:

      file dead, keychain alive  -> publish the keychain login to the file.
      keychain dead, file alive  -> DELETE the keychain item, so the turns
                                    it was shadowing fall back to the file.

    Deliberately never WRITES the keychain. Two stores holding the same
    rotating refresh token is not redundancy, it is a race: whichever side
    refreshes first invalidates the other's token, and the loser dies. One
    store, many readers, is the configuration that survives rotation --
    which is exactly why the per-user workspaces symlink to a single file
    instead of each holding a copy.

    (An earlier draft of this function did copy the file into the keychain.
    `security add-generic-password` reading its secret from stdin silently
    truncates at 128 bytes and still exits 0, so it wrote a corrupt 129-byte
    credential over the real one and reported success. The credential was
    rebuilt from the file; the lesson is in _delete_keychain_item's
    neighbours -- this module now only ever removes keychain items.)

    Returns a one-line description of what was healed, or None if nothing
    needed doing.

    Errors: the body catches what it can foresee (OSError on the file read,
    subprocess failures inside the keychain helpers) but does NOT promise to
    swallow everything -- test_never_raises_into_the_turn_it_runs_beside
    shows an OSError from the keychain read escaping. The guarantee callers
    actually rely on is theirs to keep: reconcile_shared_credentials wraps
    this in `except Exception`. Any new caller must do the same, because this
    runs alongside a turn, not instead of one.
    """
    if sys.platform != "darwin":
        return None  # one chain only; nothing to heal from

    legacy_root = legacy_root or default_legacy_root()

    # The plain keychain item is the partner of the DEFAULT config dir and
    # nothing else. Given some other root, there is no second chain to pair
    # it with -- and reaching for the machine's real keychain anyway would
    # copy a live token into a directory the caller never said was a
    # credential store. Caught by test_env_key_alone_is_enough, which
    # deletes its temp credential and then found a real one waiting there.
    try:
        if legacy_root.resolve() != default_legacy_root().resolve():
            return None
    except OSError:
        return None

    shared = legacy_root / ".credentials.json"

    # Clear hollow items first. One under the plain service is both a
    # reason this chain reads as dead AND the thing that poisons the read
    # below, so it has to go before either side is judged.
    _purge_hollow_keychain_items(_KEYCHAIN_SERVICE)

    try:
        file_raw = shared.read_text() if shared.exists() else None
    except OSError as exc:
        logger.warning("unreadable shared credential %s: %s", shared, exc)
        file_raw = None
    keychain_raw = _read_keychain_item(_KEYCHAIN_SERVICE)

    now_ms = time.time() * 1000 if now_ms is None else now_ms
    file_alive = _chain_is_alive(file_raw, now_ms)
    keychain_alive = _chain_is_alive(keychain_raw, now_ms)

    if file_alive == keychain_alive:
        # Both alive: nothing to do. Both dead: a human really is required,
        # and the login check is what says so.
        return None

    if keychain_alive:
        if _write_shared_credential(shared, keychain_raw.encode("utf-8")):
            logger.warning(
                "shared credential file was dead; restored it from the keychain login"
            )
            return "restored the shared credential file from the keychain login"
        return None

    if keychain_raw is None:
        return None  # nothing shadowing the healthy file; already correct

    # A dead keychain item over a healthy file is the 2026-07-20 outage in
    # miniature: default-config turns read the corpse and fail while the
    # file beside it works. Removing it is safe precisely because the file
    # is alive -- those turns fall straight back to it.
    removed = 0
    for account in _keychain_accounts(_KEYCHAIN_SERVICE):
        secret = _read_keychain_secret(_KEYCHAIN_SERVICE, account)
        if secret and not _chain_is_alive(secret, now_ms):
            if _delete_keychain_item(_KEYCHAIN_SERVICE, account):
                removed += 1
    if not removed:
        return None
    logger.warning(
        "removed %d dead keychain login(s) that were shadowing a healthy "
        "credential file", removed,
    )
    return "removed a dead keychain login so every job falls back to the shared file"


def reconcile_shared_credentials(
    user_id: int,
    *,
    legacy_root: Optional[Path] = None,
) -> bool:
    """Fold a per-user token refresh back into the shared credential file.

    The symlink from a workspace's .credentials.json to the shared file
    only survives until the CLI refreshes the OAuth token. On refresh the
    CLI writes the new token to a temp file and renames it onto
    .credentials.json; rename onto a symlink REPLACES the symlink with a
    private regular file (proven on this macOS). After that first refresh
    the workspace holds the freshest token privately while the shared file
    -- and every other workspace still linked to it -- goes stale. With
    rotated refresh tokens the stale copy soon fails to refresh, the turn
    falls open to the shared config dir, and the per-user isolation the
    workspace exists to provide silently unwinds.

    So after every per-user CLI turn we check whether this workspace's
    credential has detached: if it has, copy it onto the shared file
    (atomically, and only when the bytes actually changed) and restore the
    symlink. The shared file thus always carries the freshest token and
    every workspace re-converges on it at its next turn. Idempotent and
    safe to call when nothing refreshed -- a still-symlinked or absent
    credential is a no-op.

    Errors: individual steps are guarded, but the body does not promise to
    absorb every failure. A failure here must not fail the turn it runs
    after, and that is enforced at the two call sites rather than here --
    core/llm.py wraps the post-turn call in `except Exception`, and the
    pre-turn call inside ensure_user_claude_config is covered by the same
    fail-open guard around ensure_user_claude_config itself. A new caller
    that skips that guard breaks the contract.

    Returns True if a refreshed credential was propagated to the shared file.
    """
    legacy_root = legacy_root or default_legacy_root()
    shared = legacy_root / ".credentials.json"
    config_dir = user_claude_dir(user_id)
    link = config_dir / ".credentials.json"

    propagated = False

    # Case 0: one of the two machine-level chains has died outright. Run
    # first, because the cases below both assume the shared file is worth
    # converging on -- folding a fresh token into a dead file just makes a
    # newer dead file.
    try:
        heal_credential_chains(legacy_root)
    except Exception:  # pragma: no cover - never fail a turn over this
        logger.exception("credential chain healing failed")

    # Case 1: a refresh detached the symlink into a private regular file.
    # A still-symlinked (or absent) credential means the CLI never
    # rewrote it, so there is nothing to fold.
    if link.is_file() and not link.is_symlink():
        propagated = _reconcile_detached_file(link, shared) or propagated

    # Case 2: macOS keychain shadowing. Checked even when the symlink is
    # intact, because the item is written independently of the file -- an
    # untouched symlink says nothing about whether one exists. Runs last
    # because the item is what the CLI actually reads, which makes it the
    # CLI's own view of the current credential: if both moved, it wins.
    propagated = _reconcile_keychain_credential(config_dir, shared) or propagated

    return propagated


def ensure_user_claude_config(
    user_id: int,
    *,
    legacy_root: Optional[Path] = None,
    bot_dir: Optional[Path] = None,
) -> Path:
    """Create (idempotently) and return the per-user CLAUDE_CONFIG_DIR."""
    legacy_root = legacy_root or default_legacy_root()
    bot_dir = bot_dir or _BOT_DIR

    # A workspace the CLI cannot authenticate from is worse than the shared
    # dir: the turn would die with "Not logged in". Auth reaches the CLI
    # either as env keys (passed through build_cli_env) or as a credential
    # file inside the config dir. If neither is available, refuse -- the
    # caller fails open to the shared dir and the turn still runs.
    have_env_auth = any(
        os.environ.get(k)
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    )
    if not _ensure_shared_credentials(legacy_root) and not have_env_auth:
        raise RuntimeError(
            "no Claude CLI credentials reachable for a per-user workspace"
        )

    cfg = user_claude_dir(user_id)
    cfg.mkdir(parents=True, exist_ok=True)

    # Heal a credential that a prior turn's token refresh detached from the
    # shared file. The post-turn hook normally does this, but a crash or
    # restart between the refresh and that hook would leave the private copy
    # in place -- and the symlink loop below skips an entry that already
    # exists as a regular file, so it could never re-link on its own. Running
    # reconcile here first re-converges this user on the next turn.
    reconcile_shared_credentials(user_id, legacy_root=legacy_root)

    for name in _SHARED_CONFIG_ENTRIES:
        src, dst = legacy_root / name, cfg / name
        try:
            if src.exists() and not (dst.exists() or dst.is_symlink()):
                dst.symlink_to(src)
        except OSError as exc:
            logger.warning("could not link %s into %s: %s", name, cfg, exc)

    # Seed CLI state so a fresh config dir never stalls a headless -p run:
    # onboarding marked done, plus the logged-in account identity carried
    # over from the machine's existing state file.
    state = cfg / ".claude.json"
    if not state.exists():
        seed: dict = {"hasCompletedOnboarding": True}
        for candidate in (legacy_root / ".claude.json", Path.home() / ".claude.json"):
            try:
                if candidate.exists():
                    legacy_state = json.loads(candidate.read_text())
                    for key in ("oauthAccount", "userID"):
                        if key in legacy_state:
                            seed[key] = legacy_state[key]
                    break
            except (OSError, ValueError) as exc:
                logger.warning("unreadable CLI state %s: %s", candidate, exc)
        try:
            state.write_text(json.dumps(seed))
        except OSError as exc:
            logger.warning("could not seed %s: %s", state, exc)

    try:
        migrate_legacy_memory(legacy_root, bot_dir)
    except Exception as exc:
        # The split must never block a turn; isolation works even if the
        # legacy split has to be retried on a later turn.
        logger.warning("legacy memory split failed (will retry): %s", exc)

    return cfg


def _session_user_id(transcript: Path) -> Optional[int]:
    """Telegram user a legacy session served, from the bot's prompt stamp."""
    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(_TRANSCRIPT_SCAN_LINES):
                line = fh.readline()
                if not line:
                    break
                m = _TELEGRAM_ID_RE.search(line)
                if m:
                    return int(m.group(1))
    except OSError:
        return None
    return None


def migrate_legacy_memory(legacy_root: Path, bot_dir: Path) -> dict:
    """One-time provenance split of the legacy shared memory pool.

    Copies every memory file into the private pool of the user whose
    session wrote it and rebuilds each user's MEMORY.md index from the
    legacy one. Returns {filename: user_id} for this run ({} once done).
    Runs on the event-loop thread only, so the marker file is enough to
    keep it single-shot; copies are idempotent if two runs ever race.
    """
    enc = _encode_project_dir(bot_dir)
    legacy_mem = legacy_root / "projects" / enc / "memory"
    if not legacy_mem.is_dir():
        return {}
    marker = legacy_mem / _MIGRATION_MARKER
    if marker.exists():
        return {}

    from core.config import get_primary_admin_id
    admin_id = get_primary_admin_id()

    files = [p for p in sorted(legacy_mem.glob("*.md")) if p.name != "MEMORY.md"]
    assignment: dict = {}
    unresolved: list = []
    for path in files:
        uid: Optional[int] = None
        try:
            m = _ORIGIN_RE.search(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            m = None
        if m:
            uid = _session_user_id(
                legacy_root / "projects" / enc / f"{m.group(1)}.jsonl"
            )
        if uid is None:
            uid = admin_id
        if uid is None:
            unresolved.append(path.name)
            continue
        dest = user_claude_dir(uid) / "projects" / enc / "memory"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest / path.name)
        assignment[path.name] = uid

    # Split the index: each entry line references its file as (file.md).
    index = legacy_mem / "MEMORY.md"
    if index.exists():
        per_user: dict = {}
        for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"\(([^()\s]+\.md)\)", line)
            uid = assignment.get(m.group(1)) if m else None
            if uid is None and line.strip() and admin_id is not None:
                uid = admin_id  # headers / unmatched lines stay with the admin
            if uid is not None:
                per_user.setdefault(uid, []).append(line)
        for uid, lines in per_user.items():
            dest = user_claude_dir(uid) / "projects" / enc / "memory"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "MEMORY.md").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )

    if unresolved:
        # No admin to fall back to (bootstrap window before users.json
        # exists). Leave the marker unwritten so the split retries once
        # roles are recorded.
        logger.warning("memory split incomplete, unassigned: %s", unresolved)
        return assignment

    marker.write_text(
        json.dumps(
            {"split": {k: v for k, v in sorted(assignment.items())},
             "note": "legacy pool preserved as backup"},
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("legacy memory pool split per user: %s", assignment)
    return assignment
