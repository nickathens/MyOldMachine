"""requirements.txt holds certifi at the release that dropped the compromised
eMudhra root.

certifi before 2026.10.19 still trusts emSign ECC Root CA - G3, which signed
eight intermediates whose private keys leaked in October 2026
(GHSA-6q48-6c7w-9gw2). httpx, and with it python-telegram-bot, verifies every
connection against certifi's list, and pip never moves a dependency that is
already met, so the floor is what brings an existing install up on /update.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CertifiFloorTests(unittest.TestCase):
    def test_the_floor_is_the_fixed_release(self):
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        specs = [line.split("#")[0].strip() for line in lines]
        self.assertEqual([s for s in specs if re.match(r"certifi\b", s, re.I)],
                         ["certifi>=2026.10.19"])


if __name__ == "__main__":
    unittest.main()
