#!/usr/bin/env python3
"""
URL Summarizer - Fetches a URL and extracts the main content.

Usage:
    python summarize_url.py <url> [--max-chars N]

Fixed in the Linux bot's 2026-09-27 review: whitespace cleaning flattened
every article onto one line; the first <article> on the page won even when
it was a one line "Related" teaser, so the real article was dropped; a page
that names its charset only in <meta> (older Greek sites in windows-1253)
came out as replacement characters; the 15,000 character cut was silent;
PDF links were refused.
"""

import argparse
import re
import sys
import tempfile
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


def clean_text(text):
    """Collapse spaces inside lines and drop empty lines, keeping the line breaks."""
    lines = (re.sub(r'[ \t\f\v\u00a0]+', ' ', line).strip() for line in text.split('\n'))
    return '\n'.join(line for line in lines if line)


def extract_article_content(soup):
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()

    # Of every element that looks like an article container, take the one
    # holding the most text: the first match is often a teaser card
    candidates = []
    for selector in ['article', 'main', '[role="main"]', '.post-content', '.article-content',
                     '.entry-content', '.content', '#content', '.story-body']:
        candidates.extend(soup.select(selector))
    content = max(candidates, key=lambda el: len(el.get_text(strip=True)), default=None)

    if content is None or len(content.get_text(strip=True)) < 200:
        # Nothing article-like with real text: the whole body
        content = soup.body if soup.body else soup

    return clean_text(content.get_text(separator='\n', strip=True))


def pdf_text(data):
    """Text of a PDF through markitdown (the docs skill's backend for pdf)."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise RuntimeError("reading a PDF needs markitdown, which the docs skill installs "
                           "(pip install markitdown)") from None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        return MarkItDown().convert(tmp.name).text_content


# Past this a link is not an article or a document worth reading here. The
# body is streamed and the type checked first: httpx.get used to read the whole
# thing into memory, inside the bot's process, before refusing it (Linux bot
# sweep 2026-10-07: a 40 MB video link pulled all 40 MB to say "unsupported").
MAX_BYTES = 50 * 1024 * 1024


def _read_capped(response, limit: int):
    """The body, or None as soon as it passes limit bytes."""
    chunks, total = [], 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_url(url):
    parsed = urlparse(url)
    if not parsed.scheme:
        url = 'https://' + url

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=30) as response:
            response.raise_for_status()
            content_type = response.headers.get('content-type', '')
            final_url = str(response.url)
            is_html = 'text/html' in content_type or 'xhtml' in content_type
            is_pdf = 'application/pdf' in content_type or final_url.lower().endswith('.pdf')
            is_json = 'application/json' in content_type
            if not (is_html or is_pdf or is_json or 'text/' in content_type):
                return {"error": f"Unsupported content type: {content_type}", "url": final_url}
            data = _read_capped(response, MAX_BYTES)
            if data is None:
                return {"error": f"The page is larger than {MAX_BYTES // (1024 * 1024)} MB; "
                                 "download it with the downloads skill instead", "url": final_url}
            charset = response.charset_encoding

        if is_html:
            # Bytes, so BeautifulSoup can honour a <meta charset> when the
            # header names none (httpx alone assumes UTF-8)
            soup = BeautifulSoup(data, 'html.parser', from_encoding=charset)
            title = soup.title.get_text(strip=True) if soup.title else None
            meta_desc = (soup.find('meta', attrs={'name': 'description'})
                         or soup.find('meta', attrs={'property': 'og:description'}))
            description = meta_desc.get('content') if meta_desc else None
            return {
                "url": final_url, "title": title,
                "description": description, "content": extract_article_content(soup),
                "content_type": "html"
            }
        if is_pdf:
            return {"url": final_url, "content": clean_text(pdf_text(data)), "content_type": "pdf"}
        return {"url": final_url, "content": data.decode(charset or "utf-8", errors="replace"),
                "content_type": "json" if is_json else "text"}

    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Fetch a URL and print its main text")
    parser.add_argument("url")
    parser.add_argument("--max-chars", type=int, default=15000,
                        help="Characters of content to print (default 15000; 0 = all)")
    args = parser.parse_args()

    result = fetch_url(args.url)
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    content = result['content']
    total = len(content)
    if args.max_chars and total > args.max_chars:
        content = content[:args.max_chars]

    print(f"URL: {result['url']}")
    if result.get('title'):
        print(f"Title: {result['title']}")
    if result.get('description'):
        print(f"Description: {result['description']}")
    print(f"\n--- Content ({result['content_type']}) ---\n")
    print(content)
    if len(content) < total:
        print(f"\n[truncated: {len(content):,} of {total:,} characters shown; --max-chars 0 prints all]")


if __name__ == "__main__":
    main()
