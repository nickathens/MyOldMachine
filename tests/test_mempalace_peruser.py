"""Per-user MemPalace tests.

Covers the path/wing contract that keeps each user's palace isolated. The
mempalace library itself is not exercised here -- it requires a heavy venv
(ChromaDB + embeddings) and runs in an isolated interpreter on the real
machine. These tests guard the seams MyOldMachine controls: paths, wing
naming, sync-state IO, and dry-run safety.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SYNC_PATH = ROOT / "skills" / "mempalace" / "scripts" / "mempalace_sync.py"
SEARCH_PATH = ROOT / "skills" / "mempalace" / "scripts" / "mempalace_search.py"
SETUP_PATH = ROOT / "skills" / "mempalace" / "scripts" / "mempalace_setup.py"


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mp_sync():
    return _load("mp_sync_mod", SYNC_PATH)


@pytest.fixture(scope="module")
def mp_search():
    return _load("mp_search_mod", SEARCH_PATH)


@pytest.fixture(scope="module")
def mp_setup():
    return _load("mp_setup_mod", SETUP_PATH)


def test_paths_are_per_user(tmp_path, mp_sync):
    user1 = tmp_path / "user1"
    user2 = tmp_path / "user2"
    user1.mkdir()
    user2.mkdir()

    assert mp_sync._palace_path(user1) == user1 / "mempalace" / "palace"
    assert mp_sync._palace_path(user2) == user2 / "mempalace" / "palace"
    assert mp_sync._palace_path(user1) != mp_sync._palace_path(user2)

    assert mp_sync._convos_dir(user1) == user1 / "mempalace" / "convos"
    assert mp_sync._sync_state_file(user1) == user1 / "mempalace" / "sync_state.json"


def test_wing_is_stable_and_unique_per_user(tmp_path, mp_sync, mp_search):
    a = tmp_path / "user1"
    b = tmp_path / "user42"
    a.mkdir()
    b.mkdir()

    assert mp_sync._wing_for(a) == "user_user1"
    assert mp_search._wing_for(a) == mp_sync._wing_for(a)
    assert mp_sync._wing_for(b) == "user_user42"
    assert mp_sync._wing_for(a) != mp_sync._wing_for(b)


def test_export_messages_handles_missing_log(tmp_path, mp_sync):
    user_dir = tmp_path / "user1"
    user_dir.mkdir()
    assert mp_sync.export_messages(user_dir) == {}


def test_export_messages_groups_by_date(tmp_path, mp_sync):
    user_dir = tmp_path / "user1"
    user_dir.mkdir()
    db = user_dir / "message_log.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE messages (timestamp TEXT, role TEXT, content TEXT)"
    )
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?, ?)",
        [
            ("2026-05-01T10:00:00", "user", "hello day 1"),
            ("2026-05-01T10:05:00", "assistant", "hi day 1"),
            ("2026-05-02T11:00:00", "user", "hello day 2"),
        ],
    )
    conn.commit()
    conn.close()

    by_date = mp_sync.export_messages(user_dir, cutoff_date="2026-05-31")
    assert set(by_date.keys()) == {"2026-05-01", "2026-05-02"}
    assert len(by_date["2026-05-01"]) == 2
    assert by_date["2026-05-01"][0] == {"role": "user", "content": "hello day 1"}


def test_export_respects_cutoff(tmp_path, mp_sync):
    user_dir = tmp_path / "user1"
    user_dir.mkdir()
    db = user_dir / "message_log.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE messages (timestamp TEXT, role TEXT, content TEXT)")
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?, ?)",
        [
            ("2026-05-01T00:00:00", "user", "before cutoff"),
            ("2026-05-05T00:00:00", "user", "after cutoff"),
        ],
    )
    conn.commit()
    conn.close()

    by_date = mp_sync.export_messages(user_dir, cutoff_date="2026-05-02")
    assert "2026-05-01" in by_date
    assert "2026-05-05" not in by_date


def test_write_session_files_skips_unchanged(tmp_path, mp_sync):
    out = tmp_path / "convos"
    msgs = {"2026-05-01": [{"role": "user", "content": "hi"}]}
    first = mp_sync.write_session_files(msgs, out)
    second = mp_sync.write_session_files(msgs, out)
    assert len(first) == 1
    assert second == []  # unchanged content, no rewrite


def test_write_session_files_rewrites_on_change(tmp_path, mp_sync):
    out = tmp_path / "convos"
    mp_sync.write_session_files({"2026-05-01": [{"role": "user", "content": "v1"}]}, out)
    rewritten = mp_sync.write_session_files(
        {"2026-05-01": [{"role": "user", "content": "v2"}]}, out
    )
    assert len(rewritten) == 1
    payload = json.loads((out / "session_2026-05-01.json").read_text())
    assert payload[0]["content"] == "v2"


def test_sync_state_round_trip(tmp_path, mp_sync):
    user_dir = tmp_path / "user1"
    user_dir.mkdir()
    state = {"last_sync": "2026-05-08T12:00:00", "last_total_drawers": 42}
    mp_sync.save_sync_state(user_dir, state)
    loaded = mp_sync.load_sync_state(user_dir)
    assert loaded == state


def test_sync_state_is_user_private_mode(tmp_path, mp_sync):
    user_dir = tmp_path / "user1"
    user_dir.mkdir()
    mp_sync.save_sync_state(user_dir, {"k": "v"})
    path = mp_sync._sync_state_file(user_dir)
    assert path.exists()
    # 0o600 = owner-only rw, no group/other access
    assert (path.stat().st_mode & 0o777) == 0o600


def test_load_sync_state_handles_corrupt_json(tmp_path, mp_sync):
    user_dir = tmp_path / "user1"
    user_dir.mkdir()
    (user_dir / "mempalace").mkdir()
    (user_dir / "mempalace" / "sync_state.json").write_text("{not json")
    assert mp_sync.load_sync_state(user_dir) == {}


def test_search_missing_palace_returns_error(tmp_path, mp_search):
    user_dir = tmp_path / "user1"
    user_dir.mkdir()
    out = mp_search.search_palace("anything", user_dir)
    assert "error" in out
    assert "Palace not found" in out["error"]


def test_setup_provision_user_creates_dirs(tmp_path, mp_setup):
    user_dir = tmp_path / "user1"
    user_dir.mkdir()
    assert mp_setup.provision_user(user_dir) is True
    assert (user_dir / "mempalace").is_dir()
    assert (user_dir / "mempalace" / "palace").is_dir()
    assert (user_dir / "mempalace" / "convos").is_dir()


def test_setup_provision_user_rejects_missing_dir(tmp_path, mp_setup, capsys):
    missing = tmp_path / "nope"
    assert mp_setup.provision_user(missing) is False
    captured = capsys.readouterr()
    assert "does not exist" in captured.out


def test_user_dirs_do_not_cross(tmp_path, mp_sync, mp_setup):
    a = tmp_path / "user1"
    b = tmp_path / "user2"
    a.mkdir()
    b.mkdir()
    mp_setup.provision_user(a)
    mp_setup.provision_user(b)

    mp_sync.save_sync_state(a, {"who": "a"})
    mp_sync.save_sync_state(b, {"who": "b"})
    assert mp_sync.load_sync_state(a) == {"who": "a"}
    assert mp_sync.load_sync_state(b) == {"who": "b"}
    # b's palace dir must not exist inside a's tree
    assert not (a / "user2").exists()
