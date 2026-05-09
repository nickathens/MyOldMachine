"""Tests for resume-time detection of optional features in install.wizard.

When an existing install is upgraded to a version that ships a new optional
feature (e.g. local Telegram Bot API server), the wizard's resume path used
to short-circuit out before ever asking about it. The user had to wipe
.env or hand-edit it to discover the new feature exists.

`_offer_missing_optional_features` walks an in-process registry on every
resume. Each entry knows:
  - how to check whether the feature is already configured
  - how to ask the user if they want to set it up now (defaulting to no)

If anything is newly enabled, the caller rewrites .env so the gated install
steps later in main() pick up the change.
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install import wizard  # noqa: E402


class OfferMissingOptionalFeaturesTests(unittest.TestCase):
    def setUp(self):
        # Replace the live registry with a controllable double per-test, so
        # adding real features later doesn't break these cases.
        self._original_registry = wizard.OPTIONAL_FEATURES

    def tearDown(self):
        wizard.OPTIONAL_FEATURES = self._original_registry

    def _set_registry(self, features):
        wizard.OPTIONAL_FEATURES = features

    def test_returns_false_when_registry_empty(self):
        self._set_registry([])
        config = {"telegram_token": "x"}
        with patch.object(wizard, "ask") as ask_mock:
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        ask_mock.assert_not_called()

    def test_returns_false_when_all_features_configured(self):
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            }
        ])
        config = {"feat_a_on": True}
        with patch.object(wizard, "ask") as ask_mock:
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        ask_mock.assert_not_called()

    def test_skips_feature_when_user_declines(self):
        configure_mock = unittest_mock_callable()
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": configure_mock,
            }
        ])
        config = {}
        with patch.object(wizard, "ask", return_value="n"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        self.assertFalse(configure_mock.called)
        self.assertNotIn("feat_a_on", config)

    def test_default_decline_on_empty_input(self):
        # ask() returns the default ("n") on empty input; we get the literal
        # default back. Verify we treat that as decline.
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            }
        ])
        config = {}
        # Simulate user pressing Enter — `ask` returns its default.
        with patch.object(wizard, "ask", return_value="n"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        self.assertNotIn("feat_a_on", config)

    def test_runs_configure_when_user_accepts(self):
        def configure(c):
            c["feat_a_on"] = True

        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": configure,
            }
        ])
        config = {}
        with patch.object(wizard, "ask", return_value="y"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertTrue(result)
        self.assertTrue(config.get("feat_a_on"))

    def test_returns_false_when_user_accepts_outer_but_inner_declines(self):
        # User says "y" to "Set up now?" but then bails out inside the
        # feature's own prompts. The configure callback explicitly leaves
        # feat_a_on as False to model this.
        def configure_that_aborts(c):
            c["feat_a_on"] = False  # user declined inside

        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": configure_that_aborts,
            }
        ])
        config = {}
        with patch.object(wizard, "ask", return_value="y"):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertFalse(result)
        self.assertFalse(config.get("feat_a_on"))

    def test_walks_multiple_features_independently(self):
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            },
            {
                "key": "feat_b",
                "label": "Feature B",
                "summary": "does B",
                "is_configured": lambda c: c.get("feat_b_on", False),
                "configure": lambda c: c.update({"feat_b_on": True}),
            },
        ])
        config = {}
        # User says yes to A, no to B
        with patch.object(wizard, "ask", side_effect=["y", "n"]):
            result = wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertTrue(result)  # at least one enabled
        self.assertTrue(config.get("feat_a_on"))
        self.assertNotIn("feat_b_on", config)

    def test_already_configured_features_are_not_prompted(self):
        # Pre-existing feat_a (already configured); only feat_b should prompt.
        ask_calls = []

        def fake_ask(prompt, default=None):
            ask_calls.append(prompt)
            return "n"

        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            },
            {
                "key": "feat_b",
                "label": "Feature B",
                "summary": "does B",
                "is_configured": lambda c: c.get("feat_b_on", False),
                "configure": lambda c: c.update({"feat_b_on": True}),
            },
        ])
        config = {"feat_a_on": True}
        with patch.object(wizard, "ask", side_effect=fake_ask):
            wizard._offer_missing_optional_features(Path("/tmp"), config)
        self.assertEqual(len(ask_calls), 1)  # only feat_b

    def test_accepts_yes_in_any_case(self):
        self._set_registry([
            {
                "key": "feat_a",
                "label": "Feature A",
                "summary": "does A",
                "is_configured": lambda c: c.get("feat_a_on", False),
                "configure": lambda c: c.update({"feat_a_on": True}),
            }
        ])
        for accept_word in ("y", "Y", "yes", "YES", "Yes"):
            with self.subTest(answer=accept_word):
                config = {}
                with patch.object(wizard, "ask", return_value=accept_word):
                    result = wizard._offer_missing_optional_features(Path("/tmp"), config)
                self.assertTrue(result)
                self.assertTrue(config.get("feat_a_on"))


class TelegramBotApiRegistryEntryTests(unittest.TestCase):
    """The shipped registry must contain the telegram-bot-api entry."""

    def test_registry_contains_telegram_bot_api(self):
        keys = {f["key"] for f in wizard.OPTIONAL_FEATURES}
        self.assertIn("telegram_bot_api", keys)

    def test_telegram_bot_api_is_configured_reads_env_flag(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "telegram_bot_api")
        self.assertFalse(feat["is_configured"]({}))
        self.assertFalse(feat["is_configured"]({"telegram_local_api_enabled": False}))
        self.assertTrue(feat["is_configured"]({"telegram_local_api_enabled": True}))

    def test_telegram_bot_api_configure_invokes_step(self):
        feat = next(f for f in wizard.OPTIONAL_FEATURES
                    if f["key"] == "telegram_bot_api")
        config = {}
        with patch.object(wizard, "_run_telegram_bot_api_step") as step_mock:
            feat["configure"](config)
        step_mock.assert_called_once_with(config)

    def test_required_keys_present_on_every_entry(self):
        for feat in wizard.OPTIONAL_FEATURES:
            with self.subTest(feature=feat.get("key")):
                for key in ("key", "label", "summary", "is_configured", "configure"):
                    self.assertIn(key, feat)
                self.assertTrue(callable(feat["is_configured"]))
                self.assertTrue(callable(feat["configure"]))

    def test_keys_are_unique(self):
        keys = [f["key"] for f in wizard.OPTIONAL_FEATURES]
        self.assertEqual(len(keys), len(set(keys)),
                         "Optional feature keys must be unique")


def unittest_mock_callable():
    """Tiny callable double that records whether it was invoked.

    Avoids importing MagicMock just for one boolean — keeps test deps minimal
    and the call-site obvious.
    """

    class _Probe:
        def __init__(self):
            self.called = False
            self.calls = []

        def __call__(self, *args, **kwargs):
            self.called = True
            self.calls.append((args, kwargs))

    return _Probe()


class StdoutSilencer:
    """Context manager: swallows stdout to keep test output clean."""

    def __enter__(self):
        self._real = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *exc):
        sys.stdout = self._real


# Wrap every test method's stdout. The wizard prints headers and labels via
# `print` on the real stdout; that noise drowns the actual test report.
def _wrap_with_stdout_silencer(cls):
    for name in list(vars(cls)):
        if name.startswith("test_"):
            method = getattr(cls, name)

            def make_wrapped(m):
                def wrapped(self, *a, **kw):
                    with StdoutSilencer():
                        return m(self, *a, **kw)
                return wrapped

            setattr(cls, name, make_wrapped(method))
    return cls


OfferMissingOptionalFeaturesTests = _wrap_with_stdout_silencer(
    OfferMissingOptionalFeaturesTests
)
TelegramBotApiRegistryEntryTests = _wrap_with_stdout_silencer(
    TelegramBotApiRegistryEntryTests
)


if __name__ == "__main__":
    unittest.main()
