"""C1 decision conflict governance tests."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore, ResolutionStore
from piia_engram.reconcile_proposal import (
    CHOICE_DIVERGENCE_THRESHOLD,
    CONFLICT_QUESTION_THRESHOLD,
    similarity as jaccard_similarity,
)
from piia_engram.storage import CONFLICT_C_CEILING, CONFLICT_Q_THRESHOLD, _read_json


def _decision(id_: str, question: str, choice: str, *, domain: str = "ops") -> dict:
    return {
        "id": id_,
        "type": "decision",
        "status": "active",
        "question": question,
        "choice": choice,
        "domain": domain,
    }


def _seed_conflict(eng: Engram) -> tuple[dict, dict]:
    existing = eng._read_entries(eng._knowledge_dir / "decisions.json", "decision", migrate=False)
    index = len(existing)
    first = _decision(
        f"conflict-{index}-a",
        "which release gate should Engram use",
        "manual owner approval",
        domain="release",
    )
    second = _decision(
        f"conflict-{index}-b",
        "which release gate should Engram use",
        "automated pipeline gate",
        domain="release",
    )
    eng._write_entries(eng._knowledge_dir / "decisions.json", [*existing, first, second], "decision")
    return first, second


def test_threshold_constants_are_single_semantic_source():
    assert CONFLICT_Q_THRESHOLD == pytest.approx(0.6)
    assert CONFLICT_C_CEILING == pytest.approx(0.5)
    assert CONFLICT_QUESTION_THRESHOLD == pytest.approx(CONFLICT_Q_THRESHOLD)
    assert CHOICE_DIVERGENCE_THRESHOLD == pytest.approx(CONFLICT_C_CEILING)


def test_retrieval_f1_and_reconcile_jaccard_boundaries_are_distinct(tmp_path: Path):
    eng = Engram(root=tmp_path)
    a = "alpha beta gamma"
    b = "alpha beta gamma delta"

    assert eng._bigram_similarity(a, b) != pytest.approx(jaccard_similarity(a, b))
    assert eng._bigram_similarity(a, b) >= CONFLICT_Q_THRESHOLD
    assert jaccard_similarity(a, b) >= CONFLICT_QUESTION_THRESHOLD


def test_detect_conflicts_returns_ids_scores_and_skips_missing_ids(tmp_path: Path):
    eng = Engram(root=tmp_path)
    decisions = [
        _decision("d1", "which queue backend should Engram use", "Redis"),
        _decision("d2", "which queue backend should Engram use", "SQLite"),
        {"question": "which queue backend should Engram use", "choice": "Postgres", "domain": "ops"},
    ]

    conflicts = eng._detect_decision_conflicts(decisions)

    assert len(conflicts) == 1
    assert conflicts[0]["id1"] == "d1"
    assert conflicts[0]["id2"] == "d2"
    assert conflicts[0]["q_sim"] >= CONFLICT_Q_THRESHOLD
    assert conflicts[0]["c_sim"] < CONFLICT_C_CEILING


def test_supersedes_component_is_excluded_but_led_to_is_not(tmp_path: Path):
    eng = Engram(root=tmp_path)
    first = _decision("old", "which sync strategy should Engram use", "polling")
    second = _decision("new", "which sync strategy should Engram use", "webhooks")

    supersedes = [{"src": "new", "rel": "supersedes", "dst": "old"}]
    led_to = [{"src": "new", "rel": "led_to", "dst": "old"}]

    assert eng.detect_active_decision_conflicts([first, second], relations=supersedes) == []
    assert len(eng.detect_active_decision_conflicts([first, second], relations=led_to)) == 1


def test_resolution_store_dismiss_uses_update_json_and_reports_changed_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import piia_engram.governance_store as gs

    calls = []
    original_update_json = gs._update_json

    def tracking_update_json(path, mutator, default=None):
        calls.append(Path(path).name)
        return original_update_json(path, mutator, default=default)

    monkeypatch.setattr(gs, "_update_json", tracking_update_json)
    store = ResolutionStore(tmp_path)
    first = _decision("a", "which cache should Engram use", "memory")
    second = _decision("b", "which cache should Engram use", "disk")

    record = store.dismiss(first, second, note="not actually conflicting")
    repeated = store.dismiss(first, second, note="not actually conflicting")

    assert calls == ["conflict_resolutions.json", "conflict_resolutions.json"]
    assert record["pair_key"] == "a::b"
    assert repeated["pair_key"] == record["pair_key"]
    changed = dict(first)
    changed["choice"] = "memory with TTL"
    assert store.content_changed(changed, second) is True


def test_dismissed_pairs_are_suppressed_from_detection(tmp_path: Path):
    eng = Engram(root=tmp_path)
    first = _decision("a", "which cache should Engram use", "memory")
    second = _decision("b", "which cache should Engram use", "disk")
    # Guard against a vacuous pass: the pair must be a real conflict pre-dismiss.
    assert len(eng.detect_active_decision_conflicts([first, second])) == 1
    store = ResolutionStore(tmp_path)
    store.dismiss(first, second)

    conflicts = eng.detect_active_decision_conflicts(
        [first, second],
        resolutions=store.all_records(),
    )

    assert conflicts == []


def test_conflicts_resolve_supersede_archive_and_dismiss_dry_run_then_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # Import via setup_wizard (the canonical re-export hub); importing
    # cli_commands first would trip the known module-order circularity.
    from piia_engram.setup_wizard import run_conflicts

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram(root=tmp_path)
    first, second = _seed_conflict(eng)

    rc = run_conflicts([
        "resolve",
        first["id"],
        second["id"],
        "--action",
        "supersede",
        "--keep",
        second["id"],
        "--json",
    ])
    dry = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert dry["dry_run"] is True
    assert RelationStore(tmp_path).all_edges() == []

    rc = run_conflicts([
        "resolve",
        first["id"],
        second["id"],
        "--action",
        "supersede",
        "--keep",
        second["id"],
        "--commit",
        "--yes",
        "--json",
    ])
    applied = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert applied["changed"] is True
    assert {"src": second["id"], "rel": "supersedes", "dst": first["id"]} in RelationStore(tmp_path).all_edges()
    # Stored entries are enriched on write (timestamps/tier/provenance), so compare ids.
    assert [d["id"] for d in eng.get_decisions(limit=None, _update_access=False)] == [second["id"]]

    third, fourth = _seed_conflict(eng)
    rc = run_conflicts([
        "resolve",
        third["id"],
        fourth["id"],
        "--action",
        "archive",
        "--keep",
        fourth["id"],
        "--commit",
        "--yes",
        "--json",
    ])
    archived = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert archived["changed"] is True
    active_ids = {d["id"] for d in eng.get_decisions(limit=None, _update_access=False)}
    assert third["id"] not in active_ids
    assert fourth["id"] in active_ids

    fifth, sixth = _seed_conflict(eng)
    rc = run_conflicts([
        "resolve",
        fifth["id"],
        sixth["id"],
        "--action",
        "dismiss",
        "--commit",
        "--yes",
        "--json",
    ])
    dismissed = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert dismissed["changed"] is True
    assert ResolutionStore(tmp_path).is_suppressed(fifth, sixth) is True


def test_conflicts_list_json_contains_ids_scores_and_changed_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # Import via setup_wizard (the canonical re-export hub); importing
    # cli_commands first would trip the known module-order circularity.
    from piia_engram.setup_wizard import run_conflicts

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    eng = Engram(root=tmp_path)
    first, second = _seed_conflict(eng)

    rc = run_conflicts(["list", "--json"])
    listed = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert listed["count_unsuppressed"] == len(listed["conflicts"])
    assert listed["conflicts"][0]["id1"] == first["id"]
    assert listed["conflicts"][0]["id2"] == second["id"]
    assert "q_sim" in listed["conflicts"][0]

    ResolutionStore(tmp_path).dismiss(first, second)
    eng.update_decision(first["id"], {"choice": "manual owner approval with emergency override"})
    rc = run_conflicts(["list", "--json"])
    after = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert after["count_unsuppressed"] == 0
    assert after["count_suppressed"] == 1
    assert after["suppressed"][0]["content_changed"] is True


def test_mcp_doctor_json_and_markdown_include_actionable_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    # Import mcp_server first (canonical order) — importing mcp_tools_admin
    # directly would trip the known module-order circularity.
    from piia_engram import mcp_server  # noqa: F401
    from piia_engram import mcp_tools_admin

    eng = Engram(root=tmp_path)
    first, second = _seed_conflict(eng)
    monkeypatch.setattr(mcp_tools_admin.S, "_engram", eng)

    raw = asyncio.run(mcp_tools_admin.doctor(output_format="json"))
    parsed = json.loads(raw)
    check = next(c for c in parsed["checks"] if c["name"] == "decision_conflicts")

    assert check["status"] == "WARN"
    assert check["count_unsuppressed"] >= 1
    assert check["samples"][0]["id1"] == first["id"]
    assert check["samples"][0]["id2"] == second["id"]

    markdown = asyncio.run(mcp_tools_admin.doctor(output_format="markdown"))
    assert "Decision conflict samples" in markdown
    assert first["id"] in markdown
    assert "engram conflicts list" in markdown


def test_conflicts_cli_is_registered_in_top_level_help(capsys: pytest.CaptureFixture[str]):
    from piia_engram import setup_wizard

    old_argv = sys.argv
    sys.argv = ["engram", "unknown-command"]
    try:
        with pytest.raises(SystemExit):
            setup_wizard.main()
    finally:
        sys.argv = old_argv

    output = capsys.readouterr().out
    assert "engram conflicts" in output
    assert "决策冲突" in output
    assert "decision conflicts" in output


def test_export_import_native_includes_relations_and_conflict_resolutions(tmp_path: Path):
    source = Engram(root=tmp_path / "source")
    first, second = _seed_conflict(source)
    source.add_relation(second["id"], "supersedes", first["id"])
    ResolutionStore(source.root).dismiss(first, second)
    export_path = source.export_all(str(tmp_path / "backup.json"))

    exported = _read_json(Path(export_path))
    assert exported["knowledge"]["relations"]
    assert exported["knowledge"]["conflict_resolutions"]

    target = Engram(root=tmp_path / "target")
    target.import_all(export_path, merge=True)
    target.import_all(export_path, merge=True)
    assert RelationStore(target.root).all_edges() == RelationStore(source.root).all_edges()
    assert ResolutionStore(target.root).all_records() == ResolutionStore(source.root).all_records()

    replacement = dict(exported)
    replacement["knowledge"] = dict(exported["knowledge"])
    replacement["knowledge"]["relations"] = []
    replacement["knowledge"]["conflict_resolutions"] = []
    replacement_path = tmp_path / "replacement.json"
    replacement_path.write_text(json.dumps(replacement, ensure_ascii=False), encoding="utf-8")
    target.import_all(str(replacement_path), merge=False)
    assert RelationStore(target.root).all_edges() == []
    assert ResolutionStore(target.root).all_records() == {}
