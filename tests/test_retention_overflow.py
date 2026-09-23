"""Knowledge cap overflow: rows pushed out by the per-type cap are archived, never dropped silently."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from piia_engram import Engram
from piia_engram import core as core_mod
from piia_engram.storage import MAX_KNOWLEDGE_ENTRIES, _read_json

KINDS = ("lesson", "decision")
OVERFLOW_FIELDS = ("overflow_archived_at", "overflow_archive_reason")


def _words(i: int, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}-{i}".encode()).hexdigest()
    return " ".join("w" + digest[k:k + 7] for k in range(0, 56, 7))


def _row(kind: str, i: int, salt: str, **extra) -> dict:
    if kind == "lesson":
        row = {"summary": _words(i, salt), "detail": "detail " + _words(i, salt + "-d")}
    else:
        row = {"question": _words(i, salt), "choice": _words(i, salt + "-c"),
               "reasoning": "reasoning " + _words(i, salt + "-r")}
    row.update(extra)
    return row


def _add(engram: Engram, kind: str, row: dict, **kwargs) -> dict:
    if kind == "lesson":
        return engram.add_lesson(row, domain="cap-test", **kwargs)
    return engram.add_decision(row, **kwargs)


def _active_path(root: Path, kind: str) -> Path:
    return root / "knowledge" / f"{kind}s.json"


def _archive_path(root: Path, kind: str) -> Path:
    return root / "knowledge" / "overflow_archive" / f"{kind}s.jsonl"


def _active_ids(root: Path, kind: str) -> list[str]:
    path = _active_path(root, kind)
    return [r.get("id") for r in _read_json(path)] if path.is_file() else []


def _archive_lines(root: Path, kind: str) -> list[dict]:
    path = _archive_path(root, kind)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _archived_ids(root: Path, kind: str) -> list[str]:
    return [row.get("id") for row in _archive_lines(root, kind)]


@pytest.fixture(scope="module")
def _full_stores(tmp_path_factory):
    """One store per kind whose active file holds MAX_KNOWLEDGE_ENTRIES verified rows."""
    base = tmp_path_factory.mktemp("full-stores")
    stores = {}
    for kind in KINDS:
        root = base / kind
        engram = Engram(root=root)
        ids = [_add(engram, kind, _row(kind, i, "S", tier="verified"))["id"] for i in range(MAX_KNOWLEDGE_ENTRIES)]
        stores[kind] = (root, ids)
    return stores


def _copy_store(full_stores, kind: str, tmp_path: Path, monkeypatch) -> tuple[Path, list[str], Engram]:
    source, seeded_ids = full_stores[kind]
    root = tmp_path / kind
    shutil.copytree(source, root)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return root, list(seeded_ids), Engram(root=root)


# -- no silent loss on a full store -------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_verified_write_into_full_store_keeps_the_oldest_row(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    new = _add(engram, kind, _row(kind, 900, "N", tier="verified"))
    active = _active_ids(root, kind)
    assert new["id"] in active
    assert len(active) == MAX_KNOWLEDGE_ENTRIES
    assert seeded[0] in active or seeded[0] in _archived_ids(root, kind)


@pytest.mark.parametrize("kind", KINDS)
def test_strict_write_into_full_store_keeps_the_new_row(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    new = _add(engram, kind, _row(kind, 901, "N"))
    assert new["tier"] == "staging"
    active = _active_ids(root, kind)
    assert new["id"] in active
    assert seeded[0] in _archived_ids(root, kind)
    assert len(active) == MAX_KNOWLEDGE_ENTRIES


@pytest.mark.parametrize("kind", KINDS)
def test_consecutive_overflows_keep_every_archived_row(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    for i in range(3):
        _add(engram, kind, _row(kind, 910 + i, "N", tier="verified"))
    assert _archived_ids(root, kind) == seeded[:3]
    for archived_id in seeded[:3]:
        assert engram.get_overflow_archived(kind, archived_id) is not None


def test_archive_is_never_trimmed_to_the_cap(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    earlier = [dict(_row("lesson", i, "OLD"), id=f"earlier-{i:03d}", tier="verified")
               for i in range(MAX_KNOWLEDGE_ENTRIES)]
    engram._archive_overflow_rows("lesson", earlier)
    _add(engram, "lesson", _row("lesson", 920, "N", tier="verified"))
    ids = _archived_ids(root, "lesson")
    assert len(ids) == MAX_KNOWLEDGE_ENTRIES + 1
    assert ids[0] == "earlier-000" and ids[-1] == seeded[0]


def test_a_store_already_over_the_cap_is_brought_back_to_it(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    rows = _read_json(_active_path(root, "lesson"))
    extra = [dict(rows[-1], id=f"extra-{i}", summary=_words(i, "EXTRA")) for i in range(3)]
    _active_path(root, "lesson").write_text(json.dumps(rows + extra), encoding="utf-8")
    new = _add(engram, "lesson", _row("lesson", 921, "N", tier="verified"))
    assert len(_active_ids(root, "lesson")) == MAX_KNOWLEDGE_ENTRIES
    assert new["overflow_archived_ids"] == seeded[:4]
    assert _archived_ids(root, "lesson") == seeded[:4]


@pytest.mark.parametrize("kind", KINDS)
def test_archived_row_is_the_stored_row_plus_the_stamp(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    before = next(r for r in _read_json(_active_path(root, kind)) if r["id"] == seeded[0])
    _add(engram, kind, _row(kind, 922, "N", tier="verified"))
    archived = engram.get_overflow_archived(kind, seeded[0])
    assert archived["overflow_archive_reason"] == "capacity_overflow"
    assert archived["overflow_archived_at"]
    assert {k: v for k, v in archived.items() if k not in OVERFLOW_FIELDS} == before


# -- imports over the cap -----------------------------------------------------------


def _backup_file(tmp_path: Path, kind: str, rows: list[dict]) -> Path:
    path = tmp_path / f"backup-{kind}.json"
    path.write_text(json.dumps({"schema_version": "1.0", "knowledge": {f"{kind}s": rows}}, ensure_ascii=False),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", KINDS)
def test_merge_import_over_the_cap_archives_pushed_out_rows(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    incoming = [dict(_row(kind, i, "IMP", tier="verified"), id=f"imported-{i:03d}") for i in range(5)]
    result = engram.import_all(str(_backup_file(tmp_path, kind, incoming)), merge=True)
    assert len(_active_ids(root, kind)) == MAX_KNOWLEDGE_ENTRIES
    assert _archived_ids(root, kind) == seeded[:5]
    assert f"{kind}s(+5, archived 5)" in result["imported"]


@pytest.mark.parametrize("kind", KINDS)
def test_replace_import_over_the_cap_archives_the_extra_rows(tmp_path, monkeypatch, kind):
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    engram = Engram(root=root)
    incoming = [dict(_row(kind, i, "REP", tier="verified"), id=f"imported-{i:03d}")
                for i in range(MAX_KNOWLEDGE_ENTRIES + 5)]
    result = engram.import_all(str(_backup_file(tmp_path, kind, incoming)), merge=False)
    assert _archived_ids(root, kind) == [f"imported-{i:03d}" for i in range(5)]
    assert len(_active_ids(root, kind)) == MAX_KNOWLEDGE_ENTRIES
    assert f"{kind}s({MAX_KNOWLEDGE_ENTRIES}, archived 5)" in result["imported"]


def test_import_archives_rows_without_ids_under_stable_ids(tmp_path, monkeypatch):
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    engram = Engram(root=root)
    incoming = [_row("lesson", i, "NOID", tier="verified") for i in range(MAX_KNOWLEDGE_ENTRIES + 2)]
    engram.import_all(str(_backup_file(tmp_path, "lesson", incoming)), merge=False)
    ids = _archived_ids(root, "lesson")
    assert len(ids) == 2 and all(ids)
    assert engram.get_overflow_archived("lesson", ids[0])["summary"] == _words(0, "NOID")


# -- explicit result, read-back and reachability ------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_overflow_result_names_the_archived_rows(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    new = _add(engram, kind, _row(kind, 903, "N", tier="verified"))
    assert new["overflow_archived_ids"] == [seeded[0]]
    stored = next(r for r in _read_json(_active_path(root, kind)) if r["id"] == new["id"])
    assert "overflow_archived_ids" not in stored
    assert engram.get_overflow_archived(kind, "no-such-id") is None
    other = "decision" if kind == "lesson" else "lesson"
    assert engram.get_overflow_archived(other, seeded[0]) is None


def test_get_overflow_archived_rejects_unknown_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    engram = Engram(root=tmp_path / "store")
    with pytest.raises(ValueError):
        engram.get_overflow_archived("playbook", "x")


def test_below_the_cap_nothing_changes(tmp_path, monkeypatch):
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    engram = Engram(root=root)
    for kind in KINDS:
        result = _add(engram, kind, _row(kind, 1, "B", tier="verified"))
        assert "overflow_archived_ids" not in result
    assert not (root / "knowledge" / "overflow_archive").exists()


@pytest.mark.parametrize("kind", KINDS)
def test_archived_rows_leave_regular_reads(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    _add(engram, kind, _row(kind, 905, "N", tier="verified"))
    lister = engram.get_lessons if kind == "lesson" else engram.get_decisions
    assert seeded[0] not in [row["id"] for row in lister(limit=None, _update_access=False)]
    hits = engram.search_knowledge(_words(0, "S"), scope=f"{kind}s", limit=50)
    assert seeded[0] not in json.dumps(hits, ensure_ascii=False)


@pytest.mark.parametrize("kind", KINDS)
def test_export_includes_the_overflow_archive(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    _add(engram, kind, _row(kind, 906, "N", tier="verified"))
    exported = json.loads(Path(engram.export_all(str(tmp_path / "export.json"))).read_text(encoding="utf-8"))
    other = "decisions" if kind == "lesson" else "lessons"
    assert [row["id"] for row in exported["overflow_archive"][f"{kind}s"]] == [seeded[0]]
    assert exported["overflow_archive"][other] == []


@pytest.mark.parametrize("kind", KINDS)
def test_backup_plan_lists_the_overflow_archive(_full_stores, tmp_path, monkeypatch, kind):
    from piia_engram.recovery import build_backup_plan

    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    _add(engram, kind, _row(kind, 907, "N", tier="verified"))
    datasets = {item["dataset"]: item for item in build_backup_plan(root)["knowledge_datasets"]}
    assert datasets[f"{kind}s"]["entries"] == MAX_KNOWLEDGE_ENTRIES
    assert datasets[f"{kind}s_overflow_archive"]["entries"] == 1
    assert datasets[f"{kind}s_overflow_archive"]["file_name"] == f"overflow_archive/{kind}s.jsonl"


# -- archive integrity --------------------------------------------------------------


def test_rows_sharing_an_id_are_all_kept_and_the_newest_is_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    engram = Engram(root=tmp_path / "store")
    engram._archive_overflow_rows("lesson", [dict(_row("lesson", 1, "FIRST"), id="same-id")])
    engram._archive_overflow_rows("lesson", [dict(_row("lesson", 2, "SECOND"), id="same-id")])
    summaries = [row["summary"] for row in engram._read_overflow_archive("lesson")]
    assert summaries == [_words(1, "FIRST"), _words(2, "SECOND")]
    assert engram.get_overflow_archived("lesson", "same-id")["summary"] == _words(2, "SECOND")


def test_a_repeated_archive_of_identical_content_is_listed_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    engram = Engram(root=tmp_path / "store")
    row = dict(_row("lesson", 1, "SAME"), id="retry-id")
    engram._archive_overflow_rows("lesson", [row])
    engram._archive_overflow_rows("lesson", [row])
    assert [r["id"] for r in engram._read_overflow_archive("lesson")] == ["retry-id"]


def test_a_torn_line_is_skipped_and_later_rows_stay_readable(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    _add(engram, "lesson", _row("lesson", 930, "N", tier="verified"))
    with open(_archive_path(root, "lesson"), "ab") as f:
        f.write(b'{"id": "torn", "summ')
    _add(engram, "lesson", _row("lesson", 931, "N", tier="verified"))
    assert [row["id"] for row in engram._read_overflow_archive("lesson")] == seeded[:2]


@pytest.mark.parametrize("kind", KINDS)
def test_archive_is_written_before_the_active_file(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    real_update_json = core_mod._update_json

    def _fail_active_write(path, mutator, *, default=None):
        if path == _active_path(root, kind):
            mutator(_read_json(path))
            raise OSError("simulated failure before the active file is replaced")
        return real_update_json(path, mutator, default=default)

    monkeypatch.setattr(core_mod, "_update_json", _fail_active_write)
    with pytest.raises(OSError):
        _add(engram, kind, _row(kind, 908, "N", tier="verified"))
    monkeypatch.setattr(core_mod, "_update_json", real_update_json)
    assert seeded[0] in _active_ids(root, kind)
    assert seeded[0] in _archived_ids(root, kind)
    # the retried write archives the same row again; it is listed once
    _add(engram, kind, _row(kind, 908, "N", tier="verified"))
    assert [row["id"] for row in engram._read_overflow_archive(kind)].count(seeded[0]) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_a_failed_archive_write_fails_the_whole_write(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)

    def _no_space(path, lines):
        raise OSError("simulated full disk")

    monkeypatch.setattr(core_mod, "_append_jsonl_lines", _no_space)
    with pytest.raises(OSError):
        _add(engram, kind, _row(kind, 909, "N", tier="verified"))
    assert seeded[0] in _active_ids(root, kind)
    assert len(_active_ids(root, kind)) == MAX_KNOWLEDGE_ENTRIES
    assert _archived_ids(root, kind) == []


def test_decision_cap_fails_closed_when_the_protected_set_is_unknowable(_full_stores, tmp_path, monkeypatch):
    from piia_engram import knowledge_ops

    root, seeded, engram = _copy_store(_full_stores, "decision", tmp_path, monkeypatch)
    monkeypatch.setattr(knowledge_ops.KnowledgeOpsMixin, "_version_chain_head_ids", lambda self: None)
    new = _add(engram, "decision", _row("decision", 911, "N", tier="verified"))
    assert len(_active_ids(root, "decision")) == MAX_KNOWLEDGE_ENTRIES + 1
    assert "overflow_archived_ids" not in new
    assert not _archive_path(root, "decision").exists()


@pytest.mark.parametrize("kind", KINDS)
def test_each_archived_row_is_audited_without_content(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, _ = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    engram = Engram(root=root)
    _add(engram, kind, _row(kind, 912, "N", tier="verified"), _audit_metadata_only=True)
    lines = [json.loads(line) for line in (root / "audit.log").read_text(encoding="utf-8").splitlines() if line]
    archive_events = [e for e in lines if e.get("action") == "archive"]
    assert [(e["resource"], e["detail"]) for e in archive_events] == [
        (f"knowledge/{kind}s", f"capacity_overflow id={seeded[0]}")
    ]


def test_import_archival_is_audited(_full_stores, tmp_path, monkeypatch):
    root, seeded, _ = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    engram = Engram(root=root)
    incoming = [dict(_row("lesson", i, "AUD", tier="verified"), id=f"imported-{i}") for i in range(2)]
    engram.import_all(str(_backup_file(tmp_path, "lesson", incoming)), merge=True)
    lines = [json.loads(line) for line in (root / "audit.log").read_text(encoding="utf-8").splitlines() if line]
    details = [e["detail"] for e in lines if e.get("action") == "archive"]
    assert details == [f"capacity_overflow id={seeded[0]}", f"capacity_overflow id={seeded[1]}"]


def test_encrypted_store_archives_ciphertext_and_exports_plaintext(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_SECRET", "overflow-archive-test-key")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    engram = Engram(root=root)
    if not engram._corpus_key:
        pytest.skip("corpus encryption not active in this environment")
    first = None
    for i in range(MAX_KNOWLEDGE_ENTRIES + 1):
        row = _add(engram, "lesson", _row("lesson", i, "ENC", tier="verified"))
        first = first or row["id"]
    raw = _archive_lines(root, "lesson")
    assert [r["id"] for r in raw] == [first]
    assert raw[0]["summary"] != _words(0, "ENC") and raw[0]["detail"] != "detail " + _words(0, "ENC-d")
    assert engram.get_overflow_archived("lesson", first)["summary"] == _words(0, "ENC")
    exported = json.loads(Path(engram.export_all(str(tmp_path / "export.json"))).read_text(encoding="utf-8"))
    assert exported["overflow_archive"]["lessons"][0]["summary"] == _words(0, "ENC")
    # the missing-salt guard sees ciphertext that survives only in the archive
    for kind in KINDS:
        _active_path(root, kind).write_text("[]", encoding="utf-8")
    assert engram._has_existing_ciphertext()


# -- batches and re-capture ---------------------------------------------------------


def _bulk(engram: Engram, kind: str, rows: list[dict]) -> dict:
    return engram.bulk_add_lessons(rows) if kind == "lesson" else engram.bulk_add_decisions(rows)


@pytest.mark.parametrize("kind", KINDS)
def test_a_batch_never_pushes_out_its_own_rows(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    result = _bulk(engram, kind, [_row(kind, i, "BATCH") for i in range(3)])
    saved_ids = [item["id"] for item in result["results"] if item["status"] == "saved"]
    assert len(saved_ids) == 3
    assert set(saved_ids) <= set(_active_ids(root, kind))
    assert result["overflow_archived_ids"] == seeded[:3]
    assert [item["overflow_archived_ids"] for item in result["results"]] == [[s] for s in seeded[:3]]


def test_a_batch_keeps_its_rows_in_write_order(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    result = engram.bulk_add_lessons([_row("lesson", i, "ORDER") for i in range(5)])
    saved_ids = [item["id"] for item in result["results"]]
    active = _active_ids(root, "lesson")
    assert [i for i in active if i in saved_ids] == saved_ids
    # after the batch, the oldest staging row is the first one pushed out
    later = _add(engram, "lesson", _row("lesson", 970, "N"))
    assert later["overflow_archived_ids"] == [saved_ids[0]]


def test_a_batch_larger_than_the_cap_still_keeps_the_cap(tmp_path, monkeypatch):
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    engram = Engram(root=root)
    result = engram.bulk_add_lessons([_row("lesson", i, "BIG", tier="verified") for i in range(MAX_KNOWLEDGE_ENTRIES + 5)])
    saved_ids = [item["id"] for item in result["results"] if item["status"] == "saved"]
    assert len(saved_ids) == MAX_KNOWLEDGE_ENTRIES + 5
    assert len(_active_ids(root, "lesson")) == MAX_KNOWLEDGE_ENTRIES
    assert result["overflow_archived_ids"] == saved_ids[:5]
    assert _archived_ids(root, "lesson") == saved_ids[:5]


def test_every_batch_writer_runs_as_one_batch():
    from piia_engram import compat, reconcile_apply

    for func in (Engram.bulk_add_lessons, Engram.bulk_add_decisions, Engram.ingest_notes,
                 Engram.commit_candidates, Engram.extract_session_insights, Engram.reconcile_memories,
                 Engram.reconcile_ai_configs, compat.migrate_from_oca_memory, compat.import_from_openclaw,
                 reconcile_apply.apply_reconcile):
        assert getattr(func, "__wrapped__", None) is not None, func.__qualname__


def test_config_recapture_skips_rules_already_in_the_archive(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text(
        "# Rules\n\n## Formatting\nAlways run the formatter on every changed Python file before review.\n\n"
        "## Tests\nRun the full test suite before merging any change into the main branch.\n",
        encoding="utf-8")
    engram._discover_project_roots = lambda: [project]
    first = engram.reconcile_ai_configs()["imported"]
    assert first >= 1
    for i in range(first):  # push the captured staging rows into the archive
        _add(engram, "lesson", _row("lesson", 975 + i, "N", tier="verified"))
    archived_before = len(_archive_lines(root, "lesson"))
    assert engram.reconcile_ai_configs()["imported"] == 0
    assert len(_archive_lines(root, "lesson")) == archived_before


def test_recapture_skips_rows_already_in_the_archive(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    mem_dir = tmp_path / "fake_claude" / "projects" / "p" / "memory"
    mem_dir.mkdir(parents=True)
    for i in range(3):
        (mem_dir / f"note_{i}.md").write_text(
            f"---\nname: note {i}\ndescription: d\ntype: feedback\n---\n\n{_words(i, 'MEMO')}\n", encoding="utf-8")
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    assert engram.reconcile_memories()["imported"] == 3
    for i in range(3):  # push the captured staging rows into the archive
        _add(engram, "lesson", _row("lesson", 940 + i, "N", tier="verified"))
    archived_before = len(_archive_lines(root, "lesson"))
    assert engram.reconcile_memories()["imported"] == 0
    assert len(_archive_lines(root, "lesson")) == archived_before


# -- MCP replies --------------------------------------------------------------------


def _serve(engram: Engram, monkeypatch):
    from piia_engram import mcp_server

    monkeypatch.setattr(mcp_server, "_engram", engram)
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    old_session = mcp_server._session
    old_session._stop_event.set()
    if old_session._heartbeat_thread is not None:
        old_session._heartbeat_thread.join(timeout=2.0)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    return mcp_server


@pytest.mark.parametrize("kind", KINDS)
def test_add_tool_replies_mention_archived_rows_only_when_there_are_some(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    server = _serve(engram, monkeypatch)
    if kind == "lesson":
        reply = asyncio.run(server.add_lesson(summary=_words(950, "M"), domain="cap-test", user_confirmed=True))
    else:
        reply = asyncio.run(server.add_decision(question=_words(950, "M"), choice="c", user_confirmed=True))
    assert "1 条较早的条目已移入溢出归档" in reply
    assert seeded[0] in _archived_ids(root, kind)

    server = _serve(Engram(root=tmp_path / "small"), monkeypatch)
    quiet = asyncio.run(server.add_lesson(summary=_words(951, "M"), domain="cap-test", user_confirmed=True))
    assert "溢出归档" not in quiet


@pytest.mark.parametrize("kind", KINDS)
def test_memory_store_replies_mention_archived_rows(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    server = _serve(engram, monkeypatch)
    content = json.dumps(_row(kind, 952, "M"), ensure_ascii=False)
    reply = asyncio.run(server.memory_store(kind=kind, content_json=content, user_confirmed=True))
    assert "溢出归档" in reply
    assert seeded[0] in _archived_ids(root, kind)


def test_memory_store_batch_reply_lists_archived_rows(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    server = _serve(engram, monkeypatch)
    items = json.dumps([_row("lesson", 960 + i, "MB") for i in range(2)], ensure_ascii=False)
    reply = json.loads(asyncio.run(server.memory_store(kind="lesson", items_json=items, user_confirmed=True)))
    assert reply["overflow_archived_ids"] == seeded[:2]
