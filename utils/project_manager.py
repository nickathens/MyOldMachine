#!/usr/bin/env python3
"""
Project Manager — Create and manage project state files.

Usage:
    python project_manager.py create "Project Name" "Summary" "/path/to/project"
    python project_manager.py list
    python project_manager.py status <slug>
    python project_manager.py update <slug> --status active --next "Do this next"
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Memory lives under data/memory/ relative to bot root
BOT_DIR = Path(__file__).parent.parent
MEMORY_DIR = BOT_DIR / "data" / "memory"
PROJECTS_DIR = MEMORY_DIR / "projects"
DECISIONS_DIR = MEMORY_DIR / "decisions"
TOPICS_DIR = MEMORY_DIR / "topics"


def slugify(name: str) -> str:
    """Convert name to filesystem-safe slug."""
    slug = re.sub(r'[^\w\s-]', '', name.lower())
    slug = re.sub(r'[\s_]+', '-', slug)
    return slug.strip('-')[:50]


def ensure_dirs():
    """Create memory directory structure."""
    for d in [MEMORY_DIR, PROJECTS_DIR, DECISIONS_DIR, TOPICS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def create_project(name: str, summary: str, location: str):
    """Create a new project with state file."""
    ensure_dirs()
    slug = slugify(name)
    project_dir = PROJECTS_DIR / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "name": name,
        "slug": slug,
        "created": datetime.now().isoformat(),
        "status": "in_progress",
        "location": location,
        "summary": summary,
        "next_steps": [],
        "blockers": [],
        "decisions": [],
        "current_state": "",
    }

    state_file = project_dir / "state.json"
    state_file.write_text(json.dumps(state, indent=2) + "\n")

    # Create the actual project directory if it doesn't exist
    project_path = Path(location)
    project_path.mkdir(parents=True, exist_ok=True)

    print(f"Created project: {name}")
    print(f"  Slug: {slug}")
    print(f"  State: {state_file}")
    print(f"  Location: {location}")
    return state


def list_projects():
    """List all projects."""
    ensure_dirs()
    projects = []
    for pdir in PROJECTS_DIR.iterdir():
        if not pdir.is_dir():
            continue
        state_file = pdir / "state.json"
        if not state_file.exists():
            continue
        try:
            state = json.loads(state_file.read_text())
            projects.append(state)
        except (json.JSONDecodeError, IOError):
            continue

    if not projects:
        print("No projects found.")
        return

    for p in sorted(projects, key=lambda x: x.get("created", "")):
        status = p.get("status", "unknown")
        print(f"  [{status}] {p['name']} ({p['slug']})")
        if p.get("summary"):
            print(f"    {p['summary'][:80]}")


def get_project_status(slug: str):
    """Show detailed project status."""
    state_file = PROJECTS_DIR / slug / "state.json"
    if not state_file.exists():
        print(f"Project '{slug}' not found.")
        sys.exit(1)

    state = json.loads(state_file.read_text())
    print(f"Project: {state['name']}")
    print(f"  Status: {state.get('status', 'unknown')}")
    print(f"  Location: {state.get('location', 'unknown')}")
    print(f"  Created: {state.get('created', 'unknown')}")
    if state.get("summary"):
        print(f"  Summary: {state['summary']}")
    if state.get("next_steps"):
        print(f"  Next steps:")
        for step in state["next_steps"]:
            print(f"    - {step}")
    if state.get("blockers"):
        print(f"  Blockers:")
        for b in state["blockers"]:
            print(f"    - {b}")


def update_project(slug: str, status: str = None, next_step: str = None):
    """Update project state."""
    state_file = PROJECTS_DIR / slug / "state.json"
    if not state_file.exists():
        print(f"Project '{slug}' not found.")
        sys.exit(1)

    state = json.loads(state_file.read_text())
    if status:
        state["status"] = status
    if next_step:
        if next_step not in state.get("next_steps", []):
            state.setdefault("next_steps", []).append(next_step)

    state_file.write_text(json.dumps(state, indent=2) + "\n")
    print(f"Updated project: {state['name']}")


def main():
    parser = argparse.ArgumentParser(description="Project Manager")
    sub = parser.add_subparsers(dest="command")

    create_p = sub.add_parser("create")
    create_p.add_argument("name", type=str)
    create_p.add_argument("summary", type=str)
    create_p.add_argument("location", type=str)

    sub.add_parser("list")

    status_p = sub.add_parser("status")
    status_p.add_argument("slug", type=str)

    update_p = sub.add_parser("update")
    update_p.add_argument("slug", type=str)
    update_p.add_argument("--status", type=str)
    update_p.add_argument("--next", type=str, dest="next_step")

    args = parser.parse_args()

    if args.command == "create":
        create_project(args.name, args.summary, args.location)
    elif args.command == "list":
        list_projects()
    elif args.command == "status":
        get_project_status(args.slug)
    elif args.command == "update":
        update_project(args.slug, args.status, args.next_step)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
