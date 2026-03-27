# Email (Gmail)

Send, read, search, and draft emails via Gmail API.

## Setup

Requires Google OAuth credentials. Place `google_credentials.json` in the bot root directory.
First run will authenticate and create `gmail_token.json`.

Get credentials from: [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
- Create OAuth 2.0 Client ID (Desktop application)
- Enable Gmail API

### Headless / Remote Setup

If accessing the machine via SSH (no browser available):
1. Connect with SSH port forwarding: `ssh -L 8085:localhost:8085 user@machine`
2. Run the auth command — it will start a local server and print an authorization URL
3. Visit the printed URL in your local browser
4. After authorizing, the redirect goes to localhost:8085 — port forwarding routes it to the machine
5. Once complete, `gmail_token.json` is saved and future runs don't need a browser
6. Shares `google_credentials.json` with the calendar skill (but separate tokens/scopes)

## Commands

```bash
# Check inbox (recent 10 emails)
python skills/email/scripts/gmail.py inbox
python skills/email/scripts/gmail.py inbox --limit 20

# Check sent emails
python skills/email/scripts/gmail.py sent
python skills/email/scripts/gmail.py sent --limit 20

# Read a specific email
python skills/email/scripts/gmail.py read <message_id>

# Search emails (uses Gmail search syntax)
python skills/email/scripts/gmail.py search "from:someone@example.com"
python skills/email/scripts/gmail.py search "subject:invoice"
python skills/email/scripts/gmail.py search "in:sent after:2025/12/01"

# Create a draft (saves to Gmail drafts, does NOT send)
python skills/email/scripts/gmail.py draft "recipient@email.com" "Subject" "Body text"

# List drafts
python skills/email/scripts/gmail.py drafts

# Send an email
python skills/email/scripts/gmail.py send "recipient@email.com" "Subject" "Body"
```

## Examples

"Check my inbox"
"Find emails about the project deadline"
"Draft an email to john@example.com about the meeting"
"Read the latest email from Sarah"
"Search for emails with attachments from last month"

## Notes

- Message IDs are shown in brackets like `[abc123def456]`
- Search uses Gmail's search syntax (from:, to:, subject:, in:sent, after:, before:, etc.)
- Email bodies are limited to 5000 characters when reading
- Shares Google credentials with the calendar skill
