# Browser Control

Persistent headless browser with accessibility snapshots and ref-based interactions.
Keeps Chromium running as a daemon between commands -- cookies, tabs, and state persist.

## Quick Reference

```bash
B="python $SKILL_DIR/scripts/browser.py"
```

### Recommended workflow (persistent daemon)

```bash
$B goto https://example.com           # Navigate (auto-starts daemon)
$B snapshot                            # Get page structure with element refs
$B snapshot --interactive              # Only interactive elements (buttons, links, inputs)
$B click e5                            # Click element by ref
$B fill e3 "search query"             # Fill input by ref
$B key Enter                           # Press key
$B screenshot /tmp/shot.png            # Screenshot current page
$B extract                             # Get page text
```

### Daemon lifecycle

```bash
$B start [url]                         # Start daemon (optional: navigate to URL)
$B stop                                # Stop daemon (saves cookies)
$B status                              # Check daemon status
```

## Accessibility Snapshots

The `snapshot` command returns the page's accessibility tree with numbered refs.
Interactive elements get `[eN]` labels, structural elements get `(eN)`.

```
(e1) document
  (e2) heading "Example Domain" [level=1]
  (e3) paragraph
  (e4) paragraph
    [e5] link "Learn more"
```

Use these refs with `click`, `fill`, `hover`, `select` instead of guessing CSS selectors.
Refs persist until the next `snapshot` call.

Use `--interactive` to filter to only buttons, links, inputs, etc.

## Navigation

```bash
$B goto <url>                          # Navigate to URL
$B back                                # Go back
$B forward                             # Go forward
$B scroll down [500]                   # Scroll (up/down/left/right, default 500px)
$B wait <selector_or_ms>               # Wait for element or milliseconds
```

## Interactions

```bash
$B click <ref_or_selector>             # Click (e.g. e5, "text=Login", "#btn")
$B fill <ref_or_selector> <value>      # Fill input field
$B type <text>                         # Type via keyboard (for non-input areas)
$B key <key>                           # Press key (Enter, Tab, Escape, ArrowDown, etc.)
$B hover <ref_or_selector>             # Hover element
$B select <ref_or_selector> <value>    # Select dropdown option
```

## Tabs

```bash
$B tabs                                # List all tabs
$B tab <index>                         # Switch to tab
$B newtab [url]                        # Open new tab
$B closetab [index]                    # Close tab (default: active)
```

## Content Extraction

```bash
$B extract                             # Get page text
$B extract --format html               # Get HTML
$B extract --selector "article"        # Extract specific elements
$B eval "document.title"               # Run JavaScript
```

## Output

```bash
$B screenshot <output.png>             # Screenshot (--full-page for entire page)
$B pdf <output.pdf>                    # Save as PDF
```

## Cookies & Storage

```bash
$B cookies                             # List cookies
$B cookies --clear                     # Clear cookies
```

Cookies and storage persist across commands automatically.
Saved to `/tmp/browser_storage.json` on daemon stop.

## Multi-step Example: Login Flow

```bash
$B goto https://site.com/login
$B snapshot --interactive
# Output shows: [e3] textbox "Email", [e5] textbox "Password", [e7] button "Sign in"
$B fill e3 "user@example.com"
$B fill e5 "password123"
$B click e7
$B snapshot                            # See logged-in page
$B screenshot /tmp/logged_in.png
```

## Legacy Commands (backwards compatible)

Old v1 syntax still works:

```bash
$B screenshot <url> <output.png> [--full-page] [--width 1280] [--height 720] [--wait 1000]
$B extract <url> [--format text] [--selector css]
$B click <url> <selector>
$B fill <url> --field "sel=val" [--field ...]
$B eval <url> "javascript"
$B session <url>
```

Legacy mode is detected automatically when the first argument is a URL.
Legacy commands open a fresh browser each time (no daemon).

## Web App Verification (assertions)

After rendering or deploying a site, verify it actually works before declaring done. The `browser_assert.py` script runs a list of assertions against a URL and exits non-zero on failure. Use this in two flows:

**1. Quick CLI mode (a few checks):**

```bash
A="python $SKILL_DIR/scripts/browser_assert.py"

$A --url https://example.com/treatment \
   --http-status 200 \
   --no-console-errors \
   --no-failed-requests \
   --selector-exists "section.hero" \
   --selector-exists ".gallery img" \
   --no-text "undefined" \
   --no-text "Lorem ipsum"
```

Exit code 0 = all pass. Exit code 1 = at least one failed (with PASS/FAIL lines on stdout). Exit code 2 = misuse.

**2. Manifest mode (rich check sets, version-controlled per project):**

`treatment.assert.yaml`:

```yaml
url: https://example.com/treatment
timeout: 30
viewport: [1920, 1080]
checks:
  - http_status: 200
  - no_console_errors: true
  - no_failed_requests: true
  - selector_exists: "section.hero"
  - selector_count: ["section[data-anim]", 12]
  - selector_text_contains: ["h1", "Treatment"]
  - no_text: "undefined"
  - no_text: "Lorem ipsum"
  - eval_truthy: "document.fonts.check('1em Montserrat')"
  - max_load_time_ms: 5000
```

Run:
```bash
$A --manifest treatment.assert.yaml
$A --manifest treatment.assert.yaml --json    # JSON output for piping
```

### Available checks

| Check | Argument shape | Asserts... |
|---|---|---|
| `http_status` | int | Top-level navigation returned this status |
| `no_console_errors` | bool | No `console.error` calls during load |
| `no_failed_requests` | bool | No 4xx/5xx responses, no failed requests |
| `selector_exists` | string | At least one element matches the CSS selector |
| `selector_count` | [selector, n] | Exactly n elements match the selector |
| `selector_text_contains` | [selector, text] | First match contains the text substring |
| `no_text` | string | The string does NOT appear in rendered HTML |
| `eval_truthy` | JS expression | `Boolean(<expr>)` evaluates to true |
| `max_load_time_ms` | int | Page load completed within this many ms |

### When to use vs. screenshot-diff

| Use `browser_assert` when... | Use `screenshot-diff` when... |
|---|---|
| You care about structural correctness (right sections, no errors, fonts loaded) | You care about visual regressions |
| The page is dynamic / animation timing makes pixel diff unreliable | Layout pixel match matters |
| You want fast assertions in a CI-like loop | You want a human-reviewable image diff |

Most presentation deliveries want both -- assertions for correctness, screenshot for "looks right."

### Convention

For repeatable verification, store the manifest in the project repo as `<name>.assert.yaml`. Run it after each render. Keep it under version control alongside the source so changes to assertions live with changes to the page.

## Notes

- Uses Playwright with Chromium
- Daemon runs headless, listens on Unix socket `/tmp/browser_daemon.sock`
- Auto-starts daemon if not running when using any command
- Close tabs when done with a task to keep things clean
- Refs file: `/tmp/browser_refs.json`
- State file: `/tmp/browser_daemon_state.json`
- PID file: `/tmp/browser_daemon.pid`
- `browser_assert.py` runs in its own ephemeral context -- does not share the daemon, does not touch persistent cookies, ideal for clean verification.
