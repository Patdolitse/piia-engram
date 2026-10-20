"""Repair reports untrusted generated targets and rebuilds only canonical files."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from piia_engram.core import Engram
from piia_engram.generated_artifacts import MANIFEST
from piia_engram.isolated_store import GuardRefused
from piia_engram.replay_row_repair import PENDING, repair


CANARY = "REVIEW3_QUARANTINED_CANARY"


def _ordinary(root):
    eng = Engram(root=root)
    eng.add_lesson({"summary": CANARY, "tier": "verified"}, source_tool="cli")
    path = root / "knowledge/lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["pinned"] = True
    rows.extend({"id": f"clean-{n}", "summary": f"clean observation {n}",
                 "status": "active", "tier": "verified"} for n in range(9))
    path.write_text(json.dumps(rows), encoding="utf-8")
    return eng, path, rows


def _mark(path, rows):
    rows[0]["store_mode"] = "replay_experience"
    path.write_text(json.dumps(rows), encoding="utf-8")


def _record(root, target, *, legacy=False):
    record = str(target) if legacy else {
        "path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "generated_at": "2026-01-01T00:00:00+00:00",
    }
    (root / MANIFEST).write_text(json.dumps({"version": 1 if legacy else 2,
                                            "targets": [record]}), encoding="utf-8")


def _entry(result, target):
    return next(item for item in result["generated_files"] if Path(item["target"]) == Path(target))


def _snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in root.rglob("*")}


@pytest.mark.parametrize("legacy", [True, False])
def test_unrelated_external_manifest_target_is_never_modified(tmp_path, legacy):
    eng, path, rows = _ordinary(tmp_path / "store")
    target = tmp_path / "unrelated.txt"
    target.write_text("user-owned unrelated content", encoding="utf-8")
    _record(eng.root, target, legacy=legacy)
    _mark(path, rows)
    before = target.read_bytes(), target.stat().st_mtime_ns
    preview = repair(eng.root)
    result = repair(eng.root, apply=True)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    for report in (preview, result):
        assert _entry(report, target)["status"] == ("not_verified" if legacy else "stale_generated_file")
    Engram(root=eng.root).purge_search_index()
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


@pytest.mark.parametrize("area,name", [
    ("knowledge", "decisions.json"), ("identity", "profile.json"),
    ("playbooks", "source.md"), ("backups", "source.json"),
    ("ledgers", "receipts.jsonl"), ("recovery", "source.json"),
])
def test_manifest_cannot_authorize_source_changes_including_case_aliases(tmp_path, area, name):
    eng, path, rows = _ordinary(tmp_path / "store")
    target = eng.root / area / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('[{"id":"clean-decision"}]' if area == "knowledge" else "{}", encoding="utf-8")
    _record(eng.root, target, legacy=True)
    data = json.loads((eng.root / MANIFEST).read_text(encoding="utf-8"))
    alias = eng.root / (area.upper() if os.name == "nt" else area) / name
    data["targets"][0] = str(alias).replace("\\", "/")
    (eng.root / MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    _mark(path, rows)
    before = target.read_bytes(), target.stat().st_mtime_ns
    result = repair(eng.root, apply=True)
    entry = _entry(result, Path(data["targets"][0]))
    assert entry["status"] == "invalid"
    assert entry["reason"] == "reserved_store_source"
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_hidden_legacy_snapshot_is_reported_and_preserved(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    target = eng.root / ".cold-start.md"
    eng.refresh_quick_context(target=target)
    (eng.root / MANIFEST).unlink()
    assert CANARY in target.read_text(encoding="utf-8")
    _mark(path, rows)
    before = target.read_bytes(), target.stat().st_mtime_ns
    for result in (repair(eng.root), repair(eng.root, apply=True)):
        entry = _entry(result, target)
        assert entry["status"] == "stale_generated_file" and entry["verification"] == "legacy_header"
        assert entry["message"] == "stale generated file - regenerate or delete it yourself"
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_canonical_derived_files_are_rebuilt_clean_before_reopening(tmp_path, monkeypatch):
    from piia_engram.search_index import SearchIndex

    eng, path, rows = _ordinary(tmp_path / "store")
    quick = eng.refresh_quick_context()
    card = eng.root / "exports/identity_card.md"
    eng.export_identity_card()
    eng.rebuild_index()
    assert CANARY in quick.read_text(encoding="utf-8")
    assert CANARY in card.read_text(encoding="utf-8")
    _mark(path, rows)
    rebuild = SearchIndex.rebuild
    observed = []

    def checked(self, entries, *args, **kwargs):
        assert (eng.root / PENDING).exists()
        with pytest.raises(GuardRefused):
            Engram(root=eng.root, read_only=True)
        assert CANARY not in json.dumps(entries)
        observed.append(True)
        return rebuild(self, entries, *args, **kwargs)

    monkeypatch.setattr(SearchIndex, "rebuild", checked)
    result = repair(eng.root, apply=True)
    assert observed and not (eng.root / PENDING).exists()
    assert set(result["regenerated_artifacts"]) >= {"quick_context.md", "search_index.db", "exports/identity_card.md"}
    assert CANARY not in quick.read_text(encoding="utf-8")
    assert CANARY not in card.read_text(encoding="utf-8")
    with sqlite3.connect(eng.root / "search_index.db") as db:
        assert CANARY not in str(db.execute("SELECT eid, doc FROM fts").fetchall())
        assert db.execute("SELECT count(*) FROM fts").fetchone()[0] == 9
    assert CANARY.encode("utf-8") not in (eng.root / "search_index.db").read_bytes()
    assert len(Engram(root=eng.root, read_only=True).get_lessons()) == 9


def test_regeneration_failure_keeps_store_closed_until_retry(tmp_path, monkeypatch):
    from piia_engram.search_index import SearchIndex

    eng, path, rows = _ordinary(tmp_path / "store")
    _mark(path, rows)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise OSError("synthetic canonical rebuild failure")
        patch.setattr(SearchIndex, "rebuild", fail)
        with pytest.raises(GuardRefused, match="repair_artifact_regeneration_failed"):
            repair(eng.root, apply=True)
    assert (eng.root / PENDING).exists()
    with pytest.raises(GuardRefused):
        Engram(root=eng.root, read_only=True)
    assert repair(eng.root, apply=True)["changed"]
    assert not (eng.root / PENDING).exists()
    assert len(Engram(root=eng.root, read_only=True).get_lessons()) == 9


def test_another_writer_editing_record_during_repair_cannot_redirect_writes(tmp_path, monkeypatch):
    from piia_engram import replay_row_repair as module

    eng, path, rows = _ordinary(tmp_path / "store")
    target = tmp_path / "other-process-user.txt"
    target.write_text("keep this unrelated file", encoding="utf-8")
    _mark(path, rows)
    write_new = module._write_new

    def concurrent(destination, data):
        if destination == eng.root / PENDING:
            _record(eng.root, target)
        return write_new(destination, data)

    monkeypatch.setattr(module, "_write_new", concurrent)
    before = target.read_bytes(), target.stat().st_mtime_ns
    assert repair(eng.root, apply=True)["changed"]
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_hash_mismatch_and_missing_targets_are_unverified_and_dry_run_writes_nothing(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    changed = tmp_path / "changed.txt"
    missing = tmp_path / "missing.txt"
    for target in (changed, missing):
        eng.refresh_quick_context(target=target)
    changed.write_text("user revision", encoding="utf-8")
    missing.unlink()
    _mark(path, rows)
    before = _snapshot(tmp_path)
    preview = repair(eng.root)
    assert _snapshot(tmp_path) == before
    assert _entry(preview, changed)["status"] == "not_verified"
    assert _entry(preview, missing)["status"] == "not_verified"
    result = repair(eng.root, apply=True)
    assert _entry(result, changed)["status"] == "not_verified"
    assert changed.read_text(encoding="utf-8") == "user revision" and not missing.exists()


def test_successful_publication_records_exact_bytes_hash_and_timestamp(tmp_path):
    from datetime import datetime
    from piia_engram.generated_artifacts import write_generated_text

    eng, _, _ = _ordinary(tmp_path / "store")
    target = tmp_path / "snapshot.txt"
    write_generated_text(eng.root, target, "合成文本\nsecond line\n")
    data = json.loads((eng.root / MANIFEST).read_text(encoding="utf-8"))
    assert data["version"] == 2 and len(data["targets"]) == 1
    entry = data["targets"][0]
    assert set(entry) == {"path", "sha256", "generated_at"}
    assert entry["path"] == str(target.resolve())
    assert entry["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert datetime.fromisoformat(entry["generated_at"]).tzinfo is not None


def test_failed_publication_does_not_register_or_destroy_preexisting_user_file(tmp_path, monkeypatch):
    from piia_engram import generated_artifacts as module

    eng, path, rows = _ordinary(tmp_path / "store")
    target = tmp_path / "preexisting.txt"
    target.write_text("user's preexisting file", encoding="utf-8")
    before = target.read_bytes(), target.stat().st_mtime_ns
    write = module.atomic_write_text

    def fail(candidate, content):
        if candidate == target:
            raise OSError("synthetic publication failure")
        return write(candidate, content)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write_text", fail)
        with pytest.raises(OSError):
            eng.refresh_quick_context(target=target)
    assert target not in module.recorded_targets(eng.root)
    _mark(path, rows)
    assert repair(eng.root, apply=True)["changed"]
    Engram(root=eng.root).purge_search_index()
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_purge_does_not_consume_corrupt_or_reused_target_records(tmp_path):
    eng, _, _ = _ordinary(tmp_path / "store")
    target = tmp_path / "reused.txt"
    eng.refresh_quick_context(target=target)
    target.write_text("a new user document at the old pathname", encoding="utf-8")
    before = target.read_bytes(), target.stat().st_mtime_ns
    eng.purge_search_index()
    (eng.root / MANIFEST).write_text("{torn", encoding="utf-8")
    eng.purge_search_index()
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_record_alias_resolves_to_reserved_source_and_is_only_reported(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    source = eng.root / "identity/profile.json"
    alias = tmp_path / "source-alias.json"
    try:
        alias.symlink_to(source)
    except OSError:
        pytest.skip("this filesystem does not permit symlink fixtures")
    _record(eng.root, alias)
    _mark(path, rows)
    before = source.read_bytes(), source.stat().st_mtime_ns
    assert _entry(repair(eng.root, apply=True), alias)["status"] == "invalid"
    assert alias.is_symlink()
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_canonical_symlink_is_refused_without_following_it(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    target = tmp_path / "user.txt"
    target.write_text("do not replace this", encoding="utf-8")
    canonical = eng.root / "quick_context.md"
    try:
        canonical.symlink_to(target)
    except OSError:
        pytest.skip("this filesystem does not permit symlink fixtures")
    _mark(path, rows)
    before = _snapshot(tmp_path)
    with pytest.raises(GuardRefused, match="repair_link_refused"):
        repair(eng.root, apply=True)
    assert _snapshot(tmp_path) == before


def test_existing_canonical_sidecars_are_regenerated_without_old_content(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    for suffix in ("-wal", "-shm"):
        (eng.root / ("search_index.db" + suffix)).write_bytes(CANARY.encode("utf-8"))
    _mark(path, rows)
    result = repair(eng.root, apply=True)
    for suffix in ("-wal", "-shm"):
        relative = "search_index.db" + suffix
        assert relative in result["regenerated_artifacts"]
        assert (eng.root / relative).read_bytes() == b""


def test_legacy_path_record_with_local_generated_header_is_reported(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    target = eng.root / ".legacy.txt"
    eng.refresh_quick_context(target=target)
    _record(eng.root, target, legacy=True)
    _mark(path, rows)
    entry = _entry(repair(eng.root), target)
    assert entry["status"] == "stale_generated_file" and entry["verification"] == "legacy_header"


def test_legacy_default_exports_need_generated_headers_and_are_only_reported(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    eng.export_knowledge_report()
    review = eng.export_review_page()
    report = next((eng.root / "exports").glob("knowledge_report_*.md"))
    (eng.root / MANIFEST).unlink()
    unrelated = eng.root / "exports/review_user.html"
    unrelated.write_text("<!DOCTYPE html><title>User content</title>", encoding="utf-8")
    _mark(path, rows)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (review, report, unrelated)}
    result = repair(eng.root, apply=True)
    for target in (review, report):
        assert _entry(result, target)["verification"] == "legacy_header"
    assert not any(item["target"] == str(unrelated) for item in result["generated_files"])
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before
