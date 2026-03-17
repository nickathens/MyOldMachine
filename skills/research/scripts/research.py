#!/usr/bin/env python3
"""
Research automation - RSS feeds, web scraping, data aggregation
"""
import json
import sqlite3
import sys
import argparse
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import feedparser

DATA_DIR = Path.home() / '.local' / 'share' / 'research'
DB_FILE = DATA_DIR / 'research.db'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
}

def init_db():
    """Initialize SQLite database"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS feeds (
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE,
        url TEXT,
        last_check TEXT,
        added TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY,
        feed_id INTEGER,
        title TEXT,
        url TEXT UNIQUE,
        summary TEXT,
        published TEXT,
        collected TEXT,
        read INTEGER DEFAULT 0,
        FOREIGN KEY(feed_id) REFERENCES feeds(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS scraped (
        id INTEGER PRIMARY KEY,
        source TEXT,
        url TEXT,
        data TEXT,
        collected TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY,
        topic TEXT,
        content TEXT,
        created TEXT,
        updated TEXT
    )''')

    conn.commit()
    return conn

def add_feed(name, url):
    """Add RSS feed to monitor"""
    conn = init_db()
    c = conn.cursor()

    try:
        c.execute('INSERT INTO feeds (name, url, added) VALUES (?, ?, ?)',
                 (name, url, datetime.now().isoformat()))
        conn.commit()
        print(f"Added feed: {name}")

        # Fetch initial articles
        fetch_feed(name)
        return True
    except sqlite3.IntegrityError:
        print(f"Feed '{name}' already exists")
        return False
    finally:
        conn.close()

def remove_feed(name):
    """Remove feed and its articles"""
    conn = init_db()
    c = conn.cursor()

    c.execute('SELECT id FROM feeds WHERE name = ?', (name,))
    row = c.fetchone()
    if row:
        feed_id = row[0]
        c.execute('DELETE FROM articles WHERE feed_id = ?', (feed_id,))
        c.execute('DELETE FROM feeds WHERE id = ?', (feed_id,))
        conn.commit()
        print(f"Removed feed: {name}")
        return True
    print(f"Feed '{name}' not found")
    return False

def list_feeds():
    """List all monitored feeds"""
    conn = init_db()
    c = conn.cursor()
    c.execute('SELECT name, url, last_check FROM feeds')
    feeds = c.fetchall()
    conn.close()
    return [{'name': f[0], 'url': f[1], 'last_check': f[2]} for f in feeds]

def fetch_feed(name=None):
    """Fetch articles from feed(s)"""
    conn = init_db()
    c = conn.cursor()

    if name:
        c.execute('SELECT id, name, url FROM feeds WHERE name = ?', (name,))
    else:
        c.execute('SELECT id, name, url FROM feeds')

    feeds = c.fetchall()
    new_articles = []

    for feed_id, feed_name, url in feeds:
        try:
            parsed = feedparser.parse(url)

            for entry in parsed.entries[:20]:  # Limit to 20 per feed
                title = entry.get('title', 'No title')
                link = entry.get('link', '')
                summary = entry.get('summary', '')[:500]  # Truncate
                published = entry.get('published', datetime.now().isoformat())

                try:
                    c.execute('''INSERT INTO articles
                               (feed_id, title, url, summary, published, collected)
                               VALUES (?, ?, ?, ?, ?, ?)''',
                             (feed_id, title, link, summary, published,
                              datetime.now().isoformat()))
                    new_articles.append({'feed': feed_name, 'title': title, 'url': link})
                except sqlite3.IntegrityError:
                    pass  # Article already exists

            c.execute('UPDATE feeds SET last_check = ? WHERE id = ?',
                     (datetime.now().isoformat(), feed_id))

        except Exception as e:
            print(f"Error fetching {feed_name}: {e}")

    conn.commit()
    conn.close()

    print(f"Fetched {len(new_articles)} new articles")
    return new_articles

def get_articles(feed_name=None, unread_only=False, limit=20):
    """Get articles from database"""
    conn = init_db()
    c = conn.cursor()

    query = '''SELECT a.title, a.url, a.summary, a.published, f.name
               FROM articles a JOIN feeds f ON a.feed_id = f.id'''
    params = []

    if feed_name:
        query += ' WHERE f.name = ?'
        params.append(feed_name)

    if unread_only:
        if params:
            query += ' AND a.read = 0'
        else:
            query += ' WHERE a.read = 0'

    query += ' ORDER BY a.collected DESC LIMIT ?'
    params.append(limit)

    c.execute(query, params)
    articles = c.fetchall()
    conn.close()

    return [{'title': a[0], 'url': a[1], 'summary': a[2],
             'published': a[3], 'feed': a[4]} for a in articles]

def scrape_page(url, selector=None, extract_links=False):
    """Scrape data from a webpage"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        results = []

        if selector:
            elements = soup.select(selector)
            for elem in elements:
                item = {
                    'text': elem.get_text(strip=True),
                    'html': str(elem)[:500]
                }
                if extract_links:
                    links = elem.select('a[href]')
                    item['links'] = [{'text': l.get_text(strip=True),
                                     'href': l.get('href')} for l in links]
                results.append(item)
        else:
            # Extract basic page info
            results.append({
                'title': soup.title.string if soup.title else None,
                'text': soup.get_text(strip=True)[:2000],
                'links_count': len(soup.select('a[href]'))
            })

        # Store in database
        conn = init_db()
        c = conn.cursor()
        c.execute('INSERT INTO scraped (source, url, data, collected) VALUES (?, ?, ?, ?)',
                 ('scrape', url, json.dumps(results), datetime.now().isoformat()))
        conn.commit()
        conn.close()

        return results

    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return None

def add_note(topic, content):
    """Add research note"""
    conn = init_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('INSERT INTO notes (topic, content, created, updated) VALUES (?, ?, ?, ?)',
             (topic, content, now, now))
    conn.commit()
    conn.close()
    print(f"Note added to topic: {topic}")

def get_notes(topic=None, limit=20):
    """Get research notes"""
    conn = init_db()
    c = conn.cursor()

    if topic:
        c.execute('SELECT topic, content, created FROM notes WHERE topic = ? ORDER BY created DESC LIMIT ?',
                 (topic, limit))
    else:
        c.execute('SELECT topic, content, created FROM notes ORDER BY created DESC LIMIT ?',
                 (limit,))

    notes = c.fetchall()
    conn.close()

    return [{'topic': n[0], 'content': n[1], 'created': n[2]} for n in notes]

def export_data(format='json', output=None, data_type='articles'):
    """Export collected data"""
    conn = init_db()
    c = conn.cursor()

    if data_type == 'articles':
        c.execute('''SELECT a.title, a.url, a.summary, a.published, f.name
                    FROM articles a JOIN feeds f ON a.feed_id = f.id
                    ORDER BY a.collected DESC''')
        rows = c.fetchall()
        data = [{'title': r[0], 'url': r[1], 'summary': r[2],
                'published': r[3], 'feed': r[4]} for r in rows]

    elif data_type == 'scraped':
        c.execute('SELECT source, url, data, collected FROM scraped ORDER BY collected DESC')
        rows = c.fetchall()
        data = [{'source': r[0], 'url': r[1], 'data': json.loads(r[2]),
                'collected': r[3]} for r in rows]

    elif data_type == 'notes':
        data = get_notes(limit=1000)

    conn.close()

    if format == 'json':
        output_text = json.dumps(data, indent=2)
    elif format == 'csv':
        if not data:
            output_text = ''
        else:
            import csv
            import io
            output_buffer = io.StringIO()
            writer = csv.DictWriter(output_buffer, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
            output_text = output_buffer.getvalue()
    elif format == 'markdown':
        lines = [f"# Research Export - {data_type.title()}", ""]
        for item in data:
            if data_type == 'articles':
                lines.append(f"## [{item['title']}]({item['url']})")
                lines.append(f"*{item['feed']} - {item['published']}*")
                lines.append(f"\n{item['summary']}\n")
            elif data_type == 'notes':
                lines.append(f"### {item['topic']} ({item['created']})")
                lines.append(f"{item['content']}\n")
        output_text = '\n'.join(lines)

    if output:
        Path(output).write_text(output_text)
        print(f"Exported to: {output}")
    else:
        print(output_text)

    return data

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Research automation')
    subparsers = parser.add_subparsers(dest='command')

    # RSS commands
    rss_parser = subparsers.add_parser('rss', help='RSS feed management')
    rss_parser.add_argument('action', choices=['add', 'remove', 'list', 'check'])
    rss_parser.add_argument('name', nargs='?')
    rss_parser.add_argument('url', nargs='?')

    # Scrape command
    scrape_parser = subparsers.add_parser('scrape', help='Web scraping')
    scrape_parser.add_argument('url')
    scrape_parser.add_argument('--selector', '-s')
    scrape_parser.add_argument('--links', action='store_true')

    # Articles command
    articles_parser = subparsers.add_parser('articles', help='View articles')
    articles_parser.add_argument('--feed', '-f')
    articles_parser.add_argument('--unread', action='store_true')
    articles_parser.add_argument('--limit', type=int, default=20)

    # Notes command
    notes_parser = subparsers.add_parser('note', help='Research notes')
    notes_parser.add_argument('action', choices=['add', 'list'])
    notes_parser.add_argument('--topic', '-t')
    notes_parser.add_argument('--content', '-c')

    # Export command
    export_parser = subparsers.add_parser('export', help='Export data')
    export_parser.add_argument('--format', choices=['json', 'csv', 'markdown'], default='json')
    export_parser.add_argument('--output', '-o')
    export_parser.add_argument('--type', choices=['articles', 'scraped', 'notes'], default='articles')

    args = parser.parse_args()

    if args.command == 'rss':
        if args.action == 'add':
            if not args.name or not args.url:
                print("Error: name and url required")
                sys.exit(1)
            add_feed(args.name, args.url)
        elif args.action == 'remove':
            if not args.name:
                print("Error: name required")
                sys.exit(1)
            remove_feed(args.name)
        elif args.action == 'list':
            feeds = list_feeds()
            for f in feeds:
                print(f"  {f['name']}: {f['url']}")
        elif args.action == 'check':
            fetch_feed(args.name)

    elif args.command == 'scrape':
        results = scrape_page(args.url, args.selector, args.links)
        if results:
            print(json.dumps(results, indent=2))

    elif args.command == 'articles':
        articles = get_articles(args.feed, args.unread, args.limit)
        for a in articles:
            print(f"\n[{a['feed']}] {a['title']}")
            print(f"  {a['url']}")

    elif args.command == 'note':
        if args.action == 'add':
            if not args.topic or not args.content:
                print("Error: --topic and --content required")
                sys.exit(1)
            add_note(args.topic, args.content)
        elif args.action == 'list':
            notes = get_notes(args.topic)
            for n in notes:
                print(f"\n[{n['topic']}] {n['created']}")
                print(f"  {n['content'][:100]}...")

    elif args.command == 'export':
        export_data(args.format, args.output, args.type)
