#!/bin/bash
# Download and install DaVinci Resolve for macOS, headless. Free by default,
# Studio with --studio.
#
# Blackmagic requires a name and email registration to hand out either
# download, so pass real details. The Studio download is the SAME installer
# gate: it is unlocked at first launch by an activation key, which this script
# never sees and never writes anywhere. Apple Silicon needs Rosetta 2 (the
# installer package refuses without it); this script installs it if missing.
#
# Usage:
#   install_resolve.sh --first Jane --last Doe --email jane@example.com
#     [--studio] [--phone "+1 212 5550100"] [--city "New York"]
#     [--country us] [--keep-zip]
#
# Verified working 2026-07-18 against DaVinci Resolve 21.0.2 (free) and
# 2026-08-23 against DaVinci Resolve Studio 21.0.4. The Homebrew cask route is
# dead (cask retired), which is why this exists.
#
# Studio and free install to the SAME path, /Applications/DaVinci Resolve/, and
# the Studio package replaces the free app in place. Nothing needs uninstalling
# first, and projects and preferences survive the swap.
set -euo pipefail

# Headless sudo. macOS sudo needs a terminal to ask for a password, and this
# script's real caller is a detached background session that has none. When
# SUDO_ASKPASS points at a helper, use -A and the whole install runs unattended;
# with no helper this is plain `sudo` and behaves exactly as it always did.
if [[ -n "${SUDO_ASKPASS:-}" ]]; then
  SUDO=(sudo -A); SUDO_KEEP=(sudo -A -v)
else
  SUDO=(sudo);    SUDO_KEEP=(sudo -n -v)
fi

FIRST="" LAST="" EMAIL="" PHONE="+1 212 5550100" CITY="New York" COUNTRY="us" KEEP=0
STUDIO=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --studio)  STUDIO=1; shift ;;
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

if [[ $STUDIO -eq 1 ]]; then
  EDITION="DaVinci Resolve Studio"; WANT_PRODUCT="davinci-resolve-studio"
else
  EDITION="DaVinci Resolve";        WANT_PRODUCT="davinci-resolve"
fi

echo "Looking up the latest $EDITION for Mac..."
read -r DOWNLOAD_ID VERSION < <(curl -sL "https://www.blackmagicdesign.com/api/support/us/downloads.json" \
  | WANT_PRODUCT="$WANT_PRODUCT" python3 -c '
import json, os, sys
want = os.environ["WANT_PRODUCT"]
data = json.load(sys.stdin)
best = None
for d in data.get("downloads", []):
    if "Server" in d.get("name", ""):
        continue
    for m in d.get("urls", {}).get("Mac OS X", []):
        # The product field is the only reliable edition discriminator: the
        # free and Studio entries share almost every other field.
        if m.get("product") != want:
            continue
        key = (m["major"], m["minor"], m["releaseNum"])
        if best is None or key > best[0]:
            best = (key, m["downloadId"])
if best is None:
    raise SystemExit("no Mac download found for product %s" % want)
print(best[1], "%s.%s.%s" % best[0])')
echo "Latest: $EDITION $VERSION (download id $DOWNLOAD_ID)"

echo "Registering with Blackmagic to get the download link..."
URL=$(curl -s -X POST "https://www.blackmagicdesign.com/api/register/us/download/$DOWNLOAD_ID" \
  -H "Accept: application/json, text/plain, */*" \
  -H "Content-Type: application/json;charset=UTF-8" \
  -H "Origin: https://www.blackmagicdesign.com" \
  -H "Referer: https://www.blackmagicdesign.com/support/family/davinci-resolve-and-fusion" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36" \
  -d "{\"firstname\":\"$FIRST\",\"lastname\":\"$LAST\",\"email\":\"$EMAIL\",\"phone\":\"$PHONE\",\"country\":\"$COUNTRY\",\"state\":\"$CITY\",\"city\":\"$CITY\",\"street\":\"$CITY\",\"product\":\"$EDITION\",\"policy\":true,\"platform\":\"Mac OS X\"}")
[[ "$URL" != https://* ]] && { echo "Registration failed: $URL" >&2; exit 1; }

SUFFIX=""; [[ $STUDIO -eq 1 ]] && SUFFIX="_Studio"
ZIP="/tmp/DaVinci_Resolve${SUFFIX}_${VERSION}_Mac.zip"

# Ask the CDN how big the file is, so a part-downloaded or already-downloaded
# zip can be resumed or reused instead of fetched again. Free is about 3.5 GB,
# Studio about 8.4 GB, and on a slow line that is the difference between a
# retry and an hour lost.
REMOTE_SIZE=$(curl -sI -L "$URL" | tr -d '\r' | awk 'tolower($1)=="content-length:"{n=$2} END{print n+0}')
LOCAL_SIZE=0; [[ -f "$ZIP" ]] && LOCAL_SIZE=$(stat -f%z "$ZIP" 2>/dev/null || stat -c%s "$ZIP")
if [[ "$REMOTE_SIZE" -gt 0 && "$LOCAL_SIZE" -eq "$REMOTE_SIZE" ]]; then
  echo "Already downloaded: $ZIP ($LOCAL_SIZE bytes). Reusing it."
else
  HUMAN=$(( REMOTE_SIZE / 1000000000 ))
  echo "Downloading (~${HUMAN} GB) to $ZIP ..."
  curl -sS -L -C - --retry 3 --retry-delay 5 -o "$ZIP" "$URL"
  GOT=$(stat -f%z "$ZIP" 2>/dev/null || stat -c%s "$ZIP")
  if [[ "$REMOTE_SIZE" -gt 0 && "$GOT" -ne "$REMOTE_SIZE" ]]; then
    echo "Download is short: got $GOT bytes, expected $REMOTE_SIZE. Refusing to install a truncated package." >&2
    exit 1
  fi
fi

WORK="/tmp/resolve_install"
# Emptied first. The glob below has to be wide enough to match the Studio DMG,
# which makes it wide enough to match the free one too, and `head -1` takes the
# alphabetically first: a free DMG left here by an earlier or interrupted run
# would win over the Studio DMG that was just unzipped, install the FREE
# edition, and then be reported as Studio by the version read at the end. The
# zip is what is expensive to fetch and it lives outside this directory, so
# clearing it costs one unzip and removes the ambiguity entirely.
rm -rf "$WORK"
mkdir -p "$WORK"
unzip -o -q "$ZIP" -d "$WORK"
# Matched as a glob rather than through `ls`, so the refusal below is the thing
# that actually fires when the zip holds no disk image. Under `set -e` an `ls`
# that matches nothing exits the script first and the message is never reached.
shopt -s nullglob
DMGS=("$WORK"/DaVinci_Resolve*_Mac.dmg)
shopt -u nullglob
if [[ ${#DMGS[@]} -eq 0 ]]; then
  echo "No disk image inside $ZIP" >&2
  exit 1
fi
DMG="${DMGS[0]}"

if ! arch -x86_64 /usr/bin/true 2>/dev/null && [[ "$(uname -m)" == "arm64" ]]; then
  echo "Installing Rosetta 2 (required by the Resolve installer on Apple Silicon)..."
  "${SUDO[@]}" softwareupdate --install-rosetta --agree-to-license
fi

echo "Mounting and installing (needs sudo)..."
# The mount point is READ BACK from hdiutil rather than assumed. Free mounts at
# "Blackmagic DaVinci Resolve" and Studio at "Blackmagic DaVinci Resolve
# Studio", so a hardcoded path installs the free edition happily and then fails
# on Studio with a bare "no such file" (hit for real 2026-08-23).
VOLUME=$(hdiutil attach -nobrowse -plist "$DMG" | python3 -c '
import plistlib, sys
d = plistlib.loads(sys.stdin.buffer.read())
for e in d.get("system-entities", []):
    if e.get("mount-point"):
        print(e["mount-point"]); break
else:
    raise SystemExit("the disk image mounted nothing")')
[[ -z "$VOLUME" || ! -d "$VOLUME" ]] && { echo "Could not mount $DMG" >&2; exit 1; }
PKG=$(ls "$VOLUME"/Install*.pkg 2>/dev/null | head -1)
[[ -z "$PKG" ]] && { echo "No installer package inside $VOLUME" >&2;
                     hdiutil detach -quiet "$VOLUME" || true; exit 1; }
echo "Installing $(basename "$PKG") from $VOLUME"

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
    "${SUDO_KEEP[@]}" 2>/dev/null || true
    PID=$(pgrep -f "chown .*${HOME}/Movies" | head -1 || true)
    if [[ -n "$PID" && "$PID" == "$LAST_SEEN" ]]; then
      echo "Watchdog: clearing stuck Movies-folder permission step (pid $PID)"
      "${SUDO[@]}" -n kill "$PID" 2>/dev/null || true
    fi
    LAST_SEEN="$PID"
  done
) &
WATCHDOG=$!
trap 'kill "$WATCHDOG" 2>/dev/null || true' EXIT

"${SUDO[@]}" installer -pkg "$PKG" -target /
kill "$WATCHDOG" 2>/dev/null || true
trap - EXIT
hdiutil detach -quiet "$VOLUME"

# The pkg runs as root and leaves the user-level settings folder root-owned,
# which breaks Resolve's first launch and any menu-script install.
BMD_DIR="$HOME/Library/Application Support/Blackmagic Design"
[[ -d "$BMD_DIR" ]] && "${SUDO[@]}" chown -R "$(id -un):staff" "$BMD_DIR"

[[ $KEEP -eq 0 ]] && rm -rf "$ZIP" "$WORK"

INSTALLED=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" \
  "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Info.plist")
echo "Done. $EDITION $INSTALLED is installed."
echo "Note: the first launch shows a setup wizard that needs a person at the screen once."
if [[ $STUDIO -eq 1 ]]; then
  echo "Studio also asks for its activation key at that first launch. The key is"
  echo "a secret: keep it in the Keychain (utils/credentials_cli.py get --service"
  echo "davinci-resolve-studio) and never write it into a file in this repo."
fi
