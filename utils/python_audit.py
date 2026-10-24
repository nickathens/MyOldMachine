#!/usr/bin/env python3
"""Weekly check of every Python tool kit on this machine against known security holes.

Lists the packages installed in the bot's own .venv and in every tool kit the
nightly kit repair finds (utils/venv_repair.py: the bot's folder and the home
folder, never the folders macOS guards, never another user's private folder),
asks OSV (osv.dev, the open database that carries both the PyPI advisory
database and GitHub's) which of those exact versions have a published
advisory, and sends the admin one message naming the packages they have not
been told about yet, each with the version that fixes it.

It installs nothing. Nothing in the nightly updates the packages inside a
kit: a skill installs what it needs once, and pip never moves a requirement
that is already met, so a kit keeps the versions it was built with. Ported
from the production bot (2026-10-23), whose first run found 48 packages with
known holes. OSV lists, for example, the certifi releases that still trust
the eMudhra root behind eight leaked certificate keys (fixed in 2026.10.19)
and a Brotli decoder flaw (fixed in 1.2.1).

What leaves the machine is each package's name and version, sent to OSV. A
package built from a local folder or a git checkout is left out: OSV knows
only published releases, and the name of a private one has no business
leaving the machine.

Silent when there is nothing new. Loud, once, when it could not check: a check
that fails quietly reads exactly like a check that found nothing. Off with
"python_audit": false in data/maintenance.json.

Usage:
    python utils/python_audit.py              # what the Monday job runs
    python utils/python_audit.py --no-notify  # print it; sends and records nothing
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from utils import venv_repair  # noqa: E402

DATA_DIR = BOT_DIR / "data"
STATE_FILE = DATA_DIR / "python_audit.json"
LOG_FILE = DATA_DIR / "logs" / "python_audit.log"
SEND_SCRIPT = BOT_DIR / "utils" / "send_to_telegram.py"
VENV_PYTHON = BOT_DIR / ".venv" / "bin" / "python"

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/{}"
TIMEOUT = 60
SEVERITY_ORDER = ["low", "moderate", "medium", "high", "critical"]

# Run inside each kit's own interpreter, isolated (-I), so it reports that kit
# and nothing in the current folder can be imported in its place. A
# distribution installed from a local folder or a git checkout carries a
# direct_url.json naming dir_info or vcs_info (PEP 610); those are left out.
LIST_CODE = (
    "import json, importlib.metadata as m\n"
    "def private(d):\n"
    "    try:\n"
    "        info = json.loads(d.read_text('direct_url.json') or '{}')\n"
    "    except ValueError:\n"
    "        return True\n"
    "    return isinstance(info, dict) and ('dir_info' in info or 'vcs_info' in info)\n"
    "print(json.dumps(sorted({(d.metadata['Name'] or '', d.version or '') "
    "for d in m.distributions() if not private(d)})))\n")

_VERSION_PART = re.compile(r"\d+")


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def normalize(name: str) -> str:
    """PEP 503: Brotli, brotli and BROTLI are one project, and so are
    python_dateutil and python-dateutil."""
    return re.sub(r"[-_.]+", "-", name or "").lower()


def kits() -> list[tuple[str, str]]:
    """(label, interpreter) for the bot's .venv and every kit the nightly kit
    repair would look at, other users' private folders left out.

    The repair's own walk, so both look in the same places and skip the same
    ones; it leaves the bot's .venv alone only because it must never change
    it, and this changes nothing.
    """
    roots, skip = venv_repair.default_scan()
    found = venv_repair.find_venvs(roots, [*skip, BOT_DIR / "data" / "users"])
    return [(_label(venv), str(venv / "bin" / "python")) for venv in found]


def _label(path: Path) -> str:
    """'~/.venvs/post' for a kit under the home folder, the full path otherwise."""
    home, text = str(Path.home().resolve()), str(path)
    return "~" + text[len(home):] if text.startswith(home + os.sep) else text


def list_packages(python: str, timeout: int = 60):
    """[(name, version)] installed in a kit, None when it would not say."""
    try:
        proc = subprocess.run([python, "-I", "-c", LIST_CODE], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        pairs = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return [(n, v) for n, v in pairs if n and v] if isinstance(pairs, list) else None


def _post_json(url: str, payload: dict, timeout: int):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "myoldmachine-python-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read(20_000_000).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _get_json(url: str, timeout: int):
    req = urllib.request.Request(url, headers={"User-Agent": "myoldmachine-python-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read(5_000_000).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def osv_ids(packages: list, timeout: int = 60):
    """For each (name, version), the advisory ids OSV lists. None when OSV
    did not answer, never [] for every package, which would read as clean.

    OSV takes up to 1000 queries a call and pages a long answer per query
    (next_page_token); a page loop is bounded so a server that keeps saying
    'more' cannot hold the job.
    """
    out = []
    for start in range(0, len(packages), 1000):
        chunk = packages[start:start + 1000]
        queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in chunk]
        reply = _post_json(OSV_BATCH, {"queries": queries}, timeout)
        results = reply.get("results") if isinstance(reply, dict) else None
        if not isinstance(results, list) or len(results) != len(chunk):
            return None
        for query, result in zip(queries, results):
            ids = [v.get("id") for v in (result or {}).get("vulns") or [] if v.get("id")]
            token, pages = (result or {}).get("next_page_token"), 0
            while token and pages < 20:
                more = _post_json(OSV_BATCH, {"queries": [dict(query, page_token=token)]}, timeout)
                page = (more or {}).get("results") or [None]
                if not isinstance(page[0], dict):
                    return None
                ids += [v.get("id") for v in page[0].get("vulns") or [] if v.get("id")]
                token, pages = page[0].get("next_page_token"), pages + 1
            out.append(ids)
    return out


def _version_key(version: str):
    try:
        from packaging.version import Version
        return (0, Version(version))
    except Exception:  # noqa: BLE001 - no packaging, or not a PEP 440 version
        return (1, tuple(int(p) for p in _VERSION_PART.findall(version or "")))


def first_fix(advisory: dict, name: str, installed: str) -> str:
    """The lowest version above installed that this advisory says fixes it
    for this package, "" when it names none (no fix released yet)."""
    fixes = []
    for affected in advisory.get("affected") or []:
        pkg = affected.get("package") or {}
        if pkg.get("ecosystem") != "PyPI" or normalize(pkg.get("name")) != normalize(name):
            continue
        for rng in affected.get("ranges") or []:
            fixes += [e["fixed"] for e in rng.get("events") or [] if e.get("fixed")]
    # PEP 440 already orders a GPU build's label right (2.6.0+cu118 sits
    # above 2.6.0, so a fix of 2.6.0 counts as installed).
    above = [f for f in fixes if _version_key(f) > _version_key(installed)]
    return min(above, key=_version_key) if above else ""


def severity(advisory: dict) -> str:
    """GitHub's word for it (critical, high, moderate, low), or ""."""
    word = str((advisory.get("database_specific") or {}).get("severity") or "").lower()
    return word if word in SEVERITY_ORDER else ""


def label(ids: set) -> str:
    """The id a person can search for: a CVE when there is one, then GHSA."""
    for prefix in ("CVE-", "GHSA-", "PYSEC-"):
        named = sorted(i for i in ids if i.startswith(prefix))
        if named:
            return named[0]
    return sorted(ids)[0]


def group(ids: list, details: dict) -> list:
    """One set per real issue: PYSEC-2026-4322 and GHSA-9v8h-qqgm-4m35 are
    the same Brotli bug filed twice, and say so in their aliases."""
    groups = []
    for vid in ids:
        names = {vid} | set((details.get(vid) or {}).get("aliases") or [])
        hit = [g for g in groups if g & names]
        merged = names.union(*hit) if hit else names
        groups = [g for g in groups if not (g & names)] + [merged]
    return groups


def audit(timeout: int = TIMEOUT):
    """(findings, problems). A finding is one package in one kit with every
    advisory against its installed version."""
    findings, problems, cache = [], [], {}
    for kit, python in kits():
        packages = list_packages(python, timeout)
        if packages is None:
            problems.append(f"could not list the packages in {kit}")
            continue
        ids = osv_ids(packages, timeout)
        if ids is None:
            problems.append("could not reach OSV, the vulnerability database")
            break
        # Eight at a time: one by one, the production bot's first run of
        # about 400 advisories took 2 min 42 s, and 26 s this way.
        wanted = sorted({v for vids in ids for v in vids} - set(cache))
        with ThreadPoolExecutor(max_workers=8) as pool:
            for vid, detail in zip(wanted, pool.map(
                    lambda v: _get_json(OSV_VULN.format(v), timeout), wanted)):
                cache[vid] = detail
        unread = False
        for (name, version), vids in zip(packages, ids):
            if not vids:
                continue
            if any(not isinstance(cache[v], dict) for v in vids):
                # An advisory whose details did not arrive would show as
                # "no fix released yet" with no severity, and then be marked
                # as told. The week counts as not checked instead.
                unread = True
                break
            live = [v for v in vids if not cache[v].get("withdrawn")]
            issues = group(live, cache)
            if not issues:
                continue
            fixes = [first_fix(cache[v], name, version) for v in live]
            words = [severity(cache[v]) for v in live if severity(cache[v])]
            findings.append({
                "kit": kit, "name": name, "version": version,
                "issues": sorted(label(g) for g in issues),
                # Keyed on the ids OSV returned, never on aliases: a CVE
                # number assigned later must not make an old issue news.
                "keys": sorted(min(v for v in live if v in g) for g in issues),
                "worst": max(words, key=SEVERITY_ORDER.index) if words else "",
                # Every issue's fix is needed, so the highest of them; none
                # when any issue has no fix at all yet.
                "fix": "" if not all(fixes) else max(fixes, key=_version_key),
            })
        if unread:
            problems.append("could not reach OSV, the vulnerability database")
            break
    return findings, problems


def finding_keys(f: dict) -> list:
    return [f"{f['kit']}|{normalize(f['name'])}|{f['version']}|{k}" for k in f["keys"]]


def by_package(findings: list) -> list:
    """One row per project across every kit that has it: the same Pillow
    advisory in six kits is one problem with one fix, not six."""
    rows = {}
    for f in findings:
        row = rows.setdefault(normalize(f["name"]), {
            "name": f["name"], "where": [], "keys": set(), "fixes": [], "worst": ""})
        row["where"].append((f["kit"], f["version"]))
        row["keys"] |= set(f["keys"])
        row["fixes"].append(f["fix"])
        if f["worst"] and (not row["worst"] or SEVERITY_ORDER.index(f["worst"])
                           > SEVERITY_ORDER.index(row["worst"])):
            row["worst"] = f["worst"]
    for row in rows.values():
        row["fix"] = max(row["fixes"], key=_version_key) if all(row["fixes"]) else ""
    rank = {w: i for i, w in enumerate(SEVERITY_ORDER)}
    return sorted(rows.values(), key=lambda r: (-rank.get(r["worst"], -1), normalize(r["name"])))


def _where(row: dict) -> str:
    places = [k for k, _ in row["where"]]
    versions = sorted({ver for _, ver in row["where"]}, key=_version_key)
    shown = ", ".join(places[:3]) + (f" and {len(places) - 3} more" if len(places) > 3 else "")
    return f"{' / '.join(versions)} in {shown}"


def build_message(new: list, problems: list) -> str:
    """Plain words, worst first. Critical and high get a line each, with
    where it is and the version that fixes it; the rest are named."""
    lines = []
    if new:
        rows = by_package(new)
        lines.append(f"Weekly Python check: {len(rows)} "
                     f"{'package has' if len(rows) == 1 else 'packages have'} known security "
                     "holes you have not been told about. Nothing was changed.")
        for word, title in (("critical", "Critical"), ("high", "High")):
            picked = [r for r in rows if r["worst"] == word]
            if picked:
                lines.append(f"{title}:")
                for r in picked:
                    fix = f"fixed in {r['fix']}" if r["fix"] else "no fix released yet"
                    holes = len(r["keys"])
                    lines.append(f"  {r['name']} {_where(r)}: {holes} "
                                 f"{'hole' if holes == 1 else 'holes'}, {fix}")
        rest = [r for r in rows if r["worst"] not in ("critical", "high")]
        if rest:
            lines.append("Moderate, low or unrated: " + ", ".join(r["name"] for r in rest) + ".")
        lines.append("Ask me to update any of them.")
    for p in problems:
        lines.append(f"Weekly Python check {p}.")
    return "\n".join(lines)


def chunks(message: str, limit: int = 3900) -> list:
    """Telegram refuses a message over 4096 characters, so a long report goes
    as several, split between lines."""
    out, cur = [], ""
    for line in message.splitlines():
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def notify_admin(text: str) -> bool:
    """One message to the admin through the bot's Telegram helper, the way
    the nightly report sends. True only when Telegram took it."""
    from core.config import get_primary_admin_id
    admin_id = get_primary_admin_id()
    if not admin_id:
        log("no admin configured, nothing sent")
        return False
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    try:
        proc = subprocess.run([python, str(SEND_SCRIPT), "--user", str(admin_id), "--message", text],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"send_to_telegram did not run: {e}")
        return False
    if proc.returncode != 0:
        log(f"send_to_telegram failed: rc={proc.returncode} {proc.stderr.strip()[-200:]}")
    return proc.returncode == 0


def send(message: str, notify) -> bool:
    """Every piece has to land for the report to count as delivered."""
    return all(bool(notify(part)) for part in chunks(message))


def load_state(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    tmp = Path(f"{path}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def run(notify=None, bank: bool = True):
    """(message, delivered). The 'already told' marks advance only once the
    message has landed, the same rule as the nightly report's. bank=False
    (a look by hand) changes nothing, so the next real run still says it."""
    state = load_state(STATE_FILE)
    told = set(state.get("reported") or [])
    findings, problems = audit()
    new = [f for f in findings if not set(finding_keys(f)) <= told]
    fresh_problems = [p for p in problems if p not in (state.get("problems") or [])]
    message = build_message(new, fresh_problems)
    delivered = send(message, notify) if (message and notify) else True
    if not delivered or not bank:
        return message, delivered
    reached = not any("OSV" in p for p in problems)
    # A package that was updated drops out, so if it ever comes back it is
    # news again. A week OSV could not be reached keeps every old mark.
    current = {k for f in findings for k in finding_keys(f)}
    save_state(STATE_FILE, {"reported": sorted(current if reached else told | current),
                            "problems": problems,
                            "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return message, True


def enabled() -> bool:
    """The maintenance switch, read at run time so turning it off needs no
    restart. Unreadable settings leave it on: a check that goes quiet by
    accident is the failure this exists to prevent."""
    try:
        from utils.maintenance import load_config
        return bool(load_config().get("python_audit", True))
    except Exception:  # noqa: BLE001
        return True


def main() -> int:
    quiet = "--no-notify" in sys.argv
    if not quiet and not enabled():
        log("off in data/maintenance.json, nothing checked")
        return 0
    log("=== weekly python check started ===")
    try:
        message, delivered = run(notify=None if quiet else notify_admin, bank=not quiet)
    except Exception as exc:  # noqa: BLE001
        log(f"FAILED: {exc!r}")
        if not quiet:
            notify_admin(f"Weekly Python check crashed: {exc!r}. Check {LOG_FILE}")
        return 1
    log(("report:\n" + message) if message else "nothing new")
    if message:
        print(message)
        if not quiet:
            log("delivered to telegram" if delivered else "DELIVERY FAILED: nothing recorded")
    log("=== weekly python check complete ===")
    return 0 if (delivered or quiet or not message) else 1


if __name__ == "__main__":
    raise SystemExit(main())
