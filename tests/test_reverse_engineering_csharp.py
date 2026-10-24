"""reverse-engineering skill, .NET/Unity C# decompiler (csharp.py).

  - the decompile recovers known source from a managed assembly built here
    (DemoGame.Weapon, its Damage method, the RE_ORACLE_MARKER_42 constant), to
    stdout, for one type, and as a written project;
  - the gate on the project write refuses an unlawful purpose, a blank note and a
    non-empty folder, and a type argument that is not a plain type name is
    refused before anything runs (so it cannot smuggle an ilspycmd option);
  - a Unity IL2CPP global-metadata.dat is recognised by magic and version, and a
    non-IL2CPP file is refused.

The decompiles need Docker and the re-dotnet image and skip without them; the
gate, type-guard and IL2CPP checks are pure Python and always run. The sample
assembly (tests/fixtures/reverse_engineering/Sample.dll) was built here with the
.NET SDK.
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

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / "skills" / "reverse-engineering" / "scripts"
SAMPLE = ROOT / "fixtures" / "reverse_engineering" / "Sample.dll"


def load(name):
    spec = importlib.util.spec_from_file_location(f"re_{name}_under_test", str(SCRIPTS / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cs = load("csharp")
sbx = load("sandbox")

DOCKER_OK, _ = sbx.docker_available()
DOTNET_OK = DOCKER_OK and subprocess.run(
    ["docker", "image", "inspect", sbx.image_tag(sbx.DOTNET_DOCKERFILE)], capture_output=True).returncode == 0


def scratch(test, prefix):
    """A temp folder removed when the test ends (these tests once left one in /tmp per run)."""
    d = tempfile.mkdtemp(prefix=prefix)
    test.addCleanup(shutil.rmtree, d, True)
    return d


class CSharpUnit(unittest.TestCase):
    """Pure-Python checks: no Docker needed (they raise before the sandbox runs)."""

    def test_type_argument_must_be_a_plain_type_name(self):
        # pin the pattern itself (argv already blocks option injection, so the guard is
        # defence in depth; a decompile-raises check alone passes for the wrong reason)
        for bad in ("-o /etc", "a; rm -rf", "-p", "x\ny", "DemoGame.Weapon\n"):
            self.assertIsNone(cs.TYPE_OK.match(bad), bad)
        self.assertTrue(cs.TYPE_OK.match("DemoGame.Weapon"))
        self.assertTrue(cs.TYPE_OK.match("System.Collections.Generic.List`1"))
        with self.assertRaises(cs.CSharpError):
            cs.decompile(str(SAMPLE), type_name="-o /etc")

    def test_project_gate_refuses_unlawful_purpose_and_blank_note(self):
        out = scratch(self, "cs-gate-")
        os.rmdir(out)
        with self.assertRaises(cs.CSharpError):
            cs.decompile_project(str(SAMPLE), out, "piracy", "x")
        with self.assertRaises(cs.CSharpError):
            cs.decompile_project(str(SAMPLE), out, "interoperability", "   ")

    def test_project_gate_refuses_non_empty_folder(self):
        out = scratch(self, "cs-gate-")
        Path(out, "here").write_text("x")
        with self.assertRaises(cs.CSharpError):
            cs.decompile_project(str(SAMPLE), out, "interoperability", "reading my game")

    def test_lawful_purposes_from_schema(self):
        schema = json.loads((SCRIPTS / "dossier-schema.json").read_text())
        enum = schema["dossier_schema"]["properties"]["lawful_basis"]["properties"]["purpose"]["enum"]
        self.assertEqual(cs.lawful_purposes(), enum)

    def test_il2cpp_metadata_recognised(self):
        data = struct.pack("<II", cs.IL2CPP_MAGIC, 29) + b"\x00" * 64
        info = cs.il2cpp_info(data)
        self.assertEqual(info["version"], 29)
        self.assertTrue(info["il2cpp"])

    def test_non_il2cpp_is_refused(self):
        with self.assertRaises(cs.CSharpError):
            cs.il2cpp_info(b"MZ\x90\x00" + b"\x00" * 16)       # a PE, not IL2CPP metadata


@unittest.skipUnless(DOTNET_OK, "docker or the re-dotnet image is not present")
class CSharpDecompile(unittest.TestCase):
    def test_whole_assembly_recovers_the_source(self):
        text = cs.decompile(str(SAMPLE))
        for token in ("namespace DemoGame", "class Weapon", "Damage", "RE_ORACLE_MARKER_42"):
            self.assertIn(token, text, token)

    def test_one_type(self):
        text = cs.decompile(str(SAMPLE), type_name="DemoGame.Weapon")
        self.assertIn("Damage", text)
        self.assertNotIn("AssemblyInfo", text)        # just the one type, not the assembly scaffolding

    def test_project_write_recovers_the_method(self):
        out = scratch(self, "cs-proj-")
        os.rmdir(out)
        written, skipped = cs.decompile_project(str(SAMPLE), out, "interoperability", "reading my game's code")
        self.assertTrue(any(p.endswith(".csproj") for p in written))
        weapon = next(p for p in written if p.endswith("Weapon.cs"))
        self.assertIn("Damage", Path(weapon).read_text())

    def test_not_a_managed_assembly_errors(self):
        d = scratch(self, "cs-bad-")
        p = os.path.join(d, "native.bin")
        Path(p).write_bytes(b"\x7fELF" + b"\x00" * 200)
        with self.assertRaises(cs.CSharpError):
            cs.decompile(p)


if __name__ == "__main__":
    unittest.main()
