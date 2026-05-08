# Clipboard

Read and write the system clipboard. Bridges Telegram and the desktop: copy from phone, paste on machine, or grab whatever was last copied on the machine.

## Quick reference

```bash
C="python $SKILL_DIR/scripts/clipboard.py"

$C read                       # Print clipboard to stdout
$C read --truncate            # Truncate at 50KB (safe for Telegram)
$C write "hello world"        # Set clipboard from argument
echo "from pipeline" | $C write -   # Read stdin and set clipboard
$C clear                      # Clear clipboard
```

## Common flows

### Phone -> machine (paste a snippet from Telegram into a desktop app)
User sends "put this on my clipboard: <text>". Call:
```bash
$C write "<text>"
```
They paste it anywhere in any desktop app.

### Machine -> phone (read what's currently on the clipboard)
User asks "what's on my clipboard?". Call:
```bash
$C read --truncate
```
Then return the result in the normal text response.

### Pipeline glue
Generate something and stage it on the clipboard for the next paste:
```bash
python -c "import secrets; print(secrets.token_urlsafe(24))" | $C write -
```

## Constraints

- 50KB cap on writes. For larger blobs use a file + `send_to_telegram --document`.
- **Linux:** requires `DISPLAY`. The script defaults to `:0` if unset, which matches the user's graphical session on a single-user machine. In multi-user mode, only the slot user whose X session is active will see clipboard writes -- this skill is realistically admin-only.
- **macOS / Windows:** `pbcopy`/`pbpaste` and WinAPI handle it transparently via pyperclip. No DISPLAY plumbing needed.

## Notes

- Empty clipboard is normal; `read` returns an empty string with no error.
- `write` echoes a confirmation including character count -- always check it matches expectation.
- Treat clipboard as a side-channel, not as durable storage. Anything that runs on the desktop can read it.
