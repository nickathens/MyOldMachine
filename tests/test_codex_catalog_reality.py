"""The Codex picker may only offer models Codex will actually run.

Three of the six ids the picker shipped after PR #158 were not Codex models.
They were OpenAI **API** model ids, which is a different catalog: the bare
`gpt-5.6` alias routes to Sol on the API and is rejected outright by Codex on
a ChatGPT account. Measured 2026-09-07 against codex-cli 0.153.4 on a live
account, one `codex exec` per id, each ending in HTTP 400 / turn.failed /
exit 1:

    The 'gpt-5.6' model is not supported when using Codex with a ChatGPT
    account.

Same for `gpt-5.4` and `gpt-5.3-codex`. Two controls in the same batch
completed normally: `gpt-5.5` (the shipped default) and `gpt-5.6-terra`, so
the probe was measuring the id and not the account.

The symptom a user sees is not a message about model ids. It is the Effort
section vanishing from the Mini App, because the effort table quite correctly
had no row for a model that does not exist, followed by every turn failing.

What locks it here is the pair of directions between the picker and the
effort table. `core.model_efforts` is deliberately stdlib-only and cannot
import the wizard, so this file is where the two lists are made to agree.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["MOM_TEST"] = "1"

from core import model_efforts as me  # noqa: E402
from install import wizard  # noqa: E402

# Every id in the catalog Codex CLI itself fetched from OpenAI and cached at
# ~/.codex/models_cache.json (client_version 0.153.4, fetched 2026-09-07).
# Two are internal and are deliberately not offered: `gpt-reserve` is spare
# capacity and `codex-auto-review` backs `codex review`.
CODEX_CATALOG = frozenset({
    "gpt-6-astra", "gpt-reserve", "gpt-5.6-sol", "gpt-5.6-terra",
    "gpt-5.6-luna", "gpt-5.5", "gpt-5.4-mini", "gpt-5.3-codex-spark",
    "codex-auto-review",
})

# Ids that are valid on the OpenAI API and are NOT Codex models. Named rather
# than merely absent, so re-adding one from the `openai` list below it in the
# same file fails with the reason attached.
API_ONLY_IDS = frozenset({
    "gpt-5.6", "gpt-5.4", "gpt-5.3-codex", "gpt-5.5-pro", "gpt-5.4-nano",
    "gpt-4.1", "gpt-4.1-mini",
})


def offered() -> list[str]:
    return [mid for mid, _ in wizard.PROVIDER_MODELS["codex"]]


class CodexCatalogTests(unittest.TestCase):
    def test_no_api_only_id_is_offered_as_a_codex_model(self):
        bad = sorted(set(offered()) & API_ONLY_IDS)
        self.assertEqual(
            bad, [],
            "these are OpenAI API ids, and Codex answers HTTP 400 for each "
            f"on a ChatGPT account: {bad}")

    def test_every_offered_model_is_in_the_codex_catalog(self):
        unknown = sorted(set(offered()) - CODEX_CATALOG)
        self.assertEqual(
            unknown, [],
            "offered but not in the catalog Codex fetches; run one "
            f"`codex exec -m <id>` before adding it: {unknown}")

    def test_the_openai_provider_keeps_its_own_ids(self):
        # The reverse mistake would be just as bad: these ARE valid on the
        # API, and the fix must not have pruned them from the API provider.
        api_ids = {mid for mid, _ in wizard.PROVIDER_MODELS["openai"]}
        self.assertIn("gpt-5.6", api_ids)
        self.assertIn("gpt-5.4", api_ids)


class PickerAndEffortTableAgreeTests(unittest.TestCase):
    """Both directions. Either one alone leaves half the defect standing."""

    def test_every_offered_model_has_an_effort_row(self):
        missing = [m for m in offered() if not me.efforts_for("codex", m)]
        self.assertEqual(
            missing, [],
            "offered with no effort row, so the Mini App hides the whole "
            f"Effort section with no explanation: {missing}")

    def test_every_model_in_the_effort_table_can_be_selected(self):
        # The other half of the same drift: four rows (the 5.6 aliases and
        # codex-spark) were unreachable, so their levels were dead weight.
        unreachable = sorted(set(me._MODEL_EFFORTS) - set(offered()))
        self.assertEqual(
            unreachable, [],
            f"in the effort table but not offered anywhere: {unreachable}")

    def test_every_effort_row_has_a_default_inside_its_own_levels(self):
        for model, levels in me._MODEL_EFFORTS.items():
            with self.subTest(model=model):
                default = me.default_effort_for("codex", model)
                self.assertIn(default, levels)

    def test_the_default_model_is_still_first_and_still_not_astra(self):
        # models[0][0] is the wizard's fallback when DEFAULT_MODELS misses,
        # so first place is a second door to becoming the default.
        self.assertEqual(offered()[0], wizard.DEFAULT_MODELS["codex"])
        self.assertNotEqual(offered()[0], "gpt-6-astra")

    def test_the_five_six_tiers_are_offered_by_their_real_ids(self):
        ids = offered()
        for tier in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
            with self.subTest(tier=tier):
                self.assertIn(tier, ids)

    def test_luna_has_no_ultra_and_sol_does(self):
        # Straight from the catalog rows, and the reason one shared list per
        # provider could never have been right.
        self.assertNotIn("ultra", me.efforts_for("codex", "gpt-5.6-luna"))
        self.assertIn("ultra", me.efforts_for("codex", "gpt-5.6-sol"))


if __name__ == "__main__":
    unittest.main()
