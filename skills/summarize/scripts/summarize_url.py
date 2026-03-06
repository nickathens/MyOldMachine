#!/usr/bin/env python3
"""
URL Summarizer - Fetches a URL and extracts the main content.

Usage:
    python summarize_url.py <url>
"""

import re
import sys
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


def clean_text(text):
    text = re.sub(r'\s+', ' ', text)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)


def extract_article_content(soup):
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()

    content = None
    for selector in ['article', 'main', '[role="main"]', '.post-content',
                     '.article-content', '.entry-content', '.content', '#content']:
        content = soup.select_one(selector)
        if content:
            break

    if not content:
        content = soup.body if soup.body else soup

    return clean_text(content.get_text(separator='\n', strip=True))


def fetch_url(url):
    parsed = urlparse(url)
    if not parsed.scheme:
        url = 'https://' + url

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
        response.raise_for_status()
        content_type = response.headers.get('content-type', '')

        if 'text/html' in content_type:
            soup = BeautifulSoup(response.text, 'html.parser')
            title = soup.title.string if soup.title else None
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            description = meta_desc.get('content') if meta_desc else None
            content = extract_article_content(soup)
            return {
                "url": str(response.url), "title": title,
                "description": description, "content": content[:15000],
                "content_type": "html"
            }
        elif 'application/json' in content_type:
            return {"url": str(response.url), "content": response.text[:15000], "content_type": "json"}
        elif 'text/' in content_type:
            return {"url": str(response.url), "content": response.text[:15000], "content_type": "text"}
        else:
            return {"error": f"Unsupported content type: {content_type}"}

    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def main():
    if len(sys.argv) < 2:
        print("Usage: python summarize_url.py <url>")
        sys.exit(1)

    result = fetch_url(sys.argv[1])
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"URL: {result['url']}")
    if result.get('title'):
        print(f"Title: {result['title']}")
    if result.get('description'):
        print(f"Description: {result['description']}")
    print(f"\n--- Content ({result['content_type']}) ---\n")
    print(result['content'])


if __name__ == "__main__":
    main()
