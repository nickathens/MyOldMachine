#!/usr/bin/env python3
"""Drive DaVinci Resolve from outside the app.

External scripting needs the Studio edition. On the free edition every API
command here fails with a clear pointer to the two free-edition bridges:
`queue-job` (one click inside Resolve runs the job) and build_fcpxml.py
(importable timeline file). `status` and `queue-job` always work.
"""
import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path("/Applications/DaVinci Resolve/DaVinci Resolve.app")
API_DIR = Path(os.environ.get(
    "RESOLVE_SCRIPT_API",
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"))
LIB = Path(os.environ.get("RESOLVE_SCRIPT_LIB",
                          str(APP / "Contents/Libraries/Fusion/fusionscript.so")))
JOB_FILE = Path.home() / "Library/Application Support/CooCoo/resolve_job.json"

FREE_EDITION_HINT = (
    "Could not connect to Resolve from outside the app. Either Resolve is not "
    "running, or this is the free edition (external scripting needs Studio), or "
    "Preferences > System > General > 'External scripting using' is set to None.\n"
    "Free-edition bridges: 'queue-job' + the CooCoo Run Job menu script, or "
    "build_fcpxml.py for an importable timeline.")


def app_version():
    info = APP / "Contents/Info.plist"
    if not info.exists():
        return None
    with open(info, "rb") as f:
        return plistlib.load(f).get("CFBundleShortVersionString")


def is_running():
    r = subprocess.run(["pgrep", "-f", "DaVinci Resolve.app/Contents/MacOS"],
                       capture_output=True)
    return r.returncode == 0


def connect():
    """Return (resolve, error_message). resolve is None on failure."""
    os.environ["RESOLVE_SCRIPT_API"] = str(API_DIR)
    os.environ["RESOLVE_SCRIPT_LIB"] = str(LIB)
    modules = API_DIR / "Modules"
    if str(modules) not in sys.path:
        sys.path.insert(0, str(modules))
    try:
        import DaVinciResolveScript as dvr
    except ImportError as e:
        return None, f"Scripting module not importable ({e}). Is Resolve installed?"
    try:
        resolve = dvr.scriptapp("Resolve")
    except Exception as e:
        return None, f"Connection error: {e}"
    if resolve is None:
        return None, FREE_EDITION_HINT
    return resolve, None


def require_project(resolve):
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        sys.exit("No project is open in Resolve.")
    return pm, project


def cmd_status(args):
    installed = APP.exists()
    print(f"app installed : {'yes, ' + str(APP) if installed else 'NO'}")
    if installed:
        print(f"version       : {app_version()}")
    print(f"api module    : {'present' if (API_DIR / 'Modules/DaVinciResolveScript.py').exists() else 'MISSING'}")
    running = is_running()
    print(f"process       : {'running' if running else 'not running'}")
    resolve, err = connect()
    if resolve:
        print(f"external api  : CONNECTED — {resolve.GetProductName()} {resolve.GetVersionString()}")
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        print(f"open project  : {project.GetName() if project else 'none'}")
    else:
        print("external api  : not reachable")
        print(f"                {err.splitlines()[0]}")
    job = read_job()
    if job:
        print(f"queued job    : '{job.get('timeline')}' — status {job.get('status')}")


def cmd_launch(args):
    if not APP.exists():
        sys.exit("Resolve is not installed. Run scripts/install_resolve.sh first.")
    if args.nogui:
        binary = APP / "Contents/MacOS/Resolve"
        subprocess.Popen([str(binary), "-nogui"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        print("Launched Resolve headless (-nogui). Note: headless needs Studio; "
              "on the free edition nothing will come up.")
    else:
        subprocess.run(["open", "-a", str(APP)], check=True)
        print("Launched Resolve (GUI). First launch shows a setup wizard that "
              "needs a person at the screen once.")
    if args.wait:
        deadline = time.time() + args.wait
        while time.time() < deadline:
            resolve, _ = connect()
            if resolve:
                print(f"Connected: {resolve.GetProductName()} {resolve.GetVersionString()}")
                return
            time.sleep(2)
        print("Not reachable from outside within the wait window (normal on the free edition).")


def cmd_quit(args):
    subprocess.run(["osascript", "-e", 'tell application "DaVinci Resolve" to quit'],
                   capture_output=True)
    print("Asked Resolve to quit (it will prompt to save unsaved work).")


def cmd_projects(args):
    resolve, err = connect()
    if not resolve:
        sys.exit(err)
    pm = resolve.GetProjectManager()
    for name in pm.GetProjectListInCurrentFolder():
        print(name)


def cmd_import_media(args):
    resolve, err = connect()
    if not resolve:
        sys.exit(err)
    _, project = require_project(resolve)
    pool = project.GetMediaPool()
    paths = [str(Path(p).resolve()) for p in args.files]
    items = pool.ImportMedia(paths)
    print(f"Imported {len(items) if items else 0} of {len(paths)} files into '{project.GetName()}'.")


def cmd_import_timeline(args):
    resolve, err = connect()
    if not resolve:
        sys.exit(err)
    _, project = require_project(resolve)
    pool = project.GetMediaPool()
    timeline = pool.ImportTimelineFromFile(str(Path(args.file).resolve()))
    if timeline:
        print(f"Imported timeline '{timeline.GetName()}'.")
    else:
        sys.exit("Timeline import failed.")


def cmd_render(args):
    resolve, err = connect()
    if not resolve:
        sys.exit(err)
    _, project = require_project(resolve)
    if args.preset and not project.LoadRenderPreset(args.preset):
        sys.exit(f"Render preset '{args.preset}' not found.")
    settings = {}
    if args.out:
        settings["TargetDir"] = str(Path(args.out).expanduser().resolve())
    if args.name:
        settings["CustomName"] = args.name
    if settings:
        project.SetRenderSettings(settings)
    job = project.AddRenderJob()
    if not job:
        sys.exit("Could not add a render job (is a timeline open?).")
    project.StartRendering(job)
    print("Rendering", end="", flush=True)
    while project.IsRenderingInProgress():
        print(".", end="", flush=True)
        time.sleep(3)
    status = project.GetRenderJobStatus(job)
    print(f"\nRender {status.get('JobStatus', 'finished')}: {status}")


def read_job():
    if JOB_FILE.exists():
        try:
            return json.loads(JOB_FILE.read_text())
        except json.JSONDecodeError:
            return None
    return None


def cmd_queue_job(args):
    media = []
    for p in args.files:
        path = Path(p).expanduser().resolve()
        if not path.exists():
            sys.exit(f"Media not found: {path}")
        media.append(str(path))
    job = {
        "timeline": args.name,
        "fps": args.fps,
        "media": media,
        "render": ({"preset": args.render_preset, "out": str(Path(args.render_out).expanduser().resolve())}
                   if args.render_preset or args.render_out else None),
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": "pending",
    }
    JOB_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOB_FILE.write_text(json.dumps(job, indent=2))
    print(f"Job queued: {len(media)} clips -> timeline '{args.name}' at {args.fps} fps.")
    if job["render"]:
        print(f"Will also render (preset: {args.render_preset or 'current settings'}).")
    print("Next: open Resolve, then Workspace menu > Scripts > CooCoo Run Job.")


def cmd_check_job(args):
    job = read_job()
    if not job:
        print("No job file.")
        return
    print(f"timeline : {job.get('timeline')}")
    print(f"status   : {job.get('status')}")
    for key in ("error", "result", "finished"):
        if job.get(key):
            print(f"{key:9}: {job[key]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="installed / running / reachable / edition")

    p = sub.add_parser("launch", help="start Resolve")
    p.add_argument("--nogui", action="store_true", help="headless (Studio only)")
    p.add_argument("--wait", type=int, default=0, metavar="SEC",
                   help="wait up to SEC seconds for the API to come up")

    sub.add_parser("quit", help="politely quit Resolve")
    sub.add_parser("projects", help="list projects (needs external API)")

    p = sub.add_parser("import-media", help="import files into the open project")
    p.add_argument("files", nargs="+")

    p = sub.add_parser("import-timeline", help="import .fcpxml/.otio/.edl/.aaf timeline")
    p.add_argument("file")

    p = sub.add_parser("render", help="render the current timeline")
    p.add_argument("--preset", help="render preset name")
    p.add_argument("--out", help="output directory")
    p.add_argument("--name", help="output file name")

    p = sub.add_parser("queue-job", help="write a job for the CooCoo Run Job menu script (free edition)")
    p.add_argument("files", nargs="+", help="video clips, in timeline order")
    p.add_argument("--name", default="CooCoo Rough Cut", help="timeline name")
    p.add_argument("--fps", default="25", help="timeline frame rate")
    p.add_argument("--render-preset", help="also render, with this preset")
    p.add_argument("--render-out", help="render output directory")

    sub.add_parser("check-job", help="show the queued job's status")

    args = ap.parse_args()
    {
        "status": cmd_status,
        "launch": cmd_launch,
        "quit": cmd_quit,
        "projects": cmd_projects,
        "import-media": cmd_import_media,
        "import-timeline": cmd_import_timeline,
        "render": cmd_render,
        "queue-job": cmd_queue_job,
        "check-job": cmd_check_job,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
