# Adobe Creative Cloud

Photoshop, After Effects, Illustrator and the rest of Creative Cloud on macOS:
how to get them onto the machine, what they can and cannot be driven to do
without a human at the screen, and which parts of the suite are worth the disk.

Platform scope: macOS only. Nothing here applies on Linux, where Creative Cloud
does not run at all.

## The automation ceiling (read this first)

This is the finding that decides how you use the suite, so it comes before the
install. **Adobe apps are hands-on tools, not bot-at-3am tools.** That is the
opposite of most of this toolkit, and planning a job around an Adobe app
without knowing it is how an overnight pipeline turns into a morning of
clicking.

| What you want | Works headless? |
|---|---|
| Render an existing After Effects project | **Yes** — `aerender`, no GUI needed |
| Run a script that *builds* or *edits* an AE comp | **No** — needs a live GUI session |
| Photoshop actions / ExtendScript on a batch | **No** — needs a live GUI session |
| Install an Adobe app | **No** — needs a signed-in desktop app |

The one genuinely headless thing is `aerender`, and it only renders what is
already in a project file. Everything that *authors* goes through ExtendScript,
and on macOS ExtendScript is reachable only by an AppleScript bridge into an
already-running, already-unlocked GUI session. No terminal entry point exists.

So the shape that works is: a human builds the template once, the bot renders
variations of it forever. Do not promise a job that needs the bot to author a
comp from nothing.

`scripts/adobe_status.py` reports what is actually on this machine and which of
the above are available. Never assume; run it.

```bash
python skills/adobe/scripts/adobe_status.py
```

## Installing

Two steps, and the second one needs the account holder.

**Step 1 — the desktop app (scriptable, no human):**

```bash
brew install --cask adobe-creative-cloud
```

That installs silently and needs sudo. It is about 300 MB down.

**Step 2 — the apps themselves (needs a human, once):**

There is no command line for this on an individual or consumer licence. The
apps are delivered by the Creative Cloud desktop app after an Adobe ID signs
in, and the sign-in is a GUI window with a password in it.

The enterprise `Setup` binary is still there, and it is a dead end. Measured
11 Sep 2026 on desktop app 6.10.0.253:

```
/Library/Application Support/Adobe/Adobe Desktop Common/HDBox/Setup
```

It advertises exactly the interface you want. Its own strings say `sapCode`,
`baseVersion`, `productVersion`, `platform`, `install`, `uninstall`, and
"Both sapcode and productVersion(or baseVersion) should be specified when
driverXML is not specified" — i.e. it will name a product directly, no
Admin Console package required. It refuses anyway:

```
FATAL: Adobe Setup is not Authorized
Exit Code: -1
```

That is not a privilege error. It fails identically under `sudo` with uid 0 in
the log. The authorization it wants comes from an Admin Console deployment
package, which an individual or consumer licence cannot produce. Do not try to
get around that check: it is a licence-enforcement gate, not a bug.

So installing the apps needs the account holder at the screen, twice over: once
to sign in, once per app to click Install.

**Driving the GUI instead needs two macOS permissions, granted by hand.**
Worth knowing before planning around it. Synthetic clicks and
accessibility-tree reads need Privacy & Security > Accessibility; screenshots
need Screen Recording. Without the first, `System Events` fails with
"osascript is not allowed assistive access. (-1719)"; without the second,
`screencapture` fails with "could not create image from display". macOS
attributes both to the *responsible* process, which for an agent is whatever
launched it (here the bot's Python), not `osascript`. Granting Accessibility to
a long-lived agent process gives it control of every app on the machine, so it
is the account holder's call, not a detail to slip past them.

```bash
open -a "/Applications/Utilities/Adobe Creative Cloud/ACC/Creative Cloud.app"
```

## Reading the licence without opening a window

Once an Adobe ID has signed in, the desktop app caches the account's entire
entitled product catalog on disk as XML:

```
~/Library/Application Support/Adobe/OOBE/com.adobe.accc.apps/products/<accountId>@AdobeID/_data
```

About 13 MB, 678 product entries on an All Apps licence. Each carries the SAP
code, the display name, the platform build and both version numbers. That is
the honest answer to "what does this subscription actually cover" and "what
version would I get", with no GUI and no guessing. Measured 11 Sep 2026:

| App | SAP code | Version | Base | Platform |
|---|---|---|---|---|
| Photoshop | `PHSP` | 27.9.1 | 27.0 | macuniversal |
| After Effects | `AEFT` | 26.5 | 26.0 | macuniversal |
| Illustrator | `ILST` | 30.8.1 | 30.0 | macuniversal |
| Media Encoder | `AME` | 26.5 | — | macarm64 |
| InDesign | `IDSN` | 21.5.1 | — | macuniversal |
| Bridge | `KBRG` | 16.0.7 | — | macuniversal |

Knowing the codes does not let you install them (see above), but it does let
you check a version, confirm an entitlement, or tell an Apple Silicon build
from a universal one before committing disk.

**Telling signed-in from signed-out.** Read account-keyed markers, never file
presence. `logged_out_guid` in `OOBE/` is written before the first sign-in and
is NOT deleted afterwards, so it survives as a stale liar; the installer's
scratch files (`filesync.db`, `temp_lbs_wid`, `*.default.prefs`) appear before
anyone types a password. The markers that mean something are keyed by account
id: the `products/<id>@AdobeID` cache above, prefs files suffixed with that id
instead of `default`, and the `Adobe User Info` keychain item. `adobe_status.py`
reads all three and returns None rather than guessing.

## What is worth installing

Ranked by what they add over what this toolkit already does, not by fame.

**After Effects — the strongest case.** Content-aware fill across a moving shot
produces clean plates without solving them by hand, and the AI roto brush cuts
subjects out of plates. Both are jobs otherwise done the long way. Also the
only app here with a real headless mode (`aerender`).

**Photoshop — second, clearly.** Generative fill invents plausible pixels
better than a procedural recipe can, and Camera Raw is a better front end for
stills than anything else on the machine. Caveat: it is *uncontrolled*. Where a
measured recipe already exists for a fault, keep using the recipe — a
generative fix that cannot be re-run identically is not a deliverable process.

**Adobe Fonts — quiet but real.** Licensed for client work, and it is where the
"the client has no published brand manual and we must identify the face
ourselves" problem actually gets solved.

**Frame.io — 100 GB included.** Worth knowing about because it addresses a
recurring pain: a client returns a recut and nobody is certain which version it
came from. Purpose-built for exactly that.

**Illustrator — weaker than its reputation here.** Real value for logos and
brand assets. Little value for finishing, where geometry gets computed and
placed by measurement rather than drawn by eye.

## What is not worth installing

- **Premiere Pro** — duplicates DaVinci Resolve, which is already here and
  already the finishing tool. Two NLEs is a conform problem, not a capability.
- **Media Encoder** — duplicates ffmpeg, and is slower at it.
- **InDesign** — offers, timelines and decks are *generated* in this toolkit.
  Putting them in InDesign puts a human back into a loop that no longer has one.
- **Audition** — unlikely to beat a dedicated DAW for anyone who has one.

## The Mocha trap (costly, and invisible from the price page)

After Effects ships a **stripped** Mocha ("Mocha AE"). It does planar tracking
and roto. That is all.

Held back for the paid Mocha Pro, and *not* purchasable from Adobe:

- mesh warp / PowerMesh — the curved-panel problem
- remove module — object removal
- mega plate — clean plate across a whole shot
- insert module
- stabilise, lens
- **export a track to anything outside After Effects**, including Resolve

If a job needs any of those, Mocha AE will not do it and no Adobe tier adds it.
Mocha Pro is Boris FX, roughly 295 USD/yr or 695 USD perpetual for a single
host. For screen-comp work — curved panels, occluded corners, clean plates —
the perpetual licence is a higher-value purchase than any Adobe tier upgrade,
and it is completely invisible when comparing Adobe plans.

## Buying

Three single-app subscriptions cost about the same as the whole bundle, so
there is no arrangement where buying two or more singles makes sense.

Between **Standard** and **Pro**, the meaningful difference is a monthly
allowance of Firefly generative credits. If you already generate images and
video elsewhere, Standard is the buy and the Pro premium is wasted.

## What it costs the machine

The desktop app installs background services that start at login:
`CCXProcess`, `AdobeIPCBroker`, `Adobe Desktop Service`, Core Sync, and the
Adobe Genuine Service. `CCXProcess` in particular has a long public history of
sitting at high CPU while idle. On a machine that also does long renders, that
is worth watching rather than assuming.

They can be unloaded (the launch agents live in `~/Library/LaunchAgents`), but
Adobe re-enables them whenever the desktop app runs, so treat it as a trim, not
a removal.

Adobe's own RAM guidance for After Effects works out to roughly *4 GB per CPU
core plus 20 GB*. Check that against the machine before promising AE work:
`adobe_status.py` prints the figure and what this machine actually has.

## Uninstalling

The cask knows how. It removes the launch agents and the background services
too, which a drag-to-bin does not:

```bash
brew uninstall --cask adobe-creative-cloud
```

If an install goes wrong, Adobe's own cleaner is the next step:
`brew install --cask adobe-creative-cloud-cleaner-tool`.

## Rendering an After Effects project headlessly

Once After Effects is installed, this is the one thing that runs with nobody at
the screen:

```bash
# render one comp
python skills/adobe/scripts/ae_render.py --project job.aep --comp "Main" --out out.mov

# render the project's own render queue (each item to its configured path)
python skills/adobe/scripts/ae_render.py --project job.aep

# see the command without running it
python skills/adobe/scripts/ae_render.py --project job.aep --comp "Main" --out out.mov --dry-run

# Multi-Frame Rendering on, leaving half the CPU for everything else
python skills/adobe/scripts/ae_render.py --project job.aep --comp "Main" --out out.mov \
    --mfr on --max-cpu 50
```

**Multi-Frame Rendering is `-mfr`, not `-mp`.** Adobe's syntax is
`-mfr mfr_flag max_cpu_percent`: `ON` or `OFF`, then a 1-100 CPU ceiling that
aerender ignores when the flag is `OFF`. `-mp` is the older "Render Multiple
Frames Simultaneously" multiprocessing switch from before Multi-Frame Rendering
existed in After Effects 2022, and passing it does not turn MFR on. `--mfr` is
off by default here, which leaves aerender on whatever the install itself
prefers. The flag syntax comes from Adobe's own help text and has not been run
against a real aerender from this repo.

It finds `aerender` itself across After Effects versions, refuses early with a
readable reason rather than failing deep in a render, and streams progress so a
long render is distinguishable from a hung one. Render settings and output
module templates come from the project, so whoever built the template controls
the codec, which is the right place for that decision.

There is deliberately no "list the comps in this file" command. Comp names live
in a binary project format and the only trustworthy way to read them is to open
the file in After Effects. A best-effort scrape that quietly confuses a layer
name for a comp name would cost more than it saves. Comp names are case
sensitive; take them from the file itself.
