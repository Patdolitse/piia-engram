"""P0: the local audit.log is ON by default (opt-out), not opt-in.

Owner-approved direction (Q1): every identity/knowledge write should leave a
local, tamper-evident trail by default. This trail is a *local file only* — it
is unrelated to network telemetry, which stays opt-in / off by default.

The default flip is keyed so the existing suite is unaffected: under
``ENGRAM_TEST=1`` audit stays OFF unless a test opts in (``ENGRAM_AUDIT=1``).
These tests therefore clear ``ENGRAM_TEST`` to observe the real production
default, and one test pins the in-suite carve-out itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram.core import Engram


def _production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a real (non-test) process.

    The autouse conftest fixture sets ``ENGRAM_TEST=1``; remove it (and any
    inherited ``ENGRAM_AUDIT``) so we see the production default.
    """
    monkeypatch.delenv("ENGRAM_TEST", raising=False)
    monkeypatch.delenv("ENGRAM_AUDIT", raising=False)


def test_audit_on_by_default_in_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    eng = Engram(root=tmp_path)
    assert eng._audit.enabled is True
    assert eng._audit.log_path == tmp_path / "audit.log"


def test_audit_writes_without_any_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    eng = Engram(root=tmp_path)
    eng.add_lesson({"summary": "a benign default-audited note", "domain": "misc"})
    # The default-on logger should have recorded the gate decision unprompted.
    assert (tmp_path / "audit.log").exists()


def test_audit_opt_out_disables_and_clears_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", "0")
    eng = Engram(root=tmp_path)
    assert eng._audit.enabled is False
    assert eng._audit.log_path is None


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "FALSE", " Off "])
def test_audit_opt_out_spellings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, falsy: str) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", falsy)
    eng = Engram(root=tmp_path)
    assert eng._audit.enabled is False


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "TRUE", " On "])
def test_audit_explicit_opt_in_spellings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", truthy)
    eng = Engram(root=tmp_path)
    assert eng._audit.enabled is True


def test_audit_default_off_under_test_env_for_suite_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin the carve-out: with ENGRAM_TEST=1 and no explicit ENGRAM_AUDIT, audit
    # defaults OFF so the existing suite doesn't get a surprise audit.log. A
    # test that wants the trail opts in explicitly.
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.delenv("ENGRAM_AUDIT", raising=False)
    eng = Engram(root=tmp_path)
    assert eng._audit.enabled is False
    assert eng._audit.log_path is None
