# Screen control on macOS (opt-in)

An assistant that can run any command still cannot touch a window. On macOS
that is a separate decision, made by a human in System Settings, and no amount
of privilege gets around it: `sudo` does not help, and neither does running as
root.

Most of this toolkit does not care. Video, audio, images, documents, the whole
measured pipeline, all of it is command line work. Three things are not:

- clicking **Install** in an app store window that has no command line
- driving **Photoshop** or **After Effects**, which script only through a live
  GUI session
- **seeing the screen** to check its own work

If none of those matter to you, skip this page. Nothing here is required and
nothing degrades without it.

## Three permissions, not one

They are separate TCC services, granted separately, failing differently. They
get talked about as if they were one thing, and that mistake costs an afternoon.

| Permission | What it allows | How it fails |
|---|---|---|
| **Automation** | sending an Apple event to another app at all | `-1743`, "not authorized to send Apple events" |
| **Accessibility** | clicking, typing, reading another app's windows | `-25211`, "not allowed assistive access" |
| **Screen Recording** | `screencapture`, window images | "could not create image from display" |

Measured on one machine, 11 Sep 2026: Automation **granted**, Accessibility
**refused**, Screen Recording **refused**. So a script that successfully lists
running processes is not evidence that clicking will work. Check the one you
actually need.

## The entry to add is not the one you would guess

macOS gives the permission to the **responsible process**: for a launch agent,
the first program the job runs that is not part of macOS. It is not the
`osascript` the bot shells out to, and not Terminal, which is not in the
picture at all when the bot runs from a launch agent.

The launch agent starts the bot through a small **starter** of its own (see
[the trap that bites later](#the-trap-that-bites-later)), so on an install that
has one, the entry is the starter:

```
~/Library/Application Support/MyOldMachine/MyOldMachine
```

That is also the name macOS puts on its consent boxes: "MyOldMachine" would
like to access files on a removable volume.

Without a starter (an install that predates it, or a Mac without the Command
Line Tools to build one) the responsible process is the bot's own Python, and a
framework Python does one more thing that trips everybody: it re-execs itself
through a `Python.app` bundle inside the framework so it can reach the window
server. That bundle is what appears in `ps`, and that bundle is what System
Settings has to be given:

```
/opt/homebrew/Cellar/python@3.12/3.12.14/Frameworks/Python.framework/Versions/3.12/Resources/Python.app
```

Not `.venv/bin/python`, not `bin/python3.12`. Add either of those and you get a
grant that applies to nothing, with no error anywhere to say so.

Do not work this out by hand. Ask:

```bash
python install/macos_permissions.py --check
```

## Granting it

```bash
python install/macos_permissions.py --grant
```

It names the entry, copies it to the clipboard, opens the right System Settings
pane, and then **re-probes to see whether it actually took**. In the pane:
click **+**, press **Shift-Command-G**, paste, Return, Open, and check the new
row's switch is on. A permission added while the bot is already running usually
only takes effect after the bot restarts.

The installer offers the same step on macOS, always opt-in, never assumed.

## What you are agreeing to

Accessibility is not scoped to one app. Whatever holds it can control every
application on the machine, read what is in their windows, and synthesise
clicks and keystrokes, for as long as it holds it. It is the same permission a
keylogger wants.

That can be a perfectly reasonable trade for an assistant you run yourself on
your own machine. It is not something to wave through, and it is not something
this project will ever grant quietly. It is offered once, explained, and
declined by default.

To take it back: System Settings, Privacy & Security, Accessibility, switch the
row off or select it and press **-**.

## The trap that bites later

The grant is keyed to a code signature. A Homebrew Python is **ad-hoc signed**,
which means its signing identifier is derived from the binary itself and changes
every time it is rebuilt. Its path carries the patch version too.

So `brew upgrade python@3.12` moves the binary, changes its hash, and changes
its identifier. The switch in System Settings still looks on. It now applies to
a file that no longer exists. Nothing announces this, and a machine running
unattended package updates on a nightly timer will hit it eventually.

It did on 2 Oct 2026. The 04:00 update moved Python 3.12.14 to 3.12.15, and
both grants the bot had, Accessibility and files on the removable storage
drive, stopped applying. At the 05:00 restart the bot looked at the drive, macOS
asked again, and the consent box waited on a screen with nobody at it. Until a
person clicked Allow, macOS held every read of that drive behind the box. A
program cannot click it: macOS ignores synthetic clicks on its consent boxes.

### The starter

The fix is a program at the top of the job that never changes. The launch
agent runs

```
/bin/bash -c "set -a; source .env; set +a; ... exec <starter> .venv/bin/python bot.py"
```

and the starter, built from `install/macos_starter.c`, starts the bot as its
child and stays. `/bin/bash` is part of macOS, so it is passed over, and the
starter is held responsible for everything the bot and its tools ask for.
`sudo launchctl procinfo <bot pid>` shows which program that is, on the
`responsible path` line. The starter passes on SIGTERM and the other signals
launchd and `/restart` send, hands back the bot's exit status, and keeps the bot
in its process group, so stopping the job still stops everything in it.

Grant the starter once and Python updates stop mattering.

**It is built once and never rebuilt.** An ad hoc signature is a hash of the
program's bytes, so a rebuilt starter, even from the same source, is a new
program to macOS and loses the grants exactly as Python does.
`install/service.py` builds it only when it is missing, and keeps any copy that
is already there whatever the source in the repo says now.

**A missing starter never stops the bot.** If it is not there or cannot be
executed, the launch agent starts Python directly, exactly as before, and the
grants are Python's again until the starter is back.

An install from before the starter moves onto it in two steps:

```bash
python install/service.py --repo-dir . --no-load   # build it, rewrite the agent, leave the bot running
```

then a `/restart`. The first look at an external drive after that raises one
consent box naming MyOldMachine; allow it once. Add the starter for
Accessibility with **+** as above. `--check` names it.

Full Disk Access would cover the drive as well, but it reaches much further:
every session of every user of this bot could then read the account's Mail and
Messages. The removable volume grant is the narrow one.

That is why the grant is recorded with the signing identifier it was given to,
and why the nightly report has a section that stays silent unless a permission
that was **observed working** has stopped:

```
SCREEN CONTROL
  Accessibility has stopped applying: the interpreter it was granted to was
  replaced, most likely by a Homebrew Python upgrade. Re-add
  /opt/homebrew/.../Python.app in System Settings.
```

A machine that never granted anything never sees that section.

It says each loss **once**, on the night it happens. Nothing here can tell a
permission you switched off on purpose from one that broke, so a line that
repeated until the permission came back would be a line you learn to skim. The
slate clears itself when the permission is seen working again, so a genuine
second loss still speaks up, and a replaced interpreter reports again on its
own because it is a different situation with a different instruction. A probe
that could not tell either way does not clear anything -- one flaky night must
not put the nag back.

If you revoked it deliberately and want the step to stop offering itself at
install time as well, that is already how it behaves: the record of the grant
stays, so the installer does not ask again.

## For agents working on this

`install/macos_permissions.py` is the only place that should answer "can I
drive the screen". Two rules it exists to enforce:

- **Probe, never look for a file.** A permission that was granted and then
  revoked leaves every file exactly where it was. Every probe here does the
  thing and reads what came back.
- **Three states, not two.** `granted`, `denied`, `unknown`. When Automation is
  off, an Accessibility probe fails for a reason that has nothing to do with
  Accessibility, and answering `denied` sends someone to the wrong pane.

And one for the starter: **never rebuild it, and never delete it to "refresh"
it.** Either one voids every grant it holds, and the next drive access waits on
a person at the screen.

```bash
python install/macos_permissions.py --json      # machine readable
```

## An unanswered removable volume permission box can stall drive access

macOS protects access to removable volumes through Files and Folders permissions.
See [Apple's file access documentation](https://support.apple.com/en-ie/guide/security/secddd1d86a6/web).

In the incident that motivated this check, an application restored documents
from an external drive after login and raised a permission box. Later drive
access stalled until that box was cleared. This is one possible explanation
for a timeout, not a diagnosis of every slow or disconnected drive.

MOM checks external drives about every five minutes. Each directory check runs
in a separate child with a bounded wait. Linux discovers mounts from
`/proc/self/mountinfo`, without listing mounted directories; macOS enumerates
names under `/Volumes` without inspecting the mount points in the parent.
The probe keeps successful, timed out, skipped and unknown results separate.
Only a successful listing proves recovery. Removing a drive does not.

When a probe times out, the assistant is told to avoid that drive and the
idle timeout message names it as a possible cause, including when a partial
reply has been preserved. Alerts track successful delivery to each admin;
a failed delivery is retried at the next check.

On a Mac with a visible removable volume permission box:

1. Answer it at the screen, allowing access only if intended.
2. If that is not possible, `killall UserNotificationCenter` was observed to
   cancel the pending request in the reported incident. It cancels consent;
   it does not grant access and may also dismiss other pending prompts.
3. If the drive still does not answer, stop work using it before reconnecting
   it or restarting the machine. Check hardware and connection issues too.

The optional dialog reader needs Accessibility access and recognises English
removable volume text. It is best effort; not finding a dialog does not prove
there is none. Automatic consent clicks are not part of this feature.

Coverage is limited to `/Volumes` on macOS and `/media`, `/mnt`, and
`/run/media` mount locations on Linux. Other mount locations are not monitored.
The probe can lag a new freeze by one check interval. Up to eight volumes are
checked concurrently. A child blocked inside the operating system may survive
a kill request; the parent stops waiting rather than hanging with it.
