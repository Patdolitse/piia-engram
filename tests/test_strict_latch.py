"""Strict latch (4.21.1 plan V4 section (vi)): on the Owner's store strict stays on.

Effective strict = ENGRAM_APPROVAL=strict OR <root>/approval_mode.json exists. The
marker is written by bootstrap (MCP server start, CLI review apply) under strict,
never by a tool call; only an attributed CLI clear removes it. A latched store
with the variable unset stays strict and says so (start warning, audit entry,
doctor line). Stores that never ran strict see nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from piia_engram import strict_mode
from piia_engram.core import Engram


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "engram"
    (r / "knowledge").mkdir(parents=True)
    monkeypatch.setenv("ENGRAM_DIR", str(r))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return r


def _marker(root: Path) -> Path:
    return root / "approval_mode.json"


def _audit(root: Path) -> list[dict]:
    path = root / "audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_strict_bootstrap_writes_the_marker_atomically_and_keeps_first_seen(root, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")

    strict_mode.bootstrap(root, source="mcp")
    first = json.loads(_marker(root).read_text(encoding="utf-8"))
    strict_mode.bootstrap(root, source="cli")
    second = json.loads(_marker(root).read_text(encoding="utf-8"))

    assert {"strict_first_seen_at", "strict_last_seen_at", "last_host"} <= set(first)
    assert second["strict_first_seen_at"] == first["strict_first_seen_at"]
    assert not list(root.glob("approval_mode.json.*"))  # no temp file left behind


def test_unset_bootstrap_never_writes_a_marker(root):
    strict_mode.bootstrap(root, source="mcp")

    assert not _marker(root).exists()
    assert strict_mode.approval_strict(root) is False


def test_latched_store_stays_strict_with_the_variable_unset(root, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    strict_mode.bootstrap(root, source="mcp")
    monkeypatch.delenv("ENGRAM_APPROVAL")
    import piia_engram.mcp_server as m

    m._engram = Engram(root)

    assert strict_mode.approval_strict(root) is True
    assert "ENGRAM_APPROVAL=strict" in _run(m.update_identity(field="profile", updates_json='{"role": "x"}'))
    assert "wrap_up_session" not in m.server_instructions()
    lesson = m._engram.add_lesson("latched proposal", domain="t")
    assert lesson["tier"] == "staging"


def test_latched_env_unset_warns_audits_and_doctor_reports(root, monkeypatch, capsys):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    strict_mode.bootstrap(root, source="mcp")
    monkeypatch.delenv("ENGRAM_APPROVAL")

    note = strict_mode.bootstrap(root, source="mcp")

    assert "latched" in note and "strict-marker --clear" in note
    assert any("strict_latched_env_unset" in json.dumps(a) for a in _audit(root))
    from piia_engram import setup_wizard  # noqa: F401  (doctor is imported through it)
    from piia_engram.doctor import _run_functional_checks

    _run_functional_checks()
    out = capsys.readouterr().out
    assert "strict is latched" in out


def test_no_marker_means_no_warning(root):
    assert strict_mode.bootstrap(root, source="mcp") == ""
    assert strict_mode.latch_note(root) == ""


def _cli(monkeypatch, capsys, *argv):
    from piia_engram import setup_wizard

    monkeypatch.setattr(sys, "argv", ["engram", "review", *argv])
    with pytest.raises(SystemExit) as exc:
        setup_wizard.main()
    return int(exc.value.code or 0), capsys.readouterr().out


def test_clear_needs_an_operator_and_reports_pending_in_dry_run(root, monkeypatch, capsys):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    strict_mode.bootstrap(root, source="mcp")
    Engram(root).add_lesson("still pending", domain="t")
    monkeypatch.delenv("ENGRAM_APPROVAL")

    code, out = _cli(monkeypatch, capsys, "strict-marker", "--clear")
    assert code == 0 and "dry_run" in out and '"pending": 1' in out
    assert _marker(root).exists()

    code, _out = _cli(monkeypatch, capsys, "strict-marker", "--clear", "--yes")
    assert code != 0 and _marker(root).exists()

    code, _out = _cli(monkeypatch, capsys, "strict-marker", "--clear", "--operator", "owner", "--yes")
    assert code == 0 and not _marker(root).exists()
    assert any(a.get("action") == "owner_cli" and a.get("verb") == "strict-marker-clear" for a in _audit(root))


def test_audit_entries_record_the_effective_mode(root, monkeypatch):
    Engram(root).add_lesson("default mode write", domain="t")
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    Engram(root).add_lesson("strict mode write", domain="t")

    modes = [a.get("mode") for a in _audit(root) if a.get("action") == "write"]
    assert "default" in modes and "strict" in modes


def test_export_engram_leaves_the_marker_out(root, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    strict_mode.bootstrap(root, source="mcp")

    path = Engram(root).export_all(str(root.parent / "export.json"))

    text = Path(path).read_text(encoding="utf-8")
    assert "approval_mode" not in text and "last_host" not in text


def test_a_tool_call_never_writes_the_marker(root, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    import piia_engram.mcp_server as m

    m._engram = Engram(root)
    _run(m.add_lesson(summary="a strict proposal", user_confirmed=True))

    assert not _marker(root).exists()
