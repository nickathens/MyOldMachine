"""Tests for the local Telegram Bot API server installer.

All tests are offline and avoid running the actual builder. They cover:
- credential validation
- canonical paths
- systemd / launchd template substitution
- env-write and env-resume round-trip
- wizard step config gating
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from install.telegram_bot_api import (  # noqa: E402
    DEFAULT_PORT,
    LINUX_BUILD_DEPS,
    MACOS_BUILD_DEPS,
    _ensure_state_dir_owned,
    paths_for,
    render_launchd_plist,
    render_systemd_unit,
    setup_telegram_bot_api,
    validate_credentials,
)


# ---- validate_credentials ----

def test_validate_ok():
    valid, _ = validate_credentials("12345", "0123456789abcdef0123456789abcdef")
    assert valid


def test_validate_uppercase_hex_ok():
    valid, _ = validate_credentials("9", "ABCDEF0123456789ABCDEF0123456789")
    assert valid


def test_validate_mixed_case_hex_ok():
    valid, _ = validate_credentials("1", "AbCdEf0123456789aBcDeF0123456789")
    assert valid


def test_validate_id_with_whitespace_ok():
    # The wizard already trims, but the validator should also be tolerant
    # of trailing newline from copy-paste.
    valid, _ = validate_credentials(
        "  12345  ", "  0123456789abcdef0123456789abcdef  "
    )
    assert valid


def test_validate_id_non_numeric():
    valid, reason = validate_credentials("abc", "0123456789abcdef0123456789abcdef")
    assert not valid
    assert "api_id" in reason


def test_validate_id_negative_rejected():
    valid, reason = validate_credentials("-1", "0123456789abcdef0123456789abcdef")
    assert not valid
    assert "api_id" in reason


def test_validate_empty_id():
    valid, reason = validate_credentials("", "0123456789abcdef0123456789abcdef")
    assert not valid
    assert "api_id" in reason


def test_validate_empty_hash():
    valid, reason = validate_credentials("12345", "")
    assert not valid
    assert "api_hash" in reason


def test_validate_hash_wrong_length():
    valid, reason = validate_credentials("12345", "abc")
    assert not valid
    assert "32" in reason


def test_validate_hash_too_long():
    valid, reason = validate_credentials("12345", "0" * 33)
    assert not valid
    assert "32" in reason


def test_validate_hash_non_hex():
    valid, reason = validate_credentials("12345", "g" * 32)
    assert not valid
    assert "hex" in reason.lower()


# ---- paths_for ----

def test_paths_for_layout(tmp_path):
    p = paths_for(tmp_path)
    assert p["work_dir"] == tmp_path / "data" / "telegram-bot-api"
    assert p["repo_path"] == tmp_path / "data" / "telegram-bot-api" / "repo"
    assert p["build_dir"] == p["repo_path"] / "build"
    assert p["binary_path"] == p["build_dir"] / "telegram-bot-api"
    assert p["state_dir"] == tmp_path / "data" / "telegram-bot-api" / "state"
    assert p["log_path"] == p["state_dir"] / "api.log"


def test_paths_for_pure_function(tmp_path):
    """paths_for should not create any directories."""
    p = paths_for(tmp_path)
    assert not p["work_dir"].exists()
    assert not p["state_dir"].exists()


# ---- render_systemd_unit ----

SYSTEMD_TEMPLATE = (REPO_DIR / "install" / "templates"
                    / "telegram-bot-api.service").read_text(encoding="utf-8")


def test_systemd_template_substitution():
    rendered = render_systemd_unit(
        SYSTEMD_TEMPLATE,
        user="ntouri",
        env_file=Path("/home/ntouri/repo/.env"),
        binary_path=Path("/home/ntouri/repo/data/telegram-bot-api/repo/build/telegram-bot-api"),
        state_dir=Path("/home/ntouri/repo/data/telegram-bot-api/state"),
    )
    assert "User=ntouri" in rendered
    assert "EnvironmentFile=/home/ntouri/repo/.env" in rendered
    assert "/build/telegram-bot-api --local --http-ip-address=127.0.0.1" in rendered
    assert "--http-port=8081" in rendered
    assert "--dir=/home/ntouri/repo/data/telegram-bot-api/state" in rendered
    assert "{{" not in rendered  # no leftover placeholders
    assert "}}" not in rendered


def test_systemd_template_custom_port():
    rendered = render_systemd_unit(
        SYSTEMD_TEMPLATE,
        user="alice",
        env_file=Path("/etc/foo/.env"),
        binary_path=Path("/opt/x/telegram-bot-api"),
        state_dir=Path("/var/lib/x"),
        port=9999,
    )
    assert "--http-port=9999" in rendered


# ---- render_launchd_plist ----

LAUNCHD_TEMPLATE = (REPO_DIR / "install" / "templates"
                    / "com.telegram-bot-api.plist").read_text(encoding="utf-8")


def test_launchd_template_substitution():
    rendered = render_launchd_plist(
        LAUNCHD_TEMPLATE,
        user="orchestrator",
        env_file=Path("/Users/x/repo/.env"),
        binary_path=Path("/Users/x/repo/data/telegram-bot-api/repo/build/telegram-bot-api"),
        state_dir=Path("/Users/x/repo/data/telegram-bot-api/state"),
    )
    assert "<string>orchestrator</string>" in rendered
    assert "source /Users/x/repo/.env" in rendered
    assert "exec /Users/x/repo/data/telegram-bot-api/repo/build/telegram-bot-api" in rendered
    assert "--http-port=8081" in rendered
    assert "{{" not in rendered
    assert "}}" not in rendered


def test_launchd_template_label_present():
    """The launchd Label must remain stable so reloads can find it."""
    rendered = render_launchd_plist(
        LAUNCHD_TEMPLATE,
        user="x", env_file=Path("/.env"),
        binary_path=Path("/x"), state_dir=Path("/y"),
    )
    assert "<string>com.telegram-bot-api</string>" in rendered


# ---- setup_telegram_bot_api gating ----

def test_setup_skips_when_disabled(tmp_path):
    config = {"telegram_local_api_enabled": False}
    os_info = mock.Mock(os_type="linux")
    assert setup_telegram_bot_api(tmp_path, config, os_info, password=None) is True


def test_setup_skips_when_key_missing(tmp_path):
    """No key in config behaves like opt-out, not failure."""
    config = {}  # nothing set
    os_info = mock.Mock(os_type="linux")
    assert setup_telegram_bot_api(tmp_path, config, os_info, password=None) is True


def test_setup_rejects_invalid_creds(tmp_path):
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "abc",
        "telegram_api_hash": "0" * 32,
    }
    os_info = mock.Mock(os_type="linux")
    assert setup_telegram_bot_api(tmp_path, config, os_info, password=None) is False


def test_setup_rejects_unsupported_os(tmp_path):
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "12345",
        "telegram_api_hash": "0" * 32,
    }
    os_info = mock.Mock(os_type="windows")
    # paths_for binary doesn't exist, so it falls through to the OS check.
    assert setup_telegram_bot_api(tmp_path, config, os_info, password=None) is False


def test_setup_reuses_existing_binary(tmp_path, monkeypatch):
    """If the binary already exists, dep install + clone + build are skipped."""
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "12345",
        "telegram_api_hash": "0" * 32,
    }
    os_info = mock.Mock(os_type="linux")
    # Pre-create the binary path so the build short-circuits.
    p = paths_for(tmp_path)
    p["binary_path"].parent.mkdir(parents=True, exist_ok=True)
    p["binary_path"].write_text("#!/bin/sh\nexit 0\n")
    p["binary_path"].chmod(0o755)

    called = {"deps": False, "clone": False, "build": False, "service": False}

    def fake_deps(*a, **kw):
        called["deps"] = True
        return True

    def fake_clone(*a, **kw):
        called["clone"] = True
        return True

    def fake_build(*a, **kw):
        called["build"] = True
        return True

    def fake_service(*a, **kw):
        called["service"] = True
        return True

    monkeypatch.setattr(
        "install.telegram_bot_api._install_linux_build_deps", fake_deps
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._clone_repo", fake_clone
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._cmake_build", fake_build
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._register_systemd_service", fake_service
    )

    result = setup_telegram_bot_api(tmp_path, config, os_info, password=None)
    assert result is True
    assert called["deps"] is False, "should not reinstall deps when binary present"
    assert called["clone"] is False, "should not re-clone when binary present"
    assert called["build"] is False, "should not rebuild when binary present"
    assert called["service"] is True, "service must always be (re)registered"


def test_setup_full_path_runs_in_order(tmp_path, monkeypatch):
    """Cold-path: deps install → clone → build → service register."""
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "f" * 32,
    }
    os_info = mock.Mock(os_type="linux")
    # Don't pre-create the binary — force the cold path.

    order = []

    def fake_deps(*a, **kw):
        order.append("deps")
        return True

    def fake_present(*a, **kw):
        return (True, [])

    def fake_clone(repo_path, *a, **kw):
        order.append("clone")
        # Simulate clone result: dir + binary parent
        repo_path.mkdir(parents=True, exist_ok=True)
        return True

    def fake_build(repo_path, build_dir, binary_path, *a, **kw):
        order.append("build")
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_text("#!/bin/sh\n")
        return True

    def fake_service(*a, **kw):
        order.append("service")
        return True

    monkeypatch.setattr(
        "install.telegram_bot_api._install_linux_build_deps", fake_deps
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._build_deps_present", fake_present
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._clone_repo", fake_clone
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._cmake_build", fake_build
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._register_systemd_service", fake_service
    )

    result = setup_telegram_bot_api(tmp_path, config, os_info, password="pw")
    assert result is True
    assert order == ["deps", "clone", "build", "service"]


def test_setup_aborts_when_deps_missing(tmp_path, monkeypatch):
    """If toolchain probe says missing after dep install, abort cleanly."""
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "0" * 32,
    }
    os_info = mock.Mock(os_type="linux")

    monkeypatch.setattr(
        "install.telegram_bot_api._install_linux_build_deps",
        lambda *a, **kw: True,
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._build_deps_present",
        lambda *a, **kw: (False, ["cmake", "g++"]),
    )

    clone_called = {"v": False}

    def fake_clone(*a, **kw):
        clone_called["v"] = True
        return True

    monkeypatch.setattr(
        "install.telegram_bot_api._clone_repo", fake_clone
    )

    result = setup_telegram_bot_api(tmp_path, config, os_info, password=None)
    assert result is False
    assert clone_called["v"] is False, "must not clone if deps missing"


def test_setup_aborts_on_clone_failure(tmp_path, monkeypatch):
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "0" * 32,
    }
    os_info = mock.Mock(os_type="linux")

    monkeypatch.setattr(
        "install.telegram_bot_api._install_linux_build_deps",
        lambda *a, **kw: True,
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._build_deps_present",
        lambda *a, **kw: (True, []),
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._clone_repo", lambda *a, **kw: False
    )

    build_called = {"v": False}

    def fake_build(*a, **kw):
        build_called["v"] = True
        return True

    monkeypatch.setattr(
        "install.telegram_bot_api._cmake_build", fake_build
    )

    result = setup_telegram_bot_api(tmp_path, config, os_info, password=None)
    assert result is False
    assert build_called["v"] is False


def test_setup_aborts_on_build_failure(tmp_path, monkeypatch):
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "0" * 32,
    }
    os_info = mock.Mock(os_type="linux")
    monkeypatch.setattr(
        "install.telegram_bot_api._install_linux_build_deps",
        lambda *a, **kw: True,
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._build_deps_present",
        lambda *a, **kw: (True, []),
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._clone_repo", lambda *a, **kw: True
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._cmake_build", lambda *a, **kw: False
    )
    service_called = {"v": False}
    monkeypatch.setattr(
        "install.telegram_bot_api._register_systemd_service",
        lambda *a, **kw: service_called.update(v=True) or True,
    )

    result = setup_telegram_bot_api(tmp_path, config, os_info, password=None)
    assert result is False
    assert service_called["v"] is False


def test_setup_macos_routes_to_launchd(tmp_path, monkeypatch):
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "0" * 32,
    }
    os_info = mock.Mock(os_type="macos", brew_path="/opt/homebrew/bin/brew")

    monkeypatch.setattr(
        "install.telegram_bot_api._install_macos_build_deps",
        lambda *a, **kw: True,
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._build_deps_present",
        lambda *a, **kw: (True, []),
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._clone_repo", lambda *a, **kw: True
    )

    def fake_build(repo_path, build_dir, binary_path, *a, **kw):
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_text("#!/bin/sh")
        return True

    monkeypatch.setattr(
        "install.telegram_bot_api._cmake_build", fake_build
    )

    flags = {"systemd": False, "launchd": False}
    monkeypatch.setattr(
        "install.telegram_bot_api._register_systemd_service",
        lambda *a, **kw: flags.update(systemd=True) or True,
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._register_launchd_service",
        lambda *a, **kw: flags.update(launchd=True) or True,
    )

    result = setup_telegram_bot_api(tmp_path, config, os_info, password=None)
    assert result is True
    assert flags["launchd"] is True
    assert flags["systemd"] is False


def test_setup_uses_orchestrator_user_in_multiuser_mode(tmp_path, monkeypatch):
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "0" * 32,
        "multiuser_enabled": True,
        "multiuser_orchestrator_user": "mom_orchestrator",
    }
    os_info = mock.Mock(os_type="linux")

    p = paths_for(tmp_path)
    p["binary_path"].parent.mkdir(parents=True, exist_ok=True)
    p["binary_path"].write_text("#!/bin/sh")

    # Stub multiuser helpers so the chown path doesn't shell out to sudo.
    fake_mu = type(sys)("install.multiuser")
    fake_mu.set_owner = lambda *a, **kw: True
    fake_mu.set_perms = lambda *a, **kw: True
    monkeypatch.setitem(sys.modules, "install.multiuser", fake_mu)

    captured = {}

    def fake_service(repo_dir, *, binary_path, state_dir, env_file, user,
                     password):
        captured["user"] = user
        return True

    monkeypatch.setattr(
        "install.telegram_bot_api._register_systemd_service", fake_service
    )

    setup_telegram_bot_api(tmp_path, config, os_info, password=None)
    assert captured["user"] == "mom_orchestrator"


def test_setup_uses_current_user_in_single_user_mode(tmp_path, monkeypatch):
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "0" * 32,
    }
    os_info = mock.Mock(os_type="linux")

    p = paths_for(tmp_path)
    p["binary_path"].parent.mkdir(parents=True, exist_ok=True)
    p["binary_path"].write_text("#!/bin/sh")

    captured = {}

    def fake_service(repo_dir, *, binary_path, state_dir, env_file, user,
                     password):
        captured["user"] = user
        return True

    monkeypatch.setattr(
        "install.telegram_bot_api._register_systemd_service", fake_service
    )
    monkeypatch.setenv("USER", "alice")

    setup_telegram_bot_api(tmp_path, config, os_info, password=None)
    assert captured["user"] == "alice"


# ---- env round-trip ----

def test_env_resume_roundtrip(tmp_path, monkeypatch):
    """write_env then _load_config_from_env must preserve TBA fields."""
    from install import wizard

    repo_dir = tmp_path
    config = {
        "telegram_token": "tok",
        "llm_provider": "openrouter",
        "llm_model": "gpt-4",
        "llm_api_key": "key",
        "telegram_user_id": "12345",
        "bot_name": "Foo",
        "timezone": "UTC",
        "takeover": "minimal",
        "multiuser_enabled": False,
        "multiuser_num_slots": 1,
        "multiuser_queue_enabled": False,
        "telegram_local_api_enabled": True,
        "telegram_api_id": "999",
        "telegram_api_hash": "abcdef0123456789abcdef0123456789",
    }
    wizard.write_env(repo_dir, config)

    env_text = (repo_dir / ".env").read_text(encoding="utf-8")
    assert "TELEGRAM_API_BASE=http://localhost:8081" in env_text
    assert "TELEGRAM_API_ID=999" in env_text
    assert "TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789" in env_text

    loaded = wizard._load_config_from_env(repo_dir)
    assert loaded["telegram_local_api_enabled"] is True
    assert loaded["telegram_api_id"] == "999"
    assert loaded["telegram_api_hash"] == "abcdef0123456789abcdef0123456789"


def test_env_writes_no_tba_keys_when_disabled(tmp_path):
    from install import wizard

    repo_dir = tmp_path
    config = {
        "telegram_token": "tok",
        "llm_provider": "openrouter",
        "llm_model": "gpt-4",
        "llm_api_key": "key",
        "telegram_user_id": "12345",
        "bot_name": "Foo",
        "timezone": "UTC",
        "takeover": "minimal",
        "multiuser_enabled": False,
        "multiuser_num_slots": 1,
        "multiuser_queue_enabled": False,
        "telegram_local_api_enabled": False,
    }
    wizard.write_env(repo_dir, config)

    env_text = (repo_dir / ".env").read_text(encoding="utf-8")
    assert "TELEGRAM_API_BASE" not in env_text
    assert "TELEGRAM_API_ID" not in env_text
    assert "TELEGRAM_API_HASH" not in env_text


def test_env_resume_default_disabled_when_keys_absent(tmp_path):
    from install import wizard
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=t\nLLM_PROVIDER=openrouter\nLLM_MODEL=m\n",
        encoding="utf-8",
    )
    loaded = wizard._load_config_from_env(tmp_path)
    assert loaded["telegram_local_api_enabled"] is False


# ---- constants sanity ----

# ---- _ensure_state_dir_owned ----
#
# The state_dir is what launchd redirects StandardOutPath/StandardErrorPath
# into and what the daemon writes api.log to. If it is not owned by the
# runtime user (the install user in single-user mode, mom_orchestrator in
# multi-user mode), launchd fails with EX_CONFIG (78) before exec and
# systemd fails on the first api.log write. These tests pin the contract.


def test_ensure_state_dir_creates_dir_in_single_user(tmp_path):
    state = tmp_path / "data" / "telegram-bot-api" / "state"
    assert not state.exists()
    ok = _ensure_state_dir_owned(
        state, "alice", multiuser_enabled=False, password=None
    )
    assert ok is True
    assert state.is_dir()
    # On platforms that honor chmod (POSIX), confirm 0o700.
    import os
    if os.name == "posix":
        mode = state.stat().st_mode & 0o777
        assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


def test_ensure_state_dir_idempotent_in_single_user(tmp_path):
    state = tmp_path / "data" / "telegram-bot-api" / "state"
    state.mkdir(parents=True, exist_ok=True)
    # Run twice — second run must still succeed.
    assert _ensure_state_dir_owned(
        state, "alice", multiuser_enabled=False, password=None
    ) is True
    assert _ensure_state_dir_owned(
        state, "alice", multiuser_enabled=False, password=None
    ) is True


def test_ensure_state_dir_no_chown_in_single_user(tmp_path, monkeypatch):
    """Single-user mode must NOT call sudo chown — install user already owns."""
    state = tmp_path / "data" / "telegram-bot-api" / "state"
    sentinel = {"chown_called": False, "chmod_called": False}

    def boom_set_owner(*a, **kw):
        sentinel["chown_called"] = True
        return True

    def boom_set_perms(*a, **kw):
        sentinel["chmod_called"] = True
        return True

    # Even if the multiuser module IS importable, we should not be calling
    # its helpers from the single-user path.
    fake_mu = type(sys)("install.multiuser")
    fake_mu.set_owner = boom_set_owner
    fake_mu.set_perms = boom_set_perms
    monkeypatch.setitem(sys.modules, "install.multiuser", fake_mu)

    ok = _ensure_state_dir_owned(
        state, "alice", multiuser_enabled=False, password=None
    )
    assert ok is True
    assert sentinel["chown_called"] is False
    assert sentinel["chmod_called"] is False


def test_ensure_state_dir_chowns_in_multiuser(tmp_path, monkeypatch):
    state = tmp_path / "data" / "telegram-bot-api" / "state"
    captured = {}

    def fake_set_owner(path, user, *, password=None, recursive=False):
        captured["set_owner"] = {
            "path": Path(path),
            "user": user,
            "password": password,
            "recursive": recursive,
        }
        return True

    def fake_set_perms(path, mode, *, password=None, recursive=False):
        captured["set_perms"] = {
            "path": Path(path),
            "mode": mode,
            "recursive": recursive,
        }
        return True

    fake_mu = type(sys)("install.multiuser")
    fake_mu.set_owner = fake_set_owner
    fake_mu.set_perms = fake_set_perms
    monkeypatch.setitem(sys.modules, "install.multiuser", fake_mu)

    ok = _ensure_state_dir_owned(
        state, "mom_orchestrator", multiuser_enabled=True, password="pw"
    )
    assert ok is True
    assert state.is_dir()
    assert captured["set_owner"]["path"] == state
    assert captured["set_owner"]["user"] == "mom_orchestrator"
    assert captured["set_owner"]["password"] == "pw"
    assert captured["set_owner"]["recursive"] is True
    assert captured["set_perms"]["path"] == state
    assert captured["set_perms"]["mode"] == 0o700


def test_ensure_state_dir_chown_failure_returns_false(tmp_path, monkeypatch):
    state = tmp_path / "data" / "telegram-bot-api" / "state"

    fake_mu = type(sys)("install.multiuser")
    fake_mu.set_owner = lambda *a, **kw: False
    fake_mu.set_perms = lambda *a, **kw: True
    monkeypatch.setitem(sys.modules, "install.multiuser", fake_mu)

    ok = _ensure_state_dir_owned(
        state, "mom_orchestrator", multiuser_enabled=True, password="pw"
    )
    assert ok is False


def test_ensure_state_dir_chmod_failure_returns_false(tmp_path, monkeypatch):
    state = tmp_path / "data" / "telegram-bot-api" / "state"

    fake_mu = type(sys)("install.multiuser")
    fake_mu.set_owner = lambda *a, **kw: True
    fake_mu.set_perms = lambda *a, **kw: False
    monkeypatch.setitem(sys.modules, "install.multiuser", fake_mu)

    ok = _ensure_state_dir_owned(
        state, "mom_orchestrator", multiuser_enabled=True, password="pw"
    )
    assert ok is False


def test_setup_calls_ensure_state_dir_in_multiuser(tmp_path, monkeypatch):
    """End-to-end: setup_telegram_bot_api must invoke ensure_state_dir
    with multiuser_enabled=True before registering the service."""
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "0" * 32,
        "multiuser_enabled": True,
        "multiuser_orchestrator_user": "mom_orchestrator",
    }
    os_info = mock.Mock(os_type="linux")
    p = paths_for(tmp_path)
    p["binary_path"].parent.mkdir(parents=True, exist_ok=True)
    p["binary_path"].write_text("#!/bin/sh")

    captured = {}

    def fake_ensure(state_dir, user, multiuser_enabled, password):
        captured["state_dir"] = Path(state_dir)
        captured["user"] = user
        captured["multiuser_enabled"] = multiuser_enabled
        captured["password"] = password
        return True

    monkeypatch.setattr(
        "install.telegram_bot_api._ensure_state_dir_owned", fake_ensure
    )
    monkeypatch.setattr(
        "install.telegram_bot_api._register_systemd_service",
        lambda *a, **kw: True,
    )

    setup_telegram_bot_api(tmp_path, config, os_info, password="pw")
    assert captured["state_dir"] == p["state_dir"]
    assert captured["user"] == "mom_orchestrator"
    assert captured["multiuser_enabled"] is True
    assert captured["password"] == "pw"


def test_setup_aborts_if_state_dir_chown_fails(tmp_path, monkeypatch):
    config = {
        "telegram_local_api_enabled": True,
        "telegram_api_id": "1",
        "telegram_api_hash": "0" * 32,
        "multiuser_enabled": True,
        "multiuser_orchestrator_user": "mom_orchestrator",
    }
    os_info = mock.Mock(os_type="linux")
    p = paths_for(tmp_path)
    p["binary_path"].parent.mkdir(parents=True, exist_ok=True)
    p["binary_path"].write_text("#!/bin/sh")

    monkeypatch.setattr(
        "install.telegram_bot_api._ensure_state_dir_owned",
        lambda *a, **kw: False,
    )
    service_called = {"v": False}
    monkeypatch.setattr(
        "install.telegram_bot_api._register_systemd_service",
        lambda *a, **kw: service_called.update(v=True) or True,
    )

    result = setup_telegram_bot_api(tmp_path, config, os_info, password="pw")
    assert result is False
    assert service_called["v"] is False, (
        "service registration must NOT proceed if state_dir ownership failed"
    )


def test_default_port_is_8081():
    """Bot's get_telegram_api_base() reads TELEGRAM_API_BASE which defaults
    to http://localhost:8081, so the server must listen there."""
    assert DEFAULT_PORT == 8081


def test_linux_build_deps_cover_all_pkg_managers():
    """Each entry in LINUX_INSTALL_TEMPLATES has matching deps."""
    from install.telegram_bot_api import LINUX_INSTALL_TEMPLATES
    assert set(LINUX_BUILD_DEPS.keys()) == set(LINUX_INSTALL_TEMPLATES.keys())


def test_macos_deps_include_openssl_and_cmake():
    assert "cmake" in MACOS_BUILD_DEPS
    # openssl@3 is the modern Homebrew name; older was just "openssl"
    assert any("openssl" in d for d in MACOS_BUILD_DEPS)
    assert "gperf" in MACOS_BUILD_DEPS
