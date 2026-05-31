# Compute pool

Offload heavy compute jobs — stem separation, image upscaling, Blender renders,
video encodes, ML training — from this MyOldMachine instance to other machines
you own. The machine running the bot is the **orchestrator**; the machines it
sends work to are **workers**.

A worker is just another computer reachable over SSH with key auth. It runs no
service and opens no port. The orchestrator pushes a small self-contained runner
into a per-job directory on the worker, launches it detached, and later reaches
back in over SSH to read a status file and pull results. Jobs are asynchronous:
you submit and walk away; the bot messages you when each one finishes.

## Why

The bot's host might be a 2013 laptop. Routing a Demucs run or a Blender render
to the desktop with the GPU — without leaving Telegram — turns a pile of old
machines into one pool. Capability routing means you can say "anything with a
GPU and 16 GB of RAM" instead of naming a box.

## Trust model

Pairing a worker means the orchestrator can run commands on it as your SSH user
— exactly what you could already do by SSHing in yourself. **Only pair machines
you control.** There is no sandbox on the worker: a job runs with your full user
privileges there, the same as the bot already has on its own host.

The registry stores connection info only — host, SSH user, port, and an optional
path to an identity file. It never stores passwords or private keys; all auth is
your existing SSH key setup (`BatchMode=yes`, so a misconfigured worker fails
fast instead of hanging on a prompt).

Workers and jobs are per Telegram user. Worker records live in
`data/workers.json` keyed by user id; job records and pulled results live under
each user's own data dir. The CLI enforces session binding, so one user can
never submit to, poll, or cancel another user's jobs.

## Prerequisites

On the worker:

- **SSH key auth** from the orchestrator (you can `ssh worker` with no password).
- **`python3`** on the worker's `PATH` (the runner and the capability probe are
  stdlib-only — no packages to install).
- **Linux** is recommended for SSH workers: launching detached uses `setsid`,
  which is not present on stock macOS. A same-machine `local` worker works on
  any platform.

Whatever a job actually needs (ffmpeg, blender, the `claude` CLI, CUDA, …) must
be installed on the worker. The capability probe reports what it finds so you
can route accordingly.

## Quick start

```bash
# Register a worker. This probes it over SSH and records its capabilities.
python utils/worker_cli.py add --user 12345 --name gpubox \
    --host 100.x.y.z --ssh-user me --label "RTX 4090 box"

python utils/worker_cli.py list --user 12345
python utils/worker_cli.py caps --user 12345 --name gpubox     # re-probe

# Submit a command. Everything after `--` runs verbatim on the worker.
python utils/worker_cli.py submit --user 12345 --worker gpubox \
    --input ./song.wav --output stems.zip -- demucs ./song.wav -o .

# Or route by capability instead of naming a worker:
python utils/worker_cli.py submit --user 12345 --needs gpu --min-ram 16 \
    -- python upscale.py in.png out.png

python utils/worker_cli.py jobs   --user 12345                 # list jobs
python utils/worker_cli.py status --user 12345 --id ab12cd34ef56
python utils/worker_cli.py poll   --user 12345                 # advance active jobs
python utils/worker_cli.py cancel --user 12345 --id ab12cd34ef56
python utils/worker_cli.py forget --user 12345 --id ab12cd34ef56
```

## Two modes

- **spine** (default) — the orchestrator sends an exact command and the worker
  runs it verbatim. No LLM is involved on the worker. This is the common case.
- **agent** (opt-in) — wraps `claude -p <task>`, so the worker runs an agent on
  the task you give it. The worker must have the `claude` CLI configured. Pass
  `--mode agent --task "…"` (or pipe the task on STDIN). Everything else is
  identical: agent mode is just a different command.

## Routing by capability

When you don't name a `--worker`, the pool picks the first registered worker
that satisfies the requirements:

- `--needs gpu` — requires a GPU (detected via `nvidia-smi`).
- `--needs ffmpeg` (or `blender`, `git`, …) — requires that binary on the worker.
- `--min-ram 16` — requires at least 16 GB of RAM.

A worker whose capabilities are unknown (never probed) is only usable by naming
it explicitly; it is never auto-selected for a job that has requirements.

## Inputs and outputs

- `--input PATH` (repeatable) — a local file pushed to the worker's job dir
  before launch. Referenced on the worker by its base name.
- `--output NAME` (repeatable) — a file the job writes, pulled back when it
  finishes. Output names must be plain relative paths inside the job dir.

Pulled results land under the submitting user's data dir in
`compute_results/<job_id>/`, alongside the captured `output.log`.

## Job lifecycle

```
submit ──> running ──> completed   (exit 0)
                  └──> failed       (non-zero exit, timeout, or launch error)
   cancel: running ──> canceled     (kills the job's process group on the worker)
```

`submitted` and `running` are active; `completed`, `failed`, and `canceled` are
terminal. `--timeout N` kills a job that runs longer than N seconds.

## Completion notifications

The bot's scheduler runs a background poll loop (every two minutes) that advances
active jobs across all users and sends each owner a Telegram message when one of
their jobs reaches a terminal state. The "already notified" flag is durable, so a
completion is delivered exactly once even across a bot restart or a transient
send failure. The poll loop is a no-op for installs that have never registered a
worker.

You can also advance and inspect jobs by hand at any time with `poll` and
`status`.

## Where things live

| Path | Contents |
|------|----------|
| `data/workers.json` | Worker registry, keyed by Telegram user id |
| `data/users/<id>/compute_jobs.json` | That user's job records |
| `data/users/<id>/compute_results/<job_id>/` | Pulled `output.log` and outputs |
| `<worker home>/.mom-worker/jobs/<job_id>/` | Per-job workspace on the worker |

`forget` deletes a job's local record and, by default, wipes its workspace on the
worker (`--keep-remote` leaves it).

## Limitations

- SSH workers should run Linux (the detached launch relies on `setsid`).
- Routing picks the first matching worker; there is no load balancing or queue
  depth awareness across workers.
- The orchestrator polls; there is no push from the worker, so completion is
  noticed within one poll interval, not instantly.
