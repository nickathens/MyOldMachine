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

## Notes

- Uses Playwright with Chromium
- Daemon runs headless, listens on Unix socket `/tmp/browser_daemon.sock`
- Auto-starts daemon if not running when using any command
- Close tabs when done with a task to keep things clean
