#!/usr/bin/env python3
"""
Premortem analysis - structured failure-mode evaluation framework.

Sibling to dialectic.py. Where dialectic answers "should I do X" (FOR/AGAINST a
proposition), premortem answers "assuming I am doing X, how does it die". It
stands at a failed future and works backwards: imagine the plan has already
failed, enumerate the failure modes, name the assumption that enabled each one,
and record the early warning signs you could actually observe.

Stores analyses in SQLite with FTS5 full-text search (shared research.db).
Exports to ~/research/<topic>/ for long-term reference.

Usage:
    # Store premortem (JSON via stdin or --input file)
    echo '{"plan": "...", ...}' | python premortem.py store
    python premortem.py store --input premortem.json

    # Search past premortems
    python premortem.py search "launch pricing"

    # List recent premortems
    python premortem.py list [--limit 10] [--tag launch]

    # View full premortem
    python premortem.py view <id>

    # Export to research directory (topic subfolder)
    python premortem.py export <id> [--topic launch] [--dir /path/to/research]

    # Delete a premortem
    python premortem.py delete <id>

    # Rebuild FTS5 index
    python premortem.py rebuild
"""

import json
import os
import re
import sqlite3
import sys
import argparse
from datetime import datetime
from pathlib import Path

DATA_DIR = Path.home() / '.local' / 'share' / 'research'
DB_FILE = DATA_DIR / 'research.db'

# Default export directory. Override via --dir flag or RESEARCH_EXPORT_DIR env var
_export_dir = os.environ.get('RESEARCH_EXPORT_DIR', '')
RESEARCH_DIR = Path(_export_dir) if _export_dir else Path.home() / 'research'

VALID_VERDICTS = ('PROCEED', 'REVISE', 'RETHINK')

# FTS5 columns that map to premortems table columns (order matters for snippet index)
_FTS_COLS = ('plan', 'context', 'failure_modes', 'most_likely',
             'most_dangerous', 'hidden_assumption', 'revised_plan',
             'checklist', 'tags')


def _init_tables() -> sqlite3.Connection:
    """Create premortems tables and FTS5 index if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS premortems (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan TEXT NOT NULL,
        context TEXT DEFAULT '',
        failure_modes TEXT NOT NULL,
        most_likely TEXT NOT NULL,
        most_dangerous TEXT NOT NULL,
        hidden_assumption TEXT DEFAULT '',
        revised_plan TEXT NOT NULL,
        checklist TEXT DEFAULT '',
        verdict TEXT NOT NULL,
        confidence INTEGER NOT NULL,
        tags TEXT DEFAULT '',
        created TEXT NOT NULL
    )''')

    # FTS5 external content table - reads from premortems via content_rowid
    c.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS premortems_fts USING fts5(
        plan,
        context,
        failure_modes,
        most_likely,
        most_dangerous,
        hidden_assumption,
        revised_plan,
        checklist,
        tags,
        content='premortems',
        content_rowid='id',
        tokenize='porter unicode61',
        prefix='2,3'
    )''')

    # Sync triggers: keep FTS5 index updated on INSERT/DELETE/UPDATE
    cols_new = ', '.join(f'new.{col}' for col in _FTS_COLS)
    cols_old = ', '.join(f'old.{col}' for col in _FTS_COLS)
    cols_list = ', '.join(_FTS_COLS)

    c.execute(f'''CREATE TRIGGER IF NOT EXISTS premortems_ai
        AFTER INSERT ON premortems BEGIN
            INSERT INTO premortems_fts(rowid, {cols_list})
            VALUES (new.id, {cols_new});
        END''')

    c.execute(f'''CREATE TRIGGER IF NOT EXISTS premortems_ad
        AFTER DELETE ON premortems BEGIN
            INSERT INTO premortems_fts(premortems_fts, rowid, {cols_list})
            VALUES ('delete', old.id, {cols_old});
        END''')

    c.execute(f'''CREATE TRIGGER IF NOT EXISTS premortems_au
        AFTER UPDATE ON premortems BEGIN
            INSERT INTO premortems_fts(premortems_fts, rowid, {cols_list})
            VALUES ('delete', old.id, {cols_old});
            INSERT INTO premortems_fts(rowid, {cols_list})
            VALUES (new.id, {cols_new});
        END''')

    conn.commit()
    return conn


def _validate(data: dict) -> dict:
    """Validate and normalize input data. Returns cleaned data or exits on error."""
    required = ['plan', 'failure_modes', 'most_likely', 'most_dangerous',
                'revised_plan', 'verdict', 'confidence']
    missing = [k for k in required if k not in data or not str(data[k]).strip()]
    if missing:
        print(f"Error: missing required fields: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    verdict = str(data['verdict']).upper()
    if verdict not in VALID_VERDICTS:
        print(f"Error: verdict must be one of {VALID_VERDICTS} "
              f"(got: {data['verdict']})", file=sys.stderr)
        sys.exit(1)

    try:
        conf = int(data['confidence'])
    except (ValueError, TypeError):
        print(f"Error: confidence must be an integer 0-100 (got: {data['confidence']})",
              file=sys.stderr)
        sys.exit(1)
    if not 0 <= conf <= 100:
        print(f"Error: confidence must be 0-100 (got: {conf})", file=sys.stderr)
        sys.exit(1)

    # Normalize tags: accept list or comma-separated string
    tags = data.get('tags', '')
    if isinstance(tags, list):
        tags = ','.join(str(t).strip() for t in tags if str(t).strip())

    return {
        'plan': str(data['plan']).strip(),
        'context': str(data.get('context', '')).strip(),
        'failure_modes': str(data['failure_modes']).strip(),
        'most_likely': str(data['most_likely']).strip(),
        'most_dangerous': str(data['most_dangerous']).strip(),
        'hidden_assumption': str(data.get('hidden_assumption', '')).strip(),
        'revised_plan': str(data['revised_plan']).strip(),
        'checklist': str(data.get('checklist', '')).strip(),
        'verdict': verdict,
        'confidence': conf,
        'tags': tags,
    }


def store(data: dict) -> int:
    """Store a premortem analysis. Returns the row ID."""
    clean = _validate(data)

    conn = _init_tables()
    c = conn.cursor()
    c.execute('''INSERT INTO premortems
        (plan, context, failure_modes, most_likely, most_dangerous,
         hidden_assumption, revised_plan, checklist, verdict, confidence,
         tags, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (clean['plan'], clean['context'], clean['failure_modes'],
         clean['most_likely'], clean['most_dangerous'], clean['hidden_assumption'],
         clean['revised_plan'], clean['checklist'], clean['verdict'],
         clean['confidence'], clean['tags'], datetime.now().isoformat()))
    conn.commit()
    row_id = c.lastrowid
    conn.close()

    print(f"Stored premortem #{row_id}: {clean['plan']}")
    return row_id


def search(query: str, limit: int = 10) -> list[dict]:
    """Search premortems using FTS5 with BM25 ranking and snippet extraction."""
    conn = _init_tables()
    c = conn.cursor()

    results = []
    # snippet -1 = best matching column across all FTS5 fields
    sql = '''SELECT p.id, p.plan, p.verdict, p.confidence,
                    p.tags, p.created,
                    snippet(premortems_fts, -1, '>>>', '<<<', '...', 40) as snippet
             FROM premortems_fts
             JOIN premortems p ON p.id = premortems_fts.rowid
             WHERE premortems_fts MATCH ?
             ORDER BY bm25(premortems_fts)
             LIMIT ?'''

    try:
        try:
            c.execute(sql, (query, limit))
            for row in c.fetchall():
                results.append({
                    'id': row[0], 'plan': row[1], 'verdict': row[2],
                    'confidence': row[3], 'tags': row[4], 'created': row[5],
                    'snippet': row[6],
                })
        except sqlite3.OperationalError:
            # Malformed FTS5 query - try quoting as phrase
            escaped = '"' + query.replace('"', '""') + '"'
            try:
                c.execute(sql, (escaped, limit))
                for row in c.fetchall():
                    results.append({
                        'id': row[0], 'plan': row[1], 'verdict': row[2],
                        'confidence': row[3], 'tags': row[4], 'created': row[5],
                        'snippet': row[6],
                    })
            except sqlite3.OperationalError:
                # Last resort: LIKE on plan + failure_modes
                c.execute('''SELECT id, plan, verdict, confidence, tags, created
                             FROM premortems
                             WHERE plan LIKE ? OR failure_modes LIKE ?
                             ORDER BY created DESC LIMIT ?''',
                          (f'%{query}%', f'%{query}%', limit))
                for row in c.fetchall():
                    results.append({
                        'id': row[0], 'plan': row[1], 'verdict': row[2],
                        'confidence': row[3], 'tags': row[4], 'created': row[5],
                        'snippet': '',
                    })
    finally:
        conn.close()

    return results


def list_premortems(limit: int = 10, tag: str = None) -> list[dict]:
    """List recent premortems, optionally filtered by tag."""
    conn = _init_tables()
    c = conn.cursor()

    if tag:
        # Tags are comma-separated, so match substring
        c.execute('''SELECT id, plan, verdict, confidence, tags, created
                     FROM premortems WHERE tags LIKE ?
                     ORDER BY created DESC LIMIT ?''',
                  (f'%{tag}%', limit))
    else:
        c.execute('''SELECT id, plan, verdict, confidence, tags, created
                     FROM premortems ORDER BY created DESC LIMIT ?''', (limit,))

    results = []
    for row in c.fetchall():
        results.append({
            'id': row[0], 'plan': row[1], 'verdict': row[2],
            'confidence': row[3], 'tags': row[4], 'created': row[5],
        })

    conn.close()
    return results


def view(premortem_id: int) -> dict | None:
    """View a full premortem by ID."""
    conn = _init_tables()
    c = conn.cursor()
    cols = ('id', 'plan', 'context', 'failure_modes', 'most_likely',
            'most_dangerous', 'hidden_assumption', 'revised_plan', 'checklist',
            'verdict', 'confidence', 'tags', 'created')
    c.execute(f'SELECT {", ".join(cols)} FROM premortems WHERE id = ?', (premortem_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return None

    return dict(zip(cols, row))


def format_premortem(p: dict) -> str:
    """Format a premortem as readable markdown."""
    lines = [
        f"# Premortem #{p['id']}",
        f"## {p['plan']}",
        "",
    ]
    if p.get('context'):
        lines.append(f"*Context: {p['context']}*")
        lines.append("")

    lines.append("### Failure Modes")
    lines.append(p['failure_modes'])
    lines.append("")

    lines.append("### Most Likely Failure")
    lines.append(p['most_likely'])
    lines.append("")

    lines.append("### Most Dangerous Failure")
    lines.append(p['most_dangerous'])
    lines.append("")

    if p.get('hidden_assumption'):
        lines.append("### Hidden Assumption")
        lines.append(p['hidden_assumption'])
        lines.append("")

    lines.append("### Revised Plan")
    lines.append(p['revised_plan'])
    lines.append("")

    if p.get('checklist'):
        lines.append("### Pre-Launch Checklist")
        lines.append(p['checklist'])
        lines.append("")

    lines.append("---")
    lines.append(f"**Verdict:** {p['verdict']} "
                 f"(Confidence: {p['confidence']}%)")
    if p.get('tags'):
        lines.append(f"**Tags:** {p['tags']}")
    lines.append(f"**Date:** {p['created'][:10]}")

    return '\n'.join(lines)


def export_research(premortem_id: int, topic: str = None, research_dir: str = None) -> str | None:
    """Export a premortem to the research directory under a topic subfolder."""
    p = view(premortem_id)
    if not p:
        print(f"Error: premortem #{premortem_id} not found", file=sys.stderr)
        return None

    base = Path(research_dir) if research_dir else RESEARCH_DIR

    # Determine topic subfolder from --topic flag, tags, or 'general'
    if not topic:
        tags = p.get('tags', '')
        topic = tags.split(',')[0].strip() if tags else ''
    if not topic:
        topic = 'general'
    topic_slug = re.sub(r'[^a-z0-9]+', '-', topic.lower()).strip('-') or 'general'

    d = base / topic_slug
    d.mkdir(parents=True, exist_ok=True)

    # Create slug from plan (fallback to 'premortem' if all special chars)
    slug = re.sub(r'[^a-z0-9]+', '-', p['plan'].lower())[:60].strip('-')
    if not slug:
        slug = 'premortem'
    date = p['created'][:10]
    filename = f"{date}_{slug}.md"
    filepath = d / filename

    # Guard against overwriting an existing file with a different premortem
    if filepath.exists():
        counter = 2
        while filepath.exists():
            filename = f"{date}_{slug}_{counter}.md"
            filepath = d / filename
            counter += 1

    content = format_premortem(p)
    filepath.write_text(content, encoding="utf-8")

    print(f"Exported to: {filepath}")
    return str(filepath)


def delete(premortem_id: int) -> bool:
    """Delete a premortem by ID."""
    conn = _init_tables()
    c = conn.cursor()
    c.execute('SELECT id FROM premortems WHERE id = ?', (premortem_id,))
    if not c.fetchone():
        print(f"Error: premortem #{premortem_id} not found", file=sys.stderr)
        conn.close()
        return False

    c.execute('DELETE FROM premortems WHERE id = ?', (premortem_id,))
    conn.commit()
    conn.close()
    print(f"Deleted premortem #{premortem_id}")
    return True


def rebuild_fts() -> int:
    """Rebuild the FTS5 index from the premortems table."""
    conn = _init_tables()
    c = conn.cursor()

    # Drop and recreate FTS5 content
    c.execute("INSERT INTO premortems_fts(premortems_fts) VALUES ('rebuild')")
    conn.commit()

    # Count indexed rows
    c.execute("SELECT COUNT(*) FROM premortems")
    count = c.fetchone()[0]
    conn.close()

    print(f"Rebuilt FTS5 index: {count} premortems indexed")
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Premortem analysis - structured failure-mode evaluation')
    subparsers = parser.add_subparsers(dest='command')

    # Store
    store_p = subparsers.add_parser('store', help='Store a new premortem (JSON via stdin or file)')
    store_p.add_argument('--input', '-i', help='JSON file path (default: read from stdin)')

    # Search
    search_p = subparsers.add_parser('search', help='Search premortems via FTS5')
    search_p.add_argument('query', help='Search query (supports FTS5 syntax)')
    search_p.add_argument('--limit', type=int, default=10)

    # List
    list_p = subparsers.add_parser('list', help='List recent premortems')
    list_p.add_argument('--limit', type=int, default=10)
    list_p.add_argument('--tag', help='Filter by tag')

    # View
    view_p = subparsers.add_parser('view', help='View full premortem by ID')
    view_p.add_argument('id', type=int)

    # Export
    export_p = subparsers.add_parser('export', help='Export premortem to research directory')
    export_p.add_argument('id', type=int)
    export_p.add_argument('--topic', help='Topic subfolder (default: first tag or "general")')
    export_p.add_argument('--dir', help='Research directory (default: ~/research)')

    # Delete
    delete_p = subparsers.add_parser('delete', help='Delete a premortem by ID')
    delete_p.add_argument('id', type=int)

    # Rebuild
    subparsers.add_parser('rebuild', help='Rebuild FTS5 index from premortems table')

    args = parser.parse_args()

    if args.command == 'store':
        if args.input:
            raw = Path(args.input).read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)

        store(data)

    elif args.command == 'search':
        results = search(args.query, args.limit)
        if not results:
            print("No premortems found.")
        else:
            for r in results:
                print(f"\n#{r['id']} [{r['verdict']} {r['confidence']}%] "
                      f"{r['plan']}")
                if r.get('tags'):
                    print(f"   Tags: {r['tags']}")
                print(f"   Date: {r['created'][:10]}")
                if r.get('snippet'):
                    print(f"   ...{r['snippet']}...")

    elif args.command == 'list':
        results = list_premortems(args.limit, getattr(args, 'tag', None))
        if not results:
            print("No premortems found.")
        else:
            for r in results:
                print(f"#{r['id']} [{r['verdict']} {r['confidence']}%] "
                      f"{r['plan']}")
                if r.get('tags'):
                    print(f"   Tags: {r['tags']}")
                print(f"   Date: {r['created'][:10]}")

    elif args.command == 'view':
        p = view(args.id)
        if p:
            print(format_premortem(p))
        else:
            print(f"Premortem #{args.id} not found.")

    elif args.command == 'export':
        export_research(args.id, getattr(args, 'topic', None),
                        getattr(args, 'dir', None))

    elif args.command == 'delete':
        delete(args.id)

    elif args.command == 'rebuild':
        rebuild_fts()

    else:
        parser.print_help()
