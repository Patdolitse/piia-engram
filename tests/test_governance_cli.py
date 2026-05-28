"""Tests for governance CLI functions (setup_wizard run_grants/trust/revoke/
audit/verify-ledger). They take an explicit root, so they're testable."""

from __future__ import annotations

from piia_engram import setup_wizard as sw
from piia_engram.governance import GovernanceLedger, default_ledger_path
from piia_engram.governance_store import GrantStore


def test_trust_sets_grant(tmp_path, capsys):
    assert sw.run_trust(tmp_path, "codex", "trusted-local") == 0
    assert GrantStore(tmp_path).trust_level_for("codex") == "trusted-local"
    assert "trusted-local" in capsys.readouterr().out


def test_trust_rejects_bad_level(tmp_path, capsys):
    assert sw.run_trust(tmp_path, "x", "super-admin") == 2
    assert "error" in capsys.readouterr().out.lower()


def test_revoke(tmp_path, capsys):
    assert sw.run_revoke(tmp_path, "codex") == 0
    assert GrantStore(tmp_path).is_revoked("codex") is True
    out = capsys.readouterr().out
    assert "cannot recall" in out.lower()  # honest revocation semantics surfaced


def test_grants_lists(tmp_path, capsys):
    sw.run_trust(tmp_path, "a", "trusted-local")
    sw.run_revoke(tmp_path, "b")
    capsys.readouterr()  # clear
    assert sw.run_grants(tmp_path) == 0
    out = capsys.readouterr().out
    assert "a: trusted-local" in out and "b" in out


def test_grants_empty(tmp_path, capsys):
    assert sw.run_grants(tmp_path) == 0
    out = capsys.readouterr().out
    assert "auto-classified" in out


def test_audit_empty(tmp_path, capsys):
    assert sw.run_audit(tmp_path) == 0
    assert "no disclosures" in capsys.readouterr().out.lower()


def test_audit_shows_records_and_integrity(tmp_path, capsys):
    led = GovernanceLedger(default_ledger_path(tmp_path))
    led.append({"agent_id": "codex", "trust_level": "trusted-local",
                "returned_count": 3, "excluded_by_sensitivity": 1})
    assert sw.run_audit(tmp_path) == 0
    out = capsys.readouterr().out
    assert "codex" in out and "OK" in out


def test_verify_ledger_ok_and_detects_tamper(tmp_path, capsys):
    led = GovernanceLedger(default_ledger_path(tmp_path))
    led.append({"a": 1})
    assert sw.run_verify_ledger(tmp_path) == 0
    # tamper → verify-ledger returns nonzero
    p = default_ledger_path(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace('"a": 1', '"a": 2'), encoding="utf-8")
    capsys.readouterr()
    assert sw.run_verify_ledger(tmp_path) == 1
