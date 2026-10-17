# SwissTransfer

Send big files as a SwissTransfer download link, free and with no account. The rule: a file over 2 GB goes as a link, never down the Telegram chat.

## The rule

- **Over 2 GB** (2,000,000,000 bytes, as the Finder counts it): upload it here and
  send the person the link. `utils/send_to_telegram.py` refuses such a file and
  points here, and Telegram's own ceiling through this bot's local Bot API server
  is 2000 MB anyway.
- **2 GB or less**: Telegram as before, unless the person asks for a link.
- When one file of a delivery is over the line, put the whole delivery in one
  transfer, so the person gets one link rather than half a chat and half a link.
- The link goes to the person who asked. Sending it to anyone else (a client, a
  venue) is their call, every time.
- Check a file against the rule: `rule PATH...` prints each size and exits 0 when
  something must go as a link, 1 when Telegram is fine.

## First use: the sender

Every transfer carries a sender address that SwissTransfer has confirmed with a
six character code. The person who opens the link never sees it: the address is
not in the link page's data (checked 2026-10-17). SwissTransfer emails the sender
the code, and for a 30 day link the extension offer on day 12. No mail went to the
sender for uploads or downloads in testing.

Use an address only with the person's yes, and only one whose inbox someone reads.
Each person's sender lives in their own folder, so nobody else's session can use it.

```bash
python3 skills/swisstransfer/scripts/swisstransfer.py sender someone@example.com
python3 skills/swisstransfer/scripts/swisstransfer.py confirm K7Q2ZD
python3 skills/swisstransfer/scripts/swisstransfer.py status
python3 skills/swisstransfer/scripts/swisstransfer.py resend      # the code expired
```

The code arrives within seconds from `noreply@swisstransfer.com`, and it is in the
**subject line**: "Swisstransfer confirmation code: K7Q2ZD". If the email skill can
read that inbox, search `from:noreply@swisstransfer.com newer_than:1h` and take it
from the subject. Otherwise ask the person for it. Asking for codes too often is
rate limited from one address.

The confirmation is kept and reused. SwissTransfer does not say how long it lasts.
`status` asks it whether the confirmation still holds, without emailing anyone, so
run it before a big delivery and get a new code first if it says no. When it stops
holding mid-job, `upload` refuses before anything is sent and says to run `sender`
and `confirm` again.

## Sending

```bash
python3 skills/swisstransfer/scripts/swisstransfer.py upload /path/master.mov
python3 skills/swisstransfer/scripts/swisstransfer.py upload a.mov b.mov FOLDER --message "Final masters"
```

- Prints `Link: https://www.swisstransfer.com/dl/...`. Send that link in a Telegram
  message of its own, so it copies cleanly on a phone.
- Defaults: 15 days and 250 downloads, the most the free plan gives without the
  sender doing anything. `--days 1|3|7|15|30`, `--downloads 1|20|100|250`. A 30 day
  link ends on day 15 unless the sender accepts the extension SwissTransfer emails
  on day 12, so offer 30 only when someone will act on that email.
- A folder goes with its structure; hidden files and links inside it are skipped.
  Two files that would arrive under one name are refused.
- `--password-stdin` sets a download password (read from stdin, never the
  command line). `--title` names the transfer; the default is the first file's name.
- Big transfers print a line at every tenth. Measured here on 2026-10-17: about
  35 MB/s up, so a 2.2 GB file took 66 s.
- Every transfer is recorded in the person's own folder with each file's SHA-256.

## Checking a link

```bash
python3 skills/swisstransfer/scripts/swisstransfer.py check LINK            # what it serves now
python3 skills/swisstransfer/scripts/swisstransfer.py check LINK --wait 600 # ride out the antivirus scan
python3 skills/swisstransfer/scripts/swisstransfer.py check LINK --download DIR
python3 skills/swisstransfer/scripts/swisstransfer.py list                  # this person's transfers
```

`check` reads the page a recipient's browser gets: ready, still being scanned,
expired, out of downloads, flagged, or password protected, plus the file names and
sizes, compared with the record. `--download` fetches every file back and compares
its SHA-256 with what was sent. It spends one of the link's downloads, so never run
it on a link whose downloads are few and meant for someone else.

## Limits (free plan, read from the site on 2026-10-17)

50 GB and 5000 files per transfer; 1, 3, 7, 15 or 30 days; 1, 20, 100 or 250
downloads. Infomaniak's FAQ adds that the "download all" zip is off over 10 GB, so
past that the recipient downloads the files one by one.

## How it works, and when it breaks

SwissTransfer publishes no upload API. The script does what the site's own web
client does (app version 0.71.1, read 2026-10-17): session and CSRF cookies from
the front page, an email validation, the ALTCHA proof of work, `POST /api/1/transfers`
in link mode, storage addresses ten at a time, 50 MiB parts PUT straight to storage
four at a time with three retries each, the quoted ETags back to the API, then the
transfer marked complete. A failed upload is marked failed on their side, as the
web client does. It identifies itself honestly as this repo's script.

The proof of work is computing, not a person check: every browser on the site runs
it unseen. If SwissTransfer ever asks for a human check instead (a code read off an
image), the script stops and says so. Never try to get past one; upload through the
website instead.

The site can change any of this without notice. Each error names the step that
broke. To repair it, read the transfer page's current script modules (the API paths
sit in plain text in them) rather than guessing. The community CLI Blutsh/swissfer
(GPL-3.0, so read it, do not copy it) tracks the same protocol.

Exit codes: 0 done, 1 something was sent and did not finish, 2 refused before
anything was sent.
