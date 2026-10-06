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


# -- the confirmed list is what gets written ---------------------------------------


def _memory_file(home, name):
    return home / ".claude" / "projects" / "demo-project" / "memory" / name


def test_write_uses_the_confirmed_list_even_if_files_change(store, other_ai_tools_home):
    preview = memory_import.plan(Engram(root=store, read_only=True))
    confirmed = sorted(item["summary"] for item in preview["items"])

    # After confirming: one file is edited, one new memory appears.
    _memory_file(other_ai_tools_home, "lint_rule.md").write_text(
        "---\nname: lint\ndescription: Always run the linter before committing code\n"
        "type: feedback\n---\n\nEDITED AFTER CONFIRMATION.\n",
        encoding="utf-8",
    )
    _memory_file(other_ai_tools_home, "late_note.md").write_text(
        "---\nname: late\ndescription: A memory written after the list was confirmed\n"
        "type: feedback\n---\n\nA memory written after the list was confirmed.\n",
        encoding="utf-8",
    )

    result = memory_import.write_plan(Engram(root=store), preview)

    rows = _imported_rows(store)
    assert sorted(row["summary"] for row in rows) == confirmed  # no rescan
    lint = next(row for row in rows if row["summary"].startswith("Always run the linter"))
    assert "CI rejects unlinted pushes" in lint["detail"]  # the confirmed text
    assert "EDITED" not in lint["detail"]
    assert result["source_changed"] == 1
    receipt = json.loads((store / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["source_changed"] == 1
    changed = [item for item in receipt["items"] if item.get("source_changed")]
    assert len(changed) == 1 and changed[0]["file"].endswith("lint_rule.md")


def test_interactive_flow_writes_what_was_shown(store, other_ai_tools_home):
    shown = []

    def ask(question):
        # The Owner says yes; meanwhile a new memory file appears.
        _memory_file(other_ai_tools_home, "late_note.md").write_text(
            "---\nname: late\ndescription: A memory written while the question was open\n"
            "type: feedback\n---\n\nA memory written while the question was open.\n",
            encoding="utf-8",
        )
        return True

    result = memory_import.interactive_import(ask, out=shown.append)

    assert result["status"] == "imported"
    assert "late_note.md" not in shown[0]
    assert not any("question was open" in row["summary"] for row in _imported_rows(store))
    assert result["imported"] == result["count"]


# -- partial failure still leaves a receipt -----------------------------------------


def test_an_error_part_way_keeps_a_partial_receipt_and_audit(store, monkeypatch):
    preview = memory_import.plan(Engram(root=store, read_only=True))
    writer = Engram(root=store)
    real_add = Engram.add_lesson
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk went away")
        return real_add(self, *args, **kwargs)

    monkeypatch.setattr(Engram, "add_lesson", flaky)

    result = memory_import.write_plan(writer, preview)

    assert result["partial"] is True and result["error"] == "OSError"
    assert result["imported"] == 2
    assert result["not_written"] == len(preview["items"]) - 2
    receipt = json.loads((store / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "partial" and receipt["error"] == "OSError"
    assert receipt["imported"] == 2 and receipt["not_written"] == result["not_written"]
    assert {item["id"] for item in receipt["items"]} == {row["id"] for row in _imported_rows(store)}
    detail = next(
        line["detail"] for line in _audit_lines(store)
        if line.get("resource") == "knowledge/import_memories"
    )
    assert "partial error=OSError" in detail and receipt["receipt_id"] in detail


def test_the_cli_reports_a_partial_import(store, monkeypatch, capsys):
    real_add = Engram.add_lesson
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real_add(self, *args, **kwargs)

    monkeypatch.setattr(Engram, "add_lesson", flaky)
    assert memory_import.run_cli(["--yes", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["partial"] is True and payload["error"] == "RuntimeError"
    assert payload["receipt"].startswith("import_receipts/")
    assert all("detail" not in item and "path" not in item for item in payload["items"])


# -- a full review queue stops the import ----------------------------------------------


def test_a_full_review_queue_stops_writing_and_the_receipt_counts_the_rest(store, monkeypatch):
    # Two older items already wait for review; the queue holds three.
    monkeypatch.setenv("ENGRAM_REVIEW_QUEUE_MAX", "3")
    monkeypatch.setenv("ENGRAM_REVIEW_MIN_STAY_DAYS", "0")
    seed = Engram(root=store)
    older = [
        seed.add_lesson(text, domain="testing", source_tool="cli", tier="staging")["id"]
        for text in ("Older queued note about flaky network retries in CI",
                     "Older queued note about rotating the signing key yearly")
    ]
    preview = memory_import.plan(Engram(root=store, read_only=True))
    assert preview["count"] >= 5

    result = memory_import.write_plan(Engram(root=store), preview)

    assert result["imported"] == 1
    assert result["queue_full"] == result["not_written"] == preview["count"] - 1
    assert len(_imported_rows(store)) == 1
    receipt = json.loads((store / result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "partial" and receipt["error"] == ""
    assert receipt["stopped_by"] == "review_queue_full"
    assert receipt["skipped"]["queue_full"] == receipt["not_written"] == preview["count"] - 1
    # the older queued items were not pushed out to make room
    active = {row["id"] for row in Engram(root=store, read_only=True).get_lessons(
        limit=None, _update_access=False)}
    assert set(older) <= active
    assert not result.get("overflow_archived_ids")
    archive = store / "knowledge" / "overflow_archive"
    assert not archive.exists() or not any(archive.iterdir())


def test_help_states_the_limits(capsys):
    assert memory_import.run_cli(["--help"]) == 0
    out = capsys.readouterr().out
    assert "at most 25 per run" in out
    assert "ENGRAM_REVIEW_QUEUE_MAX" in out
