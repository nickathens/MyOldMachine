#!/usr/bin/env python3
"""
Web Scraper - Advanced web scraping with Playwright (handles JavaScript).

Usage:
    python scrape.py screenshot "https://example.com" output.png
    python scrape.py content "https://example.com"
    python scrape.py pdf "https://example.com" output.pdf
    python scrape.py links "https://example.com"
    python scrape.py tables "https://example.com"
"""

import argparse
import json
import sys
from pathlib import Path


def get_browser():
    """Get Playwright browser instance."""
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    return playwright, browser


def take_screenshot(url: str, output_path: str, full_page: bool = True) -> dict:
    """Take a screenshot of a webpage."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.screenshot(path=output_path, full_page=full_page)
            browser.close()

        return {"success": True, "output": output_path}
    except Exception as e:
        return {"error": str(e)}


def get_content(url: str, selector: str = None) -> dict:
    """Get text content from a webpage."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)

            if selector:
                elements = page.query_selector_all(selector)
                content = [el.text_content().strip() for el in elements]
            else:
                # Get main content, removing scripts and styles
                page.evaluate("""
                    document.querySelectorAll('script, style, nav, footer, header').forEach(el => el.remove());
                """)
                content = page.locator("body").text_content()

            title = page.title()
            browser.close()

        return {
            "success": True,
            "url": url,
            "title": title,
            "content": content if isinstance(content, list) else content[:15000]
        }
    except Exception as e:
        return {"error": str(e)}


def save_pdf(url: str, output_path: str) -> dict:
    """Save webpage as PDF."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.pdf(path=output_path, format="A4", print_background=True)
            browser.close()

        return {"success": True, "output": output_path}
    except Exception as e:
        return {"error": str(e)}


def get_links(url: str, external_only: bool = False) -> dict:
    """Extract all links from a webpage."""
    try:
        from playwright.sync_api import sync_playwright
        from urllib.parse import urlparse, urljoin

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)

            links = page.evaluate("""
                Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({
                        text: a.textContent.trim().substring(0, 100),
                        href: a.href
                    }))
                    .filter(link => link.href && !link.href.startsWith('javascript:'))
            """)

            browser.close()

        # Filter external only if requested
        if external_only:
            base_domain = urlparse(url).netloc
            links = [l for l in links if urlparse(l['href']).netloc != base_domain]

        return {"success": True, "url": url, "links": links, "count": len(links)}
    except Exception as e:
        return {"error": str(e)}


def get_tables(url: str) -> dict:
    """Extract tables from a webpage."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)

            tables = page.evaluate("""
                Array.from(document.querySelectorAll('table')).map(table => {
                    const rows = Array.from(table.querySelectorAll('tr'));
                    return rows.map(row => {
                        const cells = Array.from(row.querySelectorAll('th, td'));
                        return cells.map(cell => cell.textContent.trim());
                    });
                });
            """)

            browser.close()

        return {"success": True, "url": url, "tables": tables, "count": len(tables)}
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Web scraper with Playwright")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Screenshot
    ss_parser = subparsers.add_parser("screenshot", help="Take screenshot")
    ss_parser.add_argument("url", help="URL to capture")
    ss_parser.add_argument("output", help="Output file path")
    ss_parser.add_argument("--viewport", action="store_true", help="Viewport only (not full page)")

    # Content
    content_parser = subparsers.add_parser("content", help="Get text content")
    content_parser.add_argument("url", help="URL to scrape")
    content_parser.add_argument("--selector", "-s", help="CSS selector for specific elements")

    # PDF
    pdf_parser = subparsers.add_parser("pdf", help="Save as PDF")
    pdf_parser.add_argument("url", help="URL to capture")
    pdf_parser.add_argument("output", help="Output PDF path")

    # Links
    links_parser = subparsers.add_parser("links", help="Extract links")
    links_parser.add_argument("url", help="URL to scrape")
    links_parser.add_argument("--external", action="store_true", help="External links only")

    # Tables
    tables_parser = subparsers.add_parser("tables", help="Extract tables")
    tables_parser.add_argument("url", help="URL to scrape")

    args = parser.parse_args()

    if args.command == "screenshot":
        print(f"Taking screenshot of: {args.url}")
        result = take_screenshot(args.url, args.output, not args.viewport)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(f"Saved: {result['output']}")

    elif args.command == "content":
        print(f"Getting content from: {args.url}")
        result = get_content(args.url, args.selector)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(f"Title: {result['title']}")
        print(f"\n--- Content ---\n")
        if isinstance(result['content'], list):
            for item in result['content']:
                print(f"- {item}")
        else:
            print(result['content'])

    elif args.command == "pdf":
        print(f"Saving as PDF: {args.url}")
        result = save_pdf(args.url, args.output)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(f"Saved: {result['output']}")

    elif args.command == "links":
        print(f"Extracting links from: {args.url}")
        result = get_links(args.url, args.external)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(f"Found {result['count']} links:\n")
        for link in result['links'][:50]:  # Limit output
            print(f"- {link['text'][:50]}: {link['href']}")

    elif args.command == "tables":
        print(f"Extracting tables from: {args.url}")
        result = get_tables(args.url)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print(f"Found {result['count']} tables:\n")
        for i, table in enumerate(result['tables']):
            print(f"Table {i + 1}:")
            for row in table[:10]:  # Limit rows
                print(f"  {' | '.join(row[:5])}")  # Limit columns
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
