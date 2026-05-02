#!/usr/bin/env python3
"""
Google Calendar CLI.

Usage:
    python gcal.py list [-d DAYS] [-n MAX] [-c CALENDAR_ID]
    python gcal.py add "Event title" "YYYY-MM-DDTHH:MM:SS" [-e END] [-d DESC] [-l LOC]
    python gcal.py delete <event_id>
    python gcal.py show <event_id>
    python gcal.py calendars
    python gcal.py auth
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Paths — resolve relative to the bot root (4 levels up from this script)
BOT_DIR = Path(__file__).parent.parent.parent.parent
CREDENTIALS_FILE = BOT_DIR / "google_credentials.json"
TOKEN_FILE = BOT_DIR / "google_token.json"

# Timezone — configurable via environment variable, falls back to system local
TIMEZONE = os.environ.get("CALENDAR_TIMEZONE", "")
if not TIMEZONE:
    try:
        # Try /etc/timezone first (Debian/Ubuntu)
        tz_file = Path("/etc/timezone")
        if tz_file.exists():
            TIMEZONE = tz_file.read_text(encoding="utf-8").strip()
        # Try /etc/localtime symlink (most Linux, some macOS)
        elif Path("/etc/localtime").is_symlink():
            link = os.readlink("/etc/localtime")
            if "zoneinfo/" in link:
                TIMEZONE = link.split("zoneinfo/", 1)[1]
        # macOS: read from systemsetup or defaults
        if not TIMEZONE:
            import subprocess
            try:
                result = subprocess.run(
                    ["defaults", "read", "/Library/Preferences/com.apple.timezone.auto",
                     "TimeZoneName"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and "/" in result.stdout.strip():
                    TIMEZONE = result.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        if not TIMEZONE:
            TIMEZONE = "UTC"
    except Exception:
        TIMEZONE = "UTC"

# Scopes for Calendar access
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_credentials():
    """Get valid credentials, refreshing or re-authenticating as needed."""
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"Error: Credentials file not found at {CREDENTIALS_FILE}", file=sys.stderr)
                print("Please download OAuth credentials from Google Cloud Console.", file=sys.stderr)
                print("1. Go to https://console.cloud.google.com/apis/credentials", file=sys.stderr)
                print("2. Create OAuth 2.0 Client ID (Desktop application)", file=sys.stderr)
                print("3. Enable Google Calendar API", file=sys.stderr)
                print(f"4. Save the JSON file as: {CREDENTIALS_FILE}", file=sys.stderr)
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=8085)

        # Save credentials for next run
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds


def get_service():
    """Get authenticated Calendar service."""
    creds = get_credentials()
    return build("calendar", "v3", credentials=creds)


def list_events(days=7, max_results=10, calendar_id="primary"):
    """List upcoming events."""
    service = get_service()

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    end_time = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")

    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            timeMax=end_time,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])

        if not events:
            print("No upcoming events found.")
            return

        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            summary = event.get("summary", "(No title)")
            event_id = event["id"]

            # Parse and format the date
            if "T" in start:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                formatted = dt.strftime("%a %b %d, %H:%M")
            else:
                dt = datetime.fromisoformat(start)
                formatted = dt.strftime("%a %b %d (all day)")

            print(f"- {formatted}: {summary} [id:{event_id[:8]}]")

            # Show location if present
            if event.get("location"):
                print(f"  Location: {event['location']}")

    except HttpError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


def add_event(summary, start_time, end_time=None, description=None, location=None, calendar_id="primary"):
    """Add a new event."""
    service = get_service()

    # Parse start time
    try:
        if len(start_time) == 10:  # Date only: YYYY-MM-DD
            event = {
                "summary": summary,
                "start": {"date": start_time},
                "end": {"date": end_time or start_time},
            }
        else:
            # Full datetime
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if end_time:
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            else:
                end_dt = start_dt + timedelta(hours=1)

            event = {
                "summary": summary,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
            }
    except ValueError as e:
        print(f"Error parsing date/time: {e}", file=sys.stderr)
        print("Use format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS", file=sys.stderr)
        sys.exit(1)

    if description:
        event["description"] = description
    if location:
        event["location"] = location

    try:
        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        print(f"Created: {created.get('summary')}")
        print(f"ID: {created['id'][:8]}")
        print(f"Link: {created.get('htmlLink')}")
    except HttpError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


def delete_event(event_id, calendar_id="primary"):
    """Delete an event by ID (can be partial ID)."""
    service = get_service()

    # If partial ID, search for matching event
    if len(event_id) < 20:
        try:
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=now,
                maxResults=50,
                singleEvents=True,
            ).execute()

            for event in events_result.get("items", []):
                if event["id"].startswith(event_id):
                    event_id = event["id"]
                    break
            else:
                print(f"No event found starting with ID: {event_id}", file=sys.stderr)
                sys.exit(1)
        except HttpError as error:
            print(f"Error searching: {error}", file=sys.stderr)
            sys.exit(1)

    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        print(f"Deleted event: {event_id[:8]}")
    except HttpError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


def show_event(event_id, calendar_id="primary"):
    """Show details of a specific event."""
    service = get_service()

    # If partial ID, search for matching event
    if len(event_id) < 20:
        try:
            now = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=now,
                maxResults=100,
                singleEvents=True,
            ).execute()

            for event in events_result.get("items", []):
                if event["id"].startswith(event_id):
                    event_id = event["id"]
                    break
            else:
                print(f"No event found starting with ID: {event_id}", file=sys.stderr)
                sys.exit(1)
        except HttpError as error:
            print(f"Error searching: {error}", file=sys.stderr)
            sys.exit(1)

    try:
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

        print(f"Title: {event.get('summary', '(No title)')}")

        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))
        print(f"Start: {start}")
        print(f"End: {end}")

        if event.get("location"):
            print(f"Location: {event['location']}")
        if event.get("description"):
            print(f"Description: {event['description']}")

        print(f"ID: {event['id']}")
        print(f"Link: {event.get('htmlLink')}")

    except HttpError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


def list_calendars():
    """List all available calendars."""
    service = get_service()

    try:
        calendars = service.calendarList().list().execute()

        for cal in calendars.get("items", []):
            primary = " (primary)" if cal.get("primary") else ""
            print(f"- {cal['summary']}{primary}")
            print(f"  ID: {cal['id']}")

    except HttpError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Google Calendar CLI")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Auth command
    subparsers.add_parser("auth", help="Authenticate with Google Calendar")

    # List events
    list_parser = subparsers.add_parser("list", help="List upcoming events")
    list_parser.add_argument("-d", "--days", type=int, default=7, help="Days to look ahead")
    list_parser.add_argument("-n", "--max", type=int, default=10, help="Maximum events")
    list_parser.add_argument("-c", "--calendar", default="primary", help="Calendar ID")

    # Add event
    add_parser = subparsers.add_parser("add", help="Add a new event")
    add_parser.add_argument("summary", help="Event title")
    add_parser.add_argument("start", help="Start time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)")
    add_parser.add_argument("-e", "--end", help="End time")
    add_parser.add_argument("-d", "--description", help="Event description")
    add_parser.add_argument("-l", "--location", help="Event location")
    add_parser.add_argument("-c", "--calendar", default="primary", help="Calendar ID")

    # Delete event
    del_parser = subparsers.add_parser("delete", help="Delete an event")
    del_parser.add_argument("event_id", help="Event ID (can be partial)")
    del_parser.add_argument("-c", "--calendar", default="primary", help="Calendar ID")

    # Show event
    show_parser = subparsers.add_parser("show", help="Show event details")
    show_parser.add_argument("event_id", help="Event ID (can be partial)")
    show_parser.add_argument("-c", "--calendar", default="primary", help="Calendar ID")

    # List calendars
    subparsers.add_parser("calendars", help="List available calendars")

    args = parser.parse_args()

    if args.command == "auth":
        get_credentials()
        print("Authentication successful!")
    elif args.command == "list":
        list_events(days=args.days, max_results=args.max, calendar_id=args.calendar)
    elif args.command == "add":
        add_event(
            args.summary,
            args.start,
            end_time=args.end,
            description=args.description,
            location=args.location,
            calendar_id=args.calendar,
        )
    elif args.command == "delete":
        delete_event(args.event_id, calendar_id=args.calendar)
    elif args.command == "show":
        show_event(args.event_id, calendar_id=args.calendar)
    elif args.command == "calendars":
        list_calendars()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
