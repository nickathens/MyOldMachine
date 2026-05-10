#!/usr/bin/env python3
"""Enforce per-session user binding for helper CLIs.

When the bot launches the Claude CLI for a given Telegram user, it sets
JARVIS_USER_ID in the environment. Helper scripts (send_to_telegram,
scheduler_cli, observe, project_manager) accept --user on the CLI for
ergonomics, but in soft multi-user installs we must refuse any --user that
disagrees with the session's bound user. Otherwise one user's bot session
could send Telegram messages, schedule reminders, or write observations
into another user's data dir.

If JARVIS_USER_ID is unset (e.g. an admin invoking the script directly from
a terminal), no binding is enforced.
"""

import os
import sys


def enforce(requested_user_id: int) -> None:
    bound = os.environ.get("JARVIS_USER_ID", "").strip()
    if not bound:
        return
    try:
        bound_id = int(bound)
    except ValueError:
        return
    if int(requested_user_id) == bound_id:
        return
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from core.config import is_admin
        if is_admin(bound_id):
            print(
                f"session_guard: admin user {bound_id} acting on user {requested_user_id}",
                file=sys.stderr,
            )
            return
    except Exception:
        pass
    print(
        f"Refused: this bot session is bound to user {bound_id}; "
        f"--user {requested_user_id} is not allowed.",
        file=sys.stderr,
    )
    sys.exit(2)
