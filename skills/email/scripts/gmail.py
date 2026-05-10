#!/usr/bin/env python3
"""
Gmail Integration - Send, read, search, and draft emails via Gmail API.

Usage:
    python gmail.py send "recipient@example.com" "Subject" "Body text"
    python gmail.py inbox [--limit 10]
    python gmail.py read <message_id>
    python gmail.py search "query"
    python gmail.py draft "recipient@example.com" "Subject" "Body text"
    python gmail.py drafts [--limit 10]
    python gmail.py sent [--limit 10]

First run will open browser for OAuth authentication.
"""

import argparse
import base64
import os
import sys
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Scopes for Gmail API
SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
]

# Paths — resolve relative to the bot root (4 levels up from this script)
BOT_DIR = Path(__file__).parent.parent.parent.parent
CREDENTIALS_FILE = BOT_DIR / "google_credentials.json"


def _resolve_token_path() -> Path:
    """Return the per-user token path.

    Each Telegram user gets their own Gmail token under their data dir, so
    one user's bot session cannot read another user's mailbox even when
    multiple users share the same OAuth client app.

    Falls back to the legacy bot-root path only when no user dir is set
    (preserves single-user installs that haven't migrated).
    """
    user_dir = os.environ.get("JARVIS_USER_DIR")
    if user_dir:
        google_dir = Path(user_dir) / "google"
        google_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(google_dir, 0o700)
        except OSError:
            pass
        return google_dir / "gmail_token.json"
    return BOT_DIR / "gmail_token.json"


TOKEN_FILE = _resolve_token_path()


def get_gmail_service():
    """Get authenticated Gmail API service."""
    creds = None

    if TOKEN_FILE.exists():
        # Load without scopes to avoid mismatch during refresh.
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"Error: {CREDENTIALS_FILE} not found", file=sys.stderr)
                print("Please set up Google Cloud credentials first.", file=sys.stderr)
                print("1. Go to https://console.cloud.google.com/apis/credentials", file=sys.stderr)
                print("2. Create OAuth 2.0 Client ID (Desktop application)", file=sys.stderr)
                print("3. Enable Gmail API", file=sys.stderr)
                print(f"4. Save the JSON file as: {CREDENTIALS_FILE}", file=sys.stderr)
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            try:
                creds = flow.run_local_server(port=8085, timeout_seconds=300)
            except Exception as exc:
                print(
                    "Error: Google sign-in did not complete within 5 minutes "
                    f"({exc.__class__.__name__}). Re-run the command to try again.",
                    file=sys.stderr,
                )
                sys.exit(2)

        with open(TOKEN_FILE, 'w', encoding='utf-8') as token:
            token.write(creds.to_json())
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass

    return build('gmail', 'v1', credentials=creds)


def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email."""
    service = get_gmail_service()

    message = MIMEText(body)
    message['to'] = to
    message['subject'] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    result = service.users().messages().send(
        userId='me',
        body={'raw': raw}
    ).execute()

    return {"id": result['id'], "status": "sent", "to": to, "subject": subject}


def get_inbox(limit: int = 10) -> list:
    """Get recent inbox messages."""
    service = get_gmail_service()

    results = service.users().messages().list(
        userId='me',
        labelIds=['INBOX'],
        maxResults=limit
    ).execute()

    messages = results.get('messages', [])
    emails = []

    for msg in messages:
        msg_data = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='metadata',
            metadataHeaders=['From', 'Subject', 'Date']
        ).execute()

        headers = {h['name']: h['value'] for h in msg_data['payload']['headers']}
        emails.append({
            "id": msg['id'],
            "from": headers.get('From', 'Unknown'),
            "subject": headers.get('Subject', '(no subject)'),
            "date": headers.get('Date', ''),
            "snippet": msg_data.get('snippet', '')[:100]
        })

    return emails


def _extract_body(payload):
    """Extract plain text body from message payload, handling nested parts."""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                if data:
                    return base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            elif part['mimeType'].startswith('multipart/'):
                result = _extract_body(part)
                if result:
                    return result
    elif 'body' in payload and 'data' in payload['body']:
        return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
    return ""


def _resolve_message_id(service, message_id: str, label: str = None) -> str:
    """Resolve a partial message ID to a full one by searching."""
    # First try using it directly
    try:
        service.users().messages().get(userId='me', id=message_id, format='minimal').execute()
        return message_id
    except Exception:
        pass

    # Search through messages to find a match
    kwargs = {'userId': 'me', 'maxResults': 200}
    if label:
        kwargs['labelIds'] = [label]

    results = service.users().messages().list(**kwargs).execute()
    for msg in results.get('messages', []):
        if msg['id'].startswith(message_id):
            return msg['id']

    return None


def read_email(message_id: str) -> dict:
    """Read full email content."""
    service = get_gmail_service()

    full_id = _resolve_message_id(service, message_id)
    if not full_id:
        return {"error": f"Message not found: {message_id}"}

    msg_data = service.users().messages().get(
        userId='me',
        id=full_id,
        format='full'
    ).execute()

    headers = {h['name']: h['value'] for h in msg_data['payload']['headers']}
    body = _extract_body(msg_data['payload'])

    return {
        "id": full_id,
        "from": headers.get('From', 'Unknown'),
        "to": headers.get('To', ''),
        "subject": headers.get('Subject', '(no subject)'),
        "date": headers.get('Date', ''),
        "body": body[:5000]
    }


def get_sent(limit: int = 10) -> list:
    """Get recent sent messages."""
    service = get_gmail_service()

    results = service.users().messages().list(
        userId='me',
        labelIds=['SENT'],
        maxResults=limit
    ).execute()

    messages = results.get('messages', [])
    emails = []

    for msg in messages:
        msg_data = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='metadata',
            metadataHeaders=['From', 'To', 'Subject', 'Date']
        ).execute()

        headers = {h['name']: h['value'] for h in msg_data['payload']['headers']}
        emails.append({
            "id": msg['id'],
            "to": headers.get('To', ''),
            "subject": headers.get('Subject', '(no subject)'),
            "date": headers.get('Date', ''),
            "snippet": msg_data.get('snippet', '')[:100]
        })

    return emails


def create_draft(to: str, subject: str, body: str) -> dict:
    """Create a draft email (does NOT send it)."""
    service = get_gmail_service()

    message = MIMEText(body)
    message['to'] = to
    message['subject'] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId='me',
        body={'message': {'raw': raw}}
    ).execute()

    return {"id": draft['id'], "status": "draft_created", "to": to, "subject": subject}


def list_drafts(limit: int = 10) -> list:
    """List draft emails."""
    service = get_gmail_service()

    results = service.users().drafts().list(userId='me', maxResults=limit).execute()
    drafts = results.get('drafts', [])
    items = []

    for d in drafts:
        draft_data = service.users().drafts().get(userId='me', id=d['id'], format='metadata').execute()
        msg = draft_data.get('message', {})
        payload = msg.get('payload') or {}
        headers = {h['name']: h['value'] for h in payload.get('headers', [])}
        items.append({
            "id": d['id'],
            "to": headers.get('To', ''),
            "subject": headers.get('Subject', '(no subject)'),
            "date": headers.get('Date', ''),
            "snippet": msg.get('snippet', '')[:100]
        })

    return items


def search_emails(query: str, limit: int = 10) -> list:
    """Search emails."""
    service = get_gmail_service()

    results = service.users().messages().list(
        userId='me',
        q=query,
        maxResults=limit
    ).execute()

    messages = results.get('messages', [])
    emails = []

    for msg in messages:
        msg_data = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='metadata',
            metadataHeaders=['From', 'Subject', 'Date']
        ).execute()

        headers = {h['name']: h['value'] for h in msg_data['payload']['headers']}
        emails.append({
            "id": msg['id'],
            "from": headers.get('From', 'Unknown'),
            "subject": headers.get('Subject', '(no subject)'),
            "date": headers.get('Date', ''),
        })

    return emails


def main():
    parser = argparse.ArgumentParser(description="Gmail CLI")
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Send command
    send_parser = subparsers.add_parser('send', help='Send an email')
    send_parser.add_argument('to', help='Recipient email')
    send_parser.add_argument('subject', help='Email subject')
    send_parser.add_argument('body', help='Email body')

    # Inbox command
    inbox_parser = subparsers.add_parser('inbox', help='List inbox messages')
    inbox_parser.add_argument('--limit', type=int, default=10, help='Number of messages')

    # Read command
    read_parser = subparsers.add_parser('read', help='Read an email')
    read_parser.add_argument('message_id', help='Message ID (or first 12 chars)')

    # Sent command
    sent_parser = subparsers.add_parser('sent', help='List sent messages')
    sent_parser.add_argument('--limit', type=int, default=10, help='Number of messages')

    # Search command
    search_parser = subparsers.add_parser('search', help='Search emails')
    search_parser.add_argument('query', help='Search query')
    search_parser.add_argument('--limit', type=int, default=10, help='Number of results')

    # Draft command
    draft_parser = subparsers.add_parser('draft', help='Create a draft email')
    draft_parser.add_argument('to', help='Recipient email')
    draft_parser.add_argument('subject', help='Email subject')
    draft_parser.add_argument('body', help='Email body')

    # List drafts command
    drafts_parser = subparsers.add_parser('drafts', help='List draft emails')
    drafts_parser.add_argument('--limit', type=int, default=10, help='Number of drafts')

    args = parser.parse_args()

    if args.command == 'send':
        result = send_email(args.to, args.subject, args.body)
        print(f"Email sent to {result['to']}")
        print(f"Subject: {result['subject']}")
        print(f"Message ID: {result['id']}")

    elif args.command == 'inbox':
        emails = get_inbox(args.limit)
        if not emails:
            print("No emails found.")
        else:
            for email in emails:
                print(f"\n[{email['id']}] {email['date']}")
                print(f"From: {email['from']}")
                print(f"Subject: {email['subject']}")
                if email.get('snippet'):
                    print(f"Preview: {email['snippet']}...")

    elif args.command == 'read':
        email = read_email(args.message_id)
        if 'error' in email:
            print(f"Error: {email['error']}")
        else:
            print(f"From: {email['from']}")
            print(f"To: {email['to']}")
            print(f"Date: {email['date']}")
            print(f"Subject: {email['subject']}")
            print(f"\n--- Body ---\n{email['body']}")

    elif args.command == 'sent':
        emails = get_sent(args.limit)
        if not emails:
            print("No sent emails found.")
        else:
            for email in emails:
                print(f"\n[{email['id']}] {email['date']}")
                print(f"To: {email['to']}")
                print(f"Subject: {email['subject']}")
                if email.get('snippet'):
                    print(f"Preview: {email['snippet']}...")

    elif args.command == 'search':
        emails = search_emails(args.query, args.limit)
        if not emails:
            print("No emails found matching query.")
        else:
            print(f"Found {len(emails)} emails:")
            for email in emails:
                print(f"\n[{email['id']}] {email['date']}")
                print(f"From: {email['from']}")
                print(f"Subject: {email['subject']}")

    elif args.command == 'draft':
        result = create_draft(args.to, args.subject, args.body)
        print(f"Draft created for {result['to']}")
        print(f"Subject: {result['subject']}")
        print(f"Draft ID: {result['id']}")

    elif args.command == 'drafts':
        items = list_drafts(args.limit)
        if not items:
            print("No drafts found.")
        else:
            for item in items:
                print(f"\n[{item['id']}] {item['date']}")
                print(f"To: {item['to']}")
                print(f"Subject: {item['subject']}")
                if item.get('snippet'):
                    print(f"Preview: {item['snippet']}...")


if __name__ == "__main__":
    main()
