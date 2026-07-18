#!/usr/bin/env python3
"""Runs INSIDE DaVinci Resolve: Workspace menu > Scripts > CooCoo Run Job.

Works on the free edition (scripts inside the app have full API access).
Reads the job queued by resolve_api.py queue-job, imports the media, builds
the timeline, optionally renders, and writes the outcome back into the job
file so the bot can report it.
"""
import json
import os
import time

JOB_FILE = os.path.expanduser("~/Library/Application Support/CooCoo/resolve_job.json")


def get_resolve():
    r = globals().get("resolve")
    if r is not None:
        return r
    app = globals().get("app")
    if app is not None:
        try:
            return app.GetResolve()
        except Exception:
            pass
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def save(job):
    with open(JOB_FILE, "w") as f:
        json.dump(job, f, indent=2)


def fail(job, message):
    job["status"] = "error"
    job["error"] = message
    save(job)
    print("CooCoo: FAILED —", message)


def main():
    if not os.path.exists(JOB_FILE):
        print("CooCoo: no job queued (missing %s)" % JOB_FILE)
        return
    with open(JOB_FILE) as f:
        job = json.load(f)
    if job.get("status") == "done":
        print("CooCoo: last job already done — queue a new one first.")
        return

    r = get_resolve()
    if r is None:
        print("CooCoo: cannot reach the Resolve API from this script context.")
        return

    pm = r.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        project = pm.CreateProject(job.get("timeline", "CooCoo Job"))
        if project is None:
            return fail(job, "no open project and could not create one")

    fps = str(job.get("fps", "")).strip()
    if fps:
        project.SetSetting("timelineFrameRate", fps)

    r.OpenPage("edit")
    pool = project.GetMediaPool()
    media = job.get("media", [])
    missing = [m for m in media if not os.path.exists(m)]
    if missing:
        return fail(job, "media missing on disk: %s" % ", ".join(missing))

    items = pool.ImportMedia(media)
    if not items:
        return fail(job, "media import returned nothing")

    timeline = pool.CreateTimelineFromClips(job.get("timeline", "CooCoo Timeline"), items)
    if timeline is None:
        return fail(job, "could not create timeline (name already taken?)")
    print("CooCoo: timeline '%s' created with %d clips." % (timeline.GetName(), len(items)))

    render = job.get("render")
    if render:
        preset = render.get("preset")
        if preset and not project.LoadRenderPreset(preset):
            return fail(job, "render preset not found: %s" % preset)
        if render.get("out"):
            project.SetRenderSettings({"TargetDir": render["out"]})
        render_job = project.AddRenderJob()
        if not render_job:
            return fail(job, "could not add render job")
        project.StartRendering(render_job)
        print("CooCoo: rendering...")
        while project.IsRenderingInProgress():
            time.sleep(3)
        status = project.GetRenderJobStatus(render_job)
        job["result"] = "render %s -> %s" % (status.get("JobStatus", "?"), render.get("out", "default dir"))
        print("CooCoo:", job["result"])

    job["status"] = "done"
    job["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(job)
    print("CooCoo: job done.")


main()
