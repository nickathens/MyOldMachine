# Security Audit Skill

Defensive source-code security auditing. Given a codebase you own or are authorized to review, hunt for exploitable vulnerabilities with real impact and produce a verified, machine-readable findings report a developer can act on.

Adapted from **cloudflare/security-audit-skill** (MIT). The vulnerability taxonomy, finding schema, validator, severity rubric, and anti-patterns are ported faithfully. The orchestration is adapted to run in a single process (see below).

## When to use

- "Audit this repo for security issues", "find vulnerabilities in `<path>`", "review this code for exploits".
- Reviewing your own service, CLI, or library before shipping.
- Re-checking a codebase after a change, targeting a specific subsystem.

Not for: black-box testing of a live target you cannot read (this skill audits source), or dependency CVE scanning on its own (use a lockfile scanner for that; this skill reads code).

## Single-process discipline (read first)

Cloudflare's original fans each phase out to parallel subagents (a recon fleet, one hunter per attack class, one validator per finding, one verifier per finding). A single-process assistant cannot spawn subagents, so this port runs the same phases as **sequential passes in one process**. What makes the method work is preserved:

- **The roles.** The hunt pass is biased toward finding; the validate pass is biased toward *disproving*; the verify pass re-reads the source cold and assumes nothing. Keep these mindsets distinct even though one process runs them all.
- **The independence boundary.** The validate and verify passes reason from the source as it is now, not from the conclusions of the pass that produced the finding. A finding does not exist until you reproduce it against the current code, and you never cite a file, line, or function from memory.
- **The coverage honesty.** One sequential sweep, like one parallel run, finds roughly half of what several sweeps find. Say so in the report and recommend a second run that reads the prior `findings.json` and targets different ground.

Do not re-parallelize this into subagents.

## Setup

Establish two paths before starting:

- **Target**: the codebase to audit (from the request, or the current working directory).
- **Output directory**: where all artifacts go. Default `/tmp/security-audit/<repo-name>/run-<N>` where `<N>` is the next unused integer. Create it. Separate runs never share a directory.

You write every artifact yourself: `architecture.md` (Phase 1 map), `REPORT.md` (human-readable report), `FINDINGS-DETAIL.md` (data flows for MEDIUM+ findings), and `findings.json` (machine-readable, validated).

**Prior runs.** If `/tmp/security-audit/<repo-name>/` already has runs, read their `findings.json` first. Skip re-discovering known issues (mention them, do not re-hunt them), target the gaps they missed, and resolve any conflicting verdicts.

## Core principles

- **Only report what you can exploit.** Every finding needs a concrete attack: who is the attacker, what do they send, what do they get. "An attacker could theoretically" is not a finding.
- **Determine the baseline dynamically.** Identify what this application is and what comparable applications do, and calibrate against them. A pattern exploited in the comparable is a stronger finding; a pattern that has sat unexploited there for years deserves understanding before you report it. Do not hardcode a comparable.
- **Defense-in-depth gaps are not vulnerabilities.** If Layer A already prevents the attack, the absence of Layer B is a hardening note, not a finding, and not a severity multiplier.
- **Severity requires impact.** Severity = likelihood times impact. If you cannot describe the concrete damage, the severity is lower than you think.

### Severity rubric

- **CRITICAL**: Unauthenticated RCE, full database dump, admin account takeover without credentials.
- **HIGH**: Authenticated RCE, SQLi with data exfiltration, stored XSS firing for all users, auth bypass. Also any finding where the permission model is completely defeated for an action with real consequences (publishing, deleting, modifying other users' data).
- **MEDIUM**: Targeted XSS needing specific conditions, CSRF with meaningful state change, disclosure of secrets or credentials. Also business-logic bypasses with real but limited consequences (needs auth, or confined to the attacker's own data, or needs uncommon conditions).
- **LOW**: Disclosure of non-secret data, DoS requiring sustained effort, hardening gaps.

The HIGH-vs-MEDIUM hinge for logic bugs: does the finding defeat an explicit security boundary? A user doing an action the system explicitly gates behind a higher role is HIGH. A data inconsistency, or something that needs privileged access, or has limited blast radius, is MEDIUM.

## The six phases (run in order)

1. **Recon.** Map the app before hunting: entry points (routes, handlers, CLI parsers, message consumers), the auth/authz model, the data layer, and every trust boundary where untrusted input crosses into trusted code. Identify the app type and its baseline comparable. Write `architecture.md`. Every later pass reads it first.
2. **Hunt.** Work the attack classes (`references/methodology.md`) one at a time, as separate passes. Select the classes relevant to this app type; add application-specific ones. For each class, trace untrusted input from entry point to dangerous sink, reading the actual code. Record candidate findings with a provisional file:line trace. Let class scopes overlap; duplicates are consolidated in Phase 3.
3. **Validate (adversarial).** Consolidate duplicates that share a root cause, then for each remaining finding run a pass whose explicit job is to disprove it: exploitation test, impact test, baseline test, mitigation test, parser/runtime test. Resolve each to `CONFIRMED` or `REJECTED` with code evidence. Kill false positives aggressively without killing real findings. An honest "nothing found" is valid, but push hard first.
4. **Report.** Write `REPORT.md` (executive summary, identified baseline, findings table, per-finding attack scenario and impact and fix, hardening notes, positive patterns) and `FINDINGS-DETAIL.md` (full data flow input to sink for each MEDIUM+ finding). Keep it short; if the report is longer than the codebase deserves, you are padding.
5. **Structured output.** Emit every surviving finding per `scripts/report-schema.json` into `<output-dir>/findings.json` (an array). Read the schema first: `additionalProperties: false` is enforced. Validate structure:
   ```bash
   python skills/security-audit/scripts/validate_findings.py <output-dir>/findings.json
   ```
   This is a structural check (required fields, enums, `additionalProperties`, trace entrypoint to sink ordering), not a correctness check. `scripts/findings.example.json` is a valid two-finding template.
6. **Independent verification (final gate).** Re-read every confirmed finding cold, as if you had never seen it. For each: verify the file and line at every trace step, the `scope` (function name), and the `description`; verify `root_cause` by reading the cited file; verify the `execution` payloads would work; verify `conditions` are complete; verify `remediation` prevents the attack without breaking normal behavior. Resolve to `VERIFIED`, `CORRECTED: <field>: <wrong> to <right>`, or `REJECTED`. Apply corrections, re-run the validator, and reconcile `REPORT.md` and `findings.json` so the human-readable and machine-readable outputs never disagree. Do not skip this phase.

## Files

- `scripts/validate_findings.py`: structural validator for `findings.json` (standard library, zero dependencies).
- `scripts/report-schema.json`: the finding schema and single source of truth; the validator reads it directly.
- `scripts/findings.example.json`: a valid two-finding template (one confirmed, one rejected).
- `references/methodology.md`: the full attack-class catalog, detailed phase guidance, and anti-patterns. **Read it before Phase 2.**
