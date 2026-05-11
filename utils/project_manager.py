#!/usr/bin/env python3
"""
Project Manager — Create and manage project state files.

Each project has an `owner` field:
  - "<telegram_user_id>"  → private to that user
  - "shared"              → visible to every registered user
  - missing               → treated as "shared" (legacy projects)

`create` defaults to **private** for the caller. Use `--shared` to make a
project visible to everyone from the start, or call `share <slug>` later.

Reading is permissive (admins can `_can_see` anything), but writing is
strict: `update` uses `_can_modify`, which refuses cross-user writes even
for admins unless they pass `--admin-override`.

Note: only the `projects/` tree is per-user. `decisions/` and `topics/`
under `data/memory/` are intentionally shared knowledge across all users.

Usage:
    python project_manager.py create "Name" "Summary" "/path" --user 12345
    python project_manager.py create "Name" "Summary" "/path" --user 12345 --shared
    python project_manager.py list --user 12345
    python project_manager.py list --all                  # admin only
    python project_manager.py status <slug> --user 12345
    python project_manager.py update <slug> --user 12345 --status active
    python project_manager.py update <slug> --user 999 --status active --admin-override
    python project_manager.py share <slug> --user 12345
    python project_manager.py unshare <slug> --user 12345
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BOT_DIR = Path(__file__).parent.parent
MEMORY_DIR = BOT_DIR / "data" / "memory"
PROJECTS_DIR = MEMORY_DIR / "projects"
DECISIONS_DIR = MEMORY_DIR / "decisions"
TOPICS_DIR = MEMORY_DIR / "topics"

SHARED_OWNER = "shared"

# Import atomic JSON writer. Prefer safe_json's save_json; fall back to an
# inline atomic implementation if the module isn't importable (e.g. when
# project_manager.py is invoked outside the bot's package layout).
try:
    from utils.safe_json import save_json as _atomic_save_json  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from safe_json import save_json as _atomic_save_json  # type: ignore
    except ImportError:
        def _atomic_save_json(path: Path, data, indent=2):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as _f:
                json.dump(data, _f, indent=indent, ensure_ascii=False)
                _f.flush()
                os.fsync(_f.fileno())
            tmp.rename(path)


def _is_admin(user_id) -> bool:
    """Best-effort admin check. False if core.config isn't importable."""
    if user_id is None:
        return False
    try:
        sys.path.insert(0, str(BOT_DIR))
        from core.config import is_admin  # type: ignore
        return is_admin(int(user_id))
    except Exception as exc:
        logger.warning(
            "project_manager._is_admin failed for user_id=%r: %s",
            user_id, exc,
        )
        return False


def _normalize_owner(value) -> str:
    """Return canonical owner string ('shared' or '<user_id>')."""
    if value is None or value == "":
        return SHARED_OWNER
    if value == SHARED_OWNER:
        return SHARED_OWNER
    return str(value)


def _can_see(state: dict, user_id) -> bool:
    """True if `user_id` may read this project."""
    owner = _normalize_owner(state.get("owner"))
    if owner == SHARED_OWNER:
        return True
    if user_id is None:
        # Caller didn't identify themselves: only see shared/legacy. Admins
        # who really want to inspect another user's project must pass --user
        # or use --all on `list`.
        return False
    if owner == str(user_id):
        return True
    return _is_admin(user_id)


def _can_modify(state: dict, user_id, admin_override: bool = False) -> bool:
    """True if `user_id` may write to this project.

    Stricter than `_can_see`: admins do NOT silently inherit write access
    to another user's private project. They must pass `--admin-override`.
    Shared / legacy projects remain writable by any identified user, since
    that's the explicit promise of the shared bucket.
    """
    owner = _normalize_owner(state.get("owner"))
    if owner == SHARED_OWNER:
        return user_id is not None
    if user_id is None:
        return False
    if owner == str(user_id):
        return True
    return bool(admin_override) and _is_admin(user_id)


def slugify(name: str) -> str:
    """Convert name to filesystem-safe slug."""
    slug = re.sub(r'[^\w\s-]', '', name.lower())
    slug = re.sub(r'[\s_]+', '-', slug)
    return slug.strip('-')[:50].rstrip('-')


def ensure_dirs():
    """Create memory directory structure."""
    for d in [MEMORY_DIR, PROJECTS_DIR, DECISIONS_DIR, TOPICS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _load_state(slug: str):
    state_file = PROJECTS_DIR / slug / "state.json"
    if not state_file.exists():
        return None, state_file
    try:
        return json.loads(state_file.read_text(encoding="utf-8")), state_file
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, state_file


def create_project(name: str, summary: str, location: str,
                   user_id=None, shared: bool = False):
    """Create a new project. Private to `user_id` unless `shared=True`."""
    ensure_dirs()
    slug = slugify(name)
    project_dir = PROJECTS_DIR / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    if shared or user_id is None:
        owner = SHARED_OWNER
    else:
        owner = str(user_id)

    state = {
        "name": name,
        "slug": slug,
        "created": datetime.now().isoformat(),
        "status": "in_progress",
        "location": location,
        "summary": summary,
        "owner": owner,
        "next_steps": [],
        "blockers": [],
        "decisions": [],
        "current_state": "",
    }

    state_file = project_dir / "state.json"
    _atomic_save_json(state_file, state, indent=2)

    project_path = Path(location)
    project_path.mkdir(parents=True, exist_ok=True)

    print(f"Created project: {name}")
    print(f"  Slug: {slug}")
    print(f"  Owner: {owner}")
    print(f"  State: {state_file}")
    print(f"  Location: {location}")
    return state


def list_projects(user_id=None, show_all: bool = False):
    """List projects visible to `user_id`. Admins may pass --all."""
    ensure_dirs()
    if show_all and not _is_admin(user_id):
        print("Refused: --all requires admin.", file=sys.stderr)
        sys.exit(2)

    projects = []
    for pdir in PROJECTS_DIR.iterdir():
        if not pdir.is_dir():
            continue
        state, _ = _load_state(pdir.name)
        if state is None:
            continue
        if show_all or _can_see(state, user_id):
            projects.append(state)

    if not projects:
        print("No projects found.")
        return

    for p in sorted(projects, key=lambda x: x.get("created", "")):
        status = p.get("status", "unknown")
        owner = _normalize_owner(p.get("owner"))
        tag = "shared" if owner == SHARED_OWNER else f"private:{owner}"
        print(f"  [{status}] [{tag}] {p['name']} ({p['slug']})")
        if p.get("summary"):
            print(f"    {p['summary'][:80]}")


def get_project_status(slug: str, user_id=None):
    """Show detailed project status (refuses if not visible to caller)."""
    state, state_file = _load_state(slug)
    if state is None:
        print(f"Project '{slug}' not found.", file=sys.stderr)
        sys.exit(1)
    if not _can_see(state, user_id):
        print(
            f"Refused: project '{slug}' is private to another user.",
            file=sys.stderr,
        )
        sys.exit(2)

    owner = _normalize_owner(state.get("owner"))
    print(f"Project: {state['name']}")
    print(f"  Owner: {owner}")
    print(f"  Status: {state.get('status', 'unknown')}")
    print(f"  Location: {state.get('location', 'unknown')}")
    print(f"  Created: {state.get('created', 'unknown')}")
    if state.get("summary"):
        print(f"  Summary: {state['summary']}")
    if state.get("next_steps"):
        print("  Next steps:")
        for step in state["next_steps"]:
            print(f"    - {step}")
    if state.get("blockers"):
        print("  Blockers:")
        for b in state["blockers"]:
            print(f"    - {b}")


def update_project(slug: str, user_id=None,
                   status: str = None, next_step: str = None,
                   admin_override: bool = False):
    """Update project state.

    Refuses if the caller is not the owner. Admins must pass
    `admin_override=True` (CLI: `--admin-override`) to write into another
    user's private project — silent admin writes are not allowed.
    """
    state, state_file = _load_state(slug)
    if state is None:
        print(f"Project '{slug}' not found.", file=sys.stderr)
        sys.exit(1)
    if not _can_modify(state, user_id, admin_override=admin_override):
        owner = _normalize_owner(state.get("owner"))
        if owner != SHARED_OWNER and _is_admin(user_id):
            print(
                f"Refused: '{slug}' belongs to another user. "
                f"Pass --admin-override to write to it as admin.",
                file=sys.stderr,
            )
        else:
            print(
                f"Refused: project '{slug}' is private to another user.",
                file=sys.stderr,
            )
        sys.exit(2)

    if status:
        state["status"] = status
    if next_step:
        if next_step not in state.get("next_steps", []):
            state.setdefault("next_steps", []).append(next_step)

    _atomic_save_json(state_file, state, indent=2)
    print(f"Updated project: {state['name']}")


def share_project(slug: str, user_id=None):
    """Move a project from private to shared."""
    state, state_file = _load_state(slug)
    if state is None:
        print(f"Project '{slug}' not found.", file=sys.stderr)
        sys.exit(1)

    owner = _normalize_owner(state.get("owner"))
    if owner == SHARED_OWNER:
        print(f"Project '{slug}' is already shared.")
        return

    # Only the current owner (or an admin) may share.
    if user_id is None or (
        str(user_id) != owner and not _is_admin(user_id)
    ):
        print(
            f"Refused: only the owner of '{slug}' can share it.",
            file=sys.stderr,
        )
        sys.exit(2)

    state["owner"] = SHARED_OWNER
    _atomic_save_json(state_file, state, indent=2)
    print(f"Shared project: {state['name']} (was private to {owner})")


def unshare_project(slug: str, user_id=None, claim_for=None):
    """Move a shared project back to private. Caller becomes the owner
    unless an admin specifies --for to assign to someone else."""
    state, state_file = _load_state(slug)
    if state is None:
        print(f"Project '{slug}' not found.", file=sys.stderr)
        sys.exit(1)

    owner = _normalize_owner(state.get("owner"))
    if owner != SHARED_OWNER:
        if str(user_id) == owner:
            print(f"Project '{slug}' is already private to you.")
            return
        print(
            f"Refused: '{slug}' is already private to another user.",
            file=sys.stderr,
        )
        sys.exit(2)

    if user_id is None:
        print(
            "Refused: unshare needs --user to know who should own the project.",
            file=sys.stderr,
        )
        sys.exit(2)

    if claim_for is not None and str(claim_for) != str(user_id):
        if not _is_admin(user_id):
            print(
                "Refused: only admins may assign a project to another user.",
                file=sys.stderr,
            )
            sys.exit(2)
        new_owner = str(claim_for)
    else:
        new_owner = str(user_id)

    state["owner"] = new_owner
    _atomic_save_json(state_file, state, indent=2)
    print(f"Unshared project: {state['name']} (now private to {new_owner})")


def main():
    parser = argparse.ArgumentParser(description="Project Manager")
    sub = parser.add_subparsers(dest="command")

    create_p = sub.add_parser("create")
    create_p.add_argument("name", type=str)
    create_p.add_argument("summary", type=str)
    create_p.add_argument("location", type=str)
    create_p.add_argument("--user", type=int, default=None,
                          help="Telegram ID of the owner (default: private to this user)")
    create_p.add_argument("--shared", action="store_true",
                          help="Create as shared (visible to every registered user)")

    list_p = sub.add_parser("list")
    list_p.add_argument("--user", type=int, default=None,
                        help="Telegram ID; only projects this user owns or shared ones are shown")
    list_p.add_argument("--all", action="store_true", dest="show_all",
                        help="Admin only: list every project")

    status_p = sub.add_parser("status")
    status_p.add_argument("slug", type=str)
    status_p.add_argument("--user", type=int, default=None)

    update_p = sub.add_parser("update")
    update_p.add_argument("slug", type=str)
    update_p.add_argument("--user", type=int, default=None)
    update_p.add_argument("--status", type=str)
    update_p.add_argument("--next", type=str, dest="next_step")
    update_p.add_argument("--admin-override", action="store_true",
                          dest="admin_override",
                          help="Admin only: write into another user's private project")

    share_p = sub.add_parser("share", help="Move a private project to shared")
    share_p.add_argument("slug", type=str)
    share_p.add_argument("--user", type=int, default=None)

    unshare_p = sub.add_parser("unshare", help="Move a shared project to private")
    unshare_p.add_argument("slug", type=str)
    unshare_p.add_argument("--user", type=int, default=None)
    unshare_p.add_argument("--for", type=int, default=None, dest="claim_for",
                           help="Admin only: assign the project to a different user")

    args = parser.parse_args()

    # Enforce session binding: a bot session bound to user X cannot pass
    # --user Y unless X is admin. Mirrors scheduler_cli / observe.
    try:
        from utils.session_guard import enforce as _enforce_session_user
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from session_guard import enforce as _enforce_session_user  # type: ignore
    if getattr(args, "user", None) is not None:
        _enforce_session_user(args.user)

    if args.command == "create":
        create_project(args.name, args.summary, args.location,
                       user_id=args.user, shared=args.shared)
    elif args.command == "list":
        list_projects(user_id=args.user, show_all=args.show_all)
    elif args.command == "status":
        get_project_status(args.slug, user_id=args.user)
    elif args.command == "update":
        update_project(args.slug, user_id=args.user,
                       status=args.status, next_step=args.next_step,
                       admin_override=args.admin_override)
    elif args.command == "share":
        share_project(args.slug, user_id=args.user)
    elif args.command == "unshare":
        unshare_project(args.slug, user_id=args.user, claim_for=args.claim_for)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
