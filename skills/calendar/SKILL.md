# Google Calendar

Manage your Google Calendar - view events, add events, delete events.

## Setup

Requires Google OAuth credentials. Place `google_credentials.json` in the bot root directory.
First run will authenticate and create `google_token.json`.

Get credentials from: [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
- Create OAuth 2.0 Client ID (Desktop application)
- Enable Google Calendar API

### Headless / Remote Setup

If accessing the machine via SSH (no browser available):
1. Connect with SSH port forwarding: `ssh -L 8085:localhost:8085 user@machine`
2. Run the auth command — it will start a local server and print an authorization URL
3. Visit the printed URL in your local browser
4. After authorizing, the redirect goes to localhost:8085 — port forwarding routes it to the machine
5. Once complete, `google_token.json` is saved and future runs don't need a browser

## Commands

```bash
# List upcoming events (next 7 days)
python skills/calendar/scripts/gcal.py list

# List more days ahead
python skills/calendar/scripts/gcal.py list -d 14

# Add an event
python skills/calendar/scripts/gcal.py add "Meeting with John" "2026-02-05T14:00:00"

# Add an all-day event
python skills/calendar/scripts/gcal.py add "Project deadline" "2026-02-10"

# Add event with end time and location
python skills/calendar/scripts/gcal.py add "Dinner" "2026-02-05T19:00:00" -e "2026-02-05T21:00:00" -l "Restaurant Name"

# Show event details
python skills/calendar/scripts/gcal.py show <event_id>

# Delete an event (can use partial ID from list output)
python skills/calendar/scripts/gcal.py delete <event_id>

# List all calendars
python skills/calendar/scripts/gcal.py calendars
```

## Date/Time Formats

- All-day: `YYYY-MM-DD` (e.g., `2026-02-05`)
- With time: `YYYY-MM-DDTHH:MM:SS` (e.g., `2026-02-05T14:00:00`)

## Event IDs

Event IDs are shown in brackets after each event in list output: `[id:abc12345]`
You can use just the first 8 characters when deleting or showing events.

## Timezone

Events are created in the system's local timezone by default.
Set the `CALENDAR_TIMEZONE` environment variable to override (e.g., `America/New_York`).

## Examples

"What's on my calendar this week?"
"Add a meeting with Sarah tomorrow at 3pm"
"Delete the dentist appointment"
"Show my calendar for the next 2 weeks"
