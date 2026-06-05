"""Tests for premortem.py - failure-mode analysis framework.

Isolation:
- Module-level path constants (DATA_DIR / DB_FILE / RESEARCH_DIR) are
  monkeypatched onto a tmp_path per test, so the real shared research.db is
  never touched.
- The CLI smoke test runs the script in a subprocess with HOME set to tmp_path,
  which redirects Path.home() and therefore the data dir for that process.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "premortem.py"

spec = importlib.util.spec_from_file_location("premortem", SCRIPT)
premortem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(premortem)


SAMPLE = {
    "plan": "Launch the paid tier on Friday",
    "context": "First monetization attempt",
    "failure_modes": (
        "1. Payment webhook silently drops events "
        "(assumption: Stripe retries cover us; "
        "warning sign: Stripe dashboard and our DB disagree)."
    ),
    "most_likely": (
        "Checkout conversion is far lower than projected because the "
        "pricing page buries the CTA."
    ),
    "most_dangerous": (
        "A billing bug double-charges early customers and torches trust "
        "on day one."
    ),
    "hidden_assumption": "We assume existing free users want to pay at all.",
    "revised_plan": (
        "Soft-launch to 10% of users, reconcile Stripe events nightly, "
        "add a billing kill-switch."
    ),
    "checklist": "- Stripe reconciliation job live\n- Refund path tested",
    "verdict": "revise",  # lowercase on purpose - exercises normalization
    "confidence": 70,
    "tags": ["launch", "billing"],  # list on purpose - exercises normalization
}


@pytest.fixture
def pm(tmp_path, monkeypatch):
    """premortem module with all storage paths redirected into tmp_path."""
    data_dir = tmp_path / "data"
    research_dir = tmp_path / "research"
    monkeypatch.setattr(premortem, "DATA_DIR", data_dir)
    monkeypatch.setattr(premortem, "DB_FILE", data_dir / "research.db")
    monkeypatch.setattr(premortem, "RESEARCH_DIR", research_dir)
    return premortem


def _sample(**overrides):
    d = dict(SAMPLE)
    d.update(overrides)
    return d


# ----- store / view round-trip -----

def test_store_returns_incrementing_ids(pm):
    assert pm.store(_sample()) == 1
    assert pm.store(_sample(plan="Second plan")) == 2


def test_store_and_view_round_trip(pm):
    rid = pm.store(_sample())
    p = pm.view(rid)
    assert p is not None
    assert p["plan"] == "Launch the paid tier on Friday"
    assert p["most_likely"].startswith("Checkout conversion")
    assert p["most_dangerous"].startswith("A billing bug")
    assert p["hidden_assumption"].startswith("We assume")
    assert p["verdict"] == "REVISE"          # uppercased
    assert p["confidence"] == 70
    assert p["tags"] == "launch,billing"     # list -> csv


def test_view_missing_returns_none(pm):
    assert pm.view(999) is None


# ----- validation -----

@pytest.mark.parametrize("field", [
    "plan", "failure_modes", "most_likely", "most_dangerous",
    "revised_plan", "verdict", "confidence",
])
def test_missing_required_field_exits(pm, field):
    data = _sample()
    del data[field]
    with pytest.raises(SystemExit):
        pm.store(data)


@pytest.mark.parametrize("field", [
    "plan", "failure_modes", "most_likely", "most_dangerous", "revised_plan",
])
def test_blank_required_field_exits(pm, field):
    with pytest.raises(SystemExit):
        pm.store(_sample(**{field: "   "}))


def test_invalid_verdict_exits(pm):
    with pytest.raises(SystemExit):
        pm.store(_sample(verdict="MAYBE"))


def test_confidence_out_of_range_exits(pm):
    with pytest.raises(SystemExit):
        pm.store(_sample(confidence=150))


def test_confidence_non_integer_exits(pm):
    with pytest.raises(SystemExit):
        pm.store(_sample(confidence="high"))


def test_confidence_zero_is_valid(pm):
    rid = pm.store(_sample(confidence=0))
    assert pm.view(rid)["confidence"] == 0


def test_tags_csv_string_passthrough(pm):
    rid = pm.store(_sample(tags="alpha, beta ,gamma"))
    # csv string is stored as-is (stripped), not re-parsed
    assert pm.view(rid)["tags"] == "alpha, beta ,gamma"


# ----- search -----

def test_search_finds_match(pm):
    rid = pm.store(_sample())
    results = pm.search("webhook")
    assert any(r["id"] == rid for r in results)


def test_search_ranks_and_returns_snippet(pm):
    pm.store(_sample())
    results = pm.search("Stripe")
    assert results
    assert "verdict" in results[0]
    assert results[0]["verdict"] == "REVISE"


def test_search_no_match_returns_empty(pm):
    pm.store(_sample())
    assert pm.search("quetzalcoatl") == []


def test_search_malformed_query_no_crash(pm):
    pm.store(_sample())
    # A bare double-quote is an invalid FTS5 expression; must not raise.
    result = pm.search('"')
    assert isinstance(result, list)


def test_search_respects_limit(pm):
    for i in range(5):
        pm.store(_sample(plan=f"Plan {i} with webhook risk"))
    assert len(pm.search("webhook", limit=3)) == 3


# ----- list -----

def test_list_orders_newest_first(pm):
    pm.store(_sample(plan="oldest"))
    pm.store(_sample(plan="newest"))
    rows = pm.list_premortems()
    assert rows[0]["plan"] == "newest"


def test_list_tag_filter(pm):
    pm.store(_sample(plan="has-billing", tags=["billing"]))
    pm.store(_sample(plan="no-billing", tags=["ops"]))
    rows = pm.list_premortems(tag="billing")
    plans = {r["plan"] for r in rows}
    assert "has-billing" in plans
    assert "no-billing" not in plans


# ----- delete -----

def test_delete_removes_row(pm):
    rid = pm.store(_sample())
    assert pm.delete(rid) is True
    assert pm.view(rid) is None


def test_delete_missing_returns_false(pm):
    assert pm.delete(999) is False


def test_search_after_delete_is_clean(pm):
    rid = pm.store(_sample())
    pm.delete(rid)
    # FTS delete trigger must have removed the row; no stale hits.
    assert pm.search("webhook") == []


# ----- export -----

def test_export_writes_markdown(pm):
    rid = pm.store(_sample())
    path = pm.export_research(rid)
    assert path is not None
    f = Path(path)
    assert f.exists()
    text = f.read_text()
    assert "# Premortem #" in text
    assert "Most Likely Failure" in text
    assert "Most Dangerous Failure" in text
    # topic comes from first tag
    assert f.parent.name == "launch"


def test_export_explicit_topic(pm):
    rid = pm.store(_sample())
    path = pm.export_research(rid, topic="Risk Review")
    assert Path(path).parent.name == "risk-review"


def test_export_collision_suffixes(pm):
    rid = pm.store(_sample())
    p1 = pm.export_research(rid)
    p2 = pm.export_research(rid)
    assert p1 != p2
    assert Path(p2).stem.endswith("_2")


def test_export_missing_returns_none(pm):
    assert pm.export_research(999) is None


# ----- rebuild -----

def test_rebuild_reports_count(pm):
    pm.store(_sample())
    pm.store(_sample(plan="another"))
    assert pm.rebuild_fts() == 2


def test_search_works_after_rebuild(pm):
    pm.store(_sample())
    pm.rebuild_fts()
    assert pm.search("webhook")


# ----- formatting -----

def test_format_omits_empty_optional_sections(pm):
    rid = pm.store(_sample(context="", hidden_assumption="", checklist=""))
    text = pm.format_premortem(pm.view(rid))
    assert "Hidden Assumption" not in text
    assert "Pre-Launch Checklist" not in text
    assert "Context:" not in text


# ----- CLI smoke test (subprocess, HOME-isolated) -----

def test_cli_store_view_search_roundtrip(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}

    r = subprocess.run(
        [sys.executable, str(SCRIPT), "store"],
        input=json.dumps(SAMPLE), capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "Stored premortem #1" in r.stdout

    r = subprocess.run(
        [sys.executable, str(SCRIPT), "view", "1"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "Premortem #1" in r.stdout
    assert "Most Dangerous Failure" in r.stdout

    r = subprocess.run(
        [sys.executable, str(SCRIPT), "search", "webhook"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "#1" in r.stdout

    # confirm the real shared db was never created under this HOME
    assert (tmp_path / ".local" / "share" / "research" / "research.db").exists()


def test_cli_invalid_json_exits_nonzero(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "store"],
        input="{not json", capture_output=True, text=True, env=env,
    )
    assert r.returncode == 1
    assert "invalid JSON" in r.stderr
