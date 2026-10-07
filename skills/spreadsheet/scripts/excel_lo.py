#!/usr/bin/env python3
"""
Excel helper using LibreOffice UNO bridge.

Run it with /usr/bin/python3. On Linux that is the Python python3-uno serves;
on macOS any Python 3 will do (see "Where the command runs" below).

Usage:
    /usr/bin/python3 excel_lo.py <command> <file> [options]

Commands:
    info <file>
        Show sheet names and dimensions.

    read <file> --sheet <name> [--range A1:E10]
        Read cells from a sheet. Outputs JSON.

    write <file> --sheet <name> --cell A1 --value "text" [--decimal comma|dot] [--as-text]
        Write a value to a cell.

    add-rows <file> --sheet <name> --after <row> --data <json_file> [--decimal comma|dot] [--as-text]
        Insert rows from a JSON file after the specified row.
        JSON format: [["val1", "val2", ...], ...]

    Text becomes a number only when the number is unambiguous. '2.500' is
    two thousand five hundred in Greek and two and a half in English, so it
    is refused until --decimal says which ('comma' = Greek, 'dot' = English).
    IDs with leading zeros ('0012'), 'nan', '1e5' and anything with a
    currency sign or percent stay text.

    formula <file> --sheet <name> --cell A1 --formula "=SUM(B1:B10)"
        Set a formula in a cell.

    add-sheet <file> --name <sheet_name> [--after <existing_sheet>]
        Add a new sheet.

    save-as <file> --output <output_file> [--format xlsx|pdf|csv] [--sheet <name>]
        Save/export to another format. CSV holds one sheet: the active one,
        or the one --sheet names.

    eval-formulas <file>
        Force recalculation of all formulas and save.

    stop
        Close the LibreOffice copies this tool left running for this user,
        which only happens when a command is killed before it can close its
        own. Nothing else is touched: not a LibreOffice window, not another
        user's copy, not a command that is still running.

Every command runs in a private LibreOffice of its own, on a fresh profile in
a new temporary folder, and closes that copy, and only that copy, when it
ends. One OS account hosts every user of the bot. The old
``pkill -f soffice.bin`` after every command closed every LibreOffice on a
Linux machine, other users' open windows included; on macOS, where the process
is called soffice, it matched nothing and every command left its LibreOffice
running (2026-10-07).

Where the command runs: when the Python running this script can import uno
(Linux with python3-uno), the command talks to its LibreOffice over a pipe
named after its folder. When it cannot, which is every Python on macOS
(/usr/bin/python3 has no uno, and the Python inside LibreOffice.app is signed
so that only LibreOffice itself may start it), the command runs inside its
LibreOffice as a Python macro and hands its output back through a file.
"""

import argparse
import contextlib
import datetime
import glob
import io
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


LO_START_TIMEOUT = 60  # seconds for a private LibreOffice to come up (2 s on the Mac)
LO_JOB_TIMEOUT = 600   # seconds a command may run inside its LibreOffice
LO_STOP_TIMEOUT = 20   # seconds it gets to close by itself before it is stopped
RUN_PREFIX = "mom-excel-lo-"
JOB_ENV = "MOM_EXCEL_LO_JOB"
MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"

_OFFICE = None   # the private LibreOffice this process started
_DESKTOP = None  # its Desktop


def _owner_tag():
    """Whose runs these are, for the folder name: the bot user, or 'local'."""
    uid = os.environ.get("JARVIS_USER_ID", "").strip()
    return uid if re.fullmatch(r"[0-9]+", uid) else "local"


def _soffice():
    found = shutil.which("soffice")
    if found:
        return found
    if os.path.exists(MAC_SOFFICE):
        return MAC_SOFFICE
    raise RuntimeError("LibreOffice is not installed: there is no soffice on PATH")


class PsFailed(RuntimeError):
    """The process table could not be read, so nothing can be proved from it."""


def _ps(*args):
    """ps output in the C locale, so a start time reads the same to every caller.

    A ps that cannot run raises: "could not look" must never read as "nothing
    there", or a live command would be judged gone.
    """
    try:
        return subprocess.run(["ps", *args], capture_output=True, text=True, timeout=10,
                              env=dict(os.environ, LC_ALL="C")).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise PsFailed(f"could not read the process table ({exc}); nothing was touched") from None


def _process_start(pid):
    """When a process started, as ps tells it, or None when there is no such process."""
    if not isinstance(pid, int):
        return None
    return _ps("-o", "lstart=", "-p", str(pid)).strip() or None


def _process_table():
    """(pid, argv words) for every process on the machine."""
    rows = []
    for line in _ps("-A", "-ww", "-o", "pid=,command=").splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit():
            rows.append((int(pid), command.split()))
    if not rows:  # this process at least is always there
        raise PsFailed("ps listed no processes; nothing was touched")
    return rows


def _write_json(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _marker(folder):
    """The argument that puts a LibreOffice on a run's private profile.

    The folder's name is random and made for one run, so a process carrying
    this exact argument is one that run started: that is the ownership proof,
    never a process name.
    """
    return f"-env:UserInstallation={Path(folder, 'profile').as_uri()}"


def _carrying(marker, table=None):
    """PIDs whose command line carries this exact marker."""
    rows = _process_table() if table is None else table
    return [pid for pid, words in rows if marker in words and pid != os.getpid()]


def _signal_each(pids, marker):
    """TERM, then KILL what is left, checking each PID still carries the marker."""
    def still_ours(pid):
        return marker in _ps("-ww", "-o", "command=", "-p", str(pid)).split()

    for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        live = [pid for pid in pids if still_ours(pid)]
        for pid in live:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.monotonic() + grace
        while live and time.monotonic() < deadline:
            time.sleep(0.2)
            live = [pid for pid in live if still_ours(pid)]
        if not live:
            return


class _Office:
    """One private LibreOffice: a new folder holds its profile, its pipe is
    named after the folder, and owner.json says which command started it."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix=f"{RUN_PREFIX}{_owner_tag()}-")
        self.marker = _marker(self.dir)
        self.pipe = os.path.basename(self.dir)
        self.proc = None
        try:
            started = _process_start(os.getpid())
        except PsFailed:
            started = None  # stop never judges a run without one gone
        _write_json(os.path.join(self.dir, "owner.json"),
                    {"pid": os.getpid(), "started": started,
                     "user": os.environ.get("JARVIS_USER_ID") or None})

    def launch(self, *args, env=None):
        # Not a session of its own: when a timeout kills this command's
        # process group, its LibreOffice goes with it instead of living on.
        with open(os.path.join(self.dir, "soffice.log"), "wb") as log:
            self.proc = subprocess.Popen(
                [_soffice(), "--headless", "--norestore", "--nologo", self.marker, *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, env=env)

    def log_tail(self, limit=600):
        try:
            with open(os.path.join(self.dir, "soffice.log"), encoding="utf-8",
                      errors="replace") as fh:
                lines = [ln for ln in fh.read().splitlines()
                         if ln.strip() and not ln.startswith("Fontconfig")]
        except OSError:
            return ""
        return " | ".join(lines)[-limit:]

    def close(self, grace=LO_STOP_TIMEOUT):
        """Close this LibreOffice, and nothing else, then remove its folder."""
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        # A LibreOffice that forks (soffice.bin under oosplash on Linux) can
        # outlive the process started here, still carrying the marker. When ps
        # cannot be read nothing is signalled: a leak, never a wrong kill.
        with contextlib.suppress(PsFailed):
            leftover = _carrying(self.marker)
            if leftover:
                _signal_each(leftover, self.marker)
        shutil.rmtree(self.dir, ignore_errors=True)


def start_libreoffice():
    """Start this command's own LibreOffice and connect to it over its pipe."""
    global _OFFICE, _DESKTOP
    import uno
    office = _OFFICE = _Office()
    office.launch(f"--accept=pipe,name={office.pipe};urp;StarOffice.ComponentContext")
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    deadline = time.monotonic() + LO_START_TIMEOUT
    while True:
        try:
            ctx = resolver.resolve(
                f"uno:pipe,name={office.pipe};urp;StarOffice.ComponentContext")
            break
        except Exception:
            if office.proc.poll() is not None:
                raise RuntimeError("LibreOffice closed before it was ready: "
                                   + office.log_tail()) from None
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"LibreOffice did not start within {LO_START_TIMEOUT} s") from None
            time.sleep(0.5)
    _DESKTOP = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)


def stop_libreoffice():
    """Close the LibreOffice this command started, and nothing else."""
    global _OFFICE, _DESKTOP
    office, desktop = _OFFICE, _DESKTOP
    _OFFICE = _DESKTOP = None
    if office is None:
        return
    if desktop is not None:
        try:
            desktop.terminate()
        except Exception:
            pass  # the bridge drops as the office goes down
    office.close(LO_STOP_TIMEOUT if desktop is not None else 0)


def get_desktop():
    """The Desktop of this command's own LibreOffice, started on first use."""
    if _DESKTOP is None:
        start_libreoffice()
    return _DESKTOP


def _uno_importable():
    try:
        import uno  # noqa: F401
    except ImportError:
        return False
    return True


def run_in_office(args):
    """Run one command inside a private LibreOffice, as a Python macro.

    For a Python that cannot import uno, which is every Python on macOS.
    Returns what the command printed and its error (None when it worked).
    """
    office = _Office()
    finished = False
    try:
        scripts = Path(office.dir, "profile", "user", "Scripts", "python")
        scripts.mkdir(parents=True)
        shutil.copyfile(os.path.abspath(__file__), scripts / "excel_lo.py")
        job = os.path.join(office.dir, "job.json")
        _write_json(job, {"args": vars(args)})
        office.launch("vnd.sun.star.script:excel_lo.py$run_job?language=Python&location=user",
                      env=dict(os.environ, **{JOB_ENV: job}))
        started = os.path.join(office.dir, "started")
        result_path = os.path.join(office.dir, "result.json")
        t0 = time.monotonic()
        while not os.path.exists(result_path) and office.proc.poll() is None:
            waited = time.monotonic() - t0
            if waited > LO_START_TIMEOUT and not os.path.exists(started):
                return "", f"LibreOffice did not start the command within {LO_START_TIMEOUT} s"
            if waited > LO_JOB_TIMEOUT:
                return "", f"the command did not finish within {LO_JOB_TIMEOUT} s"
            time.sleep(0.1)
        result = _read_json(result_path)
        if not isinstance(result, dict):
            why = ("in the middle of the command" if os.path.exists(started)
                   else "without running the command (is its Python scripting installed?)")
            tail = office.log_tail()
            return "", f"LibreOffice closed {why}" + (f": {tail}" if tail else "")
        finished = True
        return result.get("stdout") or "", result.get("error")
    finally:
        office.close(LO_STOP_TIMEOUT if finished else 0)


def run_job(*_):
    """The macro run_in_office starts inside its private LibreOffice."""
    global _DESKTOP
    job = os.environ.get(JOB_ENV)
    if not job:
        return
    folder = os.path.dirname(job)
    result = {"stdout": "", "error": None}
    desktop = None
    try:
        open(os.path.join(folder, "started"), "w").close()
        spec = _read_json(job)
        if not isinstance(spec, dict):
            raise RuntimeError("the command's job file could not be read")
        args = argparse.Namespace(**spec["args"])
        import uno
        ctx = uno.getComponentContext()
        desktop = _DESKTOP = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                _commands()[args.command](args)
        finally:
            result["stdout"] = out.getvalue()
    except Exception as exc:
        result["error"] = str(exc) or type(exc).__name__
    finally:
        _DESKTOP = None
        _write_json(os.path.join(folder, "result.json"), result)
        if desktop is not None:
            try:
                desktop.terminate()
            except Exception:
                pass


def stop_leftovers():
    """Close the LibreOffice copies this tool left behind for this user.

    A copy is closed only when it carries one of this user's private profiles
    on its command line AND the command that started it is provably gone (its
    PID no longer has the start time recorded for it). A command still
    running, another user's copy and any LibreOffice window are left alone.
    """
    stopped, in_use = [], []
    table = _process_table()
    pattern = os.path.join(tempfile.gettempdir(), f"{RUN_PREFIX}{_owner_tag()}-*")
    for folder in sorted(glob.glob(pattern)):
        owner = _read_json(os.path.join(folder, "owner.json"))
        if not isinstance(owner, dict) or not owner.get("started"):
            continue  # being created this instant, or nothing to prove it by
        marker = _marker(folder)
        pids = _carrying(marker, table)
        if _process_start(owner.get("pid")) == owner["started"]:
            in_use.extend(pids)
            continue
        if pids:
            _signal_each(pids, marker)
            stopped.extend(pids)
        shutil.rmtree(folder, ignore_errors=True)
    if stopped:
        message = f"Closed {len(stopped)} LibreOffice process(es) this tool had left running."
    else:
        message = "Nothing to close: no LibreOffice this tool started for you is left running."
    if in_use:
        message += f" {len(in_use)} belong(s) to a command still running and were left alone."
    message += " LibreOffice windows and other users' copies are never touched."
    return {"status": "ok", "stopped": stopped, "in_use": in_use, "message": message}


def _url_path(url):
    return unquote(urlparse(url).path)


def _read_lock(lock):
    """A LibreOffice lock file's fields: name, OS user, host, time, profile URL."""
    try:
        with open(lock, encoding="utf-8", errors="replace") as fh:
            body = fh.read().strip()
    except OSError:
        return None
    fields = [f.replace("\\,", ",") for f in re.split(r"(?<!\\),", body.rstrip(";"))]
    return fields if len(fields) >= 5 else None


def _profile_in_use(profile):
    """Is any process running on this LibreOffice profile?"""
    want = os.path.realpath(profile)
    for _, words in _process_table():
        for word in words:
            if (word.startswith("-env:UserInstallation=")
                    and os.path.realpath(_url_path(word.split("=", 1)[1])) == want):
                return True
    return False


def clear_stale_lock(path):
    """Remove the lock a killed copy of this tool left on a file; name any other holder.

    LibreOffice will not open a file whose lock names another profile, and
    headless it cannot ask, so one command killed with the file open made every
    later command on it fail with "Failed to open" (2026-10-07), and told anyone
    opening it by hand that someone had it. A lock is removed only when this
    machine wrote it from one of this tool's private profiles and nothing runs
    on that profile any more. Returns who holds a lock that stays, or None.
    """
    lock = os.path.join(os.path.dirname(path), f".~lock.{os.path.basename(path)}#")
    fields = _read_lock(lock)
    if fields is None:
        return None
    name, user, host, when, url = fields[:5]
    profile = _url_path(url)
    ours = (host == socket.gethostname() and os.path.basename(profile) == "profile"
            and os.path.basename(os.path.dirname(profile)).startswith(RUN_PREFIX))
    try:
        if ours and not _profile_in_use(profile):
            with contextlib.suppress(FileNotFoundError):
                os.remove(lock)
            return None
    except PsFailed:
        pass  # cannot prove it stale: leave it
    return f"{name or user} on {host}, since {when}"


def _props(**values):
    from com.sun.star.beans import PropertyValue
    props = []
    for name, value in values.items():
        prop = PropertyValue()
        prop.Name, prop.Value = name, value
        props.append(prop)
    return tuple(props)


def open_document(desktop, filepath, write=False):
    """Open a document and return it.

    A command that only reads opens it read-only, which writes no lock file
    (so a read that is killed leaves nothing behind) and reads a file someone
    has open. A command that writes refuses a file someone else has open
    rather than race their save: LibreOffice refused such a file outright when
    the lock came from this OS account, and opened it read-only, to fail at
    store, when it came from another (measured 2026-10-07).
    """
    abspath = os.path.abspath(filepath)
    holder = clear_stale_lock(abspath)
    if write and holder:
        raise RuntimeError(f"{filepath} is open in another LibreOffice ({holder}), so nothing "
                           "was written. Close it there and run the command again.")
    url = "file://" + quote(abspath, safe="/:@")
    doc = desktop.loadComponentFromURL(url, "_blank", 0,
                                       () if write else _props(ReadOnly=True))
    if not doc:
        raise RuntimeError(f"Failed to open: {filepath}")
    if write and doc.isReadonly():
        doc.close(True)
        raise RuntimeError(f"{filepath} opened read-only (open somewhere else, or not "
                           "writable), so nothing was written.")
    return doc


def col_letter_to_index(letter):
    """Convert column letter(s) to 0-based index. A=0, B=1, Z=25, AA=26."""
    result = 0
    for char in letter.upper():
        result = result * 26 + (ord(char) - ord('A') + 1)
    return result - 1


def parse_cell_ref(ref):
    """Parse a cell reference like 'A1' into (col_index, row_index)."""
    match = re.match(r'^([A-Za-z]+)(\d+)$', ref)
    if not match:
        raise ValueError(f"Invalid cell reference: {ref}")
    col = col_letter_to_index(match.group(1))
    row = int(match.group(2)) - 1  # 0-based
    return col, row


def parse_range(range_str):
    """Parse a range like 'A1:E10' into ((col1, row1), (col2, row2))."""
    parts = range_str.split(':')
    if len(parts) != 2:
        raise ValueError(f"Invalid range: {range_str}")
    return parse_cell_ref(parts[0]), parse_cell_ref(parts[1])


class AmbiguousNumber(ValueError):
    """A value that reads as two different numbers depending on the locale."""


# ASCII digits only: Python's \d also matches Arabic-Indic and other digits.
_INT = re.compile(r"[+-]?[0-9]+")
_ONE_MARK = re.compile(r"([+-]?)([0-9]+)([.,])([0-9]+)")
_GROUPED = {
    # thousands mark, decimal mark: 1.234.567,89 and 1,234,567.89
    "comma": re.compile(r"([+-]?)([1-9][0-9]{0,2}(?:\.[0-9]{3})+)(?:,([0-9]+))?"),
    "dot": re.compile(r"([+-]?)([1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.([0-9]+))?"),
}
_DECIMAL_HINT = (
    "pass --decimal comma for Greek numbers (2.500 = two thousand five "
    "hundred), --decimal dot for English ones (2.500 = two and a half), or "
    "--as-text to keep them exactly as typed"
)


def _number_from(sign, whole, frac):
    return float(f"{sign}{whole}.{frac}" if frac else f"{sign}{whole}")


def parse_number(text, decimal=None):
    """The number a cell text stands for, or None when it should stay text.

    ``decimal`` is 'comma' (Greek: 1.234,56), 'dot' (English: 1,234.56) or
    None. With None, a value whose meaning depends on the locale ('2.500',
    '1,234') raises AmbiguousNumber instead of guessing: the old guess read
    every Greek thousands amount as a small decimal.
    """
    t = text.strip()
    if not t:
        return None
    if _INT.fullmatch(t):
        digits = t.lstrip("+-")
        if len(digits) > 1 and digits.startswith("0"):
            return None  # an ID or invoice number, not a quantity
        return float(t)

    m = _ONE_MARK.fullmatch(t)
    if m:
        sign, whole, mark, frac = m.groups()
        could_be_thousands = (len(frac) == 3 and len(whole) <= 3
                              and not whole.startswith("0"))
        if decimal is None:
            if could_be_thousands:
                raise AmbiguousNumber(f"'{t}' could be {whole}{frac} or {whole}.{frac}")
            return _number_from(sign, whole, frac)
        decimal_mark = "," if decimal == "comma" else "."
        if mark == decimal_mark:
            return _number_from(sign, whole, frac)
        if could_be_thousands:
            return _number_from(sign, whole + frac, "")
        return None

    for style, rx in _GROUPED.items():
        if decimal is not None and style != decimal:
            continue
        g = rx.fullmatch(t)
        if g:
            sign, grouped, frac = g.groups()
            return _number_from(sign, re.sub(r"[.,]", "", grouped), frac or "")
    return None


def _cell_name(col, row):
    """0-based (col, row) to an A1 name."""
    letters = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return f"{letters}{row + 1}"


def _num(val):
    """Whole-number floats become ints; NaN and inf pass through untouched.

    Guards the bare ``val == int(val)`` comparison, which raises ValueError on
    NaN and OverflowError on inf, so one such cell no longer ends the whole read.
    """
    if math.isfinite(val) and val == int(val):
        return int(val)
    return val


def serial_to_iso(value, null_date, has_date, has_time):
    """A spreadsheet date serial as ISO text, or None to keep the number.

    ``null_date`` is the document's day zero as (year, month, day): LibreOffice
    and Excel both use 1899-12-30 unless a file says otherwise. Rounded to the
    second, because 14:05 is stored as 0.58680555... A time-only cell of a day
    or more is a duration ([HH]:MM), which a clock time would misstate, so it
    stays a number.
    """
    if not (has_date or has_time) or not math.isfinite(value):
        return None
    if has_time and not has_date and not 0 <= value < 1:
        return None
    try:
        moment = (datetime.datetime(*null_date)
                  + datetime.timedelta(seconds=round(value * 86400)))
    except (OverflowError, ValueError):
        return None
    if has_date and not has_time:
        return moment.date().isoformat()
    if has_time and not has_date:
        return moment.time().isoformat()
    return moment.isoformat(sep=" ")


def _date_text(cell, doc):
    """ISO text for a cell formatted as a date, time or both; None otherwise."""
    if doc is None:
        return None
    try:
        from com.sun.star.util.NumberFormat import DATE, TIME
        kind = doc.getNumberFormats().getByKey(cell.NumberFormat).Type
        null = doc.NullDate
    except Exception:
        return None
    return serial_to_iso(cell.getValue(), (null.Year, null.Month, null.Day),
                         bool(kind & DATE), bool(kind & TIME))


def get_cell_value(cell, doc=None):
    """Get the display value of a cell.

    A cell formatted as a date comes back as ISO text ("2026-10-07"), not the
    serial number it is stored as: read on an invoice or expenses sheet, 46302
    is not a date anyone can check (Linux bot sweep 2026-10-07). ``doc``
    supplies the number formats and the document's day zero.
    """
    from com.sun.star.table.CellContentType import EMPTY, VALUE, TEXT, FORMULA
    ctype = cell.getType()
    if ctype == EMPTY:
        return ""
    elif ctype == VALUE:
        dated = _date_text(cell, doc)
        return dated if dated is not None else _num(cell.getValue())
    elif ctype == TEXT:
        return cell.getString()
    elif ctype == FORMULA:
        # A numeric result in a date format reads as ISO text; its display
        # string ("10/08/26") is a locale's guess at month and day order.
        try:
            from com.sun.star.sheet.FormulaResult import VALUE as NUMERIC_RESULT
            numeric = cell.FormulaResultType2 == NUMERIC_RESULT
        except Exception:
            numeric = False
        if numeric:
            dated = _date_text(cell, doc)
            if dated is not None:
                return dated
        # Computed value; return the string form if the result is non-numeric text.
        s = cell.getString()
        if s and not s.replace('.', '').replace(',', '').replace('-', '').isdigit():
            return s
        return _num(cell.getValue())
    return cell.getString()


def cmd_info(args):
    """Show info about an Excel file."""
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        sheets = doc.getSheets()
        result = {"file": args.file, "sheets": []}
        for i in range(sheets.getCount()):
            sheet = sheets.getByIndex(i)
            name = sheet.getName()

            # Find used range
            cursor = sheet.createCursor()
            cursor.gotoStartOfUsedArea(False)
            cursor.gotoEndOfUsedArea(True)
            rows = cursor.getRangeAddress().EndRow + 1
            cols = cursor.getRangeAddress().EndColumn + 1

            result["sheets"].append({
                "name": name,
                "rows": rows,
                "columns": cols
            })
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        doc.close(True)


def cmd_read(args):
    """Read cells from a sheet."""
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        sheets = doc.getSheets()
        sheet = sheets.getByName(args.sheet)

        if args.range:
            (c1, r1), (c2, r2) = parse_range(args.range)
        else:
            cursor = sheet.createCursor()
            cursor.gotoStartOfUsedArea(False)
            cursor.gotoEndOfUsedArea(True)
            addr = cursor.getRangeAddress()
            c1, r1 = addr.StartColumn, addr.StartRow
            c2, r2 = addr.EndColumn, addr.EndRow

        data = []
        for row in range(r1, r2 + 1):
            row_data = []
            for col in range(c1, c2 + 1):
                cell = sheet.getCellByPosition(col, row)
                row_data.append(get_cell_value(cell, doc))
            data.append(row_data)

        print(json.dumps(data, ensure_ascii=False, indent=2))
    finally:
        doc.close(True)


def cmd_write(args):
    """Write a value to a cell."""
    desktop = get_desktop()
    doc = open_document(desktop, args.file, write=True)
    try:
        sheets = doc.getSheets()
        sheet = sheets.getByName(args.sheet)
        col, row = parse_cell_ref(args.cell)
        cell = sheet.getCellByPosition(col, row)

        # Store as text when asked (preserves leading zeros, IDs, invoice
        # numbers); otherwise a number only when the number is unambiguous.
        num = None
        if not getattr(args, "as_text", False):
            try:
                num = parse_number(args.value, getattr(args, "decimal", None))
            except AmbiguousNumber as exc:
                raise AmbiguousNumber(
                    f"{args.cell}: {exc}. Nothing was written; {_DECIMAL_HINT}.") from None
        if num is None:
            cell.setString(args.value)
        else:
            cell.setValue(num)

        doc.store()
        print(json.dumps({"status": "ok", "cell": args.cell, "value": args.value,
                          "stored_as": "text" if num is None else "number"},
                         ensure_ascii=False))
    finally:
        doc.close(True)


def cmd_add_rows(args):
    """Insert rows from a JSON file."""
    desktop = get_desktop()
    doc = open_document(desktop, args.file, write=True)
    try:
        sheets = doc.getSheets()
        sheet = sheets.getByName(args.sheet)

        with open(args.data, 'r', encoding='utf-8') as f:
            rows_data = json.load(f)

        insert_row = args.after  # 1-based row number
        num_rows = len(rows_data)
        as_text = getattr(args, "as_text", False)
        decimal = getattr(args, "decimal", None)

        # Decide every cell before touching the sheet, so an ambiguous value
        # anywhere leaves the file exactly as it was.
        plan, ambiguous, as_text_cells = [], [], []
        for i, row_data in enumerate(rows_data):
            for j, val in enumerate(row_data):
                if val is None or val == "":
                    continue
                name = _cell_name(j, insert_row + i)
                if isinstance(val, bool) or as_text:
                    num = None
                elif isinstance(val, (int, float)):
                    num = val if math.isfinite(val) else None
                else:
                    try:
                        num = parse_number(str(val), decimal)
                    except AmbiguousNumber:
                        ambiguous.append(f"{name} '{val}'")
                        continue
                if num is None:
                    as_text_cells.append(name)
                plan.append((j, i, num, val))
        if ambiguous:
            shown = ", ".join(ambiguous[:10])
            more = f" and {len(ambiguous) - 10} more" if len(ambiguous) > 10 else ""
            raise AmbiguousNumber(
                f"ambiguous numbers at {shown}{more}. Nothing was inserted; {_DECIMAL_HINT}.")

        # Insert empty rows
        sheet.getRows().insertByIndex(insert_row, num_rows)

        # Fill in data
        for j, i, num, val in plan:
            cell = sheet.getCellByPosition(j, insert_row + i)
            if num is None:
                cell.setString(str(val))
            else:
                cell.setValue(num)

        doc.store()
        print(json.dumps({
            "status": "ok",
            "rows_added": num_rows,
            "after_row": insert_row,
            "stored_as_text": as_text_cells,
        }))
    finally:
        doc.close(True)


def cmd_formula(args):
    """Set a formula in a cell."""
    desktop = get_desktop()
    doc = open_document(desktop, args.file, write=True)
    try:
        sheets = doc.getSheets()
        sheet = sheets.getByName(args.sheet)
        col, row = parse_cell_ref(args.cell)
        cell = sheet.getCellByPosition(col, row)
        cell.setFormula(args.formula)
        doc.store()

        # Read back computed value
        computed = get_cell_value(cell, doc)
        print(json.dumps({
            "status": "ok",
            "cell": args.cell,
            "formula": args.formula,
            "computed_value": computed
        }, ensure_ascii=False))
    finally:
        doc.close(True)


def cmd_add_sheet(args):
    """Add a new sheet."""
    desktop = get_desktop()
    doc = open_document(desktop, args.file, write=True)
    try:
        sheets = doc.getSheets()
        if args.after:
            # Find position of the reference sheet
            for i in range(sheets.getCount()):
                if sheets.getByIndex(i).getName() == args.after:
                    sheets.insertNewByName(args.name, i + 1)
                    break
            else:
                sheets.insertNewByName(args.name, sheets.getCount())
        else:
            sheets.insertNewByName(args.name, sheets.getCount())
        doc.store()
        print(json.dumps({"status": "ok", "sheet": args.name}))
    finally:
        doc.close(True)


def cmd_save_as(args):
    """Save/export to another format."""
    desktop = get_desktop()
    doc = open_document(desktop, args.file)
    try:
        output_path = os.path.abspath(args.output)
        url = "file://" + quote(output_path, safe="/:@")

        fmt = args.format or os.path.splitext(args.output)[1].lstrip('.')

        filter_map = {
            'xlsx': 'Calc MS Excel 2007 XML',
            'xls': 'MS Excel 97',
            'csv': 'Text - txt - csv (StarCalc)',
            'pdf': 'calc_pdf_Export',
            'ods': 'calc8',
        }

        filter_name = filter_map.get(fmt)
        if not filter_name:
            raise ValueError(f"Unsupported format: {fmt}")
        sheet = getattr(args, "sheet", None)
        if sheet is not None and fmt != 'csv':
            raise ValueError(f"--sheet is for csv only: {fmt} holds every sheet")

        result = {"status": "ok", "output": output_path, "format": fmt}
        if fmt == 'csv':
            result["sheet"], names = _store_csv(doc, output_path, sheet)
            if len(names) > 1:
                result["note"] = (f"CSV holds one sheet: this is {result['sheet']!r} of "
                                  f"{len(names)} ({', '.join(names)}); pass --sheet for another.")
        else:
            doc.storeToURL(url, _props(FilterName=filter_name))

        print(json.dumps(result, ensure_ascii=False))
    finally:
        doc.close(True)


# What LibreOffice uses for CSV when given no options (byte for byte, measured
# on 26.8), with token 12 naming the sheet. Left unnamed, a workbook of several
# sheets stops to warn that CSV holds only the active one, and a headless
# LibreOffice waits on that warning for ever (2026-10-07).
CSV_OPTIONS = "44,34,76,1,,0,false,true,false,false,false,{number}"


def _store_csv(doc, output_path, sheet=None):
    """Write one sheet as CSV to output_path; returns it and the sheet names.

    Naming the sheet makes LibreOffice write "<name>-<sheet>.csv", so it writes
    into a folder of its own and the file is moved to the path asked for. A
    killed export leaves its lock and temporary files there, not beside the
    user's file.
    """
    names = list(doc.getSheets().getElementNames())
    if sheet is None:
        controller = doc.getCurrentController()
        sheet = controller.getActiveSheet().getName() if controller is not None else names[0]
    if sheet not in names:
        raise ValueError(f"No sheet named {sheet!r}; the workbook has {', '.join(names)}")
    staging = tempfile.mkdtemp(prefix="mom-excel-csv-")
    try:
        target = os.path.join(staging, "export.csv")
        doc.storeToURL("file://" + quote(target, safe="/:@"),
                       _props(FilterName="Text - txt - csv (StarCalc)",
                              FilterOptions=CSV_OPTIONS.format(number=names.index(sheet) + 1)))
        written = [n for n in os.listdir(staging) if n.endswith(".csv")]
        if len(written) != 1:
            raise RuntimeError(f"LibreOffice wrote {len(written)} CSV files for one sheet")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)  # as storeToURL does
        shutil.move(os.path.join(staging, written[0]), output_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return sheet, names


def cmd_eval_formulas(args):
    """Force recalculation of all formulas and save."""
    desktop = get_desktop()
    doc = open_document(desktop, args.file, write=True)
    try:
        # Force recalculation
        doc.calculateAll()
        doc.store()
        print(json.dumps({"status": "ok", "message": "Formulas recalculated and saved"}))
    finally:
        doc.close(True)


def main():
    parser = argparse.ArgumentParser(description="Excel helper via LibreOffice UNO")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # info
    p = subparsers.add_parser("info")
    p.add_argument("file")

    # read
    p = subparsers.add_parser("read")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--range", default=None)

    # write
    p = subparsers.add_parser("write")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--cell", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--as-text", action="store_true",
                   help="Store as text, preserving leading zeros / IDs / invoice numbers")
    p.add_argument("--decimal", choices=["comma", "dot"], default=None,
                   help="Decimal mark of the value: comma (Greek, 2.500 = 2500) or dot (English)")

    # add-rows
    p = subparsers.add_parser("add-rows")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--after", type=int, required=True, help="1-based row number")
    p.add_argument("--data", required=True, help="Path to JSON file with row data")
    p.add_argument("--as-text", action="store_true",
                   help="Store every value as text, exactly as given")
    p.add_argument("--decimal", choices=["comma", "dot"], default=None,
                   help="Decimal mark of text values: comma (Greek) or dot (English)")

    # formula
    p = subparsers.add_parser("formula")
    p.add_argument("file")
    p.add_argument("--sheet", required=True)
    p.add_argument("--cell", required=True)
    p.add_argument("--formula", required=True)

    # add-sheet
    p = subparsers.add_parser("add-sheet")
    p.add_argument("file")
    p.add_argument("--name", required=True)
    p.add_argument("--after", default=None)

    # save-as
    p = subparsers.add_parser("save-as")
    p.add_argument("file")
    p.add_argument("--output", required=True)
    p.add_argument("--format", default=None)
    p.add_argument("--sheet", default=None,
                   help="csv only: the sheet to write (default: the active sheet)")

    # eval-formulas
    p = subparsers.add_parser("eval-formulas")
    p.add_argument("file")

    # stop: close what this tool left behind for this user, nothing else
    subparsers.add_parser("stop")

    args = parser.parse_args()

    if args.command == "stop":
        try:
            print(json.dumps(stop_leftovers(), ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)
        return

    # A timeout's SIGTERM still closes this command's LibreOffice and folder.
    # Only over the default action: a handler set by anyone else stays (one set
    # outside Python cannot even be put back from here).
    installed = False
    with contextlib.suppress(ValueError):  # not the main thread
        if signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
            signal.signal(signal.SIGTERM, _exit_on_sigterm)
            installed = True
    try:
        if not _uno_importable():
            try:
                out, error = run_in_office(args)
            except Exception as e:  # no LibreOffice, no temporary folder
                out, error = "", str(e)
            sys.stdout.write(out)
            if error is not None:
                print(json.dumps({"error": error}), file=sys.stderr)
                sys.exit(1)
            return
        try:
            _commands()[args.command](args)
        except Exception as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)
        finally:
            stop_libreoffice()
    finally:
        if installed:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _commands():
    """Looked up on each call, so a test's patch of one command is seen."""
    return {
        "info": cmd_info,
        "read": cmd_read,
        "write": cmd_write,
        "add-rows": cmd_add_rows,
        "formula": cmd_formula,
        "add-sheet": cmd_add_sheet,
        "save-as": cmd_save_as,
        "eval-formulas": cmd_eval_formulas,
    }


def _exit_on_sigterm(signum, frame):
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    main()
