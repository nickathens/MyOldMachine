# Security Audit Methodology (detailed)

The full attack-class catalog, deep phase guidance, and anti-patterns for the security-audit skill. Read this before Phase 2 (Hunt). `SKILL.md` has the overview, principles, severity rubric, and the six-phase summary; this file is the depth behind them.

Adapted from **cloudflare/security-audit-skill** (MIT). The attack taxonomy, finding schema (`scripts/report-schema.json`), validator (`scripts/validate_findings.py`), severity rubric, and anti-patterns are ported faithfully. What changed is the orchestration: parallel subagents became sequential single-process passes (see the "Single-process discipline" section of `SKILL.md`).

## Phase detail

### Phase 1: Recon

Map the application before hunting. Read entry points (routes, handlers, CLI parsers, message consumers), the auth/authz model, the data layer, and every trust boundary where untrusted input crosses into trusted code. Identify the app type and its baseline comparable. Write `architecture.md`: the input surfaces, the trust boundaries, the roles and what each may do, and a one-paragraph summary of prior runs if any. Every later pass reads this first.

### Phase 2: Hunt

Work through the attack classes below one at a time, as separate passes. Select the classes relevant to this application type from Phase 1; not every class applies to every codebase, and you should add application-specific ones. For a large codebase, split a class per subsystem across passes.

For each class, trace untrusted input from entry point to dangerous sink, reading the actual code. Record candidate findings with a provisional trace (file:line at each step). Deliberately let class scopes overlap; duplicates are consolidated in Phase 3. Push past the easy conclusion: "it uses parameterized queries" is where the hunt starts, not where it ends.

### Phase 3: Validate (adversarial)

First consolidate duplicates: merge candidate findings that share a root cause, so you validate and report each bug once.

Then, for each remaining finding, run a validation pass whose explicit job is to disprove it. Read the actual code at every step of the trace with fresh eyes and apply:

1. **Exploitation test**: Does the data flow work as claimed? Can you construct the exact input (request, CLI invocation, crafted file) that triggers it?
2. **Impact test**: What does the attacker actually get? "They learn field names" or "they cause an error" is LOW at best.
3. **Baseline test**: Does the comparable have the same pattern? If it has never been exploited there in years of production, understand why before reporting.
4. **Mitigation test**: Is there another layer (middleware, DB constraint, framework default) that prevents it?
5. **Parser/runtime test**: If the exploit depends on how a parser or runtime handles input, verify against the spec or test it. Never reason from intuition about parser behavior.

Resolve each finding to exactly one verdict:

- `CONFIRMED: <why it is real, with code evidence>`
- `REJECTED: <what the finding got wrong, with code evidence>`

Kill false positives aggressively without killing real findings. A short report with 3 real findings beats a long one with 30 theoretical ones. An honest "nothing found" is valid, but push hard first.

### Phase 4: Report

Write `REPORT.md`:

- One-paragraph executive summary (honest posture assessment).
- The identified baseline and how this app compares.
- Findings table: severity, title, one-line description.
- Each finding: file path, concrete attack scenario, impact, recommended fix.
- Hardening notes (defense-in-depth suggestions, explicitly not findings).
- Positive patterns (what the codebase does well; this calibrates trust in the findings you do report).

Write `FINDINGS-DETAIL.md` for each MEDIUM+ finding: complete data flow input to sink with file:line references, exact request(s) to trigger, what the attacker gets, and how the baseline handles the same scenario. Keep it short. If the report is longer than the codebase deserves, you are padding.

### Phase 5: Structured output

For every finding that survived Phase 3, emit a structured object per `scripts/report-schema.json` into `<output-dir>/findings.json` (an array). Read the schema before writing; `additionalProperties: false` is enforced, so extra fields make the output invalid. Two verdicts via `oneOf`: `confirmed` (full trace, execution, remediation, severity, confidence) and `rejected` (reason only). If you cannot fill `trace` with real file paths and line numbers verified against the source, the finding is not verified enough: verify it or reject it.

Validate structure:

```bash
python skills/security-audit/scripts/validate_findings.py <output-dir>/findings.json
```

This is a structural check (required fields, enums, `additionalProperties`, and the trace entrypoint to propagation to sink ordering). It does not check that findings are correct; that is Phase 6. Fix any structural failure before proceeding. `scripts/findings.example.json` is a valid two-finding template.

### Phase 6: Independent verification (final gate)

The pass that wrote a finding also wrote its JSON, so it will not catch its own blind spots. Verify every confirmed finding cold: read the source as if you had never seen the finding, and check every factual claim.

For each confirmed finding:

1. Read the file and line at every trace step. Verify the file exists at that path, the line matches the described code, the `scope` (function name) is correct, and the `description` reflects what the code actually does.
2. Verify `root_cause` by reading the cited file and confirming the defect exists there.
3. Verify the `execution` payloads would work: endpoint exists at the claimed URL, method matches, input passes validation as described, auth/access checks pass as described.
4. Verify `conditions` are complete: any missed prerequisite?
5. Verify `remediation.code_changes` would prevent the attack without breaking normal behavior.

Resolve to one of:

- `VERIFIED`: all claims check out.
- `CORRECTED: <field>: <wrong> to <right>`: a factual error in a specific field.
- `REJECTED: <reason>`: fundamentally wrong.

Apply corrections: update the fields and re-run `validate_findings.py`; flip rejected findings to `verdict: "rejected"` with the reason (or remove them). Then reconcile `REPORT.md` and `FINDINGS-DETAIL.md` so they match the final `findings.json` exactly; the human-readable and machine-readable outputs must never disagree. Do not skip this phase.

## Attack classes

Choose the classes that fit the app; add application-specific ones from Phase 1. For native/binary/kernel targets (C/C++/unsafe-Rust, parsers, decoders, JITs, firmware) the web-oriented classes fit poorly: hunt memory safety (buffer overflow, use-after-free, integer overflow, TOCTOU) instead of or alongside them.

**Injection.** Trace untrusted input to a dangerous sink. Sinks vary by app: SQL queries, HTML output, shell commands, template engines, file paths, redirects, deserialization (web); buffer ops, parsers, format strings (libraries); shell construction, path handling, env interpolation (CLI); query construction, message serialization, log/LDAP/XPath injection (services). Look beyond direct paths: indirect (stored safe, retrieved into a dangerous context by other code), injection through field names/keys/headers/metadata (not just values), and injection into secondary systems (logs, caches, search indexes).

**Access control.** Not just "does a permission check exist" but "does it check the right permission for the right resource via the right mechanism." Is there a weaker-permission path to the same state change? Can a request-body field override the intended restriction? Endpoints that gate on authentication but forget authorization? The same resource reachable by multiple paths with inconsistent checks? Do bulk/batch/export/import operations enforce per-item permissions?

**Resource and file handling.** Path traversal (including via symlinks, encoded sequences, null bytes). SSRF (including via redirects, DNS rebinding, URL-parser differentials). Unsafe deserialization, archive extraction (zip slip), temp-file handling. Memory safety where applicable. File-operation races (TOCTOU between check and use).

**Cryptography and secrets.** Weak randomness for security-critical values (tokens, keys, nonces). Hardcoded secrets, or secrets in logs, errors, URLs, or client-visible responses. Broken key derivation, missing HMAC verification, nonce reuse. Timing side-channels on secret comparison. Primitive misuse (ECB, unauthenticated encryption, static IVs). What happens when a crypto op fails: does the error path fall back to no-crypto?

**Business logic.** Where the real bugs hide; scanners cannot find these. State-machine violations (skip steps, go backwards, reach invalid states, replay a completed flow, partial-failure rollback). Business-impact races (double-spend, double-approve, lost updates: check-then-act non-atomically). Numeric/quantity manipulation (negative, zero, overflow, precision loss, string-to-number coercion). Access-boundary violations framed as "is it the right check for the business rule." Implicit trust in data from storage/config/other components ("we validated it on the way in": did a different path write it?). Time-based logic (expiry, scheduling, rate windows, clock skew, boundary moments, timezone differences). Default/fallback posture when config is missing, a flag is off, a dependency is down, or the system is mid-migration.

**Feature abuse and data leakage.** Legitimate features used for unintended ends; hunt the design, not the code. Export/backup as exfiltration (can a low-privilege user export data above their level, other users' data, deleted/draft/private content, unpruned revision history?). Import/restore as injection (overwrite existing data, bypass validation, write into collections they cannot write to?). Search/filter/sort as an oracle (reveal existence of inaccessible content, probe hidden statuses/roles/fields, leak a hidden field's values through result ordering?). Enumeration through side effects (error-message, timing, size, or status-code differences between "does not exist" and "no access"; user enumeration via reset/invite/registration). Preview/draft/staging leakage (preview tokens scoped too broadly, draft content discoverable via search/RSS/sitemap/API, CDN caching private content). Notification/webhook/callback URLs as SSRF.

**Chained attacks and trust boundaries.** Individually-safe behaviors dangerous in combination. Multi-step chains (info disclosure of an ID plus IDOR plus missing rate limit equals brute-forceable; open redirect plus OAuth callback equals token theft). Cross-component trust gaps (A validates and passes to B: does B re-validate, and is A's validation subtly different, for example A allows 255 chars, B truncates at 128?). Second-order attacks (a field name safe in SQL becomes a key in a JSON-path expression; a slug safe in a URL becomes part of a file path; config strings later parsed as URLs/regexes/templates). Scope/capability escalation (tokens or OAuth scopes granting more than their name implies; sessions surviving a role downgrade; AI/MCP tool integrations inheriting the full session). Timing/ordering (use a feature before setup/migration completes; act between soft- and hard-delete; use a token between revocation and cache expiry). Rollback/recovery abuse (undelete/restore/revert restoring more than intended or bypassing current permissions).

**Wildcard.** No category, just the codebase and a mandate to break it. Ignore the standard classes (covered above) and find what nobody thought to look for. Read the boring code. What is the strangest code and why does it exist? What features feel half-finished or bolted on (weakest review, weakest security)? What API calls are possible but the client never makes? Hidden/undocumented endpoints, parameters, headers? Feature combinations never designed to coexist? Anything in git history (reverted security fixes, commented-out auth checks, committed-then-removed secrets still in history)? What would maximum quiet sabotage look like (corrupt data, poison caches, exhaust resources)? What does the code assume about its environment (local DB, accurate clock, trustworthy DNS, case-sensitive filesystem)? What do the tests not test? Follow rabbit holes; if a comment says why something is safe, verify the explanation; if a variable is named `temp`/`hack`/`legacy`, read every line.

**Obvious things.** The dumb stuff everyone assumes someone else checked. Be thorough and literal, not creative. Hardcoded passwords/keys/tokens/secrets (grep `password`, `secret`, `apikey`, `token`, `Bearer`, `-----BEGIN`). Security-relevant TODO/FIXME/HACK/XXX comments (`TODO: add auth`, `HACK: skip permission check`). Debug/dev mode gating (enableable in prod via env/param/header?). Test/example/seed credentials that work in prod. Unprotected `/debug`, `/admin`, `/status`, `/health`, `/metrics`, `/env`, `/.env`, `/config`. `.env`/`credentials.json`/`*.pem`/`*.key` committed to the repo; does `.gitignore` actually cover secrets and uploads? Dependencies pinned; known CVEs in lockfiles. `eval()`/`exec()`/`child_process`/`Function()`/dynamic `import()` with dynamic input. CORS `*` (especially with `Access-Control-Allow-Credentials`). Cookies missing `HttpOnly`/`Secure`/`SameSite`. Open redirects (`redirect`/`return`/`next`/`url`/`goto`/`continue` params). TLS enforced; any HTTP-only endpoints. Prod error responses leaking stack traces, internal paths, or SQL errors. A flag is not a finding: trace the impact before reporting (a cookie missing `HttpOnly` only matters if it holds security-sensitive data JS should not read).

## Anti-patterns (what makes an audit useless)

1. Listing everything that deviates from OWASP as a finding. OWASP is a checklist, not a bug list.
2. Rating defense-in-depth gaps HIGH/CRITICAL.
3. Ignoring the deployment model (CDN-layer rate limiting is a valid architecture).
4. Treating designed behavior as a bug (understand the trust model: if admins are fully trusted, admin-does-admin-things is not a finding).
5. Padding with LOWs to look thorough. Three real MEDIUMs beat ten LOWs.
6. "Potential" / "theoretical" findings without proof. Either you can exploit it or you cannot.
7. Ignoring what the codebase does well; saying so builds trust in the findings you report.
8. Constructing exploits from unverified parser/runtime assumptions. Cite the spec or test it.
9. Skipping business logic and creative attacks; the standard classes are what scanners already do.
10. Giving up too easily. Check every `sql.raw()`, every dynamic identifier, every bypass path. Push.
