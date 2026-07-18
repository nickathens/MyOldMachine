#!/bin/bash
# Download and install the latest free DaVinci Resolve for macOS, headless.
#
# Blackmagic requires a name and email registration to hand out the free
# download, so pass real details. Apple Silicon needs Rosetta 2 (the installer
# package refuses without it); this script installs it if missing.
#
# Usage:
#   install_resolve.sh --first Nick --last Athens --email nick@example.com
#     [--phone "+30 210 0000000"] [--city Athens] [--country gr] [--keep-zip]
#
# Verified working 2026-07-18 against DaVinci Resolve 21.0.2. The Homebrew
# cask route is dead (cask retired), which is why this exists.
set -euo pipefail

FIRST="" LAST="" EMAIL="" PHONE="+30 210 0000000" CITY="Athens" COUNTRY="gr" KEEP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --first)   FIRST="$2"; shift 2 ;;
    --last)    LAST="$2"; shift 2 ;;
    --email)   EMAIL="$2"; shift 2 ;;
    --phone)   PHONE="$2"; shift 2 ;;
    --city)    CITY="$2"; shift 2 ;;
    --country) COUNTRY="$2"; shift 2 ;;
    --keep-zip) KEEP=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
[[ -z "$FIRST" || -z "$LAST" || -z "$EMAIL" ]] && {
  echo "Required: --first NAME --last NAME --email ADDRESS (Blackmagic registration)" >&2; exit 1; }

echo "Looking up the latest free DaVinci Resolve for Mac..."
read -r DOWNLOAD_ID VERSION < <(curl -sL "https://www.blackmagicdesign.com/api/support/us/downloads.json" \
  | python3 -c '
import json, sys
data = json.load(sys.stdin)
for d in data.get("downloads", []):
    name = d.get("name", "")
    if "DaVinci Resolve" in name and "Studio" not in name and "Server" not in name:
        mac = d.get("urls", {}).get("Mac OS X", [])
        if mac and mac[0].get("product") == "davinci-resolve":
            m = mac[0]
            print(m["downloadId"], "%s.%s.%s" % (m["major"], m["minor"], m["releaseNum"]))
            raise SystemExit
raise SystemExit("no Mac download found")')
echo "Latest: DaVinci Resolve $VERSION (download id $DOWNLOAD_ID)"

echo "Registering with Blackmagic to get the download link..."
URL=$(curl -s -X POST "https://www.blackmagicdesign.com/api/register/us/download/$DOWNLOAD_ID" \
  -H "Accept: application/json, text/plain, */*" \
  -H "Content-Type: application/json;charset=UTF-8" \
  -H "Origin: https://www.blackmagicdesign.com" \
  -H "Referer: https://www.blackmagicdesign.com/support/family/davinci-resolve-and-fusion" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36" \
  -d "{\"firstname\":\"$FIRST\",\"lastname\":\"$LAST\",\"email\":\"$EMAIL\",\"phone\":\"$PHONE\",\"country\":\"$COUNTRY\",\"state\":\"$CITY\",\"city\":\"$CITY\",\"street\":\"$CITY\",\"product\":\"DaVinci Resolve\",\"policy\":true,\"platform\":\"Mac OS X\"}")
[[ "$URL" != https://* ]] && { echo "Registration failed: $URL" >&2; exit 1; }

ZIP="/tmp/DaVinci_Resolve_${VERSION}_Mac.zip"
echo "Downloading (~3.5 GB) to $ZIP ..."
curl -sS -L --retry 3 --retry-delay 5 -o "$ZIP" "$URL"

WORK="/tmp/resolve_install"
mkdir -p "$WORK"
unzip -o -q "$ZIP" -d "$WORK"
DMG=$(ls "$WORK"/DaVinci_Resolve_*_Mac.dmg | head -1)

if ! arch -x86_64 /usr/bin/true 2>/dev/null && [[ "$(uname -m)" == "arm64" ]]; then
  echo "Installing Rosetta 2 (required by the Resolve installer on Apple Silicon)..."
  sudo softwareupdate --install-rosetta --agree-to-license
fi

echo "Mounting and installing (needs sudo)..."
hdiutil attach -nobrowse -quiet "$DMG"
VOLUME="/Volumes/Blackmagic DaVinci Resolve"
PKG=$(ls "$VOLUME"/Install*.pkg | head -1)

# Unattended quirk, hit for real on this machine 2026-07-18: on a fresh
# install the pkg postflight runs "chown <user> ~/Movies" (scratch-disk
# setup). In a headless session macOS blocks that call on the TCC-protected
# Movies folder and it never returns, stalling the whole install (34 minutes
# before we caught it). The folder already belongs to the user in any normal
# setup, so killing the stuck chown is safe: the postflight just carries on
# with its next step and the install completes cleanly, receipts included.
# The watchdog kills a chown of ~/Movies seen alive in two consecutive
# 30-second checks, and keeps the sudo timestamp warm so it can.
(
  LAST_SEEN=""
  while true; do
    sleep 30
    sudo -n -v 2>/dev/null || true
    PID=$(pgrep -f "chown .*${HOME}/Movies" | head -1 || true)
    if [[ -n "$PID" && "$PID" == "$LAST_SEEN" ]]; then
      echo "Watchdog: clearing stuck Movies-folder permission step (pid $PID)"
      sudo -n kill "$PID" 2>/dev/null || true
    fi
    LAST_SEEN="$PID"
  done
) &
WATCHDOG=$!
trap 'kill "$WATCHDOG" 2>/dev/null || true' EXIT

sudo installer -pkg "$PKG" -target /
kill "$WATCHDOG" 2>/dev/null || true
trap - EXIT
hdiutil detach -quiet "$VOLUME"

# The pkg runs as root and leaves the user-level settings folder root-owned,
# which breaks Resolve's first launch and any menu-script install.
BMD_DIR="$HOME/Library/Application Support/Blackmagic Design"
[[ -d "$BMD_DIR" ]] && sudo chown -R "$(id -un):staff" "$BMD_DIR"

[[ $KEEP -eq 0 ]] && rm -rf "$ZIP" "$WORK"

INSTALLED=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" \
  "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Info.plist")
echo "Done. DaVinci Resolve $INSTALLED is installed."
echo "Note: the first launch shows a setup wizard that needs a person at the screen once."
