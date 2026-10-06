"""``engram import-memories``: the explicit way to bring in other AI tools' memories.

Preview first (zero writes), then the review queue after a confirmation, with
a receipt and an audit line; a second run imports nothing twice. HOME /
USERPROFILE point at a temporary directory with sample files (conftest
``other_ai_tools_home``).
"""

from __future__ import annotations

import hashlib
import json
import sys

import pytest

from piia_engram import memory_import
from piia_engram.core import Engram

IMPORT_TOOLS = {"auto_reconcile", "config_scan"}


@pytest.fixture
def store(tmp_path, monkeypatch, other_ai_tools_home):
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    seed = Engram(root=root)
    seed.update_profile({"role": "developer"})
    seed.add_lesson(
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


def _audit_lines(root):
    path = root / "audit.log"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_dry_run_lists_items_and_writes_nothing(store, capsys):
    before = _snapshot(store)

    assert memory_import.run_cli(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "lint_rule.md" in out
    assert "deploy_note.md" in out
    assert "CLAUDE.md" in out and "AGENTS.md" in out and "style.mdc" in out
    assert "MEMORY.md" not in out  # the index file is not a memory
    assert _snapshot(store) == before


def test_json_dry_run_and_unconfirmed_run_write_nothing(store, capsys):
    before = _snapshot(store)

    assert memory_import.run_cli(["--json", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True and preview["count"] == len(preview["items"]) >= 5
    assert {item["source"] for item in preview["items"]} == {"memories", "configs"}
    assert all(item["status"] == "planned" and not item["id"] for item in preview["items"])

    # --json without --yes asks for confirmation and writes nothing.
    assert memory_import.run_cli(["--json"]) == 1
    assert json.loads(capsys.readouterr().out)["requires_confirmation"] is True
    # Not a terminal and no --yes: nothing is imported.
    assert memory_import.run_cli([]) == 1
    assert "--yes" in capsys.readouterr().out
    assert _snapshot(store) == before


def test_confirmed_import_lands_in_review_queue_with_receipt_and_audit(store, capsys):
    preview = memory_import.plan(Engram(root=store, read_only=True))

    assert memory_import.run_cli(["--yes"]) == 0
    out = capsys.readouterr().out

    rows = _imported_rows(store)
    assert len(rows) == preview["count"] >= 5
    for row in rows:
        assert (row.get("tier") or row.get("memory_state")) == "staging"
    # Each previewed item was imported; same texts, same hashes.
    assert sorted(row["summary"] for row in rows) == sorted(i["summary"] for i in preview["items"])

    receipts = sorted((store / "import_receipts").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["imported"] == len(rows)
    assert receipt["tier"] == "staging"
    assert receipt["created_at"].endswith("Z")
    assert {item["id"] for item in receipt["items"]} == {row["id"] for row in rows}
    hashes = {i["summary"]: i["content_sha256"] for i in preview["items"]}
    by_id = {row["id"]: row for row in rows}
    for item in receipt["items"]:
        assert item["content_sha256"] == hashes[by_id[item["id"]]["summary"]]
        assert item["file"].startswith("~/")
    assert sum(f["count"] for f in receipt["files"]) == len(rows)
    assert any(f["file"].endswith("lint_rule.md") for f in receipt["files"])
    # metadata only: the receipt never carries the imported text
    text = receipts[0].read_text(encoding="utf-8")
    assert "linter" not in text and "staging bucket" not in text
    assert f"import_receipts/{receipts[0].name}" in out

    lines = _audit_lines(store)
    summary_lines = [
        line for line in lines
        if line.get("resource") == "knowledge/import_memories"
    ]
    assert len(summary_lines) == 1
    assert receipt["receipt_id"] in summary_lines[0]["detail"]
    assert f"imported={len(rows)}" in summary_lines[0]["detail"]


def test_running_again_imports_nothing_twice(store, capsys):
    assert memory_import.run_cli(["--yes"]) == 0
    first = _imported_rows(store)
    capsys.readouterr()

    before = _snapshot(store)
    assert memory_import.plan(Engram(root=store, read_only=True))["count"] == 0
    assert memory_import.run_cli(["--yes"]) == 0
    # Nothing new: the second run stops after the preview and writes nothing.
    assert _snapshot(store) == before
    assert len(_imported_rows(store)) == len(first)
    assert len(list((store / "import_receipts").glob("*.json"))) == 1

    # Even calling the write step directly adds no duplicate rows; that run is
    # audited without a receipt.
    payload = memory_import.run(Engram(root=store))
    assert payload["imported"] == 0 and payload["receipt"] == ""
    assert len(_imported_rows(store)) == len(first)
    details = [
        line["detail"] for line in _audit_lines(store)
        if line.get("resource") == "knowledge/import_memories"
    ]
    assert len(details) == 2 and "receipt=none imported=0" in details[1]


def test_interactive_answer_decides(store):
    before = _snapshot(store)
    questions = []

    declined = memory_import.interactive_import(
        lambda q: questions.append(q) or False, out=lambda _line: None,
    )
    assert declined["status"] == "declined"
    assert len(questions) == 1
    assert _snapshot(store) == before

    accepted = memory_import.interactive_import(lambda q: True, out=lambda _line: None)
    assert accepted["status"] == "imported"
    assert accepted["imported"] == len(_imported_rows(store)) > 0


def test_source_option_limits_what_is_read(store, capsys):
    assert memory_import.run_cli(["--source", "memories", "--yes"]) == 0
    rows = _imported_rows(store)
    assert rows and {row["source_tool"] for row in rows} == {"auto_reconcile"}

    assert memory_import.run_cli(["--source=configs", "--json", "--dry-run"]) == 0
    capsys.readouterr()
    assert memory_import.run_cli(["--source", "nonsense"]) == 2


@pytest.mark.parametrize("switch", ["env", "config"])
def test_off_switch_refuses_and_writes_nothing(store, monkeypatch, capsys, switch):
    if switch == "env":
        monkeypatch.setenv("ENGRAM_RECONCILE", "0")
        expected = "ENGRAM_RECONCILE=0"
    else:
        (store / "telemetry_config.json").write_text(
            json.dumps({"reconcile_authorized": False}), encoding="utf-8"
        )
        expected = "reconcile_authorized=false"
    before = _snapshot(store)

    assert memory_import.run_cli(["--yes"]) == 1
    assert expected in capsys.readouterr().out
    assert memory_import.importable_summary(store) == {
        "enabled": False, "disabled_by": expected, "count": 0, "more": False,
    }
    assert _snapshot(store) == before


def test_importable_summary_is_read_only(store):
    before = _snapshot(store)
    summary = memory_import.importable_summary(store)
    assert summary["enabled"] is True
    assert summary["count"] >= 5
    assert set(summary["by_source"]) == {"memories", "configs"}
    assert "engram import-memories" in memory_import.importable_text(summary)
    assert _snapshot(store) == before


def test_bad_option_is_a_usage_error(store, capsys):
    assert memory_import.run_cli(["--frobnicate"]) == 2
    assert memory_import.run_cli(["--help"]) == 0
    assert "engram import-memories" in capsys.readouterr().out


def test_cli_entry_dispatches_the_command(store, monkeypatch, capsys):
    from piia_engram import setup_wizard

    before = _snapshot(store)
    monkeypatch.setattr(sys, "argv", ["engram", "import-memories", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        setup_wizard.main()
    assert exc.value.code == 0
    assert "lint_rule.md" in capsys.readouterr().out
    assert _snapshot(store) == before
