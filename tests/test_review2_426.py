"""Regression witnesses for conservative repair and physical version activity."""

import json
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.isolated_store import (
    CONFIG_ENV, LIMITS_FILE, MARKER, REPLAY_MARKER_FILE, Config,
    GuardRefused, IsolatedStore, init_root,
)
from piia_engram.isolated_store_launch import build_child_env
from piia_engram.replay_row_repair import repair
from test_isolated_store import RESTORE, RETIRE, _make_world, _snap
from test_isolated_supersede import admit
from test_replay_experience import _admit, _world


def test_nested_external_ledger_must_refuse_repair(tmp_path, monkeypatch):
    w = _make_world(tmp_path, monkeypatch, init=False)
    w.data.update(mode="replay_experience", receipts_dir=str(w.area / "ledgers" / "custom"))
    w.cfg_path.write_text(json.dumps(w.data), encoding="utf-8")
    w.cfg = Config.load(w.cfg_path)
    for key, value in build_child_env({}, w.cfg).items():
        monkeypatch.setenv(key, value)
    w.pr = init_root(w.cfg)
    assert _admit(w)["result"] == "admitted"
    for name in (MARKER, LIMITS_FILE, REPLAY_MARKER_FILE):
        (w.pr.root / name).unlink()
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    with pytest.raises(GuardRefused):
        Engram(root=w.pr.root, read_only=True)
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="repair_replay_dominant"):
        repair(w.pr.root, apply=True)
    assert _snap(w.area) == before
    with pytest.raises(GuardRefused):
        Engram(root=w.pr.root, read_only=True)


def test_all_store_local_snapshot_extensions_are_reported(tmp_path):
    root = tmp_path / "ordinary"
    eng = Engram(root=root)
    eng.add_lesson("CUSTOM_SNAPSHOT_CANARY synthetic fixture", source_tool="cli")
    path = root / "cold-start.txt"
    eng.refresh_quick_context(target=path)
    assert "CUSTOM_SNAPSHOT_CANARY" in path.read_text(encoding="utf-8")
    knowledge = root / "knowledge" / "lessons.json"
    rows = json.loads(knowledge.read_text(encoding="utf-8"))
    rows[0]["store_mode"] = "replay_experience"
    # Eligible ordinary store: only one of ten rows is accidentally marked.
    rows.extend({"id": f"clean-{n}", "summary": "Synthetic clean fixture"} for n in range(9))
    knowledge.write_text(json.dumps(rows), encoding="utf-8")
    result = repair(root, apply=True)
    assert result["changed"]
    assert len(Engram(root=root, read_only=True).get_lessons()) == 9
    assert path.exists() and "CUSTOM_SNAPSHOT_CANARY" in path.read_text(encoding="utf-8")
    assert any(item["target"] == str(path) and item["status"] == "stale_generated_file"
               for item in result["generated_files"])


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_crashed_restore_does_not_allow_two_active_versions(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    old = admit(w, 1)
    new = admit(w, 2, supersedes=old["item_id"])
    assert w.pr.retire(new["item_id"], "round", RETIRE)["result"] == "retired"
    original_append = w.pr._append

    def crash(record):
        if record.get("op") == "restore" and record.get("result") == "restored":
            raise SystemExit("crash after physical restore before receipt")
        return original_append(record)

    with monkeypatch.context() as patch:
        patch.setattr(w.pr, "_append", crash)
        with pytest.raises(SystemExit):
            w.pr.restore(old["item_id"], "round", RESTORE)
    w.pr = IsolatedStore.open(w.cfg)
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="supersede_state_mismatch"):
        w.pr.restore(new["item_id"], "round", RESTORE)
    assert _snap(w.area) == before
    active = [r["id"] for r in w.pr._rows(w.pr._engram(read_only=True))
              if r.get("status") == "active"]
    assert active == [old["item_id"]]
    assert any(p.startswith("supersede_state_mismatch:") for p in w.pr.reconcile()["problems"])


def _ordinary(root, total=10):
    eng = Engram(root=root)
    eng.add_lesson("GENERATED_ARTIFACT_CANARY synthetic observation", source_tool="cli")
    path = root / "knowledge/lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows.extend({"id": f"extra-{n}", "status": "retired"} for n in range(total - 1))
    path.write_text(json.dumps(rows), encoding="utf-8")
    return eng, path, rows


def _mark(path, rows, count=1):
    for row in rows[:count]:
        row["store_mode"] = "replay_experience"
    path.write_text(json.dumps(rows), encoding="utf-8")


@pytest.mark.parametrize("marked,total,refused", [
    (1, 1, True), (5, 49, True), (6, 100, True), (1, 10, False), (5, 50, False),
])
def test_repair_minority_limits_are_conjunctive_and_dry_run_is_zero_write(tmp_path, marked, total, refused):
    eng, path, rows = _ordinary(tmp_path / "ordinary", total=total)
    _mark(path, rows, marked)
    before = _snap(tmp_path)
    preview = repair(eng.root)
    assert preview["total_rows"] == total and preview["marked_rows"] == marked
    assert preview["would_refuse"] == ("repair_replay_dominant" if refused else None)
    assert _snap(tmp_path) == before
    if refused:
        with pytest.raises(GuardRefused, match="repair_replay_dominant.*doctor.*metadata.*backup"):
            repair(eng.root, apply=True)
        assert _snap(tmp_path) == before
    else:
        assert repair(eng.root, apply=True)["quarantined"] == marked


def test_refused_cli_reports_counts_rules_and_backup_guidance_without_writes(tmp_path, monkeypatch, capsys):
    from piia_engram.isolated_store import stored_replay_findings
    from piia_engram.replay_row_repair import run_cli

    eng, path, rows = _ordinary(tmp_path / "ordinary", total=1)
    _mark(path, rows)
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    before = _snap(tmp_path)
    assert run_cli(["replay-rows", "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["total_rows"] == preview["marked_rows"] == 1
    assert preview["refusal_rules"] == ["marked_rows_over_10_percent"]
    assert run_cli(["replay-rows", "--apply"]) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["error"] == "repair_replay_dominant"
    assert "doctor" in refusal["hint"] and "metadata" in refusal["hint"]
    assert "repair_replay_dominant" in stored_replay_findings(eng.root)["hint"]
    assert _snap(tmp_path) == before


def test_minority_counts_include_both_archives_and_decisions(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "ordinary", total=1)
    _mark(path, rows)
    decisions = eng.root / "knowledge/decisions.json"
    decisions.write_text(json.dumps([{"id": "clean-decision"}]), encoding="utf-8")
    archive = eng.root / "knowledge/overflow_archive"
    archive.mkdir(exist_ok=True)
    for name in ("lessons.jsonl", "decisions.jsonl"):
        (archive / name).write_text("\n".join(json.dumps({"id": f"clean-{n}"}) for n in range(4)),
                                   encoding="utf-8")
    preview = repair(eng.root)
    assert preview["total_rows"] == 10 and len(preview["knowledge_rows"]) == 4
    assert repair(eng.root, apply=True)["quarantined"] == 1


@pytest.mark.parametrize("outside", [False, True])
@pytest.mark.parametrize("suffix", [".txt", ".json", ".snapshot", ""])
def test_registered_generated_targets_are_reported_without_extension_filter(tmp_path, outside, suffix):
    import hashlib
    from piia_engram.generated_artifacts import MANIFEST

    eng, path, rows = _ordinary(tmp_path / "ordinary")
    target = (tmp_path if outside else eng.root) / ("cold-start" + suffix)
    eng.refresh_quick_context(target=target)
    manifest = json.loads((eng.root / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["version"] == 2 and len(manifest["targets"]) == 1
    record = manifest["targets"][0]
    assert set(record) == {"path", "sha256", "generated_at"}
    assert record["path"] == str(target.resolve())
    assert record["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert record["generated_at"]
    assert "GENERATED_ARTIFACT_CANARY" in target.read_text(encoding="utf-8")
    _mark(path, rows)
    result = repair(eng.root, apply=True)
    assert target.exists()
    assert any(item["target"] == str(target) and item["status"] == "stale_generated_file"
               for item in result["generated_files"])


def test_legacy_unregistered_store_local_text_snapshot_is_also_reported(tmp_path):
    from piia_engram.generated_artifacts import MANIFEST

    eng, path, rows = _ordinary(tmp_path / "ordinary")
    target = eng.root / "legacy-cold-start.txt"
    eng.refresh_quick_context(target=target)
    (eng.root / MANIFEST).unlink()  # an artifact generated before manifests existed
    _mark(path, rows)
    result = repair(eng.root, apply=True)
    assert result["changed"] and target.exists()
    assert any(item["target"] == str(target) and item["verification"] == "legacy_header"
               for item in result["generated_files"])


def test_external_unreadable_artifact_is_reported_without_hiding_other_targets(tmp_path, monkeypatch):
    eng, path, rows = _ordinary(tmp_path / "ordinary")
    external = tmp_path / "external.txt"
    local = eng.root / "another.snapshot"
    for target in (external, local):
        eng.refresh_quick_context(target=target)
    _mark(path, rows)
    open_file = Path.open

    def deny(target, *args, **kwargs):
        if target == external:
            raise PermissionError("synthetic unreadable target")
        return open_file(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    result = repair(eng.root, apply=True)
    assert result["changed"] and local.exists() and external.exists()
    assert any(item["target"] == str(external) and item["status"] == "not_verified"
               and item["reason"] == "PermissionError" for item in result["generated_files"])
    assert any(item["target"] == str(local) and item["status"] == "stale_generated_file"
               for item in result["generated_files"])


def test_local_custom_artifact_is_left_untouched_without_blocking_repair(tmp_path, monkeypatch):
    eng, path, rows = _ordinary(tmp_path / "ordinary")
    target = eng.root / "locked.snapshot"
    eng.refresh_quick_context(target=target)
    _mark(path, rows)
    original = target.read_bytes()
    unlink = Path.unlink

    def deny(candidate, *args, **kwargs):
        if candidate == target:
            raise PermissionError("synthetic unwritable target")
        return unlink(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny)
        result = repair(eng.root, apply=True)
    assert result["changed"] and target.read_bytes() == original
    assert len(Engram(root=eng.root, read_only=True).get_lessons()) == 0
    assert any(item["target"] == str(target) and item["status"] == "stale_generated_file"
               for item in result["generated_files"])


def test_index_purge_leaves_registered_artifacts_untouched(tmp_path):
    eng, _, _ = _ordinary(tmp_path / "ordinary")
    targets = [eng.root / "local.txt", tmp_path / "external.snapshot"]
    for target in targets:
        eng.refresh_quick_context(target=target)
    before = {target: target.read_bytes() for target in targets}
    eng.purge_search_index()
    assert {target: target.read_bytes() for target in targets} == before


def test_generators_register_external_model_facing_exports(tmp_path, monkeypatch):
    from piia_engram.compat import export_to_openclaw
    from piia_engram.context_preview import write_context_preview_html
    from piia_engram.generated_artifacts import recorded_targets

    eng, path, rows = _ordinary(tmp_path / "ordinary")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    export_to_openclaw(eng, str(tmp_path / "exports"))
    preview = write_context_preview_html({}, eng.root, tmp_path / "preview.snapshot")
    targets = [preview, *(tmp_path / "exports" / name for name in ("SOUL.md", "USER.md", "MEMORY.md"))]
    assert set(targets).issubset(set(recorded_targets(eng.root)))
    _mark(path, rows)
    result = repair(eng.root, apply=True)
    assert result["changed"] and all(target.exists() for target in targets)
    assert set(targets).issubset({Path(item["target"]) for item in result["generated_files"]})


def test_failed_publication_never_registers_unwritten_content(tmp_path, monkeypatch):
    from piia_engram import generated_artifacts as generated

    eng, _, _ = _ordinary(tmp_path / "ordinary")
    target = tmp_path / "external.txt"
    write = generated.atomic_write_text

    def crash(path, text):
        if path == target:
            raise SystemExit("synthetic interrupted publication")
        return write(path, text)

    monkeypatch.setattr(generated, "atomic_write_text", crash)
    with pytest.raises(SystemExit):
        eng.refresh_quick_context(target=target)
    assert target not in generated.recorded_targets(eng.root)
    assert not target.exists()


def test_corrupt_informational_record_is_reported_without_blocking_repair(tmp_path):
    from piia_engram.generated_artifacts import MANIFEST

    eng, path, rows = _ordinary(tmp_path / "ordinary")
    _mark(path, rows)
    (eng.root / MANIFEST).write_text("{torn", encoding="utf-8")
    result = repair(eng.root, apply=True)
    assert result["changed"]
    assert result["generated_files"] == [{"target": str(eng.root / MANIFEST),
                                         "status": "invalid", "reason": "JSONDecodeError",
                                         "message": "not verified, left untouched"}]


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
@pytest.mark.parametrize("activation", ["restore", "supersede"])
def test_all_physical_chain_members_are_checked_before_activation(tmp_path, monkeypatch, mode, activation):
    w = _world(tmp_path, monkeypatch, mode=mode)
    first = admit(w, 1)
    second = admit(w, 2, supersedes=first["item_id"])
    third = admit(w, 3, supersedes=second["item_id"])
    # Simulate pre-existing drift in a non-adjacent ancestor of the target.
    eng = w.pr._engram(read_only=False)
    eng.update_lesson(first["item_id"], {"status": "active"})
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="supersede_state_mismatch"):
        if activation == "restore":
            w.pr.restore(second["item_id"], "round", RESTORE)
        else:
            admit(w, 4, supersedes=third["item_id"])
    assert _snap(w.area) == before
    assert any(p.startswith("supersede_state_mismatch:") for p in w.pr.reconcile()["problems"])


@pytest.mark.parametrize("mode", ["production", "replay_experience"])
def test_crashed_retire_also_refuses_conflicting_restore(tmp_path, monkeypatch, mode):
    w = _world(tmp_path, monkeypatch, mode=mode)
    first = admit(w, 1)
    second = admit(w, 2, supersedes=first["item_id"])
    append = w.pr._append

    def crash(record):
        if record.get("op") == "retire" and record.get("result") == "retired":
            raise SystemExit("crash after physical retire before receipt")
        return append(record)

    with monkeypatch.context() as patch:
        patch.setattr(w.pr, "_append", crash)
        with pytest.raises(SystemExit):
            w.pr.retire(second["item_id"], "round", RETIRE)
    w.pr = IsolatedStore.open(w.cfg)
    before = _snap(w.area)
    with pytest.raises(GuardRefused, match="supersede_state_mismatch"):
        w.pr.restore(first["item_id"], "round", RESTORE)
    assert _snap(w.area) == before
