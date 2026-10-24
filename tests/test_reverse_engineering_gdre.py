"""reverse-engineering skill, games tier: Godot scripts and scenes via GDRE Tools (gdre.py).

Evidence, matching the verification spine:
  - every compiled-script format from a real editor: main.gdc in the packs made
    here by Godot 3.6.3, 4.4.1, 4.6.2 and 4.7.2 (tests/fixtures/reverse_engineering/gdre,
    built by proof/gdre/prove.py) must decompile to its committed source token
    for token, except the one measured loss (a Godot 4 literal -0.0 comes back as
    0.0), and with the bytecode table GDRE should pick for that engine;
  - a second reader: GDRE's own listing of each pack must equal godot_pck's;
  - a recovered project must carry the same script, scene and resource;
  - failures must stay failures: GDRE exits 0 when a decompile fails, so a missing
    output is an error; a wrong engine table and a corrupt script are errors; a
    crash is reported with its signal; an output left as a link is never read;
  - the recover gate refuses an unlawful purpose, a blank note and a full folder.

The GDRE runs need Docker and the re-gdre image and skip without them; the gate,
the version guard, the parsers and the fake-sandbox checks always run. The
behaviour half of the proof (original and decompiled scripts run side by side in
each engine, with planted controls) is proof/gdre/prove.py verify; its last run is
proof/gdre/result.json. The comparison helpers are prove.py's, loaded here.
"""
import importlib.util
import json
import os
import struct
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
SKILL = ROOT.parent / "skills" / "reverse-engineering"
SCRIPTS = SKILL / "scripts"
FX = ROOT / "fixtures" / "reverse_engineering" / "gdre"
SCENE = "res://.godot/exported/133200997/export-3070c538c03ee49b7677ff960a3f5195-main.scn"
STYLE = "res://.godot/exported/133200997/export-9c798deb4b444e75f415926f8ed59880-style.res"


def load(name, path):
    spec = importlib.util.spec_from_file_location(f"re_{name}_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gd = load("gdre", SCRIPTS / "gdre.py")
prove = load("gdre_prove", SKILL / "proof" / "gdre" / "prove.py")

DOCKER_OK, _ = gd.sandbox.docker_available()
GDRE_OK = DOCKER_OK and subprocess.run(
    ["docker", "image", "inspect", gd.sandbox.image_tag(gd.sandbox.GDRE_DOCKERFILE)],
    capture_output=True).returncode == 0
# fixture: (source folder, the bytecode table GDRE must pick for its engine)
PACKS = {
    "godot3.6.3": ("src3", "3.5.0-stable (a7aad78)"),
    "godot4.4.1": ("src4", "4.3.0-stable (77af6ca)"),
    "godot4.6.2": ("src4", "4.5.0-stable (ebc36a7)"),
    "godot4.7.2": ("src4", "4.5.0-stable (ebc36a7)"),
}


def source(src, name="main.gd"):
    return (FX / src / name).read_text(encoding="utf-8")


def pack_v2(entries):
    """A small format-2 pack whose entries carry flags: (path, data, flags)."""
    def pad(b):
        return b + b"\x00" * (((len(b) + 3) & ~3) - len(b))
    head = bytearray(b"GDPC" + struct.pack("<IIIII", 2, 4, 2, 0, 0) + struct.pack("<Q", 0) + b"\x00" * 64)
    dirsize = 4 + sum(4 + len(pad(p.encode())) + 8 + 8 + 16 + 4 for p, _, _ in entries)
    base = len(head) + dirsize
    struct.pack_into("<Q", head, 24, base)                 # file base after magic, 4 u32 and flags
    d, blob = bytearray(struct.pack("<I", len(entries))), bytearray()
    for path, data, flags in entries:
        pb = pad(path.encode())
        d += struct.pack("<I", len(pb)) + pb + struct.pack("<QQ", len(blob), len(data)) + b"\x00" * 16
        d += struct.pack("<I", flags)
        blob += data
    return bytes(head) + bytes(d) + bytes(blob)


def fake_run(writes=None, rc=0, stdout="", stderr=""):
    """A stand-in for sandbox.run: writes {relative path: bytes or ('link', target)}
    into the run's writable mount, then answers like a finished container."""
    def run(cmd, rw=(), **kw):
        if rw:
            host = rw[0].rpartition(":")[0]
            for rel, what in (writes or {}).items():
                path = os.path.join(host, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if isinstance(what, tuple):
                    os.symlink(what[1], path)
                else:
                    Path(path).write_bytes(what)
        return {"returncode": rc, "timed_out": False, "over_cap": False, "stdout": stdout,
                "stdout_cut": False, "stderr": stderr, "argv": cmd}
    return run


def scratch(test, prefix):
    """A temp folder removed when the test ends (these tests once left one in /tmp per run)."""
    d = tempfile.mkdtemp(prefix=prefix)
    test.addCleanup(shutil.rmtree, d, True)
    return d


class GdreUnit(unittest.TestCase):
    """No Docker: everything here stops before, or fakes, the sandbox."""

    def setUp(self):
        p = mock.patch.object(gd.sandbox, "ensure_image", lambda *a, **k: "fake:tag")
        p.start()
        self.addCleanup(p.stop)
        self.tmp = scratch(self, "gdre-unit-")
        self.gdc = os.path.join(self.tmp, "x.gdc")
        Path(self.gdc).write_bytes(b"GDSC" + b"\x00" * 32)

    def test_engine_version_must_be_plain(self):
        for good in ("4.7.2", "3.6", "1.0.0", "4.10.1"):
            self.assertTrue(gd.ENGINE_OK.match(good), good)
        for bad in ("9.9.9", "5.0", "4", "4.7.2.stable", "4.7.2; rm -rf /", "--key=00", "", "4.7.2\n"):
            self.assertIsNone(gd.ENGINE_OK.match(bad), bad)
        boom = mock.Mock(side_effect=AssertionError("the sandbox must not run on a bad version"))
        with mock.patch.object(gd.sandbox, "run", boom):
            with self.assertRaises(gd.GdreError):
                gd.decompile(self.gdc, "9.9.9")
        boom.assert_not_called()

    def test_kind_of(self):
        self.assertEqual(gd.kind_of("res://a/main.gdc"), "script")
        self.assertEqual(gd.kind_of("MAIN.GDC"), "script")
        self.assertEqual(gd.kind_of("x.scn"), "resource")
        self.assertEqual(gd.kind_of("x.res"), "resource")
        with self.assertRaisesRegex(gd.GdreError, "encrypted"):
            gd.kind_of("x.gde")
        for text in ("x.gd", "x.tscn", "x.tres", "game.pck"):
            with self.assertRaises(gd.GdreError):
                gd.kind_of(text)

    def test_a_failed_decompile_that_exits_zero_is_an_error(self):
        run = fake_run(rc=0, stdout="Error: failed to decompile /in/script.gdc\nDecompilation complete\n",
                       stderr="ERROR: Binary GDScript is too recent! Please use a newer engine version.\n")
        err = None
        with mock.patch.object(gd.sandbox, "run", run):
            try:
                gd.decompile(self.gdc, "4.3.0")
            except Exception as e:                 # any other outcome (no error, the wrong error) fails below
                err = e
        self.assertIsInstance(err, gd.GdreError)
        self.assertIn("too recent", str(err))

    def test_a_decompile_reports_the_table_it_used(self):
        run = fake_run(writes={"script.gd": b"extends Node\n"},
                       stdout="Decompiling from bytecode version ebc36a7 (4.5.0-stable)\n")
        with mock.patch.object(gd.sandbox, "run", run):
            self.assertEqual(gd.decompile(self.gdc, "4.7.2"), ("extends Node\n", "4.5.0-stable (ebc36a7)"))

    def test_a_crash_is_reported_with_its_signal(self):
        with mock.patch.object(gd.sandbox, "run", fake_run(rc=139, stderr="timeout: the monitored command dumped core")):
            with self.assertRaisesRegex(gd.GdreError, "crashed.*signal 11"):
                gd.decompile(self.gdc, "4.7.2")

    def test_a_planted_link_is_not_read(self):
        secret = os.path.join(self.tmp, "secret.env")
        Path(secret).write_text("TOKEN=do-not-leak")
        with mock.patch.object(gd.sandbox, "run", fake_run(writes={"script.gd": ("link", secret)})):
            with self.assertRaises(gd.sandbox.SandboxError) as cm:
                gd.decompile(self.gdc, "4.7.2")
        self.assertNotIn("do-not-leak", str(cm.exception))

    def test_to_text_needs_exactly_one_output(self):
        res = os.path.join(self.tmp, "x.scn")
        Path(res).write_bytes(b"RSRC")
        for writes in ({}, {"a.scn": b"[gd_scene]", "b.scn": b"[gd_scene]"}):
            with mock.patch.object(gd.sandbox, "run", fake_run(writes=writes)):
                with self.assertRaises(gd.GdreError):
                    gd.to_text(res)
        with mock.patch.object(gd.sandbox, "run", fake_run(writes={"resource.scn": b"[gd_resource]\n"})):
            self.assertEqual(gd.to_text(res), "[gd_resource]\n")

    def test_member_bytes_reads_the_stored_script(self):
        for name, engine in (("godot4.7.2", "4.7.2"), ("godot3.6.3", "3.6.3")):
            for member in ("res://main.gdc", "main.gdc"):
                with self.subTest(pack=name, member=member):
                    try:
                        data, eng = gd.member_bytes(str(FX / f"{name}.pck"), member)
                    except gd.GdreError as e:
                        self.fail(f"{member} should be found in {name}: {e}")
                    self.assertEqual(data[:4], b"GDSC")
                    self.assertEqual(eng, engine)

    def test_member_missing_encrypted_or_twice_is_refused(self):
        path = os.path.join(self.tmp, "flags.pck")
        Path(path).write_bytes(pack_v2([("a.gdc", b"GDSC1", 0), ("locked.gdc", b"xxxx", 1),
                                        ("twice.gdc", b"GDSC2", 0), ("twice.gdc", b"GDSC3", 0)]))
        self.assertEqual(gd.member_bytes(path, "res://a.gdc"), (b"GDSC1", "4.2.0"))
        for member, why in (("res://nope.gdc", "not in the pack"), ("res://locked.gdc", "encrypted"),
                            ("res://twice.gdc", "appears 2 times")):
            with self.subTest(member=member):
                with self.assertRaisesRegex(gd.GdreError, why):
                    gd.member_bytes(path, member)

    def test_recover_gate_refuses_unlawful_purpose_and_blank_note(self):
        out = os.path.join(self.tmp, "out")
        boom = mock.Mock(side_effect=AssertionError("the gate must stop before the sandbox"))
        with mock.patch.object(gd.sandbox, "run", boom):
            with self.assertRaises(gd.GdreError):
                gd.recover(str(FX / "godot4.7.2.pck"), out, "piracy", "x")
            with self.assertRaises(gd.GdreError):
                gd.recover(str(FX / "godot4.7.2.pck"), out, "own_work", "   ")
        boom.assert_not_called()

    def test_recover_refuses_a_folder_that_is_not_empty(self):
        out = os.path.join(self.tmp, "full")
        os.makedirs(out)
        Path(out, "here").write_text("x")
        boom = mock.Mock(side_effect=AssertionError("the gate must stop before the sandbox"))
        with mock.patch.object(gd.sandbox, "run", boom):
            with self.assertRaises(gd.GdreError):
                gd.recover(str(FX / "godot4.7.2.pck"), out, "own_work", "my own game")

    def test_recover_failure_is_an_error(self):
        out = os.path.join(self.tmp, "rec")
        run = fake_run(writes={"project/gdre_export.log": b"ERROR: FATAL ERROR: Can't open pack!\n"}, rc=1,
                       stdout="ERROR: FATAL ERROR: Can't open pack!\n")
        with mock.patch.object(gd.sandbox, "run", run):
            with self.assertRaisesRegex(gd.GdreError, "Can't open pack"):
                gd.recover(str(FX / "godot4.7.2.pck"), out, "own_work", "my own game")

    def test_lawful_purposes_from_schema(self):
        schema = json.loads((SCRIPTS / "dossier-schema.json").read_text())
        enum = schema["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]
        self.assertEqual(gd.lawful_purposes(), enum)

    def test_parse_listing(self):
        out = ("Godot Engine v4.8.dev\nOpening file: /in/target.pck\n\nContents:\nres://a.gdc\n"
               "WARNING: a line GDRE could print in between\nres://b/c.scn\n\nTotal files: 2\n")
        try:
            got = gd.parse_listing(out)
        except gd.GdreError as e:              # a warning line counted as a path trips the total check
            self.fail(f"a listing with a warning line in it should parse: {e}")
        self.assertEqual(got, ["res://a.gdc", "res://b/c.scn"])
        with self.assertRaisesRegex(gd.GdreError, "total of 3"):
            gd.parse_listing(out.replace("Total files: 2", "Total files: 3"))
        with self.assertRaises(gd.GdreError):
            gd.parse_listing("ERROR: FATAL ERROR: Can't open pack!\n")

    def test_parse_report(self):
        log = ("junk\n****EXPORT REPORT****\n\nTotals:\nDecompiled scripts:                     3\n"
               "Scripts not decompiled:                 1\nNon-importable conversions:             2\n"
               "Failed conversions:                     0\n-------------\nLossy:   9\n")
        self.assertEqual(gd.parse_report(log), {"Decompiled scripts": 3, "Scripts not decompiled": 1,
                                                "Non-importable conversions": 2, "Failed conversions": 0})

    def test_target_mounts_only_the_file(self):
        spec, inside = gd._target(str(FX / "godot4.7.2.pck"))
        self.assertEqual(spec, f"{FX / 'godot4.7.2.pck'}:/in/target.pck")
        self.assertEqual(inside, "/in/target.pck")
        odd = os.path.join(self.tmp, "game.p ck")
        Path(odd).write_bytes(b"x")
        self.assertEqual(gd._target(odd)[1], "/in/target")
        with self.assertRaises(gd.GdreError):
            gd._target(os.path.join(self.tmp, "missing.pck"))


class ProofHelpers(unittest.TestCase):
    """The comparison the proof and the tests below lean on must see real changes."""

    def test_notation_is_canonical_but_values_are_not(self):
        same = [("x := 0xFF", "x: = 255"), ("x = 0b1011", "x = 11"), ("x = 1_000", "x = 1000"),
                ("x = 1e-7", "x = 1e-07"), ('x = r"C:\\a\\n"', 'x = "C:\\\\a\\\\n"'),
                ("x = 0.333333333333333314829616256247", "x = 0.3333333333333333"),
                ("f(a,b) # note", "f(a, b)"), ('s = "tab\\there"', "s = 'tab\\there'")]
        for a, b in same:
            self.assertEqual(prove.canonical_line(a), prove.canonical_line(b), (a, b))
        differ = [("x = -0.0", "x = 0.0"), ("x = 3.0", "x = 3"), ("if a <= 0:", "if a < 0:"),
                  ('s = "ab"', 's = "a b"'), ('n = &"a"', 'n = "a"'), ('p = ^"a/b"', 'p = "a/b"'),
                  ("x = 255", "x = 0xFE")]
        for a, b in differ:
            self.assertNotEqual(prove.canonical_line(a), prove.canonical_line(b), (a, b))

    def test_script_diff_keys_by_source_line(self):
        self.assertEqual(prove.script_diff("a\n# c\nb = 1\n", "a\n\nb = 1\n"), {})
        self.assertEqual(set(prove.script_diff("a\nb = 1\n", "a\nb = 2\n")), {2})
        self.assertEqual(set(prove.script_diff("a\nb\n", "a\n")), {2})

    def test_scene_lines_ignore_ids_and_order_but_not_values(self):
        a = '[gd_scene load_steps=2 format=3]\n\n[node name="N" type="Node"]\nb = 2\na = 1\n'
        b = '[gd_scene format=3 uid="uid://x"]\n[node name="N" type="Node" unique_id=12]\na = 1\nb = 2\n'
        self.assertEqual(prove.scene_lines(a), prove.scene_lines(b))
        self.assertNotEqual(prove.scene_lines(a), prove.scene_lines(b.replace("b = 2", "b = 3")))
        self.assertNotEqual(prove.scene_lines(a), prove.scene_lines(b.replace('name="N"', 'name="M"')))


@unittest.skipUnless(GDRE_OK, "docker or the re-gdre image is not present")
class GdreReal(unittest.TestCase):
    def test_every_engine_decompiles_to_its_source(self):
        for name, (src, table) in PACKS.items():
            with self.subTest(pack=name):
                try:
                    text, used = gd.decompile_member(str(FX / f"{name}.pck"), "res://main.gdc")
                except gd.GdreError as e:
                    self.fail(f"{name} should decompile: {e}")
                self.assertEqual(used, table)
                self.assertEqual(prove.script_diff(source(src), text), prove.KNOWN[name])
                self.assertIn("RE_GDRE_MARKER_", text)

    def test_a_loose_gdc_decompiles_with_its_engine(self):
        data, engine = gd.member_bytes(str(FX / "godot4.7.2.pck"), "res://main.gdc")
        path = os.path.join(scratch(self, "gdre-loose-"), "main.gdc")
        Path(path).write_bytes(data)
        text, _ = gd.decompile(path, engine)
        self.assertEqual(prove.script_diff(source("src4"), text), prove.KNOWN["godot4.7.2"])

    def test_listing_agrees_with_godot_pck(self):
        for name in PACKS:
            with self.subTest(pack=name):
                _rep, ours = prove._pck_files(FX / f"{name}.pck")
                self.assertEqual(set(gd.list_files(str(FX / f"{name}.pck"))), ours)

    def test_a_wrong_engine_table_is_an_error(self):
        with self.assertRaisesRegex(gd.GdreError, "too recent"):
            gd.decompile_member(str(FX / "godot4.7.2.pck"), "res://main.gdc", engine="4.3.0")

    def test_a_corrupt_script_is_an_error(self):
        data, _ = gd.member_bytes(str(FX / "godot4.7.2.pck"), "res://main.gdc")
        path = os.path.join(scratch(self, "gdre-bad-"), "cut.gdc")
        Path(path).write_bytes(data[:300])
        with self.assertRaises(gd.GdreError):
            gd.decompile(path, "4.7.2")

    def test_scene_and_resource_as_text(self):
        text, table = gd.from_member(str(FX / "godot4.7.2.pck"), SCENE)
        self.assertIsNone(table)
        for token in ("PackedScene", "RE_SCENE_MARKER_55", "Timer", "_on_timer_timeout"):
            self.assertIn(token, text)
        style, _ = gd.from_member(str(FX / "godot4.7.2.pck"), STYLE)
        self.assertEqual(prove.scene_lines(style), prove.scene_lines(source("src4", "style.tres")))

    def test_recover_rebuilds_script_scene_and_resource(self):
        out = os.path.join(scratch(self, "gdre-rec-"), "project")
        written, skipped, report = gd.recover(str(FX / "godot4.7.2.pck"), out, "own_work", "my own probe project")
        self.assertEqual(skipped, [])
        self.assertEqual(report.get("Decompiled scripts"), 1)
        self.assertEqual(report.get("Failed conversions"), 0)
        rel = {os.path.relpath(w, out) for w in written}
        self.assertTrue({"main.gd", "main.tscn", "style.tres", "project.godot"} <= rel, rel)
        text, _ = gd.decompile_member(str(FX / "godot4.7.2.pck"), "res://main.gdc")
        self.assertEqual(Path(out, "main.gd").read_text(encoding="utf-8"), text)
        for name in ("main.tscn", "style.tres"):
            self.assertEqual(prove.scene_lines(Path(out, name).read_text(encoding="utf-8")),
                             prove.scene_lines(source("src4", name)), name)

    def test_recover_scripts_only(self):
        out = os.path.join(scratch(self, "gdre-scr-"), "project")
        written, _, _ = gd.recover(str(FX / "godot4.7.2.pck"), out, "own_work", "my own probe project",
                                   scripts_only=True)
        rel = {os.path.relpath(w, out) for w in written}
        self.assertIn("main.gd", rel)
        self.assertFalse({"main.tscn", "style.tres"} & rel, rel)

    def test_recover_godot3(self):
        out = os.path.join(scratch(self, "gdre-g3-"), "project")
        gd.recover(str(FX / "godot3.6.3.pck"), out, "own_work", "my own probe project")
        self.assertEqual(prove.script_diff(source("src3"), Path(out, "main.gd").read_text(encoding="utf-8")), {})
        self.assertEqual(prove.scene_lines(Path(out, "main.tscn").read_text(encoding="utf-8")),
                         prove.scene_lines(source("src3", "main.tscn")))


if __name__ == "__main__":
    unittest.main()
