#!/usr/bin/env python3
"""
Browser Control Script v2

Persistent headless browser with accessibility snapshots and ref-based interactions.
Inspired by OpenClaw's browser architecture.

Architecture:
  - A daemon process keeps Chromium running and listens on a Unix socket
  - CLI commands are sent to the daemon via the socket
  - If daemon isn't running, commands auto-start it
  - Accessibility snapshots provide structured element refs (e1, e2, e3...)
  - All interactions can use refs instead of CSS selectors

Commands:
    python browser.py start [url]                     -- Start daemon (optional: navigate to url)
    python browser.py stop                             -- Stop daemon
    python browser.py status                           -- Check daemon status
    python browser.py goto <url>                       -- Navigate to URL
    python browser.py snapshot [--interactive]         -- Get accessibility snapshot with refs
    python browser.py screenshot <output> [--full-page] -- Take screenshot
    python browser.py click <ref_or_selector>          -- Click element by ref (e5) or selector
    python browser.py fill <ref_or_selector> <value>   -- Fill input
    python browser.py type <text>                      -- Type text via keyboard
    python browser.py key <key>                        -- Press key (Enter, Tab, etc.)
    python browser.py hover <ref_or_selector>          -- Hover element
    python browser.py select <ref_or_selector> <value> -- Select dropdown option
    python browser.py extract [--selector css]         -- Extract page text/html
    python browser.py eval <javascript>                -- Execute JavaScript
    python browser.py tabs                             -- List open tabs
    python browser.py tab <index>                      -- Switch to tab
    python browser.py newtab [url]                     -- Open new tab
    python browser.py closetab [index]                 -- Close tab
    python browser.py scroll <direction> [amount]      -- Scroll up/down/left/right
    python browser.py wait <selector_or_ms>            -- Wait for element or milliseconds
    python browser.py back                             -- Navigate back
    python browser.py forward                          -- Navigate forward
    python browser.py cookies [--clear]                -- Get or clear cookies
    python browser.py pdf <output>                     -- Save page as PDF

    Legacy (backwards compatible):
    python browser.py screenshot <url> <output> [opts] -- Old-style screenshot
    python browser.py extract <url> [opts]             -- Old-style extract
    python browser.py click <url> <selector>           -- Old-style click
    python browser.py fill <url> --field sel=val       -- Old-style fill
    python browser.py eval <url> <js>                  -- Old-style eval
    python browser.py session <url>                    -- Old-style session start
"""

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOCKET_PATH = "/tmp/browser_daemon.sock"
PID_FILE = "/tmp/browser_daemon.pid"
STATE_FILE = "/tmp/browser_daemon_state.json"
STORAGE_FILE = "/tmp/browser_storage.json"
REFS_FILE = "/tmp/browser_refs.json"

MAX_TABS = 5  # Hard limit to prevent RAM exhaustion on 16GB machine

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "checkbox", "radio", "combobox", "listbox",
    "menuitem", "menuitemcheckbox", "menuitemradio", "option", "searchbox",
    "slider", "spinbutton", "switch", "tab", "treeitem",
}

# ---------------------------------------------------------------------------
# Ref system -- parse aria snapshots into numbered refs
# ---------------------------------------------------------------------------

def parse_aria_snapshot(snapshot_text: str, interactive_only: bool = False):
    """Parse Playwright's aria snapshot format into structured refs.

    Returns:
        (formatted_text, refs_dict)
        formatted_text: human-readable snapshot with ref labels
        refs_dict: {ref_id: {role, name, level, ...}}
    """
    refs = {}
    lines = snapshot_text.strip().split("\n")
    output_lines = []
    ref_counter = 0

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Parse role and name: "- role "name"" or "- role "name" [attr]"
        m = re.match(r'^- (\w+)(?: "([^"]*)")?(.*)$', stripped)
        if not m:
            # Text content line or other
            if stripped.startswith("- "):
                output_lines.append(line)
            else:
                output_lines.append(line)
            continue

        role = m.group(1)
        name = m.group(2) or ""
        rest = m.group(3).strip()

        # Parse attributes like [checked], [disabled], [level=2], etc.
        attrs = {}
        for attr_match in re.finditer(r'\[(\w+)(?:=([^\]]+))?\]', rest):
            attrs[attr_match.group(1)] = attr_match.group(2) or True

        is_interactive = role in INTERACTIVE_ROLES

        if interactive_only and not is_interactive:
            continue

        ref_counter += 1
        ref_id = f"e{ref_counter}"

        refs[ref_id] = {
            "role": role,
            "name": name,
            "attrs": attrs if attrs else None,
        }

        # Build display line
        ref_label = f"[{ref_id}]" if is_interactive else f"({ref_id})"
        display_name = f' "{name}"' if name else ""
        attr_str = ""
        if attrs:
            attr_str = " " + " ".join(
                f"[{k}={v}]" if v is not True else f"[{k}]"
                for k, v in attrs.items()
            )
        prefix = " " * indent
        output_lines.append(f"{prefix}{ref_label} {role}{display_name}{attr_str}")

    return "\n".join(output_lines), refs


def resolve_ref(ref_or_selector: str, refs: dict) -> str:
    """Convert ref like 'e5' to a Playwright selector, or pass through CSS selectors."""
    if re.match(r'^e\d+$', ref_or_selector):
        ref_data = refs.get(ref_or_selector)
        if not ref_data:
            raise ValueError(f"Unknown ref: {ref_or_selector}. Run 'snapshot' first.")
        role = ref_data["role"]
        name = ref_data.get("name", "")
        if name:
            return f'role={role}[name="{name}"]'
        else:
            return f'role={role}'
    # Text selector
    if ref_or_selector.startswith("text="):
        return ref_or_selector
    # Role selector
    if ref_or_selector.startswith("role="):
        return ref_or_selector
    # CSS selector
    return ref_or_selector


# ---------------------------------------------------------------------------
# Daemon server
# ---------------------------------------------------------------------------

class BrowserDaemon:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.contexts = {}   # name -> BrowserContext
        self.pages = {}      # tab_index -> Page
        self.active_tab = 0
        self.refs = {}       # current ref map
        self.running = False

    async def start(self, url: str = None):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)

        # Load storage state if available
        storage_state = None
        if Path(STORAGE_FILE).exists():
            try:
                storage_state = json.loads(Path(STORAGE_FILE).read_text())
            except Exception:
                pass

        ctx = await self.browser.new_context(
            viewport={"width": 1280, "height": 720},
            storage_state=storage_state,
        )
        self.contexts["default"] = ctx

        page = await ctx.new_page()
        self.pages[0] = page
        self.active_tab = 0
        self.running = True

        if url:
            await self._navigate(page, url)

        self._save_state()

    async def _navigate(self, page, url: str):
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                return {"error": str(e)}
        await asyncio.sleep(0.5)
        return {"url": page.url, "title": await page.title()}

    def _get_page(self):
        page = self.pages.get(self.active_tab)
        if not page or page.is_closed():
            raise RuntimeError(f"No active page (tab {self.active_tab})")
        return page

    def _save_state(self):
        state = {
            "active_tab": self.active_tab,
            "tabs": {},
        }
        for idx, page in self.pages.items():
            if not page.is_closed():
                state["tabs"][str(idx)] = {"url": page.url}
        Path(STATE_FILE).write_text(json.dumps(state, indent=2))

    def _save_refs(self):
        Path(REFS_FILE).write_text(json.dumps(self.refs, indent=2))

    def _load_refs(self):
        if Path(REFS_FILE).exists():
            try:
                self.refs = json.loads(Path(REFS_FILE).read_text())
            except Exception:
                self.refs = {}

    async def _save_storage(self):
        ctx = self.contexts.get("default")
        if ctx:
            try:
                storage = await ctx.storage_state()
                Path(STORAGE_FILE).write_text(json.dumps(storage, indent=2))
            except Exception:
                pass

    async def handle_command(self, cmd: dict) -> dict:
        """Handle a command from the client."""
        action = cmd.get("action", "")
        try:
            handler = getattr(self, f"cmd_{action}", None)
            if not handler:
                return {"error": f"Unknown action: {action}"}
            result = await handler(cmd)
            self._save_state()
            return result or {"ok": True}
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    # --- Command handlers ---

    async def cmd_goto(self, cmd):
        url = cmd.get("url", "")
        if not url:
            return {"error": "URL required"}
        page = self._get_page()
        result = await self._navigate(page, url)
        await self._save_storage()
        return result

    async def cmd_snapshot(self, cmd):
        page = self._get_page()
        interactive_only = cmd.get("interactive", False)

        # Use Playwright's aria_snapshot on the root locator
        try:
            raw = await page.locator(":root").aria_snapshot()
        except Exception as e:
            return {"error": f"Snapshot failed: {e}"}

        formatted, refs = parse_aria_snapshot(raw, interactive_only=interactive_only)
        self.refs = refs
        self._save_refs()

        return {
            "snapshot": formatted,
            "url": page.url,
            "title": await page.title(),
            "stats": {
                "total_refs": len(refs),
                "interactive": sum(1 for r in refs.values() if r["role"] in INTERACTIVE_ROLES),
            },
        }

    async def cmd_screenshot(self, cmd):
        page = self._get_page()
        output = cmd.get("output", "/tmp/browser_screenshot.png")
        full_page = cmd.get("full_page", False)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=output, full_page=full_page)
        return {"path": output}

    async def cmd_click(self, cmd):
        page = self._get_page()
        target = cmd.get("target", "")
        if not target:
            return {"error": "Target required (ref like e5 or CSS selector)"}
        self._load_refs()
        selector = resolve_ref(target, self.refs)
        await page.click(selector, timeout=10000)
        await asyncio.sleep(0.5)
        await self._save_storage()
        return {"clicked": target, "url": page.url}

    async def cmd_fill(self, cmd):
        page = self._get_page()
        target = cmd.get("target", "")
        value = cmd.get("value", "")
        if not target:
            return {"error": "Target required"}
        self._load_refs()
        selector = resolve_ref(target, self.refs)
        await page.fill(selector, value, timeout=10000)
        await self._save_storage()
        return {"filled": target, "value": value}

    async def cmd_type(self, cmd):
        page = self._get_page()
        text = cmd.get("text", "")
        delay = cmd.get("delay", 50)
        await page.keyboard.type(text, delay=delay)
        return {"typed": text}

    async def cmd_key(self, cmd):
        page = self._get_page()
        key = cmd.get("key", "")
        if not key:
            return {"error": "Key required"}
        await page.keyboard.press(key)
        return {"pressed": key}

    async def cmd_hover(self, cmd):
        page = self._get_page()
        target = cmd.get("target", "")
        if not target:
            return {"error": "Target required"}
        self._load_refs()
        selector = resolve_ref(target, self.refs)
        await page.hover(selector, timeout=10000)
        return {"hovered": target}

    async def cmd_select(self, cmd):
        page = self._get_page()
        target = cmd.get("target", "")
        value = cmd.get("value", "")
        if not target:
            return {"error": "Target required"}
        self._load_refs()
        selector = resolve_ref(target, self.refs)
        await page.select_option(selector, value, timeout=10000)
        await self._save_storage()
        return {"selected": value, "in": target}

    async def cmd_extract(self, cmd):
        page = self._get_page()
        selector = cmd.get("selector")
        fmt = cmd.get("format", "text")

        if selector:
            elements = await page.query_selector_all(selector)
            if fmt == "html":
                results = [await el.inner_html() for el in elements]
            else:
                results = [await el.inner_text() for el in elements]
            content = "\n\n".join(results)
        else:
            if fmt == "html":
                content = await page.content()
            else:
                content = await page.inner_text("body")

        return {"content": content, "url": page.url}

    async def cmd_eval(self, cmd):
        page = self._get_page()
        js = cmd.get("javascript", "")
        if not js:
            return {"error": "JavaScript code required"}
        result = await page.evaluate(js)
        return {"result": result}

    async def cmd_tabs(self, cmd):
        tabs = []
        for idx, page in sorted(self.pages.items()):
            if not page.is_closed():
                tabs.append({
                    "index": idx,
                    "url": page.url,
                    "title": await page.title(),
                    "active": idx == self.active_tab,
                })
        return {"tabs": tabs}

    async def cmd_tab(self, cmd):
        idx = cmd.get("index", 0)
        if idx not in self.pages or self.pages[idx].is_closed():
            return {"error": f"Tab {idx} not found"}
        self.active_tab = idx
        page = self.pages[idx]
        return {"active_tab": idx, "url": page.url, "title": await page.title()}

    async def cmd_newtab(self, cmd):
        # Count open (non-closed) tabs
        open_tabs = sum(1 for p in self.pages.values() if not p.is_closed())
        if open_tabs >= MAX_TABS:
            return {"error": f"Tab limit reached ({MAX_TABS}). Close some tabs first. Use 'tabs' to list open tabs and 'closetab <index>' to close one."}
        url = cmd.get("url", "about:blank")
        ctx = self.contexts.get("default")
        page = await ctx.new_page()
        idx = max(self.pages.keys(), default=-1) + 1
        self.pages[idx] = page
        self.active_tab = idx
        if url and url != "about:blank":
            await self._navigate(page, url)
        return {"tab": idx, "url": page.url, "open_tabs": open_tabs + 1, "max_tabs": MAX_TABS}

    async def cmd_closetab(self, cmd):
        idx = cmd.get("index", self.active_tab)
        if idx not in self.pages:
            return {"error": f"Tab {idx} not found"}
        page = self.pages.pop(idx)
        if not page.is_closed():
            await page.close()
        # Switch to another tab
        if self.pages:
            self.active_tab = min(self.pages.keys())
        return {"closed": idx, "active_tab": self.active_tab}

    async def cmd_scroll(self, cmd):
        page = self._get_page()
        direction = cmd.get("direction", "down")
        amount = cmd.get("amount", 500)
        dx, dy = 0, 0
        if direction == "down":
            dy = amount
        elif direction == "up":
            dy = -amount
        elif direction == "right":
            dx = amount
        elif direction == "left":
            dx = -amount
        await page.mouse.wheel(dx, dy)
        await asyncio.sleep(0.3)
        return {"scrolled": direction, "amount": amount}

    async def cmd_wait(self, cmd):
        page = self._get_page()
        target = cmd.get("target", "")
        if target.isdigit():
            await asyncio.sleep(int(target) / 1000)
            return {"waited_ms": int(target)}
        else:
            await page.wait_for_selector(target, timeout=30000)
            return {"found": target}

    async def cmd_back(self, cmd):
        page = self._get_page()
        await page.go_back(timeout=10000)
        await asyncio.sleep(0.5)
        return {"url": page.url}

    async def cmd_forward(self, cmd):
        page = self._get_page()
        await page.go_forward(timeout=10000)
        await asyncio.sleep(0.5)
        return {"url": page.url}

    async def cmd_cookies(self, cmd):
        ctx = self.contexts.get("default")
        if cmd.get("clear"):
            await ctx.clear_cookies()
            return {"cleared": True}
        cookies = await ctx.cookies()
        return {"cookies": cookies}

    async def cmd_pdf(self, cmd):
        page = self._get_page()
        output = cmd.get("output", "/tmp/browser_page.pdf")
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        await page.pdf(path=output)
        return {"path": output}

    async def cmd_status(self, cmd):
        page = self._get_page()
        tabs = []
        for idx, p in sorted(self.pages.items()):
            if not p.is_closed():
                tabs.append({"index": idx, "url": p.url, "active": idx == self.active_tab})
        return {
            "running": True,
            "active_tab": self.active_tab,
            "url": page.url,
            "title": await page.title(),
            "tabs": tabs,
            "refs_loaded": len(self.refs),
        }

    async def cmd_stop(self, cmd):
        await self._save_storage()
        self.running = False
        return {"stopped": True}


async def run_daemon(url: str = None):
    """Run the browser daemon server."""
    import socket as sock

    # Clean up old socket
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    daemon = BrowserDaemon()
    await daemon.start(url)

    # Write PID
    Path(PID_FILE).write_text(str(os.getpid()))

    server_socket = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
    server_socket.bind(SOCKET_PATH)
    server_socket.listen(5)
    server_socket.setblocking(False)

    loop = asyncio.get_event_loop()

    print(f"Browser daemon started (PID {os.getpid()})", file=sys.stderr)
    if url:
        print(f"Navigated to: {url}", file=sys.stderr)

    async def handle_client(conn):
        try:
            data = b""
            while True:
                chunk = await loop.sock_recv(conn, 65536)
                if not chunk:
                    break
                data += chunk
                # Protocol: JSON terminated by newline
                if b"\n" in data:
                    break

            if data:
                cmd = json.loads(data.decode().strip())
                result = await daemon.handle_command(cmd)
                response = json.dumps(result, default=str) + "\n"
                await loop.sock_sendall(conn, response.encode())
        except Exception as e:
            try:
                err = json.dumps({"error": str(e)}) + "\n"
                await loop.sock_sendall(conn, err.encode())
            except Exception:
                pass
        finally:
            conn.close()

    try:
        while daemon.running:
            try:
                conn, _ = await asyncio.wait_for(
                    loop.sock_accept(server_socket), timeout=1.0
                )
                asyncio.create_task(handle_client(conn))
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue
    finally:
        server_socket.close()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        if os.path.exists(PID_FILE):
            os.unlink(PID_FILE)
        if daemon.browser:
            await daemon.browser.close()
        if daemon.playwright:
            await daemon.playwright.stop()
        print("Browser daemon stopped", file=sys.stderr)


# ---------------------------------------------------------------------------
# Client -- sends commands to daemon
# ---------------------------------------------------------------------------

def send_command(cmd: dict, timeout: float = 30.0) -> dict:
    """Send a command to the daemon via Unix socket."""
    import socket as sock

    s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET_PATH)
        payload = json.dumps(cmd) + "\n"
        s.sendall(payload.encode())

        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

        return json.loads(data.decode().strip())
    finally:
        s.close()


def daemon_is_running() -> bool:
    """Check if daemon is running."""
    if not os.path.exists(SOCKET_PATH):
        return False
    if os.path.exists(PID_FILE):
        try:
            pid = int(Path(PID_FILE).read_text().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            # Stale PID file
            return False
    return False


def ensure_daemon(url: str = None):
    """Start daemon if not running."""
    if daemon_is_running():
        return

    # Fork a daemon process
    pid = os.fork()
    if pid == 0:
        # Child process -- become daemon
        os.setsid()
        # Close stdio
        sys.stdin.close()
        devnull = open(os.devnull, 'w')
        sys.stdout = devnull
        sys.stderr = devnull

        try:
            asyncio.run(run_daemon(url))
        except Exception:
            pass
        finally:
            os._exit(0)
    else:
        # Parent -- wait for daemon to be ready
        for _ in range(50):  # 5 seconds max
            time.sleep(0.1)
            if daemon_is_running():
                return
        print("Warning: Daemon may not have started properly", file=sys.stderr)


def stop_daemon():
    """Stop the daemon."""
    if not daemon_is_running():
        print("Daemon not running")
        return

    try:
        result = send_command({"action": "stop"})
        print("Daemon stopped")
    except Exception:
        # Force kill
        if os.path.exists(PID_FILE):
            try:
                pid = int(Path(PID_FILE).read_text().strip())
                os.kill(pid, signal.SIGTERM)
                print("Daemon killed")
            except Exception:
                pass
    # Cleanup
    for f in [SOCKET_PATH, PID_FILE]:
        if os.path.exists(f):
            os.unlink(f)


# ---------------------------------------------------------------------------
# Legacy mode -- backwards compatible with v1
# ---------------------------------------------------------------------------

def is_url(s: str) -> bool:
    """Check if string looks like a URL."""
    return bool(re.match(r'^https?://', s)) or s.startswith("file://")


async def legacy_screenshot(args):
    """Legacy: screenshot <url> <output> [opts]"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        storage_state = None
        session_file = Path(f"/tmp/browser_session_{args.session_id}.json") if args.session_id else None
        if session_file and session_file.exists():
            try:
                storage_state = json.loads(session_file.read_text())
            except Exception:
                pass

        ctx = await browser.new_context(
            viewport={"width": args.width, "height": args.height},
            storage_state=storage_state,
        )
        page = await ctx.new_page()
        try:
            await page.goto(args.url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        if args.wait > 0:
            await asyncio.sleep(args.wait / 1000)

        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=args.output, full_page=args.full_page)
        await browser.close()
        print(f"Screenshot saved: {args.output}")


async def legacy_extract(args):
    """Legacy: extract <url> [opts]"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await ctx.new_page()
        try:
            await page.goto(args.url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        if args.wait > 0:
            await asyncio.sleep(args.wait / 1000)

        if args.selector:
            elements = await page.query_selector_all(args.selector)
            if args.format == "html":
                results = [await el.inner_html() for el in elements]
            else:
                results = [await el.inner_text() for el in elements]
            content = "\n\n".join(results)
        else:
            if args.format == "html":
                content = await page.content()
            else:
                content = await page.inner_text("body")
        await browser.close()
        print(content)


async def legacy_click(args):
    """Legacy: click <url> <selector>"""
    from playwright.async_api import async_playwright

    session_file = Path(f"/tmp/browser_session_{args.session_id}.json") if args.session_id else None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        storage_state = None
        if session_file and session_file.exists():
            try:
                storage_state = json.loads(session_file.read_text())
            except Exception:
                pass
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            storage_state=storage_state,
        )
        page = await ctx.new_page()
        try:
            await page.goto(args.url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1)

        await page.click(args.selector, timeout=10000)
        await asyncio.sleep(1)

        if session_file:
            storage = await ctx.storage_state()
            session_file.write_text(json.dumps(storage, indent=2))

        print(f"Clicked: {args.selector}")
        print(f"Current URL: {page.url}")

        if args.screenshot:
            await page.screenshot(path=args.screenshot)
            print(f"Screenshot: {args.screenshot}")
        await browser.close()


async def legacy_fill(args):
    """Legacy: fill <url> --field sel=val"""
    from playwright.async_api import async_playwright

    session_file = Path(f"/tmp/browser_session_{args.session_id}.json") if args.session_id else None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        storage_state = None
        if session_file and session_file.exists():
            try:
                storage_state = json.loads(session_file.read_text())
            except Exception:
                pass
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            storage_state=storage_state,
        )
        page = await ctx.new_page()
        try:
            await page.goto(args.url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1)

        for field_spec in args.field:
            if "=" not in field_spec:
                print(f"Invalid field format: {field_spec}", file=sys.stderr)
                continue
            selector, value = field_spec.split("=", 1)
            await page.fill(selector, value, timeout=10000)
            print(f"Filled: {selector}")

        if session_file:
            storage = await ctx.storage_state()
            session_file.write_text(json.dumps(storage, indent=2))

        if args.submit:
            await page.click(args.submit, timeout=10000)
            await asyncio.sleep(2)
            print(f"Submitted via: {args.submit}")
            print(f"Current URL: {page.url}")

        if args.screenshot:
            await page.screenshot(path=args.screenshot)
            print(f"Screenshot: {args.screenshot}")
        await browser.close()


async def legacy_eval(args):
    """Legacy: eval <url> <javascript>"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await ctx.new_page()
        try:
            await page.goto(args.url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1)
        result = await page.evaluate(args.javascript)
        print(json.dumps(result, indent=2, default=str))
        await browser.close()


async def legacy_session(args):
    """Legacy: session <url>"""
    from playwright.async_api import async_playwright

    session_file = Path(f"/tmp/browser_session_{args.session_id}.json") if args.session_id else None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        storage_state = None
        if session_file and session_file.exists():
            try:
                storage_state = json.loads(session_file.read_text())
            except Exception:
                pass
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            storage_state=storage_state,
        )
        page = await ctx.new_page()
        try:
            await page.goto(args.url, wait_until="networkidle", timeout=30000)
        except Exception:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1)

        if session_file:
            storage = await ctx.storage_state()
            session_file.write_text(json.dumps(storage, indent=2))

        print(f"Session started: {args.url}")
        print(f"Session file: {session_file}")
        print(f"Current URL: {page.url}")
        print(f"Title: {await page.title()}")
        await browser.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Browser control v2 -- persistent sessions with accessibility snapshots"
    )
    parser.add_argument("--session-id", help="Session ID (legacy compat)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- New v2 commands ---

    # start
    p_start = subparsers.add_parser("start", help="Start browser daemon")
    p_start.add_argument("url", nargs="?", help="Initial URL")

    # stop
    subparsers.add_parser("stop", help="Stop browser daemon")

    # status
    subparsers.add_parser("status", help="Daemon status")

    # goto
    p_goto = subparsers.add_parser("goto", help="Navigate to URL")
    p_goto.add_argument("url", help="URL to navigate to")

    # snapshot
    p_snap = subparsers.add_parser("snapshot", help="Get accessibility snapshot with refs")
    p_snap.add_argument("--interactive", action="store_true", help="Only show interactive elements")

    # screenshot (v2 mode: no URL needed if daemon running)
    p_ss = subparsers.add_parser("screenshot", help="Take screenshot")
    p_ss.add_argument("url_or_output", help="Output path (v2) or URL (legacy)")
    p_ss.add_argument("output", nargs="?", help="Output path (legacy mode)")
    p_ss.add_argument("--full-page", action="store_true")
    p_ss.add_argument("--width", type=int, default=1280)
    p_ss.add_argument("--height", type=int, default=720)
    p_ss.add_argument("--wait", type=int, default=1000)

    # click
    p_click = subparsers.add_parser("click", help="Click element")
    p_click.add_argument("target", help="Ref (e5) or selector or URL (legacy)")
    p_click.add_argument("selector", nargs="?", help="Selector (legacy mode)")
    p_click.add_argument("--screenshot", help="Screenshot after click")

    # fill
    p_fill = subparsers.add_parser("fill", help="Fill input field")
    p_fill.add_argument("target", help="Ref (e5) or selector or URL (legacy)")
    p_fill.add_argument("value", nargs="?", help="Value to fill (v2) or omit for legacy")
    p_fill.add_argument("--field", action="append", help="Legacy: selector=value")
    p_fill.add_argument("--submit", help="Legacy: submit button selector")
    p_fill.add_argument("--screenshot", help="Screenshot after fill")

    # type
    p_type = subparsers.add_parser("type", help="Type text via keyboard")
    p_type.add_argument("text", help="Text to type")
    p_type.add_argument("--delay", type=int, default=50, help="Delay between keys (ms)")

    # key
    p_key = subparsers.add_parser("key", help="Press a key")
    p_key.add_argument("key", help="Key to press (Enter, Tab, Escape, etc.)")

    # hover
    p_hover = subparsers.add_parser("hover", help="Hover element")
    p_hover.add_argument("target", help="Ref or selector")

    # select
    p_sel = subparsers.add_parser("select", help="Select dropdown option")
    p_sel.add_argument("target", help="Ref or selector")
    p_sel.add_argument("value", help="Option value")

    # extract
    p_ext = subparsers.add_parser("extract", help="Extract page content")
    p_ext.add_argument("url", nargs="?", help="URL (legacy) or omit for daemon")
    p_ext.add_argument("--format", choices=["text", "markdown", "html"], default="text")
    p_ext.add_argument("--selector", help="CSS selector")
    p_ext.add_argument("--wait", type=int, default=1000)

    # eval
    p_eval = subparsers.add_parser("eval", help="Execute JavaScript")
    p_eval.add_argument("url_or_js", help="URL (legacy) or JS code (v2)")
    p_eval.add_argument("javascript", nargs="?", help="JS code (legacy mode)")

    # tabs
    subparsers.add_parser("tabs", help="List tabs")

    # tab
    p_tab = subparsers.add_parser("tab", help="Switch to tab")
    p_tab.add_argument("index", type=int, help="Tab index")

    # newtab
    p_nt = subparsers.add_parser("newtab", help="Open new tab")
    p_nt.add_argument("url", nargs="?", default="about:blank", help="URL")

    # closetab
    p_ct = subparsers.add_parser("closetab", help="Close tab")
    p_ct.add_argument("index", nargs="?", type=int, help="Tab index (default: active)")

    # scroll
    p_scroll = subparsers.add_parser("scroll", help="Scroll page")
    p_scroll.add_argument("direction", choices=["up", "down", "left", "right"])
    p_scroll.add_argument("amount", nargs="?", type=int, default=500, help="Pixels")

    # wait
    p_wait = subparsers.add_parser("wait", help="Wait for element or time")
    p_wait.add_argument("target", help="CSS selector or milliseconds")

    # back
    subparsers.add_parser("back", help="Navigate back")

    # forward
    subparsers.add_parser("forward", help="Navigate forward")

    # cookies
    p_cookies = subparsers.add_parser("cookies", help="Get/clear cookies")
    p_cookies.add_argument("--clear", action="store_true", help="Clear all cookies")

    # pdf
    p_pdf = subparsers.add_parser("pdf", help="Save page as PDF")
    p_pdf.add_argument("output", help="Output path")

    # session (legacy)
    p_session = subparsers.add_parser("session", help="Start session (legacy)")
    p_session.add_argument("url", help="URL")

    args = parser.parse_args()
    cmd = args.command

    # --- Detect legacy vs v2 mode ---

    # Legacy screenshot: screenshot <url> <output>
    if cmd == "screenshot" and args.output and is_url(args.url_or_output):
        args.url = args.url_or_output
        args.output = args.output
        asyncio.run(legacy_screenshot(args))
        return

    # Legacy click: click <url> <selector>
    if cmd == "click" and args.selector and is_url(args.target):
        args.url = args.target
        args.selector = args.selector
        asyncio.run(legacy_click(args))
        return

    # Legacy fill: fill <url> --field ...
    if cmd == "fill" and args.field and is_url(args.target):
        args.url = args.target
        asyncio.run(legacy_fill(args))
        return

    # Legacy extract: extract <url>
    if cmd == "extract" and args.url and is_url(args.url):
        asyncio.run(legacy_extract(args))
        return

    # Legacy eval: eval <url> <js>
    if cmd == "eval" and args.javascript and is_url(args.url_or_js):
        args.url = args.url_or_js
        args.javascript = args.javascript
        asyncio.run(legacy_eval(args))
        return

    # Legacy session
    if cmd == "session":
        asyncio.run(legacy_session(args))
        return

    # --- V2 daemon commands ---

    if cmd == "start":
        if daemon_is_running():
            print("Daemon already running")
            result = send_command({"action": "status"})
            print(json.dumps(result, indent=2, default=str))
        else:
            ensure_daemon(args.url)
            # Show status
            time.sleep(0.5)
            if daemon_is_running():
                result = send_command({"action": "status"})
                print(json.dumps(result, indent=2, default=str))
            else:
                print("Daemon started")
        return

    if cmd == "stop":
        stop_daemon()
        return

    if cmd == "status":
        if not daemon_is_running():
            print(json.dumps({"running": False}))
            return
        result = send_command({"action": "status"})
        print(json.dumps(result, indent=2, default=str))
        return

    # For all other v2 commands, ensure daemon is running
    if not daemon_is_running():
        # Auto-start daemon
        url = None
        if cmd == "goto":
            url = args.url
        ensure_daemon(url)
        if cmd == "goto":
            # Already navigated during startup
            result = send_command({"action": "status"})
            print(json.dumps(result, indent=2, default=str))
            return

    # Build command dict
    command_map = {
        "goto": lambda: {"action": "goto", "url": args.url},
        "snapshot": lambda: {"action": "snapshot", "interactive": args.interactive},
        "screenshot": lambda: {"action": "screenshot", "output": args.url_or_output, "full_page": args.full_page},
        "click": lambda: {"action": "click", "target": args.target},
        "fill": lambda: {"action": "fill", "target": args.target, "value": args.value or ""},
        "type": lambda: {"action": "type", "text": args.text, "delay": args.delay},
        "key": lambda: {"action": "key", "key": args.key},
        "hover": lambda: {"action": "hover", "target": args.target},
        "select": lambda: {"action": "select", "target": args.target, "value": args.value},
        "extract": lambda: {"action": "extract", "selector": getattr(args, "selector", None), "format": getattr(args, "format", "text")},
        "eval": lambda: {"action": "eval", "javascript": args.url_or_js},
        "tabs": lambda: {"action": "tabs"},
        "tab": lambda: {"action": "tab", "index": args.index},
        "newtab": lambda: {"action": "newtab", "url": args.url},
        "closetab": lambda: {"action": "closetab", "index": getattr(args, "index", None)},
        "scroll": lambda: {"action": "scroll", "direction": args.direction, "amount": args.amount},
        "wait": lambda: {"action": "wait", "target": args.target},
        "back": lambda: {"action": "back"},
        "forward": lambda: {"action": "forward"},
        "cookies": lambda: {"action": "cookies", "clear": args.clear},
        "pdf": lambda: {"action": "pdf", "output": args.output},
    }

    builder = command_map.get(cmd)
    if not builder:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)

    payload = builder()

    # Special: for extract in v2 mode, print content directly
    result = send_command(payload, timeout=60.0)

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    # Format output based on command
    if cmd == "snapshot":
        print(result.get("snapshot", ""))
        stats = result.get("stats", {})
        print(f"\n--- {stats.get('total_refs', 0)} elements, {stats.get('interactive', 0)} interactive ---", file=sys.stderr)
        print(f"URL: {result.get('url', '')}", file=sys.stderr)
    elif cmd == "extract":
        print(result.get("content", ""))
    elif cmd == "eval":
        print(json.dumps(result.get("result"), indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
