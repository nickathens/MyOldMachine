# DaVinci Resolve

Drive DaVinci Resolve for real editorial work: build timelines from media, hand off rough cuts, queue renders. Complements the ffmpeg-based video-editing skill: use this one when the result must live in a Resolve project (client work, grading, finishing), use video-editing for quick mechanical cuts.

Platform scope: on Linux only build_fcpxml.py applies (it needs just Python and ffprobe). Everything else (install, status, launch, quit, the external API and the menu-script bridge) is macOS only.

## The two editions (read this first)

- **Free edition** (what gets installed by default): scripts run only INSIDE the app, from the Workspace, Scripts menu or the Console. An outside process cannot connect to it. Automation still works via two bridges below.
- **Studio edition** (paid license/dongle): full external scripting, so the bot can launch Resolve (even headless with -nogui), build projects and render with nobody at the screen.

`resolve_api.py status` reports which situation the machine is in. Never assume; run it.

## Bridge 1: one-click jobs (works on free)

The bot prepares a job file, the user triggers it with one click inside Resolve:

```bash
# 1. Bot queues the job (video clips, in order):
python skills/davinci-resolve/scripts/resolve_api.py queue-job --name "Rough Cut" --fps 25 clip1.mov clip2.mov clip3.mov

# Optionally have the job also render when it runs:
python skills/davinci-resolve/scripts/resolve_api.py queue-job --name "Rough Cut" --fps 25 clips... --render-preset "H.264 Master" --render-out ~/Desktop

# 2. User: open Resolve, then Workspace menu > Scripts > CooCoo Run Job
# 3. Bot checks the outcome:
python skills/davinci-resolve/scripts/resolve_api.py check-job
```

One-time setup (puts "CooCoo Run Job" into Resolve's Scripts menu):

```bash
python skills/davinci-resolve/scripts/install_menu_scripts.py
```

## Bridge 2: importable timelines, fully offline (works everywhere)

Build a `.fcpxml` timeline file without Resolve even running. The user imports it: File, Import Timeline, Import AAF, EDL, XML. Media relinks automatically (absolute file paths are embedded).

```bash
python skills/davinci-resolve/scripts/build_fcpxml.py out.fcpxml --name "Rough Cut" --fps 25 clip1.mov clip2.mov [--music track.mp3]
```

Music lands on a connected audio track under the first clip. Supported fps: 23.976, 24, 25, 29.97, 30, 50, 59.94, 60.

## Direct control (Studio only)

```bash
python skills/davinci-resolve/scripts/resolve_api.py status            # installed? running? reachable? which edition?
python skills/davinci-resolve/scripts/resolve_api.py launch [--nogui]  # -nogui needs Studio
python skills/davinci-resolve/scripts/resolve_api.py projects          # list projects
python skills/davinci-resolve/scripts/resolve_api.py import-media f1.mov f2.mov
python skills/davinci-resolve/scripts/resolve_api.py import-timeline cut.fcpxml
python skills/davinci-resolve/scripts/resolve_api.py render --preset "H.264 Master" --out ~/Desktop
python skills/davinci-resolve/scripts/resolve_api.py quit
```

On the free edition these commands fail with a clear message pointing to the two bridges.

## Installing Resolve itself

Not in Homebrew (the cask was retired). Blackmagic requires a name and email registration for either download, so the installer script needs real details:

```bash
bash skills/davinci-resolve/scripts/install_resolve.sh --first Jane --last Doe --email jane@example.com
bash skills/davinci-resolve/scripts/install_resolve.sh --studio --first Jane --last Doe --email jane@example.com
```

Free is about 3.5 GB, Studio about 8.4 GB. Both install to `/Applications/DaVinci Resolve/`, and the Studio package replaces the free app in place: nothing needs uninstalling first, and projects and preferences survive the swap. A part-downloaded or already-downloaded zip is resumed or reused rather than fetched again, and a short download is refused rather than installed. To uninstall, the DMG ships an "Uninstall Resolve" app.

**Headless.** macOS sudo needs a terminal to ask for a password and a detached background session has none, so export `SUDO_ASKPASS` pointing at a helper that prints the password and the whole install runs unattended. With no helper set the script uses plain `sudo` and behaves exactly as it always did.

**Studio activation is the one step that needs a person.** The key is entered at first launch, in the setup wizard, on the screen. There is no documented headless path for it. Keep the key in the Keychain, never in a file in this repo, which is public:

```bash
python utils/credentials_cli.py get --service davinci-resolve-studio
```

**Which edition is installed** cannot be read from the bundle identifier, which is the same for both. `resolve_api.py status` answers it the only way that counts, by trying the external API, which only Studio exposes.

## Examples

"Assemble these clips into a rough cut I can open in Resolve": build_fcpxml, send the file
"Put these 12 shots on a timeline and render an H.264": queue-job with render, tell user to click Workspace, Scripts, CooCoo Run Job
"Is Resolve installed / running / scriptable?": resolve_api.py status

## Notes

- Unattended installs stall on a fresh machine: the pkg's own setup step touches the Movies folder, which macOS blocks headless, and it hangs forever. install_resolve.sh runs a watchdog that clears it automatically (hit for real here 2026-07-18, 34-minute stall).
- First app launch shows a welcome and quick-setup wizard; someone must click through it once at the screen.
- Never kill the Resolve process from a hook or script; the user may be mid-edit with unsaved work.
- The job file lives at ~/Library/Application Support/CooCoo/resolve_job.json; the menu script writes its result back into it.
- Timeline fps must be decided before clips land; changing it later is a Resolve limitation.
- Verified on this machine 2026-07-18: Resolve 21.0.2 free edition; external API connection refused (as documented), menu-script bridge and fcpxml import are the working paths.
