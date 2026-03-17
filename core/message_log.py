"""
Append-only message log.

Stores every user message and bot response in a per-user SQLite database
that is never touched by conversation compaction. Provides complete,
searchable message history.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

# Per-user database directory — matches config.USERS_DIR
_USERS_DIR = Path(__file__).parent.parent / "data" / "users"


def _get_db_path(user_id: int) -> Path:
    """Return the path to a user's message log database."""
    user_dir = _USERS_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "message_log.db"


def _get_conn(user_id: int) -> sqlite3.Connection:
    """Open a connection and ensure the table exists."""
    db_path = _get_db_path(user_id)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            message_id INTEGER,
            topic TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_timestamp
        ON messages(timestamp)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_role
        ON messages(role)
    """)
    conn.commit()
    return conn


def log_message(user_id: int, role: str, content: str,
                message_id: int = None, topic: str = None):
    """Append a message to the user's log. Never deletes or overwrites."""
    conn = _get_conn(user_id)
    try:
        conn.execute(
            "INSERT INTO messages (timestamp, role, content, message_id, topic) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), role, content, message_id, topic)
        )
        conn.commit()
    finally:
        conn.close()


def log_exchange(user_id: int, user_message: str, assistant_response: str,
                 message_id: int = None, topic: str = None):
    """Log a complete user->assistant exchange in one transaction."""
    conn = _get_conn(user_id)
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO messages (timestamp, role, content, message_id, topic) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, "user", user_message, message_id, topic)
        )
        conn.execute(
            "INSERT INTO messages (timestamp, role, content, message_id, topic) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, "assistant", assistant_response, None, topic)
        )
        conn.commit()
    finally:
        conn.close()


def search_messages(user_id: int, query: str, limit: int = 50) -> list[dict]:
    """Search message history by content substring."""
    conn = _get_conn(user_id)
    try:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = conn.execute(
            "SELECT id, timestamp, role, content, topic FROM messages "
            "WHERE content LIKE ? ESCAPE '\\' ORDER BY timestamp DESC LIMIT ?",
            (f"%{escaped}%", limit)
        ).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "role": r[2],
             "content": r[3], "topic": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


def get_recent_messages(user_id: int, limit: int = 100) -> list[dict]:
    """Get the most recent messages."""
    conn = _get_conn(user_id)
    try:
        rows = conn.execute(
            "SELECT id, timestamp, role, content, topic FROM messages "
            "ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "role": r[2],
             "content": r[3], "topic": r[4]}
            for r in reversed(rows)
        ]
    finally:
        conn.close()


def get_message_count(user_id: int) -> int:
    """Get total number of logged messages for a user."""
    conn = _get_conn(user_id)
    try:
        row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return row[0]
    finally:
        conn.close()
