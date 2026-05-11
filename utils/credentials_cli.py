#!/usr/bin/env python3
"""
Credentials CLI — save/get/delete/list credentials in the OS keyring.

The bot uses this CLI mid-conversation to stash secrets a user gives it
(API tokens, service passwords) into the macOS Keychain rather than a
plain file. Every entry is namespaced under `jarvis-<service>`.

Usage:
    credentials_cli.py save --service surge --account user@example.com --password 'xxx'
    credentials_cli.py get  --service surge
    credentials_cli.py get  --service surge --account user@example.com
    credentials_cli.py delete --service surge
    credentials_cli.py list

Passwords for `save` can also be piped on stdin via `--password -`, which
avoids leaving the secret in shell history:

    echo 'xxx' | credentials_cli.py save --service surge --account u@e.com --password -
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import credentials  # noqa: E402


def _read_password(arg: str | None) -> str:
    if arg is None:
        print("error: --password is required for save", file=sys.stderr)
        sys.exit(2)
    if arg == "-":
        data = sys.stdin.read()
        # Strip exactly one trailing newline (common from `echo`) but keep
        # internal whitespace intact.
        if data.endswith("\n"):
            data = data[:-1]
        return data
    return arg


def cmd_save(args: argparse.Namespace) -> int:
    password = _read_password(args.password)
    ref = credentials.save(args.service, args.account, password)
    print(f"saved {ref.keychain_service} ({ref.account})")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    try:
        secret = credentials.get(args.service, args.account)
    except credentials.CredentialNotFound as e:
        print(str(e), file=sys.stderr)
        return 1
    sys.stdout.write(secret)
    if sys.stdout.isatty():
        sys.stdout.write("\n")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    removed = credentials.delete(args.service, args.account)
    if removed:
        print(f"deleted {credentials.SERVICE_PREFIX}{args.service}")
        return 0
    print(f"no entry for {credentials.SERVICE_PREFIX}{args.service}", file=sys.stderr)
    return 1


def cmd_list(args: argparse.Namespace) -> int:
    refs = credentials.list_all()
    if args.json:
        payload = [{"service": r.service, "account": r.account} for r in refs]
        print(json.dumps(payload, indent=2))
        return 0
    if not refs:
        print("(no jarvis-* credentials stored)")
        return 0
    width = max(len(r.service) for r in refs)
    for r in refs:
        print(f"  {r.service:<{width}}  {r.account}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="credentials_cli.py",
        description="Manage bot credentials in the OS keyring (macOS Keychain).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("save", help="Save or update a credential")
    s.add_argument("--service", required=True, help="short service name, e.g. 'surge'")
    s.add_argument("--account", required=True, help="email or username for the service")
    s.add_argument("--password", help="password (use '-' to read from stdin)")
    s.set_defaults(func=cmd_save)

    g = sub.add_parser("get", help="Print a stored password")
    g.add_argument("--service", required=True)
    g.add_argument("--account", help="scope lookup to a specific account")
    g.set_defaults(func=cmd_get)

    d = sub.add_parser("delete", help="Remove a stored credential")
    d.add_argument("--service", required=True)
    d.add_argument("--account", help="scope deletion to a specific account")
    d.set_defaults(func=cmd_delete)

    ls = sub.add_parser("list", help="List all jarvis-* credentials (no passwords)")
    ls.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ls.set_defaults(func=cmd_list)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except credentials.UnsupportedPlatform as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except credentials.CredentialError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
