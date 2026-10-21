"""Repair reports untrusted generated targets and rebuilds only canonical files."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

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
    assert CANARY.casefold().encode("utf-8") in (eng.root / "search_index.db").read_bytes().lower()
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
        assert CANARY.casefold() not in str(db.execute("SELECT eid, doc FROM fts").fetchall()).casefold()
        assert db.execute("SELECT count(*) FROM fts").fetchone()[0] == 9
    assert CANARY.casefold().encode("utf-8") not in (eng.root / "search_index.db").read_bytes().lower()
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
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("{}", encoding="utf-8")
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
    for suffix in ("-wal", "-shm", "-journal"):
        (eng.root / ("search_index.db" + suffix)).write_bytes(CANARY.encode("utf-8"))
    _mark(path, rows)
    result = repair(eng.root, apply=True)
    for suffix in ("-wal", "-shm", "-journal"):
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


def test_hot_rollback_journal_cannot_restore_quarantined_index_content(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    eng.rebuild_index()
    child = (
        "import os,sqlite3,sys; db=sqlite3.connect(sys.argv[1]); "
        "db.execute('PRAGMA cache_size=1'); db.execute('BEGIN IMMEDIATE'); "
        "db.execute('UPDATE fts SET doc=?', ('INTERRUPTED ' * 10000,)); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", child, str(eng.root / "search_index.db")], check=True)
    journal = eng.root / "search_index.db-journal"
    assert journal.exists() and journal.read_bytes()[:8] != bytes(8)
    assert CANARY.casefold().encode("utf-8") in journal.read_bytes().lower()
    _mark(path, rows)
    result = repair(eng.root, apply=True)
    with closing(sqlite3.connect(eng.root / "search_index.db")) as db:
        indexed = db.execute("SELECT eid, doc FROM fts").fetchall()
    assert CANARY.casefold() not in str(indexed).casefold()
    assert "search_index.db-journal" in result["regenerated_artifacts"]


def test_apply_regenerates_canonical_files_when_no_new_rows_are_marked(tmp_path):
    eng, path, rows = _ordinary(tmp_path / "store")
    _mark(path, rows)
    assert repair(eng.root, apply=True)["changed"]
    quick = eng.root / "quick_context.md"
    quick.write_text("<!-- Engram quick_context snapshot -->\n" + CANARY, encoding="utf-8")
    assert repair(eng.root, apply=True)["changed"]
    assert CANARY not in quick.read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", ["reports/status.html", "reports/dashboard.html"])
def test_existing_fixed_metadata_derived_files_are_also_regenerated(tmp_path, monkeypatch, relative):
    eng, path, rows = _ordinary(tmp_path / "store")
    target = eng.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(CANARY, encoding="utf-8")
    _mark(path, rows)
    before = _snapshot(eng.root)
    from piia_engram import status_report

    def forbidden(*args, **kwargs):
        raise AssertionError("repair must not run a client probe or external-memory scan")

    monkeypatch.setattr(status_report, "_probe_mcp_entry", forbidden)
    monkeypatch.setattr(status_report, "_external_memory_summary", forbidden)
    result = repair(eng.root, apply=True)
    assert CANARY not in target.read_text(encoding="utf-8")
    assert relative in result["regenerated_artifacts"]
    allowed = {"knowledge/lessons.json", "audit.log", "quick_context.md", "exports/identity_card.md",
               "reports/status.html", "reports/dashboard.html", "search_index.db"}

    def sources(snapshot):
        return {name.replace("\\", "/"): value[0] for name, value in snapshot.items()
                if value[0] is not None and name.replace("\\", "/") not in allowed
                and not name.replace("\\", "/").startswith("recovery/")}

    assert sources(_snapshot(eng.root)) == sources(before)


@pytest.mark.parametrize("name", ["file_safety_ledger.jsonl", "shared_instructions.md",
                                  "schema_version.json", "approval_mode.json", "audit.log.1"])
def test_root_source_metadata_records_are_invalid_and_cannot_redirect_repair(tmp_path, name):
    eng, path, rows = _ordinary(tmp_path / "store")
    target = eng.root / name
    target.write_text("{}", encoding="utf-8")
    _record(eng.root, target)
    _mark(path, rows)
    before = target.read_bytes(), target.stat().st_mtime_ns
    result = repair(eng.root, apply=True)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert _entry(result, target)["status"] == "invalid"


@pytest.mark.parametrize("kind", ["status", "dashboard"])
def test_custom_metadata_outputs_record_hashes_without_gaining_repair_authority(tmp_path, monkeypatch, kind):
    eng, path, rows = _ordinary(tmp_path / "store")
    monkeypatch.setenv("ENGRAM_DIR", str(eng.root))
    target = tmp_path / (kind + ".html")
    if kind == "status":
        from piia_engram.status_report import build_status, write_status_html
        write_status_html(build_status(probe=False, root=eng.root), target)
    else:
        from piia_engram.cli_commands import _run_dashboard
        assert _run_dashboard(["--html", str(target)]) == 0
    record = json.loads((eng.root / MANIFEST).read_text(encoding="utf-8"))["targets"][0]
    assert record["path"] == str(target.resolve())
    assert record["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    before = target.read_bytes(), target.stat().st_mtime_ns
    _mark(path, rows)
    assert _entry(repair(eng.root, apply=True), target)["status"] == "stale_generated_file"
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
