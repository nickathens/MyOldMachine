#!/usr/bin/env python3
"""
Record the network traffic a page makes, as ground truth.

Usage:
  python capture_net.py --url https://site.com [--out net.json]
  python capture_net.py --url https://site.com --scroll          # drive scroll-bound loads
  python capture_net.py --url https://site.com --filter xhr-fetch # only API calls
  python capture_net.py --url https://site.com --bodies           # small JSON/text bodies too

This drives a real headless browser (the same Playwright/Chromium the browser
skill uses, in its own ephemeral context) and records every request the page
issues: method, URL, resource type, status, content type, size, and POST data.
With --scroll it steps down the page so scroll-driven and lazy-loaded requests
fire -- the point for a scroll reel.

IMPORTANT (trust boundary): this EXECUTES the target's JavaScript, in headless
Chromium on this machine, inside Chromium's own sandbox (the renderer gets its
own user and network namespace and a seccomp filter; on this Ubuntu that needs
/etc/apparmor.d/playwright-chromium, and without it the launch fails rather
than run unsandboxed). The sandbox is one layer, not a container: a renderer
exploit chained with a sandbox escape would still reach the bot's user. Point
it only at a target you are authorized to analyse (a client site, your own
work, an in-scope program, a public site you are studying); a target you have
reason to think is hostile is static-only. Do not pass --bodies on an
untrusted target.

The recorded requests are ground-truth evidence of what the page fetched. What
a given API call MEANS is a hypothesis; that an endpoint is hit only on scroll
is a behavioural claim -- which this tool, being a real runtime capture, can
legitimately confirm.
"""
import argparse
import json
import sys

# Chromium starts its sandbox or dies at launch (SIGTRAP), never silently without it.
SANDBOX_HINT = ("If Chromium died at launch (SIGTRAP): it could not start its own sandbox. On Ubuntu 23.10+ "
                "that needs the AppArmor profile /etc/apparmor.d/playwright-chromium (reverse-engineering "
                "SKILL.md). A memory ulimit (ulimit -v) kills it the same way.")

TEXTY = ("application/json", "text/", "application/javascript",
         "application/xml", "application/graphql")


def looks_texty(content_type):
    if not content_type:
        return False
    ct = content_type.lower()
    return any(ct.startswith(t) or t in ct for t in TEXTY)


def capture(url, wait_ms, timeout_ms, do_scroll, scroll_steps, grab_bodies, max_body, max_post):
    from playwright.sync_api import sync_playwright

    entries_by_req = {}
    order = []

    def on_request(req):
        entry = {
            "method": req.method,
            "url": req.url,
            "resource_type": req.resource_type,
            "status": None,
            "content_type": None,
            "size": None,
            "post_data": (req.post_data or "")[:max_post] if req.post_data else None,
            "failed": False,
        }
        entries_by_req[req] = entry
        order.append(req)

    def on_response(resp):
        e = entries_by_req.get(resp.request)
        if not e:
            return
        e["status"] = resp.status
        headers = resp.headers
        e["content_type"] = headers.get("content-type")
        cl = headers.get("content-length")
        if cl and cl.isdigit():
            e["size"] = int(cl)
        if grab_bodies and resp.request.resource_type in ("xhr", "fetch") and looks_texty(e["content_type"]):
            try:
                raw = resp.body()
                if raw is not None and len(raw) <= max_body:
                    e["body"] = raw.decode("utf-8", errors="replace")
                elif raw is not None:
                    e["body"] = raw[:max_body].decode("utf-8", errors="replace") + f"...[+{len(raw) - max_body}B]"
            except Exception as ex:  # noqa: BLE001 - body may be unavailable; record why, don't crash
                e["body_error"] = str(ex)[:120]

    def on_failed(req):
        e = entries_by_req.get(req)
        if e:
            e["failed"] = True
            e["failure"] = (req.failure or "")[:160]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, chromium_sandbox=True)
        page = browser.new_page(accept_downloads=False)
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_failed)

        final_url = url
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            final_url = page.url
        except Exception as e:  # noqa: BLE001 - navigation can time out; keep whatever we captured
            print(f"Navigation warning: {e}", file=sys.stderr)

        page.wait_for_timeout(wait_ms)
        # networkidle often never settles on sites with keep-alive sockets; cap it.
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
        except Exception:  # noqa: BLE001 - expected on long-polling / streaming pages
            pass

        if do_scroll:
            for _ in range(scroll_steps):
                try:
                    page.evaluate("window.scrollBy(0, document.documentElement.clientHeight)")
                except Exception:  # noqa: BLE001 - page may navigate away mid-scroll
                    break
                page.wait_for_timeout(max(400, wait_ms // scroll_steps))

        browser.close()

    return final_url, [entries_by_req[r] for r in order]


def summarize(entries):
    by_type = {}
    for e in entries:
        by_type[e["resource_type"]] = by_type.get(e["resource_type"], 0) + 1
    xhr_fetch = sum(1 for e in entries if e["resource_type"] in ("xhr", "fetch"))
    failed = sum(1 for e in entries if e.get("failed"))
    return {"total": len(entries), "by_type": by_type, "xhr_fetch": xhr_fetch, "failed": failed}


def main():
    ap = argparse.ArgumentParser(description="Record the network traffic a page makes.")
    ap.add_argument("--url", required=True, help="Target URL (its JS WILL run -- authorized targets only).")
    ap.add_argument("--out", help="Write JSON here instead of stdout.")
    ap.add_argument("--wait-ms", type=int, default=4000, help="Settle time after load (default 4000).")
    ap.add_argument("--timeout", type=int, default=30000, help="Navigation timeout ms (default 30000).")
    ap.add_argument("--scroll", action="store_true", help="Step down the page to drive scroll-bound loads.")
    ap.add_argument("--scroll-steps", type=int, default=8)
    ap.add_argument("--filter", choices=["all", "xhr-fetch"], default="all",
                    help="Keep all requests, or only the API (xhr/fetch) calls.")
    ap.add_argument("--bodies", action="store_true",
                    help="Also capture small JSON/text response bodies (authorized targets only).")
    ap.add_argument("--max-body", type=int, default=4096)
    ap.add_argument("--max-post", type=int, default=2048)
    args = ap.parse_args()

    try:
        final_url, entries = capture(
            args.url, args.wait_ms, args.timeout, args.scroll, args.scroll_steps,
            args.bodies, args.max_body, args.max_post,
        )
    except Exception as e:  # noqa: BLE001 - surface launch/runtime errors with a hint
        print(f"Capture failed: {e}", file=sys.stderr)
        print("If Chromium is missing, the browser skill installs it: "
              "python skills/browser/scripts/browser.py status", file=sys.stderr)
        print(SANDBOX_HINT, file=sys.stderr)
        sys.exit(1)

    if args.filter == "xhr-fetch":
        entries = [e for e in entries if e["resource_type"] in ("xhr", "fetch")]

    result = {"url": args.url, "final_url": final_url, "summary": summarize(entries), "entries": entries}
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {args.out} -- {result['summary']}")
    else:
        print(text)


if __name__ == "__main__":
    main()
