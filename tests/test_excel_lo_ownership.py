"""excel_lo.py: a command closes the LibreOffice it started, and nothing else.

One OS account hosts every user of the bot. After every command the helper ran
``pkill -f soffice.bin``. On Linux that closed every LibreOffice on the machine,
other users' open windows included. On macOS, where the process is called
soffice, it matched nothing, so each command left its LibreOffice running, and
the helper could not work there anyway: /usr/bin/python3 has no uno, and the
Python inside LibreOffice.app is signed so that only LibreOffice may start it
(measured 2026-10-07: "Launch Constraint Violation").

Now every command runs in a private LibreOffice on a fresh profile in a folder
of its own and closes exactly that copy, and stop closes only a copy this tool
left behind for this user whose command is provably gone.
"""

import contextlib
import getpass
import importlib.util
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "spreadsheet" / "scripts" / "excel_lo.py"
spec = importlib.util.spec_from_file_location("excel_lo_ownership_under_test", str(SCRIPT))
xl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xl)

MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
LINUX_BIN = "/usr/lib/libreoffice/program/soffice.bin"


class _TempDir(unittest.TestCase):
    """tempfile.tempdir pointed at a folder of the test's own."""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="xl-own-")
        saved = tempfile.tempdir
        tempfile.tempdir = self.td

        def restore():
            tempfile.tempdir = saved
            shutil.rmtree(self.td, ignore_errors=True)

        self.addCleanup(restore)
        env = mock.patch.dict(os.environ, {"JARVIS_USER_ID": "4242"})
        env.start()
        self.addCleanup(env.stop)

    def run_folder(self, tag, owner_pid, started):
        """A run's folder as excel_lo leaves one: owner.json and the marker."""
        folder = tempfile.mkdtemp(prefix=f"mom-excel-lo-{tag}-", dir=self.td)
        with open(os.path.join(folder, "owner.json"), "w", encoding="utf-8") as fh:
            json.dump({"pid": owner_pid, "started": started, "user": tag}, fh)
        return folder, xl._marker(folder)


class _FakeTable:
    """A process table to serve _process_table, the per-PID ps check and os.kill."""

    def __init__(self, rows, starts=None):
        self.rows = dict(rows)
        self.starts = dict(starts or {})
        self.signals = []

    def table(self):
        return list(self.rows.items())

    def ps(self, *args):
        if args[:3] == ("-ww", "-o", "command="):
            return " ".join(self.rows.get(int(args[-1]), []))
        if args[:2] == ("-o", "lstart="):
            return self.starts.get(int(args[-1]), "")
        raise AssertionError(f"unexpected ps {args}")

    def kill(self, pid, sig):
        self.signals.append((pid, sig))
        self.rows.pop(pid, None)

    def patches(self):
        return (mock.patch.object(xl, "_process_table", side_effect=self.table),
                mock.patch.object(xl, "_ps", side_effect=self.ps),
                mock.patch.object(xl.os, "kill", side_effect=self.kill))


class StopClosesOnlyWhatThisToolLeft(_TempDir):
    def test_stop_runs_no_kill_by_name(self):
        real_run = subprocess.run
        calls = []

        def spy(argv, *a, **k):
            calls.append(list(argv))
            if argv[0] in ("pkill", "killall"):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return real_run(argv, *a, **k)

        with mock.patch.object(xl.subprocess, "run", side_effect=spy), \
                mock.patch.object(xl.os, "kill") as kill, \
                mock.patch("sys.argv", ["excel_lo.py", "stop"]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            xl.main()
        self.assertEqual([c for c in calls if c[0] in ("pkill", "killall")], [],
                         "stop closed LibreOffice by name, every user's copy with it")
        kill.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["stopped"], [])

    def test_only_a_gone_commands_copy_is_closed(self):
        gone_dir, gone = self.run_folder("4242", 990001, "Mon Jan  5 10:00:00 2026")
        busy_dir, busy = self.run_folder("4242", 990002, "Tue Jan  6 10:00:00 2026")
        reused_dir, reused = self.run_folder("4242", 990003, "Wed Jan  7 10:00:00 2026")
        other_dir, other = self.run_folder("777", 990001, "Mon Jan  5 10:00:00 2026")
        blank_dir = tempfile.mkdtemp(prefix="mom-excel-lo-4242-", dir=self.td)  # no owner.json yet
        fake = _FakeTable({
            101: ["/usr/lib/libreoffice/program/oosplash", "--headless", gone, "--accept=pipe,name=a;urp;"],
            102: [LINUX_BIN, "--headless", gone, "--accept=pipe,name=a;urp;"],
            201: [MAC_SOFFICE, "--headless", busy],
            301: [MAC_SOFFICE, "--headless", reused],
            401: [MAC_SOFFICE, "--headless", other],
            501: [MAC_SOFFICE, "--calc", "/Users/someone/book.ods"],
            502: [LINUX_BIN, "--writer", "/home/someone/letter.odt"],
            503: ["soffice", "--headless", "--norestore", "--nologo",
                  "--accept=socket,host=localhost,port=2002;urp;"],
            504: ["grep", "-r", gone[len("-env:UserInstallation="):]],
            505: [MAC_SOFFICE, "--headless", gone + "x"],
        }, starts={990002: "Tue Jan  6 10:00:00 2026",   # still running
                   990003: "Thu Jan  8 09:00:00 2026"})  # PID reused by another process
        with contextlib.ExitStack() as stack:
            for p in fake.patches():
                stack.enter_context(p)
            res = xl.stop_leftovers()
        self.assertEqual(sorted(fake.signals), [(101, signal.SIGTERM), (102, signal.SIGTERM),
                                                (301, signal.SIGTERM)])
        self.assertEqual(sorted(res["stopped"]), [101, 102, 301])
        self.assertEqual(res["in_use"], [201])
        for gone_folder in (gone_dir, reused_dir):
            self.assertFalse(os.path.exists(gone_folder))
        for kept in (busy_dir, other_dir, blank_dir):
            self.assertTrue(os.path.exists(kept))

    def test_a_folder_without_a_recorded_start_is_never_judged_gone(self):
        folder, marker = self.run_folder("4242", 990004, None)
        fake = _FakeTable({601: [MAC_SOFFICE, "--headless", marker]})
        with contextlib.ExitStack() as stack:
            for p in fake.patches():
                stack.enter_context(p)
            res = xl.stop_leftovers()
        self.assertEqual((fake.signals, res["stopped"]), ([], []))
        self.assertTrue(os.path.exists(folder))

    def test_whose_runs_and_which_processes(self):
        for uid, tag in (("123456789", "123456789"), ("", "local"), ("../x", "local"),
                         ("١٢", "local")):
            with self.subTest(uid=uid), mock.patch.dict(os.environ, {"JARVIS_USER_ID": uid}):
                self.assertEqual(xl._owner_tag(), tag)
        marker = xl._marker("/tmp/mom-excel-lo-1-abc")
        self.assertEqual(marker, "-env:UserInstallation=file:///tmp/mom-excel-lo-1-abc/profile")
        # High PIDs: _carrying never counts the caller, and a runner that is
        # pid 1 (a container, a pid namespace) failed this with pid 1 here.
        rows = [(90001, ["soffice", marker]), (90002, ["soffice", marker + "2"]),
                (90003, ["soffice", "-env:UserInstallation=file:///tmp/mom-excel-lo-1-abcd/profile"]),
                (90004, ["ls", "/tmp/mom-excel-lo-1-abc/profile"])]
        self.assertEqual(xl._carrying(marker, rows), [90001])
        self.assertEqual(xl._carrying(marker, [(os.getpid(), ["python3", marker])]), [])

    def test_start_times_are_read_in_one_locale(self):
        with mock.patch.object(xl.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, "x", "")) as run:
            xl._process_start(1)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")


class _FakeProc:
    """A started soffice: running or not, and whether a wait sees it exit."""

    def __init__(self, running=True, exits_on_wait=True):
        self.running = running
        self.exits_on_wait = exits_on_wait
        self.calls = []
        self.pid = 4242424

    def poll(self):
        return None if self.running else 0

    def wait(self, timeout=None):
        self.calls.append("wait")
        if self.running and not self.exits_on_wait:
            raise subprocess.TimeoutExpired("soffice", timeout)
        self.running = False
        return 0

    def terminate(self):
        self.calls.append("terminate")
        self.exits_on_wait = True

    def kill(self):
        self.calls.append("kill")
        self.exits_on_wait = True


class ACommandClosesItsOwnCopy(_TempDir):
    def test_close_stops_its_child_and_its_marked_straggler_only(self):
        office = xl._Office()
        office.proc = proc = _FakeProc(exits_on_wait=False)
        fake = _FakeTable({
            701: [LINUX_BIN, "--headless", office.marker, f"--accept=pipe,name={office.pipe};urp;"],
            702: [MAC_SOFFICE, "--calc", "/Users/someone/book.ods"],
            703: [LINUX_BIN, "--headless", "-env:UserInstallation=file:///tmp/elsewhere/profile"],
        })
        with contextlib.ExitStack() as stack:
            for p in fake.patches():
                stack.enter_context(p)
            office.close(0)
        self.assertEqual(proc.calls, ["wait", "terminate", "wait"])
        self.assertEqual(fake.signals, [(701, signal.SIGTERM)])
        self.assertFalse(os.path.exists(office.dir))

    def test_the_folder_says_who_started_it(self):
        office = xl._Office()
        self.addCleanup(shutil.rmtree, office.dir, True)
        self.assertTrue(os.path.basename(office.dir).startswith("mom-excel-lo-4242-"))
        self.assertEqual(os.path.dirname(office.dir), self.td)
        owner = json.loads(Path(os.path.join(office.dir, "owner.json")).read_text(encoding="utf-8"))
        self.assertEqual(owner["pid"], os.getpid())
        self.assertEqual(owner["started"], xl._process_start(os.getpid()))
        self.assertEqual(office.pipe, os.path.basename(office.dir))


def _fake_uno(resolve):
    """A uno module: the local context hands out a resolver calling resolve()."""
    desktop = SimpleNamespace(terminated=False)
    desktop.terminate = lambda: setattr(desktop, "terminated", True)
    remote = SimpleNamespace(ServiceManager=SimpleNamespace(
        createInstanceWithContext=lambda name, ctx: desktop))
    resolver = SimpleNamespace(resolve=lambda url: resolve(url) or remote)
    local = SimpleNamespace(ServiceManager=SimpleNamespace(
        createInstanceWithContext=lambda name, ctx: resolver if "Resolver" in name else desktop))
    module = types.ModuleType("uno")
    module.getComponentContext = lambda: local
    return module, desktop


class TheClientRoute(_TempDir):
    """Linux with python3-uno: the command talks to its copy over its own pipe."""

    def setUp(self):
        super().setUp()
        self.launched = []
        fake_launch = mock.patch.object(xl._Office, "launch", autospec=True,
                                        side_effect=self._launch)
        fake_launch.start()
        self.addCleanup(fake_launch.stop)
        nap = mock.patch.object(xl.time, "sleep")
        nap.start()
        self.addCleanup(nap.stop)
        self.addCleanup(setattr, xl, "_OFFICE", None)
        self.addCleanup(setattr, xl, "_DESKTOP", None)

    def _launch(self, office, *args, env=None):
        self.launched.append(args)
        office.proc = self.proc

    def test_connects_over_its_own_pipe_and_terminates_its_own_copy(self):
        urls = []

        def resolve(url):
            urls.append(url)
            if len(urls) < 3:
                raise RuntimeError("not listening yet")

        uno, desktop = _fake_uno(resolve)
        self.proc = _FakeProc()
        with mock.patch.dict(sys.modules, {"uno": uno}):
            self.assertIs(xl.get_desktop(), desktop)
            office = xl._OFFICE
            self.assertEqual(self.launched,
                             [(f"--accept=pipe,name={office.pipe};urp;StarOffice.ComponentContext",)])
            self.assertEqual(urls, [f"uno:pipe,name={office.pipe};urp;StarOffice.ComponentContext"] * 3)
            with mock.patch.object(xl, "_carrying", return_value=[]):
                xl.stop_libreoffice()
        self.assertTrue(desktop.terminated)
        self.assertEqual(self.proc.calls, ["wait"])
        self.assertFalse(os.path.exists(office.dir))
        self.assertIsNone(xl._OFFICE)

    def test_a_copy_that_dies_while_starting_fails_at_once(self):
        def resolve(url):
            raise RuntimeError("refused")

        uno, _ = _fake_uno(resolve)
        self.proc = _FakeProc(running=False)
        with mock.patch.dict(sys.modules, {"uno": uno}):
            with self.assertRaises(RuntimeError) as cm:
                xl.get_desktop()
            office = xl._OFFICE
            with mock.patch.object(xl, "_carrying", return_value=[]):
                xl.stop_libreoffice()
        self.assertIn("closed before it was ready", str(cm.exception))
        self.assertFalse(os.path.exists(office.dir))


def _lock_text(profile, name="", host=None):
    """A lock file as LibreOffice writes it: name, OS user, host, time, profile URL."""
    return (f"{name},someone,{host or socket.gethostname()},07.10.2026 21:28,"
            f"{Path(profile).as_uri()};")


class StaleLocks(_TempDir):
    """A copy killed with a file open leaves .~lock.<file># beside it, and
    LibreOffice will not open a file whose lock names another profile: one killed
    command made every later one on that file fail with "Failed to open"."""

    def setUp(self):
        super().setUp()
        self.book = os.path.join(self.td, "Λογαριασμοί.xlsx")
        Path(self.book).write_bytes(b"")
        self.lock = os.path.join(self.td, ".~lock.Λογαριασμοί.xlsx#")

    def _lock(self, text):
        Path(self.lock).write_text(text, encoding="utf-8")

    def _clear(self, rows=None, ps_fails=False):
        def table():
            if ps_fails:
                raise xl.PsFailed("ps would not run")
            return rows or [(1, ["launchd"])]

        with mock.patch.object(xl, "_process_table", side_effect=table):
            return xl.clear_stale_lock(self.book)

    def test_a_lock_left_by_a_gone_copy_of_this_tool_is_cleared(self):
        self._lock(_lock_text(os.path.join(self.td, "mom-excel-lo-4242-gone", "profile")))
        self.assertIsNone(self._clear())
        self.assertFalse(os.path.exists(self.lock))

    def test_a_lock_whose_copy_still_runs_is_kept(self):
        folder = tempfile.mkdtemp(prefix="mom-excel-lo-777-", dir=self.td)
        self._lock(_lock_text(os.path.join(folder, "profile")))
        holder = self._clear([(9, [MAC_SOFFICE, "--headless", xl._marker(folder)])])
        self.assertTrue(os.path.exists(self.lock))
        self.assertIn("someone on", holder)

    def test_a_persons_lock_another_machines_and_an_unreadable_table_keep_it(self):
        cases = {
            "a person's LibreOffice": (_lock_text("/Users/someone/Library/Application Support/"
                                                  "LibreOffice/4", name="A Person"), False),
            "another machine": (_lock_text(os.path.join(self.td, "mom-excel-lo-1-x", "profile"),
                                           host="elsewhere.local"), False),
            "ps would not run": (_lock_text(os.path.join(self.td, "mom-excel-lo-1-y", "profile")),
                                 True),
        }
        for case, (text, ps_fails) in cases.items():
            with self.subTest(case):
                self._lock(text)
                holder = self._clear(ps_fails=ps_fails)
                self.assertTrue(os.path.exists(self.lock))
                self.assertIn(" on ", holder)
        self._lock(cases["a person's LibreOffice"][0])
        self.assertTrue(self._clear().startswith(f"A Person on {socket.gethostname()}, since "))

    def test_a_held_file_is_read_read_only_and_never_written(self):
        self._lock(_lock_text("/Users/someone/Library/Application Support/LibreOffice/4",
                              name="A Person"))
        loads = []
        doc = SimpleNamespace(isReadonly=lambda: True, close=lambda flag: None)
        desktop = SimpleNamespace(loadComponentFromURL=lambda *a: loads.append(a) or doc)
        with mock.patch.object(xl, "_process_table", return_value=[(1, ["launchd"])]), \
                mock.patch.object(xl, "_props", side_effect=lambda **kw: tuple(kw.items())):
            self.assertIs(xl.open_document(desktop, self.book), doc)
            with self.assertRaises(RuntimeError) as cm:
                xl.open_document(desktop, self.book, write=True)
        self.assertEqual(len(loads), 1, "a write loaded a file someone has open")
        self.assertEqual(loads[0][3], (("ReadOnly", True),))
        self.assertIn("is open in another LibreOffice (A Person on", str(cm.exception))
        self.assertIn("nothing was written", str(cm.exception))
        self.assertTrue(os.path.exists(self.lock))

    def test_a_write_that_comes_back_read_only_stores_nothing(self):
        closed = []
        doc = SimpleNamespace(isReadonly=lambda: True, close=closed.append)
        desktop = SimpleNamespace(loadComponentFromURL=lambda *a: doc)
        with self.assertRaises(RuntimeError) as cm:
            xl.open_document(desktop, self.book, write=True)
        self.assertIn("opened read-only", str(cm.exception))
        self.assertEqual(closed, [True])

    def test_an_unreadable_start_time_never_reads_as_gone(self):
        folder, marker = self.run_folder("4242", os.getpid(), "Mon Jan  5 10:00:00 2026")
        real_run = subprocess.run

        def ps(argv, *a, **k):
            if "lstart=" in argv:
                raise subprocess.TimeoutExpired(argv, 10)
            return real_run(argv, *a, **k)

        with mock.patch.object(xl.subprocess, "run", side_effect=ps), \
                mock.patch.object(xl.os, "kill") as kill:
            with self.assertRaises(xl.PsFailed):
                xl.stop_leftovers()
        kill.assert_not_called()
        self.assertTrue(os.path.exists(folder))

    def test_an_unreadable_process_table_never_reads_as_nobody(self):
        gone_dir, gone = self.run_folder("4242", 990001, "Mon Jan  5 10:00:00 2026")
        with mock.patch.object(xl.subprocess, "run", side_effect=OSError("no ps")), \
                mock.patch.object(xl.os, "kill") as kill:
            with self.assertRaises(xl.PsFailed):
                xl.stop_leftovers()
        kill.assert_not_called()
        self.assertTrue(os.path.exists(gone_dir))


class CsvExport(_TempDir):
    """CSV holds one sheet, and the sheet is always named to LibreOffice."""

    def _doc(self, names, active):
        stored = []

        def store(url, props):
            stored.append((url, dict(props)))
            folder = os.path.dirname(xl._url_path(url))
            sheet = names[int(dict(props)["FilterOptions"].rsplit(",", 1)[1]) - 1]
            Path(folder, f"export-{sheet}.csv").write_text(f"{sheet}\n", encoding="utf-8")

        doc = SimpleNamespace(
            getSheets=lambda: SimpleNamespace(getElementNames=lambda: tuple(names)),
            getCurrentController=lambda: SimpleNamespace(
                getActiveSheet=lambda: SimpleNamespace(getName=lambda: active)),
            storeToURL=store)
        return doc, stored

    def _store(self, doc, sheet=None):
        out = os.path.join(self.td, "out.csv")
        with mock.patch.object(xl, "_props", side_effect=lambda **kw: tuple(kw.items())):
            return out, xl._store_csv(doc, out, sheet)

    def test_the_active_sheet_by_default_and_any_sheet_by_name(self):
        doc, stored = self._doc(["S", "Σύνολα", "Τρίτο"], active="Σύνολα")
        out, (sheet, names) = self._store(doc)
        self.assertEqual((sheet, names), ("Σύνολα", ["S", "Σύνολα", "Τρίτο"]))
        self.assertEqual(stored[-1][1]["FilterOptions"], "44,34,76,1,,0,false,true,false,false,false,2")
        self.assertEqual(Path(out).read_text(encoding="utf-8"), "Σύνολα\n")
        out, (sheet, _) = self._store(doc, "Τρίτο")
        self.assertEqual(stored[-1][1]["FilterOptions"][-2:], ",3")
        self.assertEqual(Path(out).read_text(encoding="utf-8"), "Τρίτο\n")
        self.assertEqual([n for n in os.listdir(self.td) if n.startswith("mom-excel-csv-")], [])

    def test_an_unknown_sheet_and_sheet_for_another_format_are_refused(self):
        doc, stored = self._doc(["S"], active="S")
        with self.assertRaises(ValueError):
            self._store(doc, "Nope")
        self.assertEqual(stored, [])
        with mock.patch.object(xl, "get_desktop"), \
                mock.patch.object(xl, "open_document", return_value=SimpleNamespace(close=lambda f: None)):
            with self.assertRaises(ValueError) as cm:
                xl.cmd_save_as(SimpleNamespace(file="f.xlsx", output="f.pdf", format=None, sheet="S"))
        self.assertIn("csv only", str(cm.exception))


class TheMacroRoute(_TempDir):
    """macOS: the command runs inside its copy and hands its output back."""

    def _job(self, args):
        folder = tempfile.mkdtemp(dir=self.td)
        job = os.path.join(folder, "job.json")
        with open(job, "w", encoding="utf-8") as fh:
            json.dump({"args": args}, fh)
        return folder, job

    def _run_job(self, job, command):
        uno, desktop = _fake_uno(lambda url: None)
        with mock.patch.dict(os.environ, {xl.JOB_ENV: job}), \
                mock.patch.dict(sys.modules, {"uno": uno}), \
                mock.patch.object(xl, "_commands", return_value={"info": command}):
            xl.run_job()
        self.addCleanup(setattr, xl, "_DESKTOP", None)
        return desktop

    def test_the_macro_runs_the_command_and_hands_back_what_it_printed(self):
        folder, job = self._job({"command": "info", "file": "Λογαριασμοί.xlsx"})
        seen = {}

        def info(args):
            seen["desktop"] = xl.get_desktop()
            print(json.dumps({"file": args.file}, ensure_ascii=False))

        desktop = self._run_job(job, info)
        result = json.loads(Path(os.path.join(folder, "result.json")).read_text(encoding="utf-8"))
        self.assertEqual(json.loads(result["stdout"]), {"file": "Λογαριασμοί.xlsx"})
        self.assertIsNone(result["error"])
        self.assertIs(seen["desktop"], desktop)
        self.assertTrue(desktop.terminated)
        self.assertTrue(os.path.exists(os.path.join(folder, "started")))

    def test_a_refusal_comes_back_as_the_error(self):
        folder, job = self._job({"command": "info", "file": "f.xlsx"})

        def info(args):
            raise xl.AmbiguousNumber("B2: '2.500' could be 2500 or 2.500")

        desktop = self._run_job(job, info)
        result = json.loads(Path(os.path.join(folder, "result.json")).read_text(encoding="utf-8"))
        self.assertEqual(result, {"stdout": "", "error": "B2: '2.500' could be 2500 or 2.500"})
        self.assertTrue(desktop.terminated)

    def _controller(self, play):
        def launch(office, *args, env=None):
            play(office, args, env)

        with mock.patch.object(xl._Office, "launch", autospec=True, side_effect=launch), \
                mock.patch.object(xl, "_carrying", return_value=[]):
            return xl.run_in_office(SimpleNamespace(command="info", file="f.xlsx"))

    def test_the_controller_starts_the_macro_and_reads_its_result(self):
        seen = {}

        def play(office, args, env):
            seen["args"], seen["job"] = args, json.loads(Path(env[xl.JOB_ENV]).read_text(encoding="utf-8"))
            seen["script"] = Path(office.dir, "profile/user/Scripts/python/excel_lo.py").read_bytes()
            open(os.path.join(office.dir, "started"), "w").close()
            xl._write_json(os.path.join(office.dir, "result.json"),
                           {"stdout": '{"ok": 1}\n', "error": None})
            office.proc = _FakeProc()
            seen["dir"] = office.dir

        out, error = self._controller(play)
        self.assertEqual((out, error), ('{"ok": 1}\n', None))
        self.assertEqual(seen["args"],
                         ("vnd.sun.star.script:excel_lo.py$run_job?language=Python&location=user",))
        self.assertEqual(seen["job"], {"args": {"command": "info", "file": "f.xlsx"}})
        self.assertEqual(seen["script"], SCRIPT.read_bytes())
        self.assertFalse(os.path.exists(seen["dir"]))

    def test_a_copy_that_never_ran_the_macro_is_reported(self):
        def play(office, args, env):
            office.proc = _FakeProc(running=False)

        out, error = self._controller(play)
        self.assertEqual(out, "")
        self.assertIn("without running the command", error)

    def test_a_sigterm_handler_set_by_anyone_else_is_left_alone(self):
        # Inside LibreOffice the handler was set outside Python: getsignal gives
        # None, and putting None back raised TypeError after every command.
        for current in (None, lambda signum, frame: None):
            with self.subTest(current=current), \
                    mock.patch.object(xl.signal, "getsignal", return_value=current), \
                    mock.patch.object(xl.signal, "signal") as install, \
                    mock.patch.object(xl, "_uno_importable", return_value=True), \
                    mock.patch.object(xl, "_commands", return_value={"info": lambda args: None}), \
                    mock.patch.object(xl, "stop_libreoffice"), \
                    mock.patch("sys.argv", ["excel_lo.py", "info", "f.xlsx"]):
                xl.main()
            install.assert_not_called()

    def test_the_default_sigterm_action_comes_back_after_a_command(self):
        with mock.patch.object(xl.signal, "getsignal", return_value=signal.SIG_DFL), \
                mock.patch.object(xl.signal, "signal") as install, \
                mock.patch.object(xl, "_uno_importable", return_value=True), \
                mock.patch.object(xl, "_commands", return_value={"info": lambda args: None}), \
                mock.patch.object(xl, "stop_libreoffice"), \
                mock.patch("sys.argv", ["excel_lo.py", "info", "f.xlsx"]):
            xl.main()
        self.assertEqual(install.call_args_list, [mock.call(signal.SIGTERM, xl._exit_on_sigterm),
                                                  mock.call(signal.SIGTERM, signal.SIG_DFL)])

    def test_main_reports_the_error_and_exits_1(self):
        with mock.patch.object(xl, "_uno_importable", return_value=False), \
                mock.patch.object(xl, "run_in_office", return_value=("", "type detection failed")), \
                mock.patch("sys.argv", ["excel_lo.py", "info", "f.xlsx"]), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                xl.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(json.loads(err.getvalue()), {"error": "type detection failed"})


class WhichRoute(unittest.TestCase):
    """Only LibreOffice's own uno takes the client route."""

    def _importable_with(self, files):
        with tempfile.TemporaryDirectory() as d:
            for name, text in files.items():
                Path(d, name).parent.mkdir(parents=True, exist_ok=True)
                Path(d, name).write_text(text, encoding="utf-8")
            with mock.patch.object(sys, "path", [d]), mock.patch.dict(sys.modules):
                sys.modules.pop("uno", None)
                return xl._uno_importable()

    def test_typing_stubs_named_uno_are_not_libreoffice(self):
        # types-uno-script (with ooo-dev-tools) ships uno/__init__.pyi and no
        # module: it imports as an empty namespace package.
        self.assertFalse(self._importable_with({"uno/__init__.pyi": "", "uno/py.typed": ""}))
        self.assertFalse(self._importable_with({}))
        self.assertTrue(self._importable_with({"uno.py": "def getComponentContext():\n    pass\n"}))


# --------------------------------------------------------------- live

# The live tests read the machine with their own helpers, not the module's, so
# they measure the helper rather than trusting it.
SOFFICE = shutil.which("soffice") or (MAC_SOFFICE if os.path.exists(MAC_SOFFICE) else None)
PYTHON = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


def _a_route():
    """Can excel_lo drive LibreOffice here: uno for PYTHON, or Python macros?"""
    if not SOFFICE:
        return False
    if subprocess.run([PYTHON, "-c", "import uno"], capture_output=True).returncode == 0:
        return True
    program = Path(os.path.realpath(SOFFICE)).parent
    return any(p.exists() for p in (program / "pythonscript.py",  # Linux: script provider
                                    Path(MAC_SOFFICE).parents[1] / "Resources" / "pythonscript.py"))


ROUTE = _a_route()


def _table():
    out = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,command="], capture_output=True,
                         text=True, timeout=10).stdout
    rows = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit():
            rows.append((int(pid), command.split()))
    return rows


def _started(pid):
    return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                          text=True, timeout=10, env=dict(os.environ, LC_ALL="C")).stdout.strip()


def _marker(folder):
    return "-env:UserInstallation=" + Path(folder, "profile").as_uri()

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _write_xlsx(path, *sheets):
    """A workbook written by hand, so no openpyxl is needed. Each sheet is
    (name, XML of row 1); style 1 is a date, style 2 a date and time. A bare
    string is one sheet named S."""
    if len(sheets) == 1 and isinstance(sheets[0], str):
        sheets = (("S", sheets[0]),)
    numbers = range(1, len(sheets) + 1)
    parts = {
        "[Content_Types].xml": _HEAD + '<Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types"><Default Extension="rels" ContentType="application/'
        'vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
        f'ContentType="application/xml"/><Override PartName="/xl/workbook.xml" '
        f'ContentType="{_CT}.sheet.main+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{n}.xml" '
                  f'ContentType="{_CT}.worksheet+xml"/>' for n in numbers)
        + f'<Override PartName="/xl/styles.xml" ContentType="{_CT}.styles+xml"/></Types>',
        "_rels/.rels": _HEAD + f'<Relationships xmlns="{_PKG}"><Relationship Id="rId1" '
        f'Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": _HEAD + f'<workbook {_NS} xmlns:r="{_REL}"><sheets>'
        + "".join(f'<sheet name="{name}" sheetId="{n}" r:id="rId{n}"/>'
                  for n, (name, _) in zip(numbers, sheets))
        + '</sheets></workbook>',
        "xl/_rels/workbook.xml.rels": _HEAD + f'<Relationships xmlns="{_PKG}">'
        + "".join(f'<Relationship Id="rId{n}" Type="{_REL}/worksheet" '
                  f'Target="worksheets/sheet{n}.xml"/>' for n in numbers)
        + f'<Relationship Id="rId0" Type="{_REL}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": _HEAD + f'<styleSheet {_NS}><fonts count="1"><font><sz val="11"/>'
        '</font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0"/>'
        '</cellStyleXfs><cellXfs count="3"><xf numFmtId="0" xfId="0"/><xf numFmtId="14" '
        'xfId="0" applyNumberFormat="1"/><xf numFmtId="22" xfId="0" applyNumberFormat="1"/>'
        '</cellXfs></styleSheet>',
    }
    for n, (_, cells) in zip(numbers, sheets):
        parts[f"xl/worksheets/sheet{n}.xml"] = (_HEAD + f'<worksheet {_NS}><sheetData>'
                                                f'<row r="1">{cells}</row></sheetData></worksheet>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            zf.writestr(name, text)


DATES = ('<c r="A1" s="1"><v>46302</v></c><c r="B1" s="2"><v>46295.586805555555</v></c>'
         '<c r="C1"><v>1234.56</v></c><c r="D1" t="inlineStr"><is><t>0012</t></is></c>')
GREEK = '<c r="A1" t="inlineStr"><is><t>Ενοίκιο</t></is></c><c r="B1"><v>450</v></c>'
FIRST = '<c r="A1" t="inlineStr"><is><t>πρώτο</t></is></c><c r="B1"><v>1</v></c>'


@unittest.skipUnless(ROUTE, "needs LibreOffice with python3-uno or its Python scripting")
class LiveOwnership(unittest.TestCase):
    """Real LibreOffice and real processes. Everything a test starts sits under
    its own temporary folder, so anything carrying that folder's path on its
    command line was started by the test, and only that is ever cleaned up."""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="xl-live-")
        self.env = dict(os.environ, TMPDIR=self.td, JARVIS_USER_ID="4242")
        self.env.pop("JARVIS_USER_DIR", None)
        self.spawned = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for proc in self.spawned:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for pid, words in _table():
            if pid != os.getpid() and any(self.td in w for w in words):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        shutil.rmtree(self.td, ignore_errors=True)

    def _office(self, *args):
        proc = subprocess.Popen([SOFFICE, "--headless", "--norestore", "--nologo", *args],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(proc)
        return proc

    def _someone_elses(self):
        """Another user's LibreOffice: its own profile, kept running by --accept."""
        return self._office("-env:UserInstallation=" + Path(self.td, "theirs").as_uri(),
                            f"--accept=pipe,name={os.path.basename(self.td)}-theirs;urp;")

    def _leftover(self, tag, owner_pid, started):
        """A copy this tool would leave behind: a run folder and a soffice on it."""
        folder = tempfile.mkdtemp(prefix=f"mom-excel-lo-{tag}-", dir=self.td)
        with open(os.path.join(folder, "owner.json"), "w", encoding="utf-8") as fh:
            json.dump({"pid": owner_pid, "started": started, "user": tag}, fh)
        return folder, self._office(_marker(folder),
                                    f"--accept=pipe,name={os.path.basename(folder)};urp;")

    def _command(self, *argv):
        return subprocess.Popen([PYTHON, str(SCRIPT), *argv], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=self.env, cwd=self.td)

    @staticmethod
    def _soffice_pids():
        return {pid for pid, words in _table()
                if words and os.path.basename(words[0]).startswith(("soffice", "oosplash"))}

    def _wait_for(self, procs, timeout=60):
        deadline = time.monotonic() + timeout
        pids = {p.pid for p in procs}
        while time.monotonic() < deadline and not pids <= self._soffice_pids():
            time.sleep(0.2)

    def _assert_nothing_left(self, before, theirs):
        left = self._soffice_pids() - before
        self.assertEqual(left, set(), "a command left its LibreOffice running: "
                         + str([w for p, w in _table() if p in left]))
        self.assertIsNone(theirs.poll(), "a command closed someone else's LibreOffice")
        self.assertEqual([n for n in os.listdir(self.td) if n.startswith("mom-excel-lo-")], [])

    def test_a_command_closes_its_own_copy_and_nothing_else(self):
        _write_xlsx(Path(self.td, "dates.xlsx"), DATES)
        theirs = self._someone_elses()
        self._wait_for([theirs])
        before = self._soffice_pids()
        run = self._command("read", "dates.xlsx", "--sheet", "S")
        out, err = run.communicate(timeout=240)
        self._assert_nothing_left(before, theirs)
        self.assertEqual(run.returncode, 0, err)
        self.assertEqual(json.loads(out), [["2026-10-07", "2026-09-30 14:05:00", 1234.56, "0012"]])

    def test_a_timeout_that_kills_the_command_takes_its_copy_with_it(self):
        # The bot's runner kills a command's process group when it times out.
        # Its LibreOffice shares that group, so it goes too instead of living on.
        _write_xlsx(Path(self.td, "dates.xlsx"), DATES)
        run = subprocess.Popen([PYTHON, str(SCRIPT), "read", "dates.xlsx", "--sheet", "S"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=self.env, cwd=self.td, start_new_session=True)
        self.spawned.append(run)

        def copies():
            return [p for p, words in _table() if words
                    and os.path.basename(words[0]).startswith(("soffice", "oosplash"))
                    and any(self.td in w for w in words)]

        deadline = time.monotonic() + 60
        while not copies() and run.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(copies(), "the command's LibreOffice never appeared")
        os.killpg(run.pid, signal.SIGKILL)
        run.wait(timeout=30)
        deadline = time.monotonic() + 0.5
        while copies() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(copies(), [], "the command's LibreOffice outlived its process group")

    def test_two_commands_at_once_both_work(self):
        # Two copies on one shared profile race for it at start: the old helper
        # lost both, so both commands at once ended with no LibreOffice at all.
        _write_xlsx(Path(self.td, "dates.xlsx"), DATES)
        _write_xlsx(Path(self.td, "Λογαριασμοί.xlsx"), GREEK)
        theirs = self._someone_elses()
        self._wait_for([theirs])
        before = self._soffice_pids()
        first = self._command("read", "dates.xlsx", "--sheet", "S")
        second = self._command("read", "Λογαριασμοί.xlsx", "--sheet", "S")
        (out1, err1), (out2, err2) = first.communicate(timeout=240), second.communicate(timeout=240)
        self._assert_nothing_left(before, theirs)
        self.assertEqual(first.returncode, 0, err1)
        self.assertEqual(second.returncode, 0, err2)
        self.assertEqual(json.loads(out1), [["2026-10-07", "2026-09-30 14:05:00", 1234.56, "0012"]])
        self.assertEqual(json.loads(out2), [["Ενοίκιο", 450]])

    def test_a_lock_left_by_a_killed_copy_no_longer_blocks_the_file(self):
        _write_xlsx(Path(self.td, "dates.xlsx"), DATES)
        lock = Path(self.td, ".~lock.dates.xlsx#")
        lock.write_text(_lock_text(Path(self.td, "mom-excel-lo-4242-killed", "profile")),
                        encoding="utf-8")
        run = self._command("read", "dates.xlsx", "--sheet", "S")
        out, err = run.communicate(timeout=240)
        self.assertEqual(run.returncode, 0, err)
        self.assertEqual(json.loads(out), [["2026-10-07", "2026-09-30 14:05:00", 1234.56, "0012"]])
        self.assertFalse(lock.exists())

    def test_a_file_someone_has_open_is_read_but_never_written(self):
        _write_xlsx(Path(self.td, "dates.xlsx"), DATES)
        lock = Path(self.td, ".~lock.dates.xlsx#")
        # This machine's lock: every LibreOffice here runs as the one OS account.
        text = _lock_text("/Users/someone/Library/Application Support/LibreOffice/4",
                          name="A Person").replace(",someone,", f",{getpass.getuser()},", 1)
        lock.write_text(text, encoding="utf-8")
        read = self._command("read", "dates.xlsx", "--sheet", "S")
        out, err = read.communicate(timeout=240)
        self.assertEqual(read.returncode, 0, err)
        self.assertEqual(json.loads(out), [["2026-10-07", "2026-09-30 14:05:00", 1234.56, "0012"]])
        write = self._command("write", "dates.xlsx", "--sheet", "S", "--cell", "E1", "--value", "x")
        out, err = write.communicate(timeout=240)
        self.assertEqual(write.returncode, 1)
        self.assertIn("is open in another LibreOffice (A Person on", err)
        self.assertIn("nothing was written", err)
        self.assertEqual(lock.read_text(encoding="utf-8"), text)

    def test_a_read_leaves_no_lock_behind(self):
        _write_xlsx(Path(self.td, "dates.xlsx"), DATES)
        read = self._command("info", "dates.xlsx")
        _, err = read.communicate(timeout=240)
        self.assertEqual(read.returncode, 0, err)
        self.assertEqual([n for n in os.listdir(self.td) if n.startswith(".~lock")], [])

    def test_a_workbook_of_several_sheets_writes_one_sheet_as_csv(self):
        # Left to pick the sheet itself, LibreOffice stopped to warn that CSV
        # holds only one, and headless it waited on that warning for ever.
        _write_xlsx(Path(self.td, "two.xlsx"), ("Α", FIRST), ("Σύνολα", GREEK))
        for extra, sheet, line in (((), "Α", "πρώτο,1"), (("--sheet", "Σύνολα"), "Σύνολα", "Ενοίκιο,450")):
            with self.subTest(sheet=sheet):
                out_csv = Path(self.td, f"out-{sheet}.csv")
                run = self._command("save-as", "two.xlsx", "--output", str(out_csv), *extra)
                out, err = run.communicate(timeout=240)
                self.assertEqual(run.returncode, 0, err)
                res = json.loads(out)
                self.assertEqual((res["sheet"], res["output"]), (sheet, str(out_csv)))
                self.assertIn("of 2", res["note"])
                self.assertEqual(out_csv.read_text(encoding="utf-8").strip(), line)
        self.assertEqual([n for n in os.listdir(self.td) if n.startswith((".~lock", "lu"))], [])

    def test_stop_closes_this_users_leftover_and_nothing_else(self):
        ended = subprocess.Popen(["sleep", "0"])
        ended_start = _started(ended.pid)
        ended.wait()
        gone_dir, gone = self._leftover("4242", ended.pid, ended_start or "Mon Jan  5 10:00:00 2026")
        busy_dir, busy = self._leftover("4242", os.getpid(), _started(os.getpid()))
        other_dir, other = self._leftover("777", ended.pid, ended_start or "Mon Jan  5 10:00:00 2026")
        theirs = self._someone_elses()
        self._wait_for([gone, busy, other, theirs])
        stop = self._command("stop")
        out, err = stop.communicate(timeout=240)
        with contextlib.suppress(subprocess.TimeoutExpired):
            gone.wait(timeout=30)
        self.assertIsNotNone(gone.poll(), "stop left this user's leftover running")
        self.assertIsNone(busy.poll(), "stop closed a copy whose command is still running")
        self.assertIsNone(other.poll(), "stop closed another user's copy")
        self.assertIsNone(theirs.poll(), "stop closed a LibreOffice this tool never started")
        self.assertEqual(stop.returncode, 0, err)
        res = json.loads(out)
        self.assertIn(gone.pid, res["stopped"])
        self.assertIn(busy.pid, res["in_use"])
        self.assertFalse(os.path.exists(gone_dir))
        self.assertTrue(os.path.exists(busy_dir) and os.path.exists(other_dir))


if __name__ == "__main__":
    unittest.main()
