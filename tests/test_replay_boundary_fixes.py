"""Replay provenance boundaries and byte comparisons with the production base."""

from __future__ import annotations

import ast
import importlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import (
    Config, GuardRefused, IsolatedStore, carries_replay_marker, REPLAY_EXPORT_MARKER,
)
from test_isolated_store import ADMIT, LATER, _card, _snap
from test_replay_experience import MODE, EARLY, _world


def _tamper(w, change):
    marker = w.pr.root / "isolated_store_root.json"
    if change == "metadata_removed":
        marker.unlink()
    elif change == "receipt_missing":
        w.pr.receipts_path.unlink()
    else:
        data = json.loads(marker.read_text(encoding="utf-8"))
        data["mode"] = "production"
        marker.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("change", ["metadata_edited", "receipt_missing", "metadata_removed"])
@pytest.mark.parametrize("route", ["ordinary", "read_only", "explicit", "isolated", "direct", "loader", "facade"])
def test_all_attachment_routes_require_consistent_initialisation(tmp_path, monkeypatch, change, route):
    w = _world(tmp_path, monkeypatch)
    _tamper(w, change)
    before = _snap(w.pr.root)
    with pytest.raises(GuardRefused):
        if route in {"ordinary", "read_only", "explicit"}:
            Engram(root=w.pr.root, read_only=route != "ordinary",
                   store_mode=MODE if route == "explicit" else "production")
        elif route == "isolated":
            IsolatedStore.open(w.cfg)
        elif route == "direct":
            IsolatedStore(w.cfg, str(w.pr.root), str(w.pr.receipts_dir))
        elif route == "loader":
            IsolatedStore.open()
        else:
            from piia_engram.embedded.snapshot import retrieve_task_context_snapshot
            retrieve_task_context_snapshot(
                engram_root=w.pr.root, project_folder=tmp_path / "project",
                project_id="sample", task_id="task", task_class="maintenance", objective="cache",
            )
    assert _snap(w.pr.root) == before
    records = [json.loads(line) for line in (w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert records[-1]["result"] == "guard_refused"


def test_attachment_uses_pinned_ledger_without_launcher_environment(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    monkeypatch.delenv("PIIA_ISOLATED_STORE_CONFIG", raising=False)
    assert Engram(root=w.pr.root, read_only=True, store_mode=MODE)._store_mode == MODE
    _tamper(w, "metadata_edited")
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=w.pr.root, read_only=True)
    assert "guard_mode_immutable" in (w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("placement", ["top", "nested", "tuple", "text"])
def test_production_admission_checks_original_before_normalisation(tmp_path, monkeypatch, placement):
    w = _world(tmp_path, monkeypatch, mode="production")
    card = _card("sample", "Q2", EARLY)
    if placement == "top":
        card["store_mode"] = MODE
    elif placement == "nested":
        card["discarded_metadata"] = {"origin": [{"mode": MODE}]}
    elif placement == "tuple":
        card["discarded_metadata"] = ({"store_mode": MODE},)
    else:
        card["detail"] += REPLAY_EXPORT_MARKER
    called = []
    original = w.pr._entry_from_card
    monkeypatch.setattr(w.pr, "_entry_from_card", lambda *args: called.append(True) or original(*args))
    result = w.pr.admit(card, "R1", ADMIT)
    assert result["result"] == "replay_experience_import_refused"
    assert called == []
    assert w.pr.receipts()[-1]["result"] == result["result"]
    assert w.pr._rows(w.pr._engram(read_only=True)) == []


@pytest.mark.parametrize("route", ["lesson", "decision", "playbook", "bulk", "update", "onboard", "project"])
def test_production_ingestion_cannot_normalise_away_original_markers(tmp_path, monkeypatch, route):
    w = _world(tmp_path, monkeypatch, mode="production")
    eng = w.pr._engram(read_only=False)
    marker = {"mode": MODE}
    row = {"summary": "cache observation", "question": "Which cache?", "choice": "Bounded cache",
           "title": "Cache steps", "provenance": {"_ingestion_origin": marker}}
    if route == "lesson":
        result = eng.add_lesson(row)
    elif route == "decision":
        result = eng.add_decision(row)
    elif route == "playbook":
        result = eng.add_playbook(row)
    elif route == "bulk":
        result = eng.bulk_add_lessons([row])
    elif route == "update":
        entry = eng.add_lesson({"summary": "original cache observation"})
        result = eng.update_lesson(entry["id"], {"summary": {"store_mode": MODE}})
    elif route == "onboard":
        result = eng.create_onboard_candidates([
            {"kind": "dep", "ref": "example", "detail": {}, "discarded": marker}
        ], repo_id="example")
    else:
        result = eng.save_project_snapshot("example", {"notes": "sample", "discarded": marker})
    assert result["error"] == "replay_experience_import_refused"
    assert "replay_experience_import_refused" in (w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8")


def test_legacy_migration_checks_whole_source_before_extraction(tmp_path, monkeypatch):
    from piia_engram.compat import migrate_from_oca_memory
    eng = Engram(root=tmp_path / "normal")
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "near_misses.json").write_text(json.dumps([
        {"what_happened": "cache observation", "what_could_have_happened": "sample",
         "discarded": {"store_mode": MODE}}
    ]), encoding="utf-8")
    result = migrate_from_oca_memory(str(legacy), eng)
    assert result["error"] == "replay_experience_import_refused"
    assert eng.get_lessons() == []


@pytest.mark.parametrize("mode", ["production", MODE])
@pytest.mark.parametrize("surface", ["hermes", "native", "openclaw", "identity", "knowledge", "review", "history", "project"])
def test_replay_and_production_export_pairs(tmp_path, monkeypatch, mode, surface):
    from piia_engram.compat import hermes_handoff_payload, export_to_openclaw
    w = _world(tmp_path, monkeypatch, mode=mode)
    eng = w.pr._engram(read_only=False)
    lesson = eng.add_lesson({"summary": "cache observation", "tier": "verified"})
    eng.add_decision({"question": "Which cache?", "choice": "Bounded cache", "tier": "verified"})
    eng.save_project_snapshot("sample", {"notes": "cache observation"})
    if surface == "hermes":
        result = hermes_handoff_payload(eng)
        if mode == MODE:
            assert result["active_decisions"][0]["store_mode"] == MODE
    elif surface == "native":
        result = json.loads(Path(eng.export_all(str(tmp_path / "out.json"))).read_text(encoding="utf-8"))
        if mode == MODE:
            assert all(row["store_mode"] == MODE for row in result["projects"].values())
    elif surface == "openclaw":
        result = export_to_openclaw(eng, str(tmp_path / "bridge"))
        for name in ("SOUL.md", "MEMORY.md", "USER.md"):
            assert (REPLAY_EXPORT_MARKER in (tmp_path / "bridge" / name).read_text(encoding="utf-8")) == (mode == MODE)
    elif surface == "identity":
        result = eng.export_identity_card()
    elif surface == "knowledge":
        result = eng.export_knowledge_report()
    elif surface == "review":
        result = eng.generate_review_page()
    elif surface == "history":
        eng.update_lesson(lesson["id"], {"detail": "revised"})
        result = eng.get_knowledge_history(lesson["id"], include_bodies=True)
        if mode == MODE:
            assert result["snapshots"][0]["store_mode"] == MODE
    else:
        result = eng.get_project_snapshot("sample")
    if isinstance(result, str):
        assert (REPLAY_EXPORT_MARKER in result) == (mode == MODE)
    else:
        assert carries_replay_marker(result) == (mode == MODE)
        if mode == MODE:
            normal = Engram(root=tmp_path / "normal")
            assert normal.add_lesson({"summary": "imported handoff", "handoff": result})["error"] == "replay_experience_import_refused"


def test_filtered_markdown_export_keeps_original_provenance():
    from piia_engram.agents_md_export import build_agents_md_export
    text = build_agents_md_export(lessons=[{"summary": "sample", "tier": "staging", "store_mode": MODE}])
    assert text.startswith(REPLAY_EXPORT_MARKER)


@pytest.mark.parametrize("surface", ["portrait", "portrait_text", "portrait_html", "saved_portrait", "resume", "agent_pack", "project_pack", "recall_digest"])
@pytest.mark.parametrize("mode", ["production", MODE])
def test_other_snapshot_and_handoff_pairs(tmp_path, monkeypatch, surface, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    eng = w.pr._engram(read_only=False)
    eng.add_lesson({"summary": "cache observation", "tier": "verified"})
    if surface.startswith("portrait") or surface == "saved_portrait":
        portrait = eng.build_user_portrait()
        if surface == "portrait_text":
            result = eng.render_user_portrait(portrait)
        elif surface == "portrait_html":
            result = eng.render_user_portrait_html(portrait)
        elif surface == "saved_portrait":
            result = eng.save_user_portrait(portrait)
            assert carries_replay_marker(json.loads(Path(result["_path"]).read_text(encoding="utf-8"))) == (mode == MODE)
        else:
            result = portrait
    elif surface == "resume":
        result = eng.get_resume_brief()
    elif surface == "agent_pack":
        result = eng.build_agent_context_pack(task_summary="cache")
    elif surface == "project_pack":
        result = eng.build_project_resume_pack("sample")
    else:
        from piia_engram.recall_service import gather_recall
        result = gather_recall(eng, query="cache")
    if isinstance(result, str):
        assert (REPLAY_EXPORT_MARKER in result) == (mode == MODE)
    else:
        assert carries_replay_marker(result) == (mode == MODE)


def test_empty_replay_markdown_export_has_explicit_root_marker():
    from piia_engram.agents_md_export import build_agents_md_export
    assert build_agents_md_export(store_mode=MODE).startswith(REPLAY_EXPORT_MARKER)


def test_replay_portrait_growth_retains_numeric_statistics(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    eng = w.pr._engram(read_only=False)
    old = eng.build_user_portrait()
    eng.add_lesson({"summary": "cache observation", "tier": "verified"})
    new = eng.build_user_portrait()
    growth = eng.compare_user_portraits(old, new)
    assert growth["deltas"]["lesson_count"]["delta"] == 1
    assert carries_replay_marker(growth)
    assert eng.render_portrait_growth(growth).startswith(REPLAY_EXPORT_MARKER)


@pytest.mark.parametrize("mode", ["production", MODE])
def test_attachment_refuses_missing_initial_receipt_in_both_modes(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    w.pr.receipts_path.write_text("", encoding="utf-8")
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=w.pr.root, read_only=True, store_mode=mode)
    assert "guard_mode_immutable" in (w.pr.receipts_dir / "refusals.jsonl").read_text(encoding="utf-8")


def test_missing_metadata_without_config_is_refused_with_external_audit(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    _tamper(w, "metadata_removed")
    monkeypatch.delenv("PIIA_ISOLATED_STORE_CONFIG", raising=False)
    before = _snap(w.pr.root)
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=w.pr.root, read_only=True)
    assert _snap(w.pr.root) == before
    assert "guard_mode_immutable" in (w.pr.root.with_name(w.pr.root.name + "_guard") / "refusals.jsonl").read_text(encoding="utf-8")


def test_external_text_import_checks_marker_before_section_filtering(tmp_path, monkeypatch):
    eng = Engram(root=tmp_path / "normal")
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text(REPLAY_EXPORT_MARKER + "\n## Rules\nUse bounded cache entries for repeated reads.\n", encoding="utf-8")
    result = eng.reconcile_ai_configs(search_roots=[str(project)])
    assert result["error"] == "replay_experience_import_refused"
    assert eng.get_lessons() == []


def test_planned_memory_writer_checks_original_before_reconstruction(tmp_path):
    from piia_engram.memory_import import write_items
    eng = Engram(root=tmp_path / "normal")
    result = write_items(eng, [{"summary": "cache observation", "detail": "sample",
                              "discarded": {"store_mode": MODE}}], sources=("memories",))
    assert result["error"] == "replay_experience_import_refused"
    assert eng.get_lessons() == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_reconcile_apply_checks_original_before_projection(tmp_path, dry_run):
    from piia_engram.reconcile_apply import apply_reconcile
    eng = Engram(root=tmp_path / "normal")
    result = apply_reconcile(eng, [{"summary": "cache observation", "detail": "sample",
                                  "discarded": {"store_mode": MODE}}], confirm=True, dry_run=dry_run)
    assert result["error"] == "replay_experience_import_refused"
    assert result["changed"] is False
    assert eng.get_lessons() == []


def test_native_export_marks_identity_and_domain_entries(tmp_path, monkeypatch):
    w = _world(tmp_path, monkeypatch)
    eng = w.pr._engram(read_only=False)
    eng.update_profile({"role": "sample"})
    eng.update_domain("example", {"project_count": 1})
    eng.add_lesson({"summary": "cache domain observation", "domain": "example", "tier": "verified"})
    payload = json.loads(Path(eng.export_all(str(tmp_path / "out.json"))).read_text(encoding="utf-8"))
    assert payload["identity"]["profile"]["store_mode"] == MODE
    assert payload["knowledge"]["domains"]["example"]["store_mode"] == MODE


@pytest.mark.parametrize("point", ["dp-live", "dp-replay", "dp-test"])
def test_production_nonempty_recall_matches_base_bytes(tmp_path, monkeypatch, point):
    w = _world(tmp_path, monkeypatch, mode="production")
    path = w.dps / f"{point}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["as_of_utc"] = LATER
    path.write_text(json.dumps(data), encoding="utf-8")
    receipt = w.pr.admit(_card("1", "Q2", EARLY), "R1", ADMIT)
    assert receipt["result"] == "admitted"
    args = dict(evidence_before=LATER, admitted_before=LATER, query="cache")
    expected = _base_function("isolated_store", "recall")(w.pr, point, "R2", **args)
    actual = w.pr.recall(point, "R2", **args)
    assert [row["id"] for row in actual["items"]] == [receipt["item_id"]]
    sealed = actual.pop("receipt")
    assert sealed == w.pr.receipts()[-1]
    assert sealed["returned_ids"] == [row["id"] for row in actual["items"]]
    assert _bytes(actual) == _bytes(expected)


def test_production_recall_digest_matches_base_bytes(tmp_path):
    from datetime import timezone
    from piia_engram.recall_service import gather_recall, render_recall_text
    eng = Engram(root=tmp_path / "normal")
    eng.add_lesson({"summary": "cache observation", "tier": "verified"})
    args = dict(query="cache", now=datetime(2020, 1, 1, tzinfo=timezone.utc))
    expected = _base_function("recall_service", "gather_recall")(eng, **args)
    actual = gather_recall(eng, **args)
    assert _bytes(actual) == _bytes(expected)
    assert render_recall_text(actual) == _base_function("recall_service", "render_recall_text")(expected)


def _base_function(module_name, name):
    """Execute the actual function frozen from 86547e88b with the same adapters."""
    module = importlib.import_module(f"piia_engram.{module_name}")
    path = Path(__file__).parent / "fixtures" / "replay_production_base" / f"{module_name}-{name}.txt"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize("point,query", [("dp-live", "cache"), ("dp-replay", "cache"), ("dp-test", ""), ("dp-test", "absent")])
def test_production_recall_matches_base_bytes(tmp_path, monkeypatch, point, query):
    w = _world(tmp_path, monkeypatch, mode="production")
    assert w.pr.admit(_card("1", "Q1", EARLY), "R1", ADMIT)["result"] == "admitted"
    args = dict(evidence_before=LATER, admitted_before=LATER, query=query)
    expected = _base_function("isolated_store", "recall")(w.pr, point, "R2", **args)
    actual = w.pr.recall(point, "R2", **args)
    sealed = actual.pop("receipt")
    assert sealed == w.pr.receipts()[-1]
    assert sealed["returned_ids"] == [row["id"] for row in actual["items"]]
    assert _bytes(actual) == _bytes(expected)


def test_production_receipt_and_reconcile_match_base_bytes(tmp_path, monkeypatch):
    from piia_engram import isolated_store
    w = _world(tmp_path, monkeypatch, mode="production")
    monkeypatch.setattr(isolated_store, "utc_now_z", lambda: "2020-01-01T00:00:00.000000Z")
    monkeypatch.setattr(w.pr, "receipts", lambda: [])
    record = {"op": "recall", "result": "recalled"}
    expected = _base_function("isolated_store", "_append")(w.pr, record)
    actual = w.pr._append(record)
    assert _bytes(actual) == _bytes(expected)
    monkeypatch.undo()
    # Use a new valid world; receipt checks above intentionally use an empty adapter.
    w = _world(tmp_path / "valid", monkeypatch, mode="production")
    assert _bytes(w.pr.reconcile()) == _bytes(_base_function("isolated_store", "reconcile")(w.pr))


@pytest.mark.parametrize("module,name", [
    ("compat", "hermes_handoff_payload"), ("reports_identity", "export_identity_card"),
    ("reports_analytics", "export_knowledge_report"), ("reports_review", "generate_review_page"),
])
def test_other_changed_production_read_paths_match_base_bytes(tmp_path, monkeypatch, module, name):
    eng = Engram(root=tmp_path / "normal")
    eng.add_lesson({"summary": "cache observation", "tier": "verified"})
    eng.add_decision({"question": "Which cache?", "choice": "Bounded cache", "tier": "verified"})
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2020, 1, 1, tzinfo=tz)
    mod = importlib.import_module(f"piia_engram.{module}")
    if hasattr(mod, "datetime"):
        monkeypatch.setattr(mod, "datetime", FrozenDateTime)
    expected = _base_function(module, name)(eng)
    actual = getattr(mod, name)(eng) if module == "compat" else getattr(eng, name)()
    assert _bytes(actual) == _bytes(expected)


def test_production_markdown_and_native_exports_match_base_bytes(tmp_path, monkeypatch):
    from piia_engram.agents_md_export import build_agents_md_export
    from piia_engram import import_export
    rows = [{"summary": "cache observation", "tier": "verified", "status": "active"}]
    assert build_agents_md_export(lessons=deepcopy(rows)) == _base_function("agents_md_export", "build_agents_md_export")(lessons=deepcopy(rows))
    eng = Engram(root=tmp_path / "normal")
    eng.add_lesson(rows[0])
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2020, 1, 1, tzinfo=tz)
    monkeypatch.setattr(import_export, "datetime", FrozenDateTime)
    monkeypatch.setattr(import_export, "_now_iso", lambda: "2020-01-01T00:00:00")
    path = tmp_path / "out.json"
    expected = _base_function("import_export", "export_all_with_summary")(eng, str(path))
    expected_bytes = path.read_bytes()
    actual = eng.export_all_with_summary(str(path))
    assert _bytes(actual) == _bytes(expected)
    assert path.read_bytes() == expected_bytes



@pytest.mark.parametrize("kind", ["lesson", "decision"])
@pytest.mark.parametrize("placement", ["text", "metadata", "mode", "nested"])
@pytest.mark.parametrize("archived", [False, True])
def test_ordinary_store_withholds_pasted_replay_row(tmp_path, monkeypatch, kind, placement, archived):
    root = tmp_path / "ordinary"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    eng = Engram(root=root)
    eng.add_lesson("Use deterministic fixtures for the clean sample", domain="testing")
    filename = "lessons.json" if kind == "lesson" else "decisions.json"
    path = root / "knowledge" / filename
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    row = {"id": "withheld-synthetic", "status": "active", "tier": "verified",
           "summary": "WITHHELD_CANARY", "detail": "WITHHELD_CANARY",
           "question": "WITHHELD_CANARY", "choice": "WITHHELD_CANARY",
           "reasoning": "WITHHELD_CANARY"}
    if placement == "text":
        row["detail"] += REPLAY_EXPORT_MARKER
    elif placement == "metadata":
        row["store_mode"] = MODE
    elif placement == "mode":
        row["mode"] = MODE
    else:
        row["provenance"] = {"origin": [{"store_mode": MODE}]}
    if archived:
        path = root / "knowledge" / "overflow_archive" / filename.replace(".json", ".jsonl")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    else:
        rows.append(row)
        path.write_text(json.dumps(rows), encoding="utf-8")
    for read_only in (True, False):
        eng = Engram(root=root, read_only=read_only)
        if not read_only:
            events = [json.loads(line) for line in (root / "audit.log").read_text(encoding="utf-8").splitlines()]
            assert any(event["action"] == "replay_withheld" for event in events)
        assert "WITHHELD_CANARY" not in json.dumps(eng.get_lessons(limit=None))
        assert "WITHHELD_CANARY" not in json.dumps(eng.get_decisions(limit=None))
        assert "WITHHELD_CANARY" not in json.dumps(eng.search_knowledge("WITHHELD_CANARY"))
        assert "WITHHELD_CANARY" not in eng.generate_context(level="full")
        assert "WITHHELD_CANARY" not in json.dumps(eng.get_knowledge_history(row["id"], include_bodies=True))
        assert eng.get_overflow_archived(kind, row["id"]) is None
        assert "WITHHELD_CANARY" not in json.dumps(eng._all_indexable_entries())
        assert "deterministic fixtures" in json.dumps(eng.get_lessons(limit=None))
        if not read_only:
            output = tmp_path / "backup.json"
            eng.export_all(str(output))
            assert "WITHHELD_CANARY" not in output.read_text(encoding="utf-8")
            eng.refresh_quick_context()
            for report in (eng.export_identity_card(), eng.export_knowledge_report()):
                assert "WITHHELD_CANARY" not in report
            assert "WITHHELD_CANARY" not in (root / "quick_context.md").read_text(encoding="utf-8")
            from piia_engram.compat import export_to_openclaw, hermes_handoff_payload
            directory = tmp_path / "openclaw"
            export_to_openclaw(eng, str(directory))
            for exported in directory.glob("*.md"):
                assert "WITHHELD_CANARY" not in exported.read_text(encoding="utf-8")
            assert "WITHHELD_CANARY" not in json.dumps(hermes_handoff_payload(eng))
            import asyncio
            from piia_engram import mcp_server as server
            monkeypatch.setattr(server, "_engram", eng)
            for response in (
                asyncio.run(server.get_lessons()), asyncio.run(server.get_decisions()),
                asyncio.run(server.search_knowledge("WITHHELD_CANARY")),
                asyncio.run(server.get_user_context()),
                asyncio.run(server.get_knowledge_history(row["id"], include_bodies=True)),
            ):
                assert "WITHHELD_CANARY" not in response
    # Neither withholding nor migrations normalize/rewrite the pasted row.
    persisted = ([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                 if archived else json.loads(path.read_text(encoding="utf-8")))
    assert next(r for r in persisted if r["id"] == row["id"]) == row
    from piia_engram import doctor
    finding = doctor.stored_replay_check(root)
    assert finding["withheld"] == {path.relative_to(root).as_posix(): 1}
    assert "archive or remove" in finding["hint"]
    assert "WITHHELD_CANARY" not in json.dumps(finding)
    audit = (root / "audit.log").read_text(encoding="utf-8")
    assert "replay_withheld" in audit
    assert "WITHHELD_CANARY" not in audit


@pytest.mark.parametrize("bad", [b"\xff\xfe", b"[{not json"])
def test_ordinary_undecodable_knowledge_is_diagnosable_without_rewrite(tmp_path, monkeypatch, bad):
    root = tmp_path / "ordinary"
    Engram(root=root)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    path = root / "knowledge" / "lessons.json"
    path.write_bytes(bad)
    for read_only in (True, False):
        with pytest.raises(Exception):
            Engram(root=root, read_only=read_only).get_lessons()
        assert path.read_bytes() == bad
    from piia_engram import doctor
    finding = doctor.stored_replay_check(root)
    assert finding["unreadable"][0]["file"] == "knowledge/lessons.json"
    assert finding["unreadable"][0]["error"] in {"UnicodeDecodeError", "JSONDecodeError"}
    assert "restore" in finding["hint"].lower()
    assert path.read_bytes() == bad



@pytest.mark.parametrize("bad", [None, b"\xff", b"{torn"])
def test_doctor_explains_withholding_and_decode_errors_read_only(tmp_path, monkeypatch, capsys, bad):
    from piia_engram import doctor

    root = tmp_path / "ordinary"
    Engram(root=root)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    path = root / "knowledge" / "lessons.json"
    if bad is None:
        path.write_text(json.dumps([{"id": "WITHHELD_CANARY", "store_mode": MODE,
                                     "summary": "WITHHELD_CANARY"}]), encoding="utf-8")
    else:
        path.write_bytes(bad)
    before = _snap(tmp_path)
    assert doctor._run_functional_checks() > 0
    text = capsys.readouterr().out
    assert "Local repair:" in text
    if bad is None:
        assert "archive or remove" in text
    else:
        assert "Knowledge file unreadable: knowledge/lessons.json" in text
    assert "WITHHELD_CANARY" not in text
    assert doctor.run_doctor_json() == (0 if bad is None else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["stored_replay"]["status"] == "WARN"
    assert "WITHHELD_CANARY" not in json.dumps(result)
    assert _snap(tmp_path) == before


def test_existing_index_drops_a_row_marked_after_open(tmp_path):
    import sqlite3

    eng = Engram(root=tmp_path / "ordinary")
    eng.add_lesson("WITHHELD_CANARY synthetic cache observation", source_tool="cli")
    assert eng.rebuild_index()["indexed"] == 1
    path = eng._knowledge_dir / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["detail"] = REPLAY_EXPORT_MARKER
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert "WITHHELD_CANARY" not in json.dumps(eng.search_knowledge("WITHHELD_CANARY"))
    assert eng.rebuild_index()["indexed"] == 0
    with sqlite3.connect(eng.root / "search_index.db") as connection:
        assert "WITHHELD_CANARY" not in "".join(connection.iterdump())


def test_unrelated_sibling_replay_receipts_do_not_isolate_ordinary_root(tmp_path, monkeypatch):
    w = _world(tmp_path / "source", monkeypatch)
    monkeypatch.delenv("PIIA_ISOLATED_STORE_CONFIG", raising=False)
    root = w.pr.root.with_name("ordinary")
    assert Engram(root=root)._store_mode == "production"


@pytest.mark.parametrize("read_only", [False, True])
def test_empty_replay_marker_file_still_blocks_ordinary_attachment(tmp_path, read_only):
    root = tmp_path / "ordinary"
    Engram(root=root)
    (root / "replay_experience.marker").write_text("", encoding="utf-8")
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=root, read_only=read_only)



@pytest.mark.parametrize("read_only", [False, True])
def test_bound_production_ledger_keeps_pasted_replay_root_isolated(tmp_path, monkeypatch, read_only):
    from piia_engram.isolated_store import CONFIG_ENV, LIMITS_FILE, MARKER

    w = _world(tmp_path, monkeypatch, mode="production")
    (w.pr.root / MARKER).unlink()
    (w.pr.root / LIMITS_FILE).unlink()
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    path = w.pr.root / "knowledge" / "lessons.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps([{"summary": "WITHHELD_CANARY", "store_mode": MODE}]), encoding="utf-8")
    before = _snap(w.pr.root)
    with pytest.raises(GuardRefused, match="guard_mode_immutable"):
        Engram(root=w.pr.root, read_only=read_only)
    assert _snap(w.pr.root) == before
