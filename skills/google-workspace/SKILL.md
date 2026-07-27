# google-workspace

Drive, Docs, Sheets and Slides from the command line, through Google's own `gws` CLI.
Nothing else in this repo can read or write them: the `email` and `calendar` skills cover
Gmail and Calendar with their own credentials and their own guardrails, and stay where
they are. `gws` also reaches Tasks, People, Forms, Keep, Meet, Chat, Classroom and Apps
Script.

---

## What it is, and one disclaimer that matters

`gws` is `@googleworkspace/cli`, published on npm by `google-wombot`, Google's internal
publishing bot, with a Google Workspace DevRel engineer as co-maintainer. Apache-2.0.

**It prints "This is not an officially supported Google product" on every `--version`
call, and it is pre-1.0.** Google-authored, not Google-supported. Expect the surface to
move between versions: check `gws <service> --help` before relying on any command in
this document.

## Shape of a command

Commands are generated from Google's Discovery service, so they mirror the REST API one
for one:

```bash
gws <service> <resource> [sub-resource] <method> [flags]
```

```bash
gws drive files list --params '{"pageSize": 10}'
gws docs documents get --params '{"documentId": "..."}'
gws sheets spreadsheets values get --params '{"spreadsheetId":"...","range":"Sheet1!A1:D20"}'
gws slides presentations get --params '{"presentationId": "..."}'
```

| flag | use |
|---|---|
| `--params <JSON>` | query and path parameters |
| `--json <JSON>` | request body for POST, PATCH, PUT |
| `--upload <PATH>` | upload a local file as media |
| `--output <PATH>` | write a binary response to disk (exports, downloads) |
| `--format json\|table\|yaml\|csv` | output shape; json is the default and the one to parse |
| `--page-all` | auto paginate, one JSON object per line (NDJSON), capped by `--page-limit` (default 10) |

When unsure of a parameter name, ask the tool instead of guessing:
`gws schema drive.files.list --resolve-refs`.

Services: `drive`, `sheets`, `docs`, `slides`, `gmail`, `calendar`, `tasks`, `people`,
`chat`, `classroom`, `forms`, `keep`, `meet`, `events`, `script`, `admin-reports`,
`modelarmor`, `workflow`.

## Authentication

Two paths. Check `gws auth status` first: if it reports an account and `token_valid`,
authentication is already done, so do not re-run either path.

**Path A, the normal one.** `gws auth setup` walks the whole thing, but it needs the
`gcloud` CLI installed and it creates a Google Cloud project for the OAuth client.

**Path B, when `gcloud` is absent or a desktop OAuth client already exists on this
machine** (the `email` and `calendar` skills each have one):

```bash
mkdir -p ~/.config/gws
cp <existing-client-secret.json> ~/.config/gws/client_secret.json
chmod 600 ~/.config/gws/client_secret.json
gws auth login --services drive,docs,sheets,slides
```

`gws` hardcodes `~/.config/gws` and ignores `XDG_CONFIG_HOME`, so that path is not a
suggestion.

### Three traps, each of which costs an hour

**1. A missing `project_id` reads as "no client configured".** Desktop OAuth client
files exported from some flows have no `project_id` field. Without it `gws` answers
`401 No OAuth client configured` and tells you to download a client secret, while
pointing at the very file it just refused to parse. Add the field (the numeric project
is the prefix of the client id) and it parses.

**2. A granted scope is not an enabled API.** These are two separate switches, and the
consent screen will happily grant a scope for an API that is switched off on the
project. Reusing a client created for Gmail or Calendar means Drive, Sheets, Docs and
Slides are all off, and every call returns:

```
403 accessNotConfigured
"Google Drive API has not been used in project <N> before or it is disabled."
```

The fix is one click per API, by the human who owns the project, at
`https://console.developers.google.com/apis/api/<api>.googleapis.com/overview?project=<N>`
with `<api>` in `drive`, `sheets`, `docs`, `slides`. Enabling it from here needs
`gcloud` plus the `cloud-platform` scope, which this skill deliberately does not hold.
Propagation takes under a minute.

**3. `cloud-platform` is pre-selected in the scope picker.** It grants the whole Cloud
project, and nothing here needs it. Deselect it. The picker appears even when the
services are named on the command line, so a re-run has to deselect it again.

### Consenting on a headless machine

`gws auth login` opens a loopback redirect on `http://localhost:<random port>`, so it
only completes in a browser that can reach this machine. Over a chat channel:

1. Start the login in a **detached tmux session**, so the flow outlives the current
   turn and keeps its port open.
2. Send the user the consent URL and tell them the browser will fail to open a
   `localhost` page afterwards. That failure is expected.
3. Have them copy the whole failed address (it carries `?code=...`) and paste it back.
4. `curl` that address against this machine's callback port to complete the exchange.

**After any auth change, make one real call per service.** `gws auth status` reporting
`token_valid: true` proves the token, not the reach: it says nothing about whether the
APIs are enabled. A live call is the only proof.

## Sending results to the user

Exports come back as files, so use the normal delivery path:

```bash
gws drive files export --params '{"fileId":"...","mimeType":"application/pdf"}' --output /tmp/doc.pdf
python utils/send_to_telegram.py --user USER_ID --document /tmp/doc.pdf
```

## Boundaries, deliberate

- **Mail stays on the `email` skill.** That skill's `gmail.py` carries the draft
  discipline (write a draft, let the human send it). `gws gmail` has a bare send verb
  and no such guard. Do not move mail here without re-imposing the rule explicitly.
- **Calendar stays on the `calendar` skill.** It works and its token is separate.
- **Local spreadsheets stay on the `spreadsheet` skill**, which evaluates formulas
  through LibreOffice. Use `gws sheets` only for spreadsheets that live in Drive.
- The Drive scope is full read and write over the user's whole Drive, not only files
  this tool created. Confirm destructive calls (`files delete`, `files update` on
  something you did not create) with the user first.
