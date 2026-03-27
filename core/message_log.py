"""
Append-only message log with FTS5 full-text search.

Stores every user message and bot response in a per-user SQLite database
that is never touched by conversation compaction. Provides complete,
searchable message history with BM25 ranking and snippet extraction.

The FTS5 index stores cleaned content (attachment paths stripped) so
searches don't match on file paths. The original content in the messages
table is never modified.
"""

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Per-user database directory — matches config.USERS_DIR
_USERS_DIR = Path(__file__).parent.parent / "data" / "users"

# Regex patterns for stripping attachment blocks from content before indexing
_ATTACHMENT_BLOCK_RE = re.compile(
    r'\[Attachments:\](?:\n- \w+: /[^\n]+)*'
)
_SENT_FILES_RE = re.compile(r'^\[Sent \d+ file\(s\)\]\s*$', re.MULTILINE)


def _strip_attachment_paths(content: str) -> str | None:
    """Strip attachment blocks and file-send markers from message content.

    Returns cleaned text, or None if nothing meaningful remains.
    """
    text = _ATTACHMENT_BLOCK_RE.sub('', content)
    text = _SENT_FILES_RE.sub('', text)
    text = text.strip()
    return text if text else None


def _get_db_path(user_id: int) -> Path:
    """Return the path to a user's message log database."""
    user_dir = _USERS_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "message_log.db"


def _get_conn(user_id: int) -> sqlite3.Connection:
    """Open a connection and ensure tables exist (messages + FTS5)."""
    db_path = _get_db_path(user_id)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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
    # FTS5 full-text search index (stores cleaned content internally)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            tokenize='porter unicode61',
            prefix='2 3'
        )
    """)
    conn.commit()
    return conn


def _index_message(conn: sqlite3.Connection, msg_id: int, content: str):
    """Strip attachment paths and index cleaned content into FTS5.

    Failures are silently ignored so FTS5 issues never block message logging.
    """
    try:
        cleaned = _strip_attachment_paths(content)
        if cleaned:
            conn.execute(
                "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                (msg_id, cleaned)
            )
    except Exception:
        pass


def _escape_fts_query(query: str) -> str:
    """Escape a user query for safe FTS5 MATCH.

    Wraps each word in double quotes to treat as a literal term.
    Words are joined with implicit AND.
    """
    clean = query.replace('"', '')
    words = clean.split()
    if not words:
        return '""'
    return ' '.join(f'"{w}"' for w in words)


def log_message(user_id: int, role: str, content: str,
                message_id: int = None, topic: str = None):
    """Append a message to the user's log and FTS5 index."""
    conn = _get_conn(user_id)
    try:
        cursor = conn.execute(
            "INSERT INTO messages (timestamp, role, content, message_id, topic) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), role, content, message_id, topic)
        )
        _index_message(conn, cursor.lastrowid, content)
        conn.commit()
    finally:
        conn.close()


def log_exchange(user_id: int, user_message: str, assistant_response: str,
                 message_id: int = None, topic: str = None):
    """Log a complete user->assistant exchange in one transaction."""
    conn = _get_conn(user_id)
    try:
        now = datetime.now().isoformat()
        cursor = conn.execute(
            "INSERT INTO messages (timestamp, role, content, message_id, topic) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, "user", user_message, message_id, topic)
        )
        _index_message(conn, cursor.lastrowid, user_message)

        cursor = conn.execute(
            "INSERT INTO messages (timestamp, role, content, message_id, topic) "
            "VALUES (?, ?, ?, ?, ?)",
            (now, "assistant", assistant_response, None, topic)
        )
        _index_message(conn, cursor.lastrowid, assistant_response)

        conn.commit()
    finally:
        conn.close()


def search_messages(user_id: int, query: str, limit: int = 50) -> list[dict]:
    """Search message history using FTS5 full-text search with BM25 ranking.

    Returns list of dicts: {id, timestamp, role, content, topic, snippet, rank}
    Falls back to LIKE search if FTS5 query fails.
    """
    conn = _get_conn(user_id)
    try:
        # Try FTS5 search (raw query first, then escaped)
        for fts_query in (query, _escape_fts_query(query)):
            try:
                rows = conn.execute(
                    "SELECT m.id, m.timestamp, m.role, m.content, m.topic, "
                    "snippet(messages_fts, 0, '>>>', '<<<', '...', 20) as snippet, "
                    "bm25(messages_fts) as rank "
                    "FROM messages_fts "
                    "JOIN messages m ON m.id = messages_fts.rowid "
                    "WHERE messages_fts MATCH ? "
                    "ORDER BY bm25(messages_fts) "
                    "LIMIT ?",
                    (fts_query, limit)
                ).fetchall()
                return [
                    {"id": r[0], "timestamp": r[1], "role": r[2],
                     "content": r[3], "topic": r[4],
                     "snippet": r[5], "rank": r[6]}
                    for r in rows
                ]
            except sqlite3.OperationalError:
                continue

        # Fallback: LIKE search (no FTS5 features)
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = conn.execute(
            "SELECT id, timestamp, role, content, topic FROM messages "
            "WHERE content LIKE ? ESCAPE '\\' ORDER BY timestamp DESC LIMIT ?",
            (f"%{escaped}%", limit)
        ).fetchall()
        return [
            {"id": r[0], "timestamp": r[1], "role": r[2],
             "content": r[3], "topic": r[4],
             "snippet": None, "rank": None}
            for r in rows
        ]
    finally:
        conn.close()


def search_context(user_id: int, query: str, limit: int = 10,
                   window: int = 3) -> list[dict]:
    """Search and return conversation fragments around each match.

    For each FTS5 hit, fetches surrounding messages (+-window) to provide
    conversation context. Overlapping windows are merged.

    Returns list of fragment dicts:
        {match_snippet, match_rank, messages: [{id, timestamp, role, content, topic, is_match}]}
    """
    matches = search_messages(user_id, query, limit)
    if not matches:
        return []

    conn = _get_conn(user_id)
    try:
        fragments = []
        seen_ids = set()

        for match in matches:
            match_id = match['id']
            if match_id in seen_ids:
                continue

            rows = conn.execute(
                "SELECT id, timestamp, role, content, topic FROM messages "
                "WHERE id BETWEEN ? AND ? ORDER BY id",
                (match_id - window, match_id + window)
            ).fetchall()

            fragment_messages = []
            for r in rows:
                if r[0] not in seen_ids:
                    seen_ids.add(r[0])
                    fragment_messages.append({
                        'id': r[0], 'timestamp': r[1], 'role': r[2],
                        'content': r[3], 'topic': r[4],
                        'is_match': r[0] == match_id
                    })

            if fragment_messages:
                fragments.append({
                    'match_snippet': match.get('snippet'),
                    'match_rank': match.get('rank'),
                    'messages': fragment_messages
                })

        return fragments
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


def prune_old_messages(user_id: int, keep_days: int = 90) -> int:
    """Delete messages older than keep_days and VACUUM. Returns count deleted."""
    conn = _get_conn(user_id)
    try:
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()

        # Delete from FTS5 index first (matching rowids)
        conn.execute(
            "DELETE FROM messages_fts WHERE rowid IN "
            "(SELECT id FROM messages WHERE timestamp < ?)",
            (cutoff,)
        )

        cursor = conn.execute(
            "DELETE FROM messages WHERE timestamp < ?", (cutoff,)
        )
        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            conn.execute("VACUUM")
        return deleted
    finally:
        conn.close()


def rebuild_fts(user_id: int) -> dict:
    """Rebuild the FTS5 index from all existing messages.

    Clears the FTS5 table and re-indexes every message with cleaned content.
    Returns stats: {total, indexed, skipped}
    """
    conn = _get_conn(user_id)
    try:
        conn.execute("DELETE FROM messages_fts")

        rows = conn.execute(
            "SELECT id, content FROM messages ORDER BY id"
        ).fetchall()

        indexed = 0
        skipped = 0
        for msg_id, content in rows:
            cleaned = _strip_attachment_paths(content)
            if cleaned:
                conn.execute(
                    "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                    (msg_id, cleaned)
                )
                indexed += 1
            else:
                skipped += 1

        conn.commit()

        # Optimize the FTS5 index
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('optimize')")
        conn.commit()

        return {"total": len(rows), "indexed": indexed, "skipped": skipped}
    finally:
        conn.close()
