#!/usr/bin/env python3
"""
Workflow Engine - Run multi-step pipelines from YAML definitions.

Chains shell commands with data piping between steps, conditional execution,
approval gates, and resume-on-crash support.

Usage:
    python workflow.py run <workflow.yaml> [--var key=value ...]
    python workflow.py run <workflow.yaml> --resume
    python workflow.py list
    python workflow.py status <run_id>
    python workflow.py history [--limit 10]

YAML Schema:
    name: extract-vocals
    description: Download video, extract audio, separate stems
    steps:
      - id: download
        command: yt-dlp -o /tmp/wf_{{run_id}}/source.mp4 {{url}}
        on_error: abort          # abort (default) | continue | retry
        retries: 0               # retry count (only if on_error: retry)
      - id: extract
        command: ffmpeg -i /tmp/wf_{{run_id}}/source.mp4 /tmp/wf_{{run_id}}/audio.wav
        depends: [download]      # explicit dependency (auto-inferred if not set)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml

# Directories
WORKFLOW_DIR = Path(__file__).parent.parent / "workflows"
STATE_DIR = Path("/tmp/workflow_runs")
HISTORY_FILE = STATE_DIR / "history.json"


def ensure_dirs():
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


class StepResult:
    """Result of a single step execution."""

    def __init__(self, step_id: str):
        self.step_id = step_id
        self.exit_code: int | None = None
        self.stdout: str = ""
        self.stderr: str = ""
        self.started: str | None = None
        self.finished: str | None = None
        self.status: str = "pending"  # pending | running | completed | failed | skipped
        self.duration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "exit_code": self.exit_code,
            "stdout": self.stdout[-5000:] if self.stdout else "",  # Cap at 5K chars
            "stderr": self.stderr[-2000:] if self.stderr else "",
            "started": self.started,
            "finished": self.finished,
            "status": self.status,
            "duration": self.duration,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StepResult":
        r = cls(data["step_id"])
        r.exit_code = data.get("exit_code")
        r.stdout = data.get("stdout", "")
        r.stderr = data.get("stderr", "")
        r.started = data.get("started")
        r.finished = data.get("finished")
        r.status = data.get("status", "pending")
        r.duration = data.get("duration", 0.0)
        return r


class WorkflowRun:
    """State of a workflow execution."""

    def __init__(self, workflow: dict, run_id: str = None, variables: dict = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.workflow = workflow
        self.name = workflow.get("name", "unnamed")
        self.variables = variables or {}
        self.variables["run_id"] = self.run_id
        self.results: dict[str, StepResult] = {}
        self.status = "pending"  # pending | running | completed | failed | aborted
        self.started: str | None = None
        self.finished: str | None = None
        self.current_step: str | None = None
        self.error: str | None = None

        # Initialize step results
        for step in workflow.get("steps", []):
            sid = step["id"]
            self.results[sid] = StepResult(sid)

    @property
    def state_file(self) -> Path:
        return STATE_DIR / f"{self.run_id}.json"

    def save_state(self):
        """Persist run state to disk for crash recovery."""
        data = {
            "run_id": self.run_id,
            "name": self.name,
            "workflow": self.workflow,
            "variables": self.variables,
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "current_step": self.current_step,
            "error": self.error,
        }
        # Write atomically
        tmp = self.state_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.rename(self.state_file)

    @classmethod
    def load_state(cls, run_id: str) -> "WorkflowRun":
        """Load a run from its state file."""
        state_file = STATE_DIR / f"{run_id}.json"
        if not state_file.exists():
            raise FileNotFoundError(f"No run state found for {run_id}")

        with open(state_file, encoding="utf-8") as f:
            data = json.load(f)

        run = cls(data["workflow"], data["run_id"], data.get("variables", {}))
        run.status = data.get("status", "pending")
        run.started = data.get("started")
        run.finished = data.get("finished")
        run.current_step = data.get("current_step")
        run.error = data.get("error")

        for sid, result_data in data.get("results", {}).items():
            run.results[sid] = StepResult.from_dict(result_data)

        return run

    def resolve_variables(self, text: str) -> str:
        """Replace {{var}} placeholders with values from variables and step results."""
        if not text:
            return text

        def replacer(match):
            expr = match.group(1).strip()

            # Check step references: step_id.stdout, step_id.stderr, step_id.exit_code
            if "." in expr:
                parts = expr.split(".", 1)
                step_id, attr = parts[0], parts[1]
                if step_id in self.results:
                    result = self.results[step_id]
                    if attr == "stdout":
                        return result.stdout.strip()
                    elif attr == "stderr":
                        return result.stderr.strip()
                    elif attr == "exit_code":
                        return str(result.exit_code if result.exit_code is not None else "")
                    elif attr == "status":
                        return result.status

            # Check variables
            if expr in self.variables:
                return str(self.variables[expr])

            # Environment variables
            env_val = os.environ.get(expr)
            if env_val is not None:
                return env_val

            return match.group(0)  # Leave unresolved

        return re.sub(r"\{\{(.+?)\}\}", replacer, text)

    def evaluate_condition(self, condition: str) -> bool:
        """Evaluate a simple condition string. Returns True if condition passes."""
        if not condition:
            return True

        resolved = self.resolve_variables(condition)

        # Simple equality: "value1 == value2"
        if "==" in resolved:
            left, right = resolved.split("==", 1)
            return left.strip() == right.strip()

        # Simple inequality: "value1 != value2"
        if "!=" in resolved:
            left, right = resolved.split("!=", 1)
            return left.strip() != right.strip()

        # Truthy check
        resolved = resolved.strip()
        return resolved not in ("", "0", "false", "False", "none", "None")


class WorkflowEngine:
    """Executes workflow definitions."""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        ensure_dirs()

    def log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    def load_workflow(self, path: str | Path) -> dict:
        """Load a workflow YAML file."""
        path = Path(path)

        # Check workflow directory if not absolute
        if not path.is_absolute() and not path.exists():
            candidates = [
                WORKFLOW_DIR / path,
                WORKFLOW_DIR / f"{path}.yaml",
                WORKFLOW_DIR / f"{path}.yml",
            ]
            for c in candidates:
                if c.exists():
                    path = c
                    break

        if not path.exists():
            raise FileNotFoundError(f"Workflow not found: {path}")

        with open(path, encoding="utf-8") as f:
            wf = yaml.safe_load(f)

        # Validate
        if not wf.get("steps"):
            raise ValueError("Workflow has no steps defined")

        for i, step in enumerate(wf["steps"]):
            if "id" not in step:
                raise ValueError(f"Step {i} missing 'id'")
            if "command" not in step:
                raise ValueError(f"Step '{step['id']}' missing 'command'")

        return wf

    def run(self, workflow: dict, variables: dict = None, resume_run: WorkflowRun = None) -> WorkflowRun:
        """Execute a workflow."""
        if resume_run:
            run = resume_run
            self.log(f"Resuming workflow '{run.name}' (run {run.run_id})")
        else:
            run = WorkflowRun(workflow, variables=variables)
            run.status = "running"
            run.started = datetime.now().isoformat()
            self.log(f"Starting workflow '{run.name}' (run {run.run_id})")

        # Create temp dir for this run
        run_dir = Path(f"/tmp/wf_{run.run_id}")
        run_dir.mkdir(parents=True, exist_ok=True)
        run.variables["run_dir"] = str(run_dir)

        run.status = "running"
        run.save_state()

        steps = workflow.get("steps", [])
        failed = False

        for step in steps:
            sid = step["id"]
            result = run.results.get(sid)

            if not result:
                result = StepResult(sid)
                run.results[sid] = result

            # Skip already completed steps (resume mode)
            if result.status == "completed":
                self.log(f"  [{sid}] already completed, skipping")
                continue

            # Skip if a dependency failed with on_error=abort
            depends = step.get("depends", [])
            if depends:
                # Only skip if a dependency failed AND that dependency used abort (not continue)
                dep_hard_failed = False
                for d in depends:
                    dep_result = run.results.get(d)
                    if dep_result and dep_result.status == "failed":
                        # Find the dep's step definition to check its on_error policy
                        dep_step = next((s for s in steps if s["id"] == d), {})
                        if dep_step.get("on_error", "abort") != "continue":
                            dep_hard_failed = True
                            break
                if dep_hard_failed:
                    result.status = "skipped"
                    self.log(f"  [{sid}] skipped (dependency failed)")
                    run.save_state()
                    continue
            elif failed:
                # No explicit deps -- sequential by default, skip on prior failure
                on_error = step.get("on_error", "abort")
                if on_error == "abort":
                    result.status = "skipped"
                    self.log(f"  [{sid}] skipped (previous step failed)")
                    run.save_state()
                    continue

            # Check condition
            condition = step.get("condition")
            if condition and not run.evaluate_condition(condition):
                result.status = "skipped"
                self.log(f"  [{sid}] skipped (condition not met: {condition})")
                run.save_state()
                continue

            # Resolve command
            command = run.resolve_variables(step["command"])

            # Handle stdin piping from previous step
            stdin_data = None
            stdin_ref = step.get("stdin")
            if stdin_ref:
                stdin_data = run.resolve_variables(stdin_ref)

            # Execute
            run.current_step = sid
            result.status = "running"
            result.started = datetime.now().isoformat()
            run.save_state()

            max_retries = step.get("retries", 0) if step.get("on_error") == "retry" else 0
            timeout = step.get("timeout", 3600)  # Default 1 hour timeout per step

            for attempt in range(max_retries + 1):
                if attempt > 0:
                    self.log(f"  [{sid}] retry {attempt}/{max_retries}")

                self.log(f"  [{sid}] running: {command[:120]}{'...' if len(command) > 120 else ''}")

                try:
                    proc = subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(run_dir),
                        env={**os.environ, "WORKFLOW_RUN_ID": run.run_id, "WORKFLOW_RUN_DIR": str(run_dir)},
                        input=stdin_data,
                    )

                    result.exit_code = proc.returncode
                    result.stdout = proc.stdout
                    result.stderr = proc.stderr

                    if proc.returncode == 0:
                        break  # Success, no need to retry
                    elif attempt < max_retries:
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    # else: fall through to failure handling

                except subprocess.TimeoutExpired:
                    result.exit_code = -1
                    result.stderr = f"Step timed out after {timeout}s"
                    if attempt < max_retries:
                        continue

                except Exception as e:
                    result.exit_code = -2
                    result.stderr = str(e)
                    if attempt < max_retries:
                        continue

            # Determine step outcome
            t_end = datetime.now()
            result.finished = t_end.isoformat()
            if result.started:
                t_start = datetime.fromisoformat(result.started)
                result.duration = (t_end - t_start).total_seconds()

            if result.exit_code == 0:
                result.status = "completed"
                self.log(f"  [{sid}] completed ({result.duration:.1f}s)")
            else:
                result.status = "failed"
                on_error = step.get("on_error", "abort")
                self.log(f"  [{sid}] failed (exit {result.exit_code}): {result.stderr[:200]}")

                if on_error == "abort":
                    failed = True
                    run.error = f"Step '{sid}' failed with exit code {result.exit_code}"
                elif on_error == "continue":
                    pass  # Keep going

            run.save_state()

        # Determine final status
        run.current_step = None
        run.finished = datetime.now().isoformat()

        all_statuses = [r.status for r in run.results.values()]
        if any(s == "failed" for s in all_statuses):
            run.status = "failed"
        elif all(s in ("completed", "skipped") for s in all_statuses):
            run.status = "completed"
        else:
            run.status = "failed"

        run.save_state()
        self._record_history(run)

        # Summary
        completed = sum(1 for s in all_statuses if s == "completed")
        skipped = sum(1 for s in all_statuses if s == "skipped")
        failed_count = sum(1 for s in all_statuses if s == "failed")

        self.log(f"\nWorkflow '{run.name}' {run.status}")
        self.log(f"  Steps: {completed} completed, {skipped} skipped, {failed_count} failed")
        self.log(f"  Run ID: {run.run_id}")
        self.log(f"  State: {run.state_file}")

        return run

    def _record_history(self, run: WorkflowRun):
        """Append run summary to history file."""
        history = []
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, IOError):
                history = []

        entry = {
            "run_id": run.run_id,
            "name": run.name,
            "status": run.status,
            "started": run.started,
            "finished": run.finished,
            "steps_total": len(run.results),
            "steps_completed": sum(1 for r in run.results.values() if r.status == "completed"),
            "error": run.error,
        }

        history.append(entry)

        # Keep last 100 entries
        history = history[-100:]

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)


def cmd_run(args):
    """Run a workflow."""
    engine = WorkflowEngine(verbose=True)

    # Parse variables
    variables = {}
    if args.var:
        for v in args.var:
            if "=" not in v:
                print(f"Error: Variable must be key=value, got: {v}")
                return 1
            key, val = v.split("=", 1)
            variables[key] = val

    if args.resume:
        # Find the most recent run of this workflow and resume it
        wf = engine.load_workflow(args.workflow)
        wf_name = wf.get("name", "unnamed")

        # Look for incomplete runs
        found = None
        for state_file in sorted(STATE_DIR.glob("*.json"), reverse=True):
            if state_file.name == "history.json":
                continue
            try:
                with open(state_file, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("name") == wf_name and data.get("status") in ("running", "failed"):
                    found = data["run_id"]
                    break
            except (json.JSONDecodeError, IOError, KeyError):
                continue

        if not found:
            print(f"No resumable run found for workflow '{wf_name}'. Starting fresh.")
            run = engine.run(wf, variables=variables)
        else:
            print(f"Found resumable run: {found}")
            run = WorkflowRun.load_state(found)
            # Merge any new variables
            run.variables.update(variables)
            run = engine.run(run.workflow, resume_run=run)
    else:
        wf = engine.load_workflow(args.workflow)
        run = engine.run(wf, variables=variables)

    return 0 if run.status == "completed" else 1


def cmd_list(args):
    """List available workflows."""
    ensure_dirs()
    found = False

    for wf_file in sorted(WORKFLOW_DIR.glob("*.yaml")) + sorted(WORKFLOW_DIR.glob("*.yml")):
        try:
            with open(wf_file, encoding="utf-8") as f:
                wf = yaml.safe_load(f)
            name = wf.get("name", wf_file.stem)
            desc = wf.get("description", "")
            steps = len(wf.get("steps", []))
            print(f"  {name:25s} {steps} steps  {desc[:60]}")
            found = True
        except (yaml.YAMLError, IOError):
            continue

    if not found:
        print(f"No workflows found in {WORKFLOW_DIR}")
        print("Create .yaml files there to define workflows.")

    return 0


def cmd_status(args):
    """Show status of a workflow run."""
    try:
        run = WorkflowRun.load_state(args.run_id)
    except FileNotFoundError:
        print(f"Run {args.run_id} not found")
        return 1

    print(f"Workflow: {run.name}")
    print(f"Run ID:  {run.run_id}")
    print(f"Status:  {run.status}")
    print(f"Started: {run.started or 'N/A'}")
    print(f"Finished: {run.finished or 'N/A'}")
    if run.error:
        print(f"Error:   {run.error}")
    print()

    for step in run.workflow.get("steps", []):
        sid = step["id"]
        result = run.results.get(sid)
        if result:
            status_icon = {
                "completed": "+",
                "failed": "X",
                "skipped": "-",
                "running": ">",
                "pending": " ",
            }.get(result.status, "?")
            dur = f" ({result.duration:.1f}s)" if result.duration else ""
            print(f"  [{status_icon}] {sid}: {result.status}{dur}")
            if result.status == "failed" and result.stderr:
                for line in result.stderr.strip().split("\n")[:3]:
                    print(f"      {line[:100]}")
        else:
            print(f"  [ ] {sid}: no data")

    return 0


def cmd_history(args):
    """Show run history."""
    ensure_dirs()

    if not HISTORY_FILE.exists():
        print("No workflow history yet.")
        return 0

    with open(HISTORY_FILE, encoding="utf-8") as f:
        history = json.load(f)

    limit = args.limit or 10
    entries = history[-limit:]

    if not entries:
        print("No workflow history yet.")
        return 0

    for entry in reversed(entries):
        status_icon = {"completed": "+", "failed": "X", "aborted": "!"}.get(entry.get("status", ""), "?")
        name = entry.get("name", "unknown")
        run_id = entry.get("run_id", "?")
        started = entry.get("started", "?")[:19]
        steps = f"{entry.get('steps_completed', 0)}/{entry.get('steps_total', 0)}"
        error = f" -- {entry.get('error', '')[:60]}" if entry.get("error") else ""
        print(f"  [{status_icon}] {started}  {name:25s} {run_id}  ({steps} steps){error}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Workflow Engine - Run multi-step pipelines from YAML definitions"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # run
    p_run = subparsers.add_parser("run", help="Run a workflow")
    p_run.add_argument("workflow", help="Path to workflow YAML file, or name from workflows dir")
    p_run.add_argument("--var", action="append", help="Variable in key=value format (repeatable)")
    p_run.add_argument("--resume", action="store_true", help="Resume the last incomplete run of this workflow")

    # list
    subparsers.add_parser("list", help="List available workflows")

    # status
    p_status = subparsers.add_parser("status", help="Show run status")
    p_status.add_argument("run_id", help="Run ID to check")

    # history
    p_hist = subparsers.add_parser("history", help="Show run history")
    p_hist.add_argument("--limit", type=int, default=10, help="Number of entries to show")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "run": cmd_run,
        "list": cmd_list,
        "status": cmd_status,
        "history": cmd_history,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
