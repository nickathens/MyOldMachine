"""Offline unit tests for the greek-engineer DXF layer (katopsi.py).

The geometry core (shoelace areas, parsers, centroid) is stdlib and always
tested. DXF writing tests run only when ezdxf is installed, mirroring the
greek-law pattern of skipping bs4 dependent tests: CI without the optional
dependency stays green, a machine with it proves the full path including the
reread audit contract (a written file that fails audit must raise, never
return success).
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "greek-engineer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import katopsi  # noqa: E402

try:
    import ezdxf  # noqa: F401
    HAS_EZDXF = True
except ImportError:
    HAS_EZDXF = False


class GeometryTests(unittest.TestCase):
    def test_shoelace_unit_square(self):
        self.assertAlmostEqual(katopsi.shoelace([(0, 0), (1, 0), (1, 1), (0, 1)]), 1.0)

    def test_shoelace_rectangle_orientation_independent(self):
        cw = [(0, 0), (0, 20), (25, 20), (25, 0)]
        ccw = [(0, 0), (25, 0), (25, 20), (0, 20)]
        self.assertAlmostEqual(katopsi.shoelace(cw), 500.0)
        self.assertAlmostEqual(katopsi.shoelace(ccw), 500.0)

    def test_shoelace_triangle(self):
        self.assertAlmostEqual(katopsi.shoelace([(0, 0), (4, 0), (0, 3)]), 6.0)

    def test_shoelace_l_shape(self):
        pts = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]
        self.assertAlmostEqual(katopsi.shoelace(pts), 12.0)

    def test_shoelace_rejects_degenerate(self):
        with self.assertRaises(ValueError):
            katopsi.shoelace([(0, 0), (1, 1)])

    def test_centroid_of_square(self):
        cx, cy = katopsi.centroid([(0, 0), (2, 0), (2, 2), (0, 2)])
        self.assertAlmostEqual(cx, 1.0)
        self.assertAlmostEqual(cy, 1.0)

    def test_parse_points(self):
        pts = katopsi.parse_points("0,0 25,0 25,20 0,20")
        self.assertEqual(len(pts), 4)
        self.assertEqual(pts[2], (25.0, 20.0))
        self.assertEqual(katopsi.parse_points("0,0;1,0;1,1"),
                         [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])

    def test_parse_points_rejects_malformed(self):
        with self.assertRaises(ValueError):
            katopsi.parse_points("0,0 1")
        with self.assertRaises(ValueError):
            katopsi.parse_points("0,0,0 1,1")

    def test_parse_rooms(self):
        rooms = katopsi.parse_rooms("ΚΑΘΙΣΤΙΚΟ:0,0,5.2,4.1 ΚΟΥΖΙΝΑ:5.45,0,3.0,4.1")
        self.assertEqual(len(rooms), 2)
        self.assertEqual(rooms[0]["onoma"], "ΚΑΘΙΣΤΙΚΟ")
        self.assertAlmostEqual(rooms[1]["x"], 5.45)

    def test_parse_rooms_rejects_malformed(self):
        with self.assertRaises(ValueError):
            katopsi.parse_rooms("ΧΩΡΙΣΓΕΩΜΕΤΡΙΑ")
        with self.assertRaises(ValueError):
            katopsi.parse_rooms("ΔΩΜΑ:1,2,3")
        with self.assertRaises(ValueError):
            katopsi.parse_rooms("ΔΩΜΑ:0,0,0,4")
        with self.assertRaises(ValueError):
            katopsi.parse_rooms("")

    def test_emvadon_cli_needs_no_ezdxf(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = katopsi.main(["emvadon", "--polygono", "0,0 25,0 25,20 0,20", "--json"])
        self.assertEqual(rc, 0)
        self.assertAlmostEqual(json.loads(buf.getvalue())["emvadon_m2"], 500.0)


@unittest.skipUnless(HAS_EZDXF, "ezdxf not installed; DXF write path untested here")
class DxfWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return str(Path(self.tmp.name) / name)

    def test_diagramma_writes_valid_audited_dxf(self):
        out = self.path("d.dxf")
        r = katopsi.diagramma([(0, 0), (25, 0), (25, 20), (0, 20)],
                              [(5, 5), (17, 5), (17, 15), (5, 15)], out)
        self.assertEqual(r["audit_sfalmata"], 0)
        self.assertAlmostEqual(r["emvadon_oikopedou_m2"], 500.0)
        self.assertAlmostEqual(r["emvadon_ktiriou_m2"], 120.0)
        self.assertAlmostEqual(r["kalypsi_pct"], 24.0)
        self.assertTrue(Path(out).is_file())
        doc = ezdxf.readfile(out)
        layers = {la.dxf.name for la in doc.layers}
        self.assertIn("ΟΙΚΟΠΕΔΟ", layers)
        self.assertIn("ΚΤΙΡΙΟ", layers)
        self.assertIn("ΚΕΙΜΕΝΑ", layers)

    def test_diagramma_rejects_building_larger_than_plot(self):
        with self.assertRaises(ValueError):
            katopsi.diagramma([(0, 0), (10, 0), (10, 10), (0, 10)],
                              [(0, 0), (20, 0), (20, 20), (0, 20)], self.path("x.dxf"))

    def test_plano_writes_rooms_with_areas(self):
        out = self.path("p.dxf")
        rooms = katopsi.parse_rooms("ΚΑΘΙΣΤΙΚΟ:0,0,5.2,4.1 ΚΟΥΖΙΝΑ:5.45,0,3.0,4.1")
        r = katopsi.plano(rooms, out)
        self.assertEqual(r["audit_sfalmata"], 0)
        self.assertEqual(r["domatia"], 2)
        self.assertAlmostEqual(r["katharo_emvadon_m2"], round(5.2 * 4.1 + 3.0 * 4.1, 2))
        doc = ezdxf.readfile(out)
        texts = [e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT"]
        self.assertTrue(any("ΚΑΘΙΣΤΙΚΟ" in t for t in texts))

    def test_diagramma_cli_json(self):
        out = self.path("c.dxf")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = katopsi.main(["diagramma", "--oikopedo", "0,0 25,0 25,20 0,20",
                               "--ktirio", "5,5 17,5 17,15 5,15", "--out", out, "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["audit_sfalmata"], 0)


if __name__ == "__main__":
    unittest.main()
