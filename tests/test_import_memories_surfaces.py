"""Where the explicit import shows up: setup, doctor, status, and auto_migrate.

- ``engram setup`` asks whether to import once now (default no) and runs the
  same flow as ``engram import-memories``; a "no" stores no switch.
- ``engram doctor`` / ``engram status`` show a read-only count and explain the
  old switches; neither writes to the store.
- ``auto_migrate`` (stdio server start) stays config-only and leaves one audit
  line per installed version.

HOME / USERPROFILE point at a temporary directory with sample files (conftest
``other_ai_tools_home``).
"""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout

import pytest

from piia_engram.core import Engram

IMPORT_TOOLS = {"auto_reconcile", "config_scan"}


@pytest.fixture
def store(tmp_path, monkeypatch, other_ai_tools_home):
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    for var in ("ENGRAM_TELEMETRY", "DO_NOT_TRACK", "NO_TELEMETRY"):
        monkeypatch.delenv(var, raising=False)
    Engram(root=root).add_lesson(
        "Pin test fixtures to a temporary store so suites never share state",
        domain="testing",
        source_tool="cli",
    )
    return root


def _snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _imported_rows(root):
    rows = Engram(root=root, read_only=True).get_lessons(limit=None, _update_access=False)
    return [row for row in rows if row.get("source_tool") in IMPORT_TOOLS]


def _answers(monkeypatch, *values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(it, ""))


def _telemetry_config(root):
    path = root / "telemetry_config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


# -- setup -------------------------------------------------------------------


def test_setup_default_answer_imports_nothing_and_stores_no_switch(store, monkeypatch, capsys):
    from piia_engram.setup_wizard import _run_privacy_preferences

    _answers(monkeypatch, "", "")  # import now: default (no); statistics: default
    _run_privacy_preferences(str(store))

    out = capsys.readouterr().out
    assert "engram import-memories" in out
    assert _imported_rows(store) == []
    assert not (store / "import_receipts").exists()
    assert "reconcile_authorized" not in _telemetry_config(store)


@pytest.mark.parametrize("flow", ["preferences", "defaults"])
def test_setup_yes_lists_then_imports_into_the_review_queue(store, monkeypatch, capsys, flow):
    from piia_engram import setup_wizard

    _answers(monkeypatch, "y", "y", "n", "n")  # import now, confirm, statistics no
    if flow == "preferences":
        setup_wizard._run_privacy_preferences(str(store))
    else:
        setup_wizard._run_privacy_defaults(str(store))

    out = capsys.readouterr().out
    assert "lint_rule.md" in out  # the list came first
    rows = _imported_rows(store)
    assert len(rows) >= 5
    assert all((row.get("tier") or row.get("memory_state")) == "staging" for row in rows)
    assert len(list((store / "import_receipts").glob("*.json"))) == 1


def test_setup_yes_then_no_at_the_list_writes_nothing(store, monkeypatch):
    from piia_engram.setup_wizard import _run_privacy_preferences

    _answers(monkeypatch, "y", "n", "")
    knowledge_before = {
        rel: digest for rel, digest in _snapshot(store).items() if rel.startswith("knowledge/")
    }
    _run_privacy_preferences(str(store))

    knowledge_after = {
        rel: digest for rel, digest in _snapshot(store).items() if rel.startswith("knowledge/")
    }
    assert knowledge_after == knowledge_before
    assert not (store / "import_receipts").exists()


def test_setup_yes_lifts_an_earlier_stored_no(store, monkeypatch):
    from piia_engram.setup_wizard import _run_privacy_preferences

    (store / "telemetry_config.json").write_text(
        json.dumps({"reconcile_authorized": False}), encoding="utf-8"
    )
    _answers(monkeypatch, "y", "y", "")
    _run_privacy_preferences(str(store))

    assert _telemetry_config(store)["reconcile_authorized"] is True
    assert _imported_rows(store)


# -- doctor / status -----------------------------------------------------------


def _doctor_output(fix=False):
    from piia_engram import doctor, setup_wizard  # noqa: F401  (doctor is imported through it)

    buf = io.StringIO()
    with redirect_stdout(buf):
        doctor._run_functional_checks(fix=fix)
    return buf.getvalue()


def test_doctor_counts_importable_memories_without_writing(store):
    before = _snapshot(store)

    out = _doctor_output()

    assert "Other AI tools' memories:" in out
    assert "engram import-memories" in out
    line = next(line for line in out.splitlines() if "Other AI tools' memories:" in line)
    assert "6" in line
    assert _snapshot(store) == before
    assert _imported_rows(store) == []


def test_doctor_explains_the_old_switches(store, monkeypatch):
    monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", "eager")
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")

    out = _doctor_output()

    assert "ENGRAM_MCP_STARTUP_SYNC=eager no longer has any effect" in out
    assert "ENGRAM_RECONCILE=1 no longer turns on any automatic import" in out

    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    out = _doctor_output()
    assert "ENGRAM_RECONCILE=0: other AI tools' files are never read" in out
    assert "switched off" in out or "已关闭" in out


def test_status_shows_the_count_and_stays_read_only(store):
    from piia_engram.status_report import build_status, render_status_text

    before = _snapshot(store)

    plain = build_status(probe=False, root=store)
    assert "external_memories" not in plain  # other callers (desktop client) unchanged

    status = build_status(probe=False, root=store, external_memories=True)
    assert status["external_memories"]["count"] == 6
    text = render_status_text(status)
    assert "Other AI tools' memories:" in text
    assert any("engram import-memories" in warning for warning in status["warnings"])
    assert _snapshot(store) == before


def test_status_cli_includes_the_count(store, capsys):
    from piia_engram.cli_commands import run_status

    assert run_status(["--no-probe"]) == 0
    assert "Other AI tools' memories:" in capsys.readouterr().out


# -- auto_migrate ----------------------------------------------------------------


def test_auto_migrate_is_config_only_idempotent_and_audited(store):
    from piia_engram.setup_wizard import auto_migrate

    knowledge = {rel: d for rel, d in _snapshot(store).items()
                 if rel.split("/")[0] in ("knowledge", "identity")}

    auto_migrate()
    auto_migrate()

    lines = [json.loads(line) for line in (store / "audit.log").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    migrate_lines = [line for line in lines if line.get("resource") == "config/auto_migrate"]
    assert len(migrate_lines) == 1  # once per installed version
    assert "memory_content=untouched" in migrate_lines[0]["detail"]
    assert {rel: d for rel, d in _snapshot(store).items()
            if rel.split("/")[0] in ("knowledge", "identity")} == knowledge
    assert _imported_rows(store) == []
