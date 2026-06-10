"""Tests for the lightweight CLI update reminder (piia_engram.update_check).

All tests run against an isolated ENGRAM_DIR (tmp_path) and never touch the
network — the PyPI fetch is monkeypatched in every test. CI/automation env
markers are cleared in the `_enabled_env` fixture so the "enabled path" tests
behave the same on a dev box and inside GitHub Actions.
"""

from __future__ import annotations

import io
import json
import time

import pytest

from piia_engram import update_check as uc


@pytest.fixture
def _isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
    return tmp_path / "engram"


@pytest.fixture
def _enabled_env(monkeypatch):
    """Clear every gate so the reminder is allowed to run."""
    monkeypatch.delenv("ENGRAM_NO_UPDATE_CHECK", raising=False)
    for marker in uc._CI_ENV_MARKERS:
        monkeypatch.delenv(marker, raising=False)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:  # noqa: D401 - simple stub
        return True


# ---------------------------------------------------------------------------
# Version parsing / comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("3.55.0", (3, 55, 0)),
        ("3.56.0rc1", (3, 56, 0)),
        ("3.56.0+local", (3, 56, 0)),
        ("3.56.0.dev3", (3, 56, 0)),
        ("10.0.1", (10, 0, 1)),
        ("", ()),
        ("not-a-version", ()),
    ],
)
def test_parse_version(text, expected):
    assert uc._parse_version(text) == expected


@pytest.mark.parametrize(
    "latest,current,newer",
    [
        ("3.56.0", "3.55.0", True),
        ("3.55.1", "3.55.0", True),
        ("4.0.0", "3.99.99", True),
        ("3.55.0", "3.55.0", False),
        ("3.54.0", "3.55.0", False),
        ("", "3.55.0", False),
        ("3.55.0", "", False),
        ("garbage", "3.55.0", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert uc._is_newer(latest, current) is newer


# ---------------------------------------------------------------------------
# Disable gates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val", ["1", "true", "on", "yes", "TRUE", "Yes"])
def test_disabled_via_env(monkeypatch, val):
    monkeypatch.setenv("ENGRAM_NO_UPDATE_CHECK", val)
    assert uc.is_disabled() is True


def test_disabled_via_ci_marker(monkeypatch, _enabled_env):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert uc.is_disabled() is True


def test_not_disabled_when_clean(_enabled_env):
    assert uc.is_disabled() is False


def test_check_returns_none_when_disabled(monkeypatch, _isolated_root):
    monkeypatch.setenv("ENGRAM_NO_UPDATE_CHECK", "1")
    calls = []
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: calls.append(1) or "9.9.9")
    assert uc.check_for_update("3.55.0") is None
    assert calls == []  # no network when disabled


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

def test_fresh_cache_avoids_network(monkeypatch, _isolated_root, _enabled_env):
    _isolated_root.mkdir(parents=True, exist_ok=True)
    (_isolated_root / ".update_check.json").write_text(
        json.dumps({"last_check": time.time(), "latest": "3.56.0"}),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: calls.append(1) or "9.9.9")
    assert uc.check_for_update("3.55.0") == "3.56.0"
    assert calls == []  # served from fresh cache, no network


def test_stale_cache_triggers_fetch_and_rewrites(monkeypatch, _isolated_root, _enabled_env):
    _isolated_root.mkdir(parents=True, exist_ok=True)
    old = time.time() - (uc._CHECK_INTERVAL_SECONDS + 100)
    (_isolated_root / ".update_check.json").write_text(
        json.dumps({"last_check": old, "latest": "3.55.0"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: "3.57.0")
    assert uc.check_for_update("3.55.0") == "3.57.0"
    written = json.loads((_isolated_root / ".update_check.json").read_text(encoding="utf-8"))
    assert written["latest"] == "3.57.0"
    assert written["last_check"] > old


def test_network_failure_silent_no_cache(monkeypatch, _isolated_root, _enabled_env):
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: None)
    assert uc.check_for_update("3.55.0") is None


def test_network_failure_reuses_prior_cache(monkeypatch, _isolated_root, _enabled_env):
    _isolated_root.mkdir(parents=True, exist_ok=True)
    old = time.time() - (uc._CHECK_INTERVAL_SECONDS + 100)
    (_isolated_root / ".update_check.json").write_text(
        json.dumps({"last_check": old, "latest": "3.58.0"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: None)  # offline
    assert uc.check_for_update("3.55.0") == "3.58.0"


def test_up_to_date_returns_none(monkeypatch, _isolated_root, _enabled_env):
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: "3.55.0")
    assert uc.check_for_update("3.55.0") is None


def test_force_bypasses_disable_and_fresh_cache(monkeypatch, _isolated_root):
    # Disabled AND a fresh cache present — force must still hit the network.
    monkeypatch.setenv("ENGRAM_NO_UPDATE_CHECK", "1")
    _isolated_root.mkdir(parents=True, exist_ok=True)
    (_isolated_root / ".update_check.json").write_text(
        json.dumps({"last_check": time.time(), "latest": "3.55.0"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: "3.60.0")
    assert uc.check_for_update("3.55.0", force=True) == "3.60.0"


# ---------------------------------------------------------------------------
# maybe_print_update_notice
# ---------------------------------------------------------------------------

def test_notice_skipped_on_non_tty(monkeypatch, _isolated_root, _enabled_env):
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: "3.99.0")
    sink = io.StringIO()  # plain StringIO.isatty() -> False
    assert uc.maybe_print_update_notice("3.55.0", stream=sink) is None
    assert sink.getvalue() == ""


def test_notice_printed_on_tty(monkeypatch, _isolated_root, _enabled_env):
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: "3.99.0")
    sink = _FakeTTY()
    assert uc.maybe_print_update_notice("3.55.0", stream=sink) == "3.99.0"
    out = sink.getvalue()
    assert "3.99.0" in out
    assert "pip install -U piia-engram" in out


def test_notice_silent_when_up_to_date(monkeypatch, _isolated_root, _enabled_env):
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: "3.55.0")
    sink = _FakeTTY()
    assert uc.maybe_print_update_notice("3.55.0", stream=sink) is None
    assert sink.getvalue() == ""


def test_notice_silent_when_disabled(monkeypatch, _isolated_root):
    monkeypatch.setenv("ENGRAM_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", lambda: "3.99.0")
    sink = _FakeTTY()
    assert uc.maybe_print_update_notice("3.55.0", stream=sink) is None
    assert sink.getvalue() == ""


def test_maybe_print_never_raises(monkeypatch, _isolated_root, _enabled_env):
    def _boom():
        raise RuntimeError("network exploded")

    monkeypatch.setattr(uc, "_fetch_latest_from_pypi", _boom)
    sink = _FakeTTY()
    # Must swallow the error and print nothing.
    assert uc.maybe_print_update_notice("3.55.0", stream=sink) is None
