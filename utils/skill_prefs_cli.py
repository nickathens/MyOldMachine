#!/usr/bin/env python3
"""Manage per-user skill preferences from the command line.

Used by CLI providers (Claude CLI, Codex CLI) which don't have
function-calling and need to edit skill preferences via the Bash tool.

API providers go through the function-calling tools in core/tools.py
instead of this CLI.

Examples:
  skill_prefs_cli.py --user-dir /path/to/data/users/userN list
  skill_prefs_cli.py --user-dir ... override vpn off
  skill_prefs_cli.py --user-dir ... override vpn on
  skill_prefs_cli.py --user-dir ... note image-gen "no cartoons"
  skill_prefs_cli.py --user-dir ... clear-note image-gen
  skill_prefs_cli.py --user-dir ... fork presentations
  skill_prefs_cli.py --user-dir ... unfork presentations
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import skill_overlay  # noqa: E402

DEFAULT_SHARED = ROOT / "skills"


def cmd_list(args) -> int:
    user_dir = Path(args.user_dir).resolve()
    settings = skill_overlay.load_settings(user_dir)
    print(json.dumps(settings, indent=2, sort_keys=True, ensure_ascii=False))
    overlay_dir = user_dir / "skills"
    if overlay_dir.is_dir():
        forks = sorted(
            p.name for p in overlay_dir.iterdir()
            if p.is_dir() and (p / "SKILL.md").exists()
        )
        print(f"\nForked skills: {forks if forks else '(none)'}")
    return 0


def cmd_override(args) -> int:
    ok, msg = skill_overlay.set_override(
        Path(args.shared).resolve(),
        Path(args.user_dir).resolve(),
        args.skill,
        args.state,
    )
    print(msg)
    return 0 if ok else 1


def cmd_note(args) -> int:
    ok, msg = skill_overlay.set_note(
        Path(args.shared).resolve(),
        Path(args.user_dir).resolve(),
        args.skill,
        args.text,
    )
    print(msg)
    return 0 if ok else 1


def cmd_clear_note(args) -> int:
    ok, msg = skill_overlay.clear_note(
        Path(args.user_dir).resolve(),
        args.skill,
    )
    print(msg)
    return 0 if ok else 1


def cmd_fork(args) -> int:
    ok, msg = skill_overlay.fork_skill(
        Path(args.shared).resolve(),
        Path(args.user_dir).resolve(),
        args.skill,
    )
    print(msg)
    return 0 if ok else 1


def cmd_unfork(args) -> int:
    ok, msg = skill_overlay.unfork_skill(
        Path(args.user_dir).resolve(),
        args.skill,
    )
    print(msg)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage per-user skill preferences.")
    parser.add_argument("--user-dir", required=True,
                        help="User's data dir (e.g. data/users/user1)")
    parser.add_argument("--shared", default=str(DEFAULT_SHARED),
                        help="Shared skills dir (default: ./skills)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Print current settings + forked skills.").set_defaults(func=cmd_list)

    p_override = sub.add_parser("override", help="Set visibility override.")
    p_override.add_argument("skill")
    p_override.add_argument("state", choices=["on", "off", "name-only"])
    p_override.set_defaults(func=cmd_override)

    p_note = sub.add_parser("note", help="Pin a short note on a skill.")
    p_note.add_argument("skill")
    p_note.add_argument("text")
    p_note.set_defaults(func=cmd_note)

    p_clear = sub.add_parser("clear-note", help="Remove a pinned note.")
    p_clear.add_argument("skill")
    p_clear.set_defaults(func=cmd_clear_note)

    p_fork = sub.add_parser("fork", help="Copy shared skill into user overlay.")
    p_fork.add_argument("skill")
    p_fork.set_defaults(func=cmd_fork)

    p_unfork = sub.add_parser("unfork", help="Delete user overlay copy.")
    p_unfork.add_argument("skill")
    p_unfork.set_defaults(func=cmd_unfork)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
