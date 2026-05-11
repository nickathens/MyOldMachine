"""
Credentials helper — store and retrieve secrets via the OS keyring.

The bot stores credentials (API tokens, service passwords) in the OS-native
encrypted store rather than plain files:

- macOS: the login Keychain via `security(1)`.
- Linux: not implemented yet — callers get a clear NotImplementedError and
  should fall back to env vars or a user-private file.

Convention: every credential the bot saves uses the service name
`mom-<service>` so they are easy to enumerate and do not collide with
other apps' Keychain entries.

This module shells out to `security` rather than depending on a third-party
keyring package — keeps the bot's runtime dependencies unchanged.
"""
from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass

SERVICE_PREFIX = "mom-"

_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class CredentialError(RuntimeError):
    """Base class for credentials errors."""


class CredentialNotFound(CredentialError):
    """Raised when a lookup finds no matching entry."""


class UnsupportedPlatform(CredentialError):
    """Raised when the current OS has no implemented backend."""


@dataclass(frozen=True)
class CredentialRef:
    service: str  # short name, no prefix (e.g. "surge")
    account: str  # email/username

    @property
    def keychain_service(self) -> str:
        return f"{SERVICE_PREFIX}{self.service}"


def _normalize_service(service: str) -> str:
    """Normalize and validate a service short-name.

    Accepts either the bare short name ("surge") or the already-prefixed
    form ("mom-surge") — both collapse to the bare form so the prefix
    is applied exactly once.
    """
    name = service.strip().lower()
    if name.startswith(SERVICE_PREFIX):
        name = name[len(SERVICE_PREFIX):]
    if not _SERVICE_RE.match(name):
        raise ValueError(
            f"Invalid service name {service!r}: must be lowercase alphanumeric "
            "with hyphens or underscores, and start with a letter or digit."
        )
    return name


def _require_macos() -> None:
    if platform.system() != "Darwin":
        raise UnsupportedPlatform(
            "Keychain credentials backend is only implemented on macOS. "
            "On Linux, use env vars or a user-private file for now."
        )


def save(service: str, account: str, password: str) -> CredentialRef:
    """Save (or update) a credential.

    On macOS this writes to the login Keychain via `security
    add-generic-password -U`. The `-U` flag updates an existing entry
    in place rather than erroring.

    Returns the CredentialRef so callers can log what was stored without
    re-deriving the service name.
    """
    _require_macos()
    if not account:
        raise ValueError("account is required")
    if password is None or password == "":
        raise ValueError("password is required")

    ref = CredentialRef(service=_normalize_service(service), account=account)
    proc = subprocess.run(
        [
            "security", "add-generic-password",
            "-a", ref.account,
            "-s", ref.keychain_service,
            "-w", password,
            "-U",
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise CredentialError(
            f"security add-generic-password failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return ref


def get(service: str, account: str | None = None) -> str:
    """Look up a password.

    If `account` is provided, the lookup is scoped to that account
    (useful when the same service has multiple identities). Otherwise
    Keychain returns the most recently added entry for the service.

    Raises CredentialNotFound if no matching entry exists.
    """
    _require_macos()
    short = _normalize_service(service)
    cmd = ["security", "find-generic-password", "-s", f"{SERVICE_PREFIX}{short}", "-w"]
    if account:
        cmd[2:2] = ["-a", account]  # insert "-a <account>" before "-s"

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if "could not be found" in stderr.lower() or proc.returncode == 44:
            raise CredentialNotFound(
                f"No credential for service {short!r}"
                + (f" account {account!r}" if account else "")
            )
        raise CredentialError(
            f"security find-generic-password failed: {stderr or proc.stdout.strip()}"
        )
    # `-w` outputs only the password followed by a newline.
    return proc.stdout.rstrip("\n")


def delete(service: str, account: str | None = None) -> bool:
    """Delete a credential. Returns True if removed, False if not found."""
    _require_macos()
    short = _normalize_service(service)
    cmd = ["security", "delete-generic-password", "-s", f"{SERVICE_PREFIX}{short}"]
    if account:
        cmd[2:2] = ["-a", account]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        return True
    stderr = (proc.stderr or proc.stdout).lower()
    if "could not be found" in stderr or proc.returncode == 44:
        return False
    raise CredentialError(
        f"security delete-generic-password failed: {proc.stderr.strip() or proc.stdout.strip()}"
    )


def list_all() -> list[CredentialRef]:
    """List every credential whose service starts with the `mom-` prefix.

    Parses `security dump-keychain` output. Passwords are not returned —
    only (service, account) pairs.
    """
    _require_macos()
    proc = subprocess.run(
        ["security", "dump-keychain"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise CredentialError(
            f"security dump-keychain failed: {proc.stderr.strip()}"
        )
    return _parse_dump(proc.stdout)


# `security dump-keychain` prints one record per credential, e.g.:
#     "svce"<blob>="mom-surge"
#     "acct"<blob>="user@example.com"
# Records are separated by blank lines / `keychain:` headers. We pair the
# svce+acct lines within each record.
_SVCE_RE = re.compile(r'"svce"<blob>="([^"]+)"')
_ACCT_RE = re.compile(r'"acct"<blob>="([^"]+)"')


def _parse_dump(text: str) -> list[CredentialRef]:
    refs: list[CredentialRef] = []
    seen: set[tuple[str, str]] = set()
    current_svce: str | None = None
    current_acct: str | None = None

    def flush() -> None:
        nonlocal current_svce, current_acct
        if current_svce and current_svce.startswith(SERVICE_PREFIX):
            short = current_svce[len(SERVICE_PREFIX):]
            acct = current_acct or ""
            key = (short, acct)
            if key not in seen:
                seen.add(key)
                refs.append(CredentialRef(service=short, account=acct))
        current_svce = None
        current_acct = None

    for line in text.splitlines():
        if line.startswith("keychain:"):
            # `keychain:` headers separate records in `security dump-keychain`
            # output. Within one record, svce and acct lines can appear in
            # either order, so we only flush on these boundaries.
            flush()
            continue
        m = _SVCE_RE.search(line)
        if m:
            current_svce = m.group(1)
            continue
        m = _ACCT_RE.search(line)
        if m:
            current_acct = m.group(1)
    flush()
    refs.sort(key=lambda r: (r.service, r.account))
    return refs
