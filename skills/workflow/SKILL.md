# Workflow Engine

Run multi-step pipelines from YAML definitions. Chains shell commands with data piping, conditions, retries, and crash recovery.

## Quick Reference

```bash
W="python skills/workflow/scripts/workflow.py"
```

### Running Workflows

```bash
$W run my-workflow.yaml                  # Run by filename
$W run my-workflow.yaml --resume         # Resume last failed/interrupted run
$W run my-workflow --var url=https://example.com --var output=/tmp/result
```

### Management

```bash
$W list                                  # List available workflows
$W status <run_id>                       # Show run details
$W history                               # Show recent runs
```

## YAML Schema

Workflows live in `skills/workflow/workflows/`

```yaml
name: workflow-name
description: What this workflow does

steps:
  - id: step1
    command: echo "hello {{name}}"      # Shell command with variables
    on_error: abort                      # abort | continue | retry
    retries: 2                           # Number of retries (requires on_error: retry)
    timeout: 300                         # Timeout in seconds (default: 3600)

  - id: step2
    command: cat -                        # Receives stdin from previous step
    stdin: "{{step1.stdout}}"            # Pipe output from step1
    depends: [step1]                     # Explicit dependency
    condition: "{{step1.exit_code}} == 0" # Only run if step1 succeeded

  - id: step3
    command: process-data
    on_error: continue                   # Keep going even if this fails
```

## Variables

Pass variables with `--var key=value`:
- `{{var_name}}` -- replaced with variable value
- `{{run_id}}` -- unique ID for this run (auto-set)
- `{{run_dir}}` -- temp directory at `/tmp/wf_<run_id>/` (auto-set)

### Step Result References

Access results from completed steps:
- `{{step_id.stdout}}` -- stdout from a previous step
- `{{step_id.stderr}}` -- stderr
- `{{step_id.exit_code}}` -- exit code (0 = success)
- `{{step_id.status}}` -- completed | failed | skipped

## Error Handling

Per-step `on_error` behavior:
- `abort` (default) -- stop the workflow, skip remaining steps
- `continue` -- log the error but keep running subsequent steps
- `retry` -- retry the step up to `retries` times with exponential backoff

## Crash Recovery

Every step saves state to `/tmp/workflow_runs/<run_id>.json`. If the process dies mid-workflow:

```bash
$W run workflow-name --resume    # Picks up from last incomplete step
```

Already-completed steps are skipped on resume.

## Notes

- Each run gets its own temp directory at `/tmp/wf_<run_id>/`
- State files in `/tmp/workflow_runs/`
- History capped at 100 entries
- Default step timeout: 1 hour
- Commands run in a shell, so pipes and redirects work
