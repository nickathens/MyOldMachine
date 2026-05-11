# Coding Skill

Structured methodology for building and debugging software. This skill has no scripts. It is a behavioral protocol loaded into context before significant coding work.

**When to load:** Before any non-trivial implementation, feature build, integration, system change, or debugging session. NOT needed for quick config edits, single-line fixes, or file reads.

**Two protocols, one report format:**
- **Build Protocol** -- creating something new (feature, integration, skill, infrastructure)
- **Diagnose Protocol** -- fixing something broken (bugs, crashes, regressions, mysterious behavior)
- **Methodology Report** -- mandatory deliverable summary appended to every non-trivial delivery

---

## Build Protocol

Follow this process strictly when building anything new:

### 0. Workspace Isolation (git repos only)

Before any non-trivial work in a git repo, set up an isolated workspace.

1. **Detect existing isolation.** If `git rev-parse --git-dir` differs from `git rev-parse --git-common-dir` and you are not in a submodule, you are already in a linked worktree. Skip to Step 1.

2. **For multi-pass audits, multi-file refactors, or anything you might throw away,** create a branch off main: `git checkout -b <slug>/<feature-name>`.

3. **For experimental work that needs full isolation from current state,** use a worktree (preferred for parallel exploration without losing in-progress work on main):
   - Check `.worktrees/` exists and is in `.gitignore`. If not, add it.
   - `git worktree add .worktrees/<branch-name> -b <branch-name>`
   - `cd .worktrees/<branch-name>`
   - Run project setup (pip install / npm install if applicable).
   - Run baseline tests. If they fail, report and ask before proceeding.

4. **Skip if:** the working directory is not a git repo, single-line config edits, file reads, or work explicitly authorized to push direct to main.

Worktrees stay around after the work lands. Clean them up with `git worktree remove .worktrees/<branch-name>` when the branch is merged.

### 1. Research

Conduct thorough web research on the topic. Read official API documentation, changelogs, pricing pages, and model specs. Never rely on training data for facts about external services, libraries, or APIs. Verify every claim against a live source.

"N/A" is only valid for changes that touch no external services, APIs, or libraries.

### 2. Plan

Break the work into concrete steps. Identify dependencies, potential failure points, and integration surfaces. Write the plan down (use todo tracking). If the scope is large, check in with the plan before implementing.

For multi-step builds (anything that takes more than 2-3 tool calls or touches >2 files), the plan must satisfy this rubric before implementation starts:

**File map.** Before listing tasks, enumerate every file being created or modified with a one-line responsibility for each. Locks decomposition in.

**Bite-sized tasks.** Each step is one action: write failing test, run to verify it fails, implement minimal code, run to verify pass, commit. 2-5 minutes per step.

**Exact paths and complete code.** Every task includes:
- Files: Create `exact/path.py` / Modify `exact/path.py:123-145` / Test `tests/path.py`
- Test code shown in full
- Implementation code shown in full
- Exact command to run with expected output

**No placeholders.** These are plan failures, never write them:
- "TBD" / "TODO" / "implement later" / "fill in details"
- "Add appropriate error handling" / "handle edge cases" / "add validation"
- "Write tests for the above" (without the actual test code)
- "Similar to Task N" (repeat the code)
- Steps that describe what to do without showing how

**Self-review before execution.** With the plan written, scan for:
1. Spec coverage -- every requirement maps to a task
2. Placeholder scan -- none of the above red flags survived
3. Type consistency -- function/method names match across tasks

Fix issues inline. Then start implementing.

### 3. Implement

Write the code. Follow existing project conventions. No over-engineering, no speculative abstractions. Keep it clean, minimal, and correct.

### 3b. Lint

Run `ruff check` on all modified files. Fix everything it reports before proceeding. If the project has a `ruff.toml`, ruff uses it automatically. If not, run with `--select F,E741` as a minimum.

This catches dead imports, unused variables, ambiguous names, and f-strings without placeholders. These are things that compile fine but accumulate as debt. The linter is the gate; nothing passes without a clean check.

Also run lint after any audit fixes (step 5), since fixes can introduce new lint issues.

### 4. Test

Run the tests. If no tests exist, write them. Verify the implementation works end-to-end, not just in isolation. Check edge cases.

### 5. Audit (minimum 3 passes)

After tests pass, perform at least 3 independent audit passes of all modified code. Each pass looks for different classes of issues:

- **Pass 1:** Logic errors, off-by-one, missing error handling, race conditions
- **Pass 2:** Security (env leaks, injection, unsafe subprocess), correctness of external API usage, hardcoded values that should be configurable
- **Pass 3:** Integration issues (does this break anything else?), dead code, unused imports, consistency with the rest of the codebase

Fix everything found. Re-run lint and tests after fixes.

### 6. Deliver

Report what was built, what decisions were made, what was found and fixed during audits. Include the Methodology Report (see below).

### 7. Push

Do not push unless told to. When given permission, push to the appropriate remote.

### 8. Update Project Docs

If the work changed the project's file structure (added files, renamed modules, restructured directories), update the relevant project documentation (`CONTEXT.md`, `README.md`, or any `state.json` the project tracks) before reporting done. Keep annotations accurate. This is not optional for structural changes.

---

## Diagnose Protocol

Follow this process when fixing bugs, investigating crashes, or chasing regressions. The phases are sequential. Do not skip ahead.

### Phase 1: Build a Feedback Loop

This is the core of the entire protocol. Before touching anything, create a fast, deterministic, agent-runnable pass/fail signal for the bug. Do NOT start hypothesizing or fixing until you have a loop.

Approaches, in priority order:

1. **Failing test** (unit, integration, or e2e) that reproduces the bug
2. **CLI invocation** diffing stdout against a known-good snapshot
3. **Curl/HTTP script** against a dev server
4. **Headless browser script** (Playwright) asserting on DOM, console, or network state
5. **Replay a captured trace** (saved request, payload, event log)
6. **Throwaway harness** (minimal subset of system, mocked deps, single function call)
7. **Property/fuzz loop** (1000 random inputs for "sometimes wrong" bugs)
8. **Bisection harness** (`git bisect run` between two known states)
9. **Differential loop** (old vs new version, diff outputs)
10. **Human-in-the-loop bash script** (last resort, structured so output feeds back)

Then iterate the loop itself: make it faster, sharper, more deterministic. For non-deterministic bugs, raise the reproduction rate until it's debuggable.

**If you genuinely cannot build a feedback loop, STOP and say so.** Do not proceed without one.

### Phase 2: Reproduce

Run the loop. Confirm it produces the failure the user described (not a different nearby failure). Confirm reproducibility across runs. If the bug manifests differently than reported, flag that immediately.

### Phase 3: Hypothesize

Generate 3 to 5 ranked, falsifiable hypotheses BEFORE testing any. Each must have the form:

> "If X is the cause, then changing Y will make it disappear / changing Z will make it worse."

Present the list. The user often has domain knowledge that instantly re-ranks or eliminates hypotheses. Wait for confirmation before instrumenting.

### Phase 4: Instrument

Each probe maps to a specific hypothesis. Change one variable at a time. Tag debug logs with a unique prefix (e.g. `[DBG-a4f2]`) so cleanup is a single grep. For performance regressions, measure first (timing harness, profiler, query plan), then bisect.

### Phase 5: Fix + Regression Test

Write a regression test before the fix (if a correct seam exists). Watch it fail. Apply the fix. Watch it pass. Re-run the original feedback loop. If the loop still fails, the fix is wrong. Go back to Phase 3.

### Phase 6: Cleanup + Lint + Post-mortem

Remove all debug instrumentation. Delete throwaway prototypes. Run `ruff check` on all modified files and fix any findings. State which hypothesis was correct in the commit message. Ask: what would have prevented this bug? If the answer is "a test," write that test now.

---

## Methodology Report

Every non-trivial delivery MUST include this block. It is the enforcement mechanism. If a line cannot be filled in, go back and do the step.

```
## Methodology Report
- **Research:** [what was checked / sources consulted, or "N/A -- internal-only change"]
- **Plan:** [summary of steps planned, or link to todo]
- **Lint:** [ruff check result -- "clean" or list of what was fixed]
- **Tests:** [what was tested, how, result -- or "wrote tests: <description>"]
- **Audit passes:** [count] -- Findings: [list of issues found]
- **Fixes from audit:** [list of what was fixed, or "none -- clean"]
```

Rules:
- **Audit passes must be >= 3** for any feature, integration, or system change. Writing "1" is an admission of skipping.
- **Research must cite what was actually checked.** "N/A" is only valid for changes that touch no external services, APIs, or libraries.
- **"None" is a valid finding** -- but only after genuinely looking. The report makes skipping visible.
- This block goes at the end of the delivery message, before asking about restart/push.

---

## Changelog Drafting

When a build ships to a client repo, an external user, or any context where someone other than you needs to know what changed, draft a changelog as part of Phase 6 (Deliver). Not on every commit -- on every release-shaped delivery.

### Source

`git log <last-tag>..HEAD --no-merges --format='%h %s'` (or a date range, or a branch range). Read the full set, not the latest commit.

### Categorise

Sort entries into the smallest set of buckets that fits the work:

- **Added** -- new features visible to the user
- **Changed** -- altered behaviour of existing features
- **Fixed** -- bug fixes
- **Removed** -- features deprecated or deleted
- **Security** -- patches with a security dimension
- **Internal** -- refactors, dep bumps, build changes (often dropped from user-facing notes)

### Translate

Each entry must be readable by someone who doesn't know the codebase.

- Drop ticket IDs, commit hashes, file paths, function names from the user-facing line.
- Replace dev language with user language: "wired up async lock at intro_command" -> "fixed a race that could greet new users twice".
- Combine multi-commit work into one line when the commits are part of the same feature.
- One sentence per entry. Verb-first, present tense. No periods if the project's existing changelog doesn't use them.

### Output shapes

- **CHANGELOG.md** -- if the repo already has one, append a new section above the previous one. Match its existing format exactly (date format, heading level, bullet style).
- **Release notes (Markdown)** -- for `gh release create` or a Slack/email blast. Same content, slightly more prose-y intro sentence.
- **Email to a client** -- prose summary first, then the bullet list. Tone matches prior correspondence with that client.

### What to never include

- Internal-only refactors that didn't change behaviour.
- Failed approaches you abandoned mid-sprint.
- Anything an attacker could pivot from (security entries name the fix, not the exploit detail).
- Commit hashes, branch names, PR numbers in user-facing copy.

### Skip when

- One-commit fixes inside the same project the user is actively driving.
- Internal-only experiments that won't ship.
- WIP branches with no consumer yet.

The changelog draft goes alongside the Methodology Report at delivery time, not in a separate message.

---

## Anti-Patterns (things this protocol exists to prevent)

- Asserting system state without reading it first (verification rule violation)
- Hypothesizing without data (skipping Phase 1 and 2 of Diagnose)
- Fixing before reproducing (jumping to Phase 5 without a feedback loop)
- Claiming "the work doesn't exist" without checking the filesystem
- Single-variable conclusions from multi-variable changes
- Audit passes that don't actually look for different issue classes
- Research that says "N/A" when external APIs are involved
