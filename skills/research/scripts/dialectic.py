#!/usr/bin/env python3
"""
Dialectical analysis — structured bull/bear evaluation framework.

Forces steel-manning of both sides of a proposition before synthesizing
a recommendation. Stores analyses in SQLite with FTS5 full-text search.
Exports to ~/research/<topic>/ for long-term reference.

Usage:
    # Store analysis (JSON via stdin or --input file)
    echo '{"proposition": "...", ...}' | python dialectic.py store
    python dialectic.py store --input analysis.json

    # Search past analyses
    python dialectic.py search "video export"

    # List recent analyses
    python dialectic.py list [--limit 10] [--tag tooling]

    # View full analysis
    python dialectic.py view <id>

    # Export to research directory (topic subfolder)
    python dialectic.py export <id> [--topic audio] [--dir /path/to/research]

    # Delete an analysis
    python dialectic.py delete <id>

    # Rebuild FTS5 index
    python dialectic.py rebuild
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

# Default export directory — override via --dir flag or RESEARCH_EXPORT_DIR env var
_export_dir = os.environ.get('RESEARCH_EXPORT_DIR', '')
RESEARCH_DIR = Path(_export_dir) if _export_dir else Path.home() / 'research'

VALID_RECOMMENDATIONS = ('FOR', 'AGAINST', 'CONDITIONAL')

# FTS5 columns that map to analyses table columns (order matters for snippet index)
_FTS_COLS = ('proposition', 'context', 'bull_case', 'bear_case',
             'cross_examination', 'synthesis', 'flip_conditions', 'tags')


def _init_tables() -> sqlite3.Connection:
    """Create analyses tables and FTS5 index if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proposition TEXT NOT NULL,
        context TEXT DEFAULT '',
        bull_case TEXT NOT NULL,
        bear_case TEXT NOT NULL,
        cross_examination TEXT NOT NULL,
        synthesis TEXT NOT NULL,
        recommendation TEXT NOT NULL,
        confidence INTEGER NOT NULL,
        flip_conditions TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        created TEXT NOT NULL
    )''')

    # FTS5 external content table — reads from analyses via content_rowid
    c.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS analyses_fts USING fts5(
        proposition,
        context,
        bull_case,
        bear_case,
        cross_examination,
        synthesis,
        flip_conditions,
        tags,
        content='analyses',
        content_rowid='id',
        tokenize='porter unicode61',
        prefix='2,3'
    )''')

    # Sync triggers: keep FTS5 index updated on INSERT/DELETE/UPDATE
    cols_new = ', '.join(f'new.{c}' for c in _FTS_COLS)
    cols_old = ', '.join(f'old.{c}' for c in _FTS_COLS)
    cols_list = ', '.join(_FTS_COLS)

    c.execute(f'''CREATE TRIGGER IF NOT EXISTS analyses_ai
        AFTER INSERT ON analyses BEGIN
            INSERT INTO analyses_fts(rowid, {cols_list})
            VALUES (new.id, {cols_new});
        END''')

    c.execute(f'''CREATE TRIGGER IF NOT EXISTS analyses_ad
        AFTER DELETE ON analyses BEGIN
            INSERT INTO analyses_fts(analyses_fts, rowid, {cols_list})
            VALUES ('delete', old.id, {cols_old});
        END''')

    c.execute(f'''CREATE TRIGGER IF NOT EXISTS analyses_au
        AFTER UPDATE ON analyses BEGIN
            INSERT INTO analyses_fts(analyses_fts, rowid, {cols_list})
            VALUES ('delete', old.id, {cols_old});
            INSERT INTO analyses_fts(rowid, {cols_list})
            VALUES (new.id, {cols_new});
        END''')

    conn.commit()
    return conn


def _validate(data: dict) -> dict:
    """Validate and normalize input data. Returns cleaned data or exits on error."""
    required = ['proposition', 'bull_case', 'bear_case', 'cross_examination',
                'synthesis', 'recommendation', 'confidence']
    missing = [k for k in required if k not in data or not str(data[k]).strip()]
    if missing:
        print(f"Error: missing required fields: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    rec = str(data['recommendation']).upper()
    if rec not in VALID_RECOMMENDATIONS:
        print(f"Error: recommendation must be one of {VALID_RECOMMENDATIONS} "
              f"(got: {data['recommendation']})", file=sys.stderr)
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
        'proposition': str(data['proposition']).strip(),
        'context': str(data.get('context', '')).strip(),
        'bull_case': str(data['bull_case']).strip(),
        'bear_case': str(data['bear_case']).strip(),
        'cross_examination': str(data['cross_examination']).strip(),
        'synthesis': str(data['synthesis']).strip(),
        'recommendation': rec,
        'confidence': conf,
        'flip_conditions': str(data.get('flip_conditions', '')).strip(),
        'tags': tags,
    }


def store(data: dict) -> int:
    """Store a dialectical analysis. Returns the row ID."""
    clean = _validate(data)

    conn = _init_tables()
    c = conn.cursor()
    c.execute('''INSERT INTO analyses
        (proposition, context, bull_case, bear_case, cross_examination,
         synthesis, recommendation, confidence, flip_conditions, tags, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (clean['proposition'], clean['context'], clean['bull_case'],
         clean['bear_case'], clean['cross_examination'], clean['synthesis'],
         clean['recommendation'], clean['confidence'], clean['flip_conditions'],
         clean['tags'], datetime.now().isoformat()))
    conn.commit()
    row_id = c.lastrowid
    conn.close()

    print(f"Stored analysis #{row_id}: {clean['proposition']}")
    return row_id


def search(query: str, limit: int = 10) -> list[dict]:
    """Search analyses using FTS5 with BM25 ranking and snippet extraction."""
    conn = _init_tables()
    c = conn.cursor()

    results = []
    # snippet -1 = best matching column across all FTS5 fields
    sql = '''SELECT a.id, a.proposition, a.recommendation, a.confidence,
                    a.tags, a.created,
                    snippet(analyses_fts, -1, '>>>', '<<<', '...', 40) as snippet
             FROM analyses_fts
             JOIN analyses a ON a.id = analyses_fts.rowid
             WHERE analyses_fts MATCH ?
             ORDER BY bm25(analyses_fts)
             LIMIT ?'''

    try:
        try:
            c.execute(sql, (query, limit))
            for row in c.fetchall():
                results.append({
                    'id': row[0], 'proposition': row[1], 'recommendation': row[2],
                    'confidence': row[3], 'tags': row[4], 'created': row[5],
                    'snippet': row[6],
                })
        except sqlite3.OperationalError:
            # Malformed FTS5 query — try quoting as phrase
            escaped = '"' + query.replace('"', '""') + '"'
            try:
                c.execute(sql, (escaped, limit))
                for row in c.fetchall():
                    results.append({
                        'id': row[0], 'proposition': row[1], 'recommendation': row[2],
                        'confidence': row[3], 'tags': row[4], 'created': row[5],
                        'snippet': row[6],
                    })
            except sqlite3.OperationalError:
                # Last resort: LIKE on proposition + synthesis
                c.execute('''SELECT id, proposition, recommendation, confidence, tags, created
                             FROM analyses
                             WHERE proposition LIKE ? OR synthesis LIKE ?
                             ORDER BY created DESC LIMIT ?''',
                          (f'%{query}%', f'%{query}%', limit))
                for row in c.fetchall():
                    results.append({
                        'id': row[0], 'proposition': row[1], 'recommendation': row[2],
                        'confidence': row[3], 'tags': row[4], 'created': row[5],
                        'snippet': '',
                    })
    finally:
        conn.close()

    return results


def list_analyses(limit: int = 10, tag: str = None) -> list[dict]:
    """List recent analyses, optionally filtered by tag."""
    conn = _init_tables()
    c = conn.cursor()

    if tag:
        # Tags are comma-separated, so match substring
        c.execute('''SELECT id, proposition, recommendation, confidence, tags, created
                     FROM analyses WHERE tags LIKE ?
                     ORDER BY created DESC LIMIT ?''',
                  (f'%{tag}%', limit))
    else:
        c.execute('''SELECT id, proposition, recommendation, confidence, tags, created
                     FROM analyses ORDER BY created DESC LIMIT ?''', (limit,))

    results = []
    for row in c.fetchall():
        results.append({
            'id': row[0], 'proposition': row[1], 'recommendation': row[2],
            'confidence': row[3], 'tags': row[4], 'created': row[5],
        })

    conn.close()
    return results


def view(analysis_id: int) -> dict | None:
    """View a full analysis by ID."""
    conn = _init_tables()
    c = conn.cursor()
    cols = ('id', 'proposition', 'context', 'bull_case', 'bear_case',
            'cross_examination', 'synthesis', 'recommendation', 'confidence',
            'flip_conditions', 'tags', 'created')
    c.execute(f'SELECT {", ".join(cols)} FROM analyses WHERE id = ?', (analysis_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return None

    return dict(zip(cols, row))


def format_analysis(a: dict) -> str:
    """Format an analysis as readable markdown."""
    lines = [
        f"# Dialectical Analysis #{a['id']}",
        f"## {a['proposition']}",
        "",
    ]
    if a.get('context'):
        lines.append(f"*Context: {a['context']}*")
        lines.append("")

    lines.append("### Bull Case (FOR)")
    lines.append(a['bull_case'])
    lines.append("")

    lines.append("### Bear Case (AGAINST)")
    lines.append(a['bear_case'])
    lines.append("")

    lines.append("### Cross-Examination")
    lines.append(a['cross_examination'])
    lines.append("")

    lines.append("### Synthesis")
    lines.append(a['synthesis'])
    lines.append("")

    lines.append("---")
    lines.append(f"**Recommendation:** {a['recommendation']} "
                 f"(Confidence: {a['confidence']}%)")
    if a.get('flip_conditions'):
        lines.append(f"**Flip conditions:** {a['flip_conditions']}")
    if a.get('tags'):
        lines.append(f"**Tags:** {a['tags']}")
    lines.append(f"**Date:** {a['created'][:10]}")

    return '\n'.join(lines)


def export_research(analysis_id: int, topic: str = None, research_dir: str = None) -> str | None:
    """Export an analysis to the research directory under a topic subfolder."""
    a = view(analysis_id)
    if not a:
        print(f"Error: analysis #{analysis_id} not found", file=sys.stderr)
        return None

    base = Path(research_dir) if research_dir else RESEARCH_DIR

    # Determine topic subfolder from --topic flag, tags, or 'general'
    if not topic:
        tags = a.get('tags', '')
        topic = tags.split(',')[0].strip() if tags else ''
    if not topic:
        topic = 'general'
    topic_slug = re.sub(r'[^a-z0-9]+', '-', topic.lower()).strip('-') or 'general'

    d = base / topic_slug
    d.mkdir(parents=True, exist_ok=True)

    # Create slug from proposition (fallback to 'analysis' if all special chars)
    slug = re.sub(r'[^a-z0-9]+', '-', a['proposition'].lower())[:60].strip('-')
    if not slug:
        slug = 'analysis'
    date = a['created'][:10]
    filename = f"{date}_{slug}.md"
    filepath = d / filename

    # Guard against overwriting an existing file with a different analysis
    if filepath.exists():
        counter = 2
        while filepath.exists():
            filename = f"{date}_{slug}_{counter}.md"
            filepath = d / filename
            counter += 1

    content = format_analysis(a)
    filepath.write_text(content, encoding="utf-8")

    print(f"Exported to: {filepath}")
    return str(filepath)


def delete(analysis_id: int) -> bool:
    """Delete an analysis by ID."""
    conn = _init_tables()
    c = conn.cursor()
    c.execute('SELECT id FROM analyses WHERE id = ?', (analysis_id,))
    if not c.fetchone():
        print(f"Error: analysis #{analysis_id} not found", file=sys.stderr)
        conn.close()
        return False

    c.execute('DELETE FROM analyses WHERE id = ?', (analysis_id,))
    conn.commit()
    conn.close()
    print(f"Deleted analysis #{analysis_id}")
    return True


def rebuild_fts() -> int:
    """Rebuild the FTS5 index from the analyses table."""
    conn = _init_tables()
    c = conn.cursor()

    # Drop and recreate FTS5 content
    c.execute("INSERT INTO analyses_fts(analyses_fts) VALUES ('rebuild')")
    conn.commit()

    # Count indexed rows
    c.execute("SELECT COUNT(*) FROM analyses")
    count = c.fetchone()[0]
    conn.close()

    print(f"Rebuilt FTS5 index: {count} analyses indexed")
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Dialectical analysis — structured bull/bear evaluation')
    subparsers = parser.add_subparsers(dest='command')

    # Store
    store_p = subparsers.add_parser('store', help='Store a new analysis (JSON via stdin or file)')
    store_p.add_argument('--input', '-i', help='JSON file path (default: read from stdin)')

    # Search
    search_p = subparsers.add_parser('search', help='Search analyses via FTS5')
    search_p.add_argument('query', help='Search query (supports FTS5 syntax)')
    search_p.add_argument('--limit', type=int, default=10)

    # List
    list_p = subparsers.add_parser('list', help='List recent analyses')
    list_p.add_argument('--limit', type=int, default=10)
    list_p.add_argument('--tag', help='Filter by tag')

    # View
    view_p = subparsers.add_parser('view', help='View full analysis by ID')
    view_p.add_argument('id', type=int)

    # Export
    export_p = subparsers.add_parser('export', help='Export analysis to research directory')
    export_p.add_argument('id', type=int)
    export_p.add_argument('--topic', help='Topic subfolder (default: first tag or "general")')
    export_p.add_argument('--dir', help='Research directory (default: ~/research)')

    # Delete
    delete_p = subparsers.add_parser('delete', help='Delete an analysis by ID')
    delete_p.add_argument('id', type=int)

    # Rebuild
    subparsers.add_parser('rebuild', help='Rebuild FTS5 index from analyses table')

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

        row_id = store(data)

    elif args.command == 'search':
        results = search(args.query, args.limit)
        if not results:
            print("No analyses found.")
        else:
            for r in results:
                print(f"\n#{r['id']} [{r['recommendation']} {r['confidence']}%] "
                      f"{r['proposition']}")
                if r.get('tags'):
                    print(f"   Tags: {r['tags']}")
                print(f"   Date: {r['created'][:10]}")
                if r.get('snippet'):
                    print(f"   ...{r['snippet']}...")

    elif args.command == 'list':
        results = list_analyses(args.limit, getattr(args, 'tag', None))
        if not results:
            print("No analyses found.")
        else:
            for r in results:
                print(f"#{r['id']} [{r['recommendation']} {r['confidence']}%] "
                      f"{r['proposition']}")
                if r.get('tags'):
                    print(f"   Tags: {r['tags']}")
                print(f"   Date: {r['created'][:10]}")

    elif args.command == 'view':
        a = view(args.id)
        if a:
            print(format_analysis(a))
        else:
            print(f"Analysis #{args.id} not found.")

    elif args.command == 'export':
        export_research(args.id, getattr(args, 'topic', None),
                        getattr(args, 'dir', None))

    elif args.command == 'delete':
        delete(args.id)

    elif args.command == 'rebuild':
        rebuild_fts()

    else:
        parser.print_help()
