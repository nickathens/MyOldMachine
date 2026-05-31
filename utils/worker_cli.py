#!/usr/bin/env python3
"""CLI for the MyOldMachine compute pool — offload jobs to worker machines.

A "worker" is another machine the same user owns, reachable over SSH with key
auth. Jobs run asynchronously on the worker; this CLI submits them, polls their
status, and pulls results back. See docs/compute-pool.md for the full model.

Usage:
    # Register a worker (probes it over SSH for capabilities):
    python utils/worker_cli.py add --user 12345 --name gpubox \
        --host 100.x.y.z --ssh-user me --label "RTX 4090 box"

    python utils/worker_cli.py list --user 12345
    python utils/worker_cli.py caps --user 12345 --name gpubox

    # Submit a plain command (spine mode — no LLM on the worker). Everything
    # after `--` is the command run verbatim on the worker:
    python utils/worker_cli.py submit --user 12345 --worker gpubox \
        --input ./song.wav --output stems.zip -- demucs ./song.wav -o .

    # Route by capability instead of naming a worker:
    python utils/worker_cli.py submit --user 12345 --needs gpu --min-ram 16 \
        -- python upscale.py in.png out.png

    # Opt-in agent mode (worker must have the claude CLI configured):
    python utils/worker_cli.py submit --user 12345 --worker gpubox \
        --mode agent --task "Summarize the logs in /var/log and report"

    python utils/worker_cli.py jobs --user 12345
    python utils/worker_cli.py status --user 12345 --id ab12cd34ef56
    python utils/worker_cli.py poll --user 12345
    python utils/worker_cli.py cancel --user 12345 --id ab12cd34ef56
    python utils/worker_cli.py forget --user 12345 --id ab12cd34ef56

Exit codes:
    0  success
    1  bad usage or operation failure
    2  unknown worker/job or a session-binding violation
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import worker  # noqa: E402


def _caps_line(caps: dict) -> str:
    if not caps:
        return "caps: unknown (run `caps` to probe)"
    gpu = caps.get("gpu_name") or ("yes" if caps.get("gpu") else "none")
    return (f"caps: {caps.get('os', '?')}/{caps.get('arch', '?')} "
            f"cpu={caps.get('cpu_cores', '?')} ram={caps.get('ram_gb', '?')}GB "
            f"gpu={gpu}")


def cmd_add(args) -> int:
    ok, msg, record = worker.add_worker(
        args.user, args.name, host=args.host or "", user=args.ssh_user,
        port=args.port, identity_file=args.identity_file, label=args.label or "",
        transport=args.transport, local_root=args.local_root,
        probe=not args.no_probe,
    )
    print(msg)
    if record and record.get("caps"):
        print(f"  {_caps_line(record['caps'])}")
    return 0 if ok else 1


def cmd_list(args) -> int:
    workers = worker.list_workers(args.user)
    if not workers:
        print("No workers registered.")
        return 0
    for name, r in workers.items():
        if r.get("transport") == "local":
            target = f"local:{r.get('remote_root', '')}"
        else:
            user = f"{r['user']}@" if r.get("user") else ""
            port = f":{r['port']}" if r.get("port") else ""
            target = f"{user}{r.get('host', '')}{port}"
        label = f"  ({r['label']})" if r.get("label") else ""
        print(f"  {name}  {target}{label}")
        print(f"      {_caps_line(r.get('caps'))}")
    print(f"\nTotal: {len(workers)} worker(s)")
    return 0


def cmd_remove(args) -> int:
    ok, msg = worker.remove_worker(args.user, args.name)
    print(msg)
    return 0 if ok else 2


def cmd_caps(args) -> int:
    ok, msg, caps = worker.refresh_caps(args.user, args.name)
    print(msg)
    if caps:
        print(f"  {_caps_line(caps)}")
        bins = caps.get("binaries", {})
        present = [b for b, ok_ in bins.items() if ok_]
        print(f"  binaries: {', '.join(present) if present else 'none detected'}")
    return 0 if ok else 2


def _read_task(args) -> str:
    if args.task:
        return args.task
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def cmd_submit(args) -> int:
    argv = None
    task = None
    if args.mode == "agent":
        task = _read_task(args).strip()
        if not task:
            print("Error: agent mode needs --task (or pipe it on STDIN).",
                  file=sys.stderr)
            return 1
    else:
        cmd = list(args.command or [])
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            print("Error: provide the command after `--`, e.g. "
                  "`submit --user 1 --worker w -- echo hi`.", file=sys.stderr)
            return 1
        argv = cmd

    ok, msg, record = worker.submit_job(
        args.user, argv=argv, worker_name=args.worker, mode=args.mode, task=task,
        needs=args.needs, min_ram_gb=args.min_ram, inputs=args.input,
        outputs=args.output, timeout=args.timeout, label=args.label,
    )
    print(msg)
    if record:
        print(f"  job {record['job_id']} state={record['state']}")
    return 0 if ok else 1


def _print_job(r: dict) -> None:
    line = f"  {r['job_id']}  {r['state']:<9}  {r.get('worker', '?')}  {r.get('label', '')}"
    print(line)


def cmd_jobs(args) -> int:
    jobs = worker.list_jobs(args.user)
    if not jobs:
        print("No jobs.")
        return 0
    jobs.sort(key=lambda j: j.get("submitted_at", ""))
    for r in jobs:
        _print_job(r)
    print(f"\nTotal: {len(jobs)} job(s)")
    return 0


def cmd_status(args) -> int:
    r = worker.get_job(args.user, args.id)
    if not r:
        print(f"No job {args.id!r}.", file=sys.stderr)
        return 2
    print(f"Job {r['job_id']}  [{r['state']}]")
    print(f"  worker:   {r.get('worker')}")
    print(f"  mode:     {r.get('mode')}")
    print(f"  command:  {' '.join(r.get('argv', []))}")
    print(f"  submitted:{r.get('submitted_at')}")
    if r.get("started_at"):
        print(f"  started:  {r['started_at']}")
    if r.get("finished_at"):
        print(f"  finished: {r['finished_at']}")
    if r.get("returncode") is not None:
        print(f"  exit code:{r['returncode']}")
    if r.get("error"):
        print(f"  error:    {r['error']}")
    if r.get("result_dir"):
        print(f"  results:  {r['result_dir']}")
    return 0


def cmd_poll(args) -> int:
    if args.id:
        r = worker.poll_job(args.user, args.id)
        if not r:
            print(f"No job {args.id!r}.", file=sys.stderr)
            return 2
        print(f"  {r['job_id']}  {r['state']}")
        return 0
    transitions = worker.poll_user_jobs(args.user)
    if not transitions:
        print("No state changes.")
        return 0
    for r, old in transitions:
        print(f"  {r['job_id']}  {old} -> {r['state']}")
    return 0


def cmd_cancel(args) -> int:
    ok, msg, _r = worker.cancel_job(args.user, args.id)
    print(msg)
    return 0 if ok else 1


def cmd_forget(args) -> int:
    ok, msg = worker.forget_job(args.user, args.id, wipe_remote=not args.keep_remote)
    print(msg)
    return 0 if ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MyOldMachine compute pool CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Register or update a worker.")
    p_add.add_argument("--user", type=int, required=True, help="Telegram user ID.")
    p_add.add_argument("--name", required=True, help="Worker name, e.g. 'gpubox'.")
    p_add.add_argument("--host", help="Hostname or IP (Tailscale name works).")
    p_add.add_argument("--ssh-user", dest="ssh_user", help="SSH username.")
    p_add.add_argument("--port", type=int, help="SSH port (default 22).")
    p_add.add_argument("--identity-file", help="Path to an SSH private key.")
    p_add.add_argument("--label", help="Freeform note.")
    p_add.add_argument("--transport", choices=("ssh", "local"), default="ssh",
                       help="ssh (default) or local (same machine / testing).")
    p_add.add_argument("--local-root", help="Job workspace dir for a local worker.")
    p_add.add_argument("--no-probe", action="store_true",
                       help="Skip the capability probe at registration.")

    p_list = sub.add_parser("list", help="List registered workers.")
    p_list.add_argument("--user", type=int, required=True)

    p_rm = sub.add_parser("remove", help="Remove a worker.")
    p_rm.add_argument("--user", type=int, required=True)
    p_rm.add_argument("--name", required=True)

    p_caps = sub.add_parser("caps", help="Re-probe a worker's capabilities.")
    p_caps.add_argument("--user", type=int, required=True)
    p_caps.add_argument("--name", required=True)

    p_sub = sub.add_parser("submit", help="Submit a job to a worker.")
    p_sub.add_argument("--user", type=int, required=True)
    p_sub.add_argument("--worker", help="Worker name (else routed by --needs).")
    p_sub.add_argument("--needs", action="append", default=[],
                       help="Required capability: gpu, ffmpeg, blender, ... (repeatable).")
    p_sub.add_argument("--min-ram", dest="min_ram", type=float,
                       help="Minimum worker RAM in GB.")
    p_sub.add_argument("--mode", choices=("spine", "agent"), default="spine",
                       help="spine (run command) or agent (claude -p <task>).")
    p_sub.add_argument("--task", help="Task text for agent mode.")
    p_sub.add_argument("--input", action="append", default=[],
                       help="Local file to send to the worker (repeatable).")
    p_sub.add_argument("--output", action="append", default=[],
                       help="Result filename to pull back when done (repeatable).")
    p_sub.add_argument("--timeout", type=int, help="Kill the job after N seconds.")
    p_sub.add_argument("--label", help="Short label for the job.")
    p_sub.add_argument("command", nargs=argparse.REMAINDER,
                       help="For spine mode: `-- <command> [args...]`.")

    p_jobs = sub.add_parser("jobs", help="List jobs.")
    p_jobs.add_argument("--user", type=int, required=True)

    p_status = sub.add_parser("status", help="Show one job's status.")
    p_status.add_argument("--user", type=int, required=True)
    p_status.add_argument("--id", required=True)

    p_poll = sub.add_parser("poll", help="Advance jobs (read remote status).")
    p_poll.add_argument("--user", type=int, required=True)
    p_poll.add_argument("--id", help="Poll one job (default: all active jobs).")

    p_cancel = sub.add_parser("cancel", help="Cancel a running job.")
    p_cancel.add_argument("--user", type=int, required=True)
    p_cancel.add_argument("--id", required=True)

    p_forget = sub.add_parser("forget", help="Delete a job record (and remote workspace).")
    p_forget.add_argument("--user", type=int, required=True)
    p_forget.add_argument("--id", required=True)
    p_forget.add_argument("--keep-remote", action="store_true",
                          help="Leave the job's directory on the worker.")

    return parser


_HANDLERS = {
    "add": cmd_add, "list": cmd_list, "remove": cmd_remove, "caps": cmd_caps,
    "submit": cmd_submit, "jobs": cmd_jobs, "status": cmd_status,
    "poll": cmd_poll, "cancel": cmd_cancel, "forget": cmd_forget,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # Refuse to act on another user's data when the bot has bound this session.
    from utils.session_guard import enforce as _enforce_session_user
    if getattr(args, "user", None) is not None:
        _enforce_session_user(args.user)

    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
