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

macOS gives the permission to the **responsible process**, which for this bot
is its own Python interpreter, not the `osascript` it shells out to, and not
Terminal, which is not in the picture at all when the bot runs from a launch
agent.

A framework Python does one more thing that trips everybody: it re-execs itself
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

## For agents working on this

`install/macos_permissions.py` is the only place that should answer "can I
drive the screen". Two rules it exists to enforce:

- **Probe, never look for a file.** A permission that was granted and then
  revoked leaves every file exactly where it was. Every probe here does the
  thing and reads what came back.
- **Three states, not two.** `granted`, `denied`, `unknown`. When Automation is
  off, an Accessibility probe fails for a reason that has nothing to do with
  Accessibility, and answering `denied` sends someone to the wrong pane.

```bash
python install/macos_permissions.py --json      # machine readable
```
