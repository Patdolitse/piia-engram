"""Overflow archive: rows the capacity rules move out of the active file are archived, never dropped.

Since v4.21 reviewed active rows are never moved by capacity; the review queue has
a quota, and the rows it moves go to the same append-only archive as before. Most
tests below fill a small review queue (quota 3, no minimum stay) and trigger a move
with one more unreviewed write. The import tests still use a store of 200 reviewed
rows: backup imports keep their positional cut until the import rework.
"""

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
QUOTA = 3
SMALL_LIMITS = {
    "ENGRAM_CAP_SOFT": "6",
    "ENGRAM_CAP_HARD": "8",
    "ENGRAM_REVIEW_QUEUE_MAX": str(QUOTA),
    "ENGRAM_REVIEW_QUEUE_CEILING": "5",
    "ENGRAM_REVIEW_MIN_STAY_DAYS": "0",
    "ENGRAM_RETIRED_GRACE_DAYS": "0",
    "ENGRAM_RETIRED_MAX": "3",
}


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


def _queue_write(engram: Engram, kind: str, i: int, salt: str = "N", **kwargs) -> dict:
    """One more unreviewed row: in a full queue this moves the oldest queue row."""
    return _add(engram, kind, _row(kind, i, salt, tier="staging"), **kwargs)


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


def _set_limits(monkeypatch, **overrides) -> None:
    for name, value in {**SMALL_LIMITS, **overrides}.items():
        monkeypatch.setenv(name, str(value))


@pytest.fixture(scope="module")
def _full_stores(tmp_path_factory):
    """One store per kind whose review queue holds QUOTA unreviewed rows (full)."""
    base = tmp_path_factory.mktemp("full-queues")
    stores = {}
    with pytest.MonkeyPatch.context() as mp:
        for name, value in SMALL_LIMITS.items():
            mp.setenv(name, value)
        mp.delenv("ENGRAM_APPROVAL", raising=False)
        for kind in KINDS:
            root = base / kind
            engram = Engram(root=root)
            ids = [_add(engram, kind, _row(kind, i, "S", tier="staging"))["id"] for i in range(QUOTA)]
            stores[kind] = (root, ids)
    return stores


@pytest.fixture(scope="module")
def _full_verified_stores(tmp_path_factory):
    """One store per kind whose active file holds MAX_KNOWLEDGE_ENTRIES reviewed rows."""
    base = tmp_path_factory.mktemp("full-reviewed")
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
    _set_limits(monkeypatch)
    return root, list(seeded_ids), Engram(root=root)


def _copy_verified_store(full_stores, kind: str, tmp_path: Path, monkeypatch) -> tuple[Path, list[str], Engram]:
    source, seeded_ids = full_stores[kind]
    root = tmp_path / kind
    shutil.copytree(source, root)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return root, list(seeded_ids), Engram(root=root)


def _fresh(tmp_path: Path, monkeypatch, approval: str | None = None, **limits) -> tuple[Path, Engram]:
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    if approval:
        monkeypatch.setenv("ENGRAM_APPROVAL", approval)
    else:
        monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    _set_limits(monkeypatch, **limits)
    return root, Engram(root=root)


# -- no silent loss --------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_reviewed_write_into_a_full_queue_moves_nothing(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    new = _add(engram, kind, _row(kind, 900, "N", tier="verified"))
    assert _active_ids(root, kind) == seeded + [new["id"]]
    assert "overflow_archived_ids" not in new
    assert not _archive_path(root, kind).exists()


@pytest.mark.parametrize("kind", KINDS)
def test_strict_write_into_a_full_queue_keeps_the_new_row(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    new = _add(engram, kind, _row(kind, 901, "N"))
    assert new["tier"] == "staging"
    active = _active_ids(root, kind)
    assert new["id"] in active
    assert _archived_ids(root, kind) == [seeded[0]]
    assert len(active) == QUOTA


@pytest.mark.parametrize("kind", KINDS)
def test_consecutive_moves_keep_every_archived_row(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    for i in range(3):
        _queue_write(engram, kind, 910 + i)
    assert _archived_ids(root, kind) == seeded[:3]
    for archived_id in seeded[:3]:
        assert engram.get_overflow_archived(kind, archived_id) is not None


def test_archive_is_never_trimmed(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    earlier = [dict(_row("lesson", i, "OLD"), id=f"earlier-{i:03d}", tier="verified")
               for i in range(MAX_KNOWLEDGE_ENTRIES)]
    engram._archive_overflow_rows("lesson", earlier)
    _queue_write(engram, "lesson", 920)
    ids = _archived_ids(root, "lesson")
    assert len(ids) == MAX_KNOWLEDGE_ENTRIES + 1
    assert ids[0] == "earlier-000" and ids[-1] == seeded[0]


def test_a_queue_already_over_its_quota_is_brought_back_to_it(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    rows = _read_json(_active_path(root, "lesson"))
    extra = [dict(rows[-1], id=f"extra-{i}", summary=_words(i, "EXTRA")) for i in range(3)]
    _active_path(root, "lesson").write_text(json.dumps(rows + extra), encoding="utf-8")
    new = _queue_write(engram, "lesson", 921)
    assert len(_active_ids(root, "lesson")) == QUOTA
    assert new["overflow_archived_ids"] == seeded + ["extra-0"]
    assert _archived_ids(root, "lesson") == seeded + ["extra-0"]


@pytest.mark.parametrize("kind", KINDS)
def test_archived_row_is_the_stored_row_plus_the_stamp(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    before = next(r for r in _read_json(_active_path(root, kind)) if r["id"] == seeded[0])
    _queue_write(engram, kind, 922)
    archived = engram.get_overflow_archived(kind, seeded[0])
    assert archived["overflow_archive_reason"] == "review_queue_quota"
    assert archived["overflow_archived_at"]
    assert {k: v for k, v in archived.items() if k not in OVERFLOW_FIELDS} == before


# -- imports over the cap (positional until the import rework) -----------------------


def _backup_file(tmp_path: Path, kind: str, rows: list[dict]) -> Path:
    path = tmp_path / f"backup-{kind}.json"
    path.write_text(json.dumps({"schema_version": "1.0", "knowledge": {f"{kind}s": rows}}, ensure_ascii=False),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", KINDS)
def test_merge_import_over_the_cap_archives_pushed_out_rows(_full_verified_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_verified_store(_full_verified_stores, kind, tmp_path, monkeypatch)
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


def test_import_archival_is_audited(_full_verified_stores, tmp_path, monkeypatch):
    root, seeded, _ = _copy_verified_store(_full_verified_stores, "lesson", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    engram = Engram(root=root)
    incoming = [dict(_row("lesson", i, "AUD", tier="verified"), id=f"imported-{i}") for i in range(2)]
    engram.import_all(str(_backup_file(tmp_path, "lesson", incoming)), merge=True)
    lines = [json.loads(line) for line in (root / "audit.log").read_text(encoding="utf-8").splitlines() if line]
    details = [e["detail"] for e in lines if e.get("action") == "archive"]
    assert details == [f"capacity_overflow id={seeded[0]}", f"capacity_overflow id={seeded[1]}"]


# -- explicit result, read-back and reachability ---------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_overflow_result_names_the_archived_rows(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    new = _queue_write(engram, kind, 903)
    assert new["overflow_archived_ids"] == [seeded[0]]
    assert "placement" not in new
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


def test_below_the_limits_nothing_changes(tmp_path, monkeypatch):
    root, engram = _fresh(tmp_path, monkeypatch)
    for kind in KINDS:
        result = _add(engram, kind, _row(kind, 1, "B", tier="verified"))
        assert "overflow_archived_ids" not in result
    assert not (root / "knowledge" / "overflow_archive").exists()


@pytest.mark.parametrize("kind", KINDS)
def test_archived_rows_leave_regular_reads(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    _queue_write(engram, kind, 905)
    lister = engram.get_lessons if kind == "lesson" else engram.get_decisions
    assert seeded[0] not in [row["id"] for row in lister(limit=None, _update_access=False)]
    hits = engram.search_knowledge(_words(0, "S"), scope=f"{kind}s", limit=50)
    assert seeded[0] not in json.dumps(hits, ensure_ascii=False)


@pytest.mark.parametrize("kind", KINDS)
def test_export_includes_the_overflow_archive(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    _queue_write(engram, kind, 906)
    exported = json.loads(Path(engram.export_all(str(tmp_path / "export.json"))).read_text(encoding="utf-8"))
    other = "decisions" if kind == "lesson" else "lessons"
    assert [row["id"] for row in exported["overflow_archive"][f"{kind}s"]] == [seeded[0]]
    assert exported["overflow_archive"][other] == []


@pytest.mark.parametrize("kind", KINDS)
def test_backup_plan_lists_the_overflow_archive(_full_stores, tmp_path, monkeypatch, kind):
    from piia_engram.recovery import build_backup_plan

    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    _queue_write(engram, kind, 907)
    datasets = {item["dataset"]: item for item in build_backup_plan(root)["knowledge_datasets"]}
    assert datasets[f"{kind}s"]["entries"] == QUOTA
    assert datasets[f"{kind}s_overflow_archive"]["entries"] == 1
    assert datasets[f"{kind}s_overflow_archive"]["file_name"] == f"overflow_archive/{kind}s.jsonl"


# -- archive integrity -----------------------------------------------------------------


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
    _queue_write(engram, "lesson", 930)
    with open(_archive_path(root, "lesson"), "ab") as f:
        f.write(b'{"id": "torn", "summ')
    _queue_write(engram, "lesson", 931)
    assert [row["id"] for row in engram._read_overflow_archive("lesson")] == seeded[:2]


@pytest.mark.parametrize("kind", KINDS)
def test_archive_is_written_before_the_active_file(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    real_update_json = core_mod._update_json

    def _fail_active_write(path, mutator, **kwargs):
        if path == _active_path(root, kind):
            mutator(_read_json(path))
            raise OSError("simulated failure before the active file is replaced")
        return real_update_json(path, mutator, **kwargs)

    monkeypatch.setattr(core_mod, "_update_json", _fail_active_write)
    with pytest.raises(OSError):
        _queue_write(engram, kind, 908)
    monkeypatch.setattr(core_mod, "_update_json", real_update_json)
    assert seeded[0] in _active_ids(root, kind)
    assert seeded[0] in _archived_ids(root, kind)
    # the retried write archives the same row again; it is listed once
    _queue_write(engram, kind, 908)
    assert [row["id"] for row in engram._read_overflow_archive(kind)].count(seeded[0]) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_a_failed_archive_write_fails_the_whole_write(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)

    def _no_space(path, lines):
        raise OSError("simulated full disk")

    monkeypatch.setattr(core_mod, "_append_jsonl_lines", _no_space)
    with pytest.raises(OSError):
        _queue_write(engram, kind, 909)
    assert _active_ids(root, kind) == seeded
    assert _archived_ids(root, kind) == []


def test_an_unreadable_relation_store_does_not_change_capacity_moves(_full_stores, tmp_path, monkeypatch):
    # v4.21 removed the 4.20.1 HEAD protection: capacity never reads relation edges.
    from piia_engram import governance_store

    def _unreadable(self):
        raise OSError("relation store unreadable")

    root, seeded, engram = _copy_store(_full_stores, "decision", tmp_path, monkeypatch)
    monkeypatch.setattr(governance_store.RelationStore, "all_edges", _unreadable)
    new = _queue_write(engram, "decision", 911)
    assert new["overflow_archived_ids"] == [seeded[0]]


@pytest.mark.parametrize("kind", KINDS)
def test_each_archived_row_is_audited_without_content(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, _ = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    engram = Engram(root=root)
    _queue_write(engram, kind, 912, _audit_metadata_only=True)
    lines = [json.loads(line) for line in (root / "audit.log").read_text(encoding="utf-8").splitlines() if line]
    archive_events = [e for e in lines if e.get("action") == "archive"]
    assert [(e["resource"], e["detail"]) for e in archive_events] == [
        (f"knowledge/{kind}s", f"review_queue_quota id={seeded[0]}")
    ]


ENCRYPTED_FIELDS = {"lesson": ("summary", "detail"), "decision": ("question", "choice", "reasoning")}


@pytest.mark.parametrize("kind", KINDS)
def test_encrypted_store_archives_ciphertext_and_exports_plaintext(tmp_path, monkeypatch, kind):
    pytest.importorskip("cryptography")
    from piia_engram.crypto import ENC_PREFIX_V2C

    monkeypatch.setenv("ENGRAM_SECRET", "overflow-archive-test-key")
    root, engram = _fresh(tmp_path, monkeypatch)
    if not engram._corpus_key:
        pytest.skip("corpus encryption not active in this environment")
    first = None
    for i in range(QUOTA + 1):
        row = _add(engram, kind, _row(kind, i, "ENC", tier="staging"))
        first = first or row["id"]
    plain = _row(kind, 0, "ENC")
    raw = _archive_lines(root, kind)
    assert [r["id"] for r in raw] == [first]
    for field in ENCRYPTED_FIELDS[kind]:
        assert str(raw[0][field]).startswith(ENC_PREFIX_V2C), field
        assert plain[field] not in _archive_path(root, kind).read_text(encoding="utf-8"), field
    archived = engram.get_overflow_archived(kind, first)
    assert all(archived[field] == plain[field] for field in ENCRYPTED_FIELDS[kind])
    exported = json.loads(Path(engram.export_all(str(tmp_path / "export.json"))).read_text(encoding="utf-8"))
    assert all(exported["overflow_archive"][f"{kind}s"][0][f] == plain[f] for f in ENCRYPTED_FIELDS[kind])
    # the missing-salt guard sees ciphertext that survives only in the archive
    for other in KINDS:
        _active_path(root, other).write_text("[]", encoding="utf-8")
    assert engram._has_existing_ciphertext()


# -- batches and re-capture ------------------------------------------------------------


def _bulk(engram: Engram, kind: str, rows: list[dict]) -> dict:
    return engram.bulk_add_lessons(rows) if kind == "lesson" else engram.bulk_add_decisions(rows)


@pytest.mark.parametrize("kind", KINDS)
def test_a_staging_batch_moves_the_oldest_queue_rows_first(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    result = _bulk(engram, kind, [_row(kind, i, "BATCH") for i in range(3)])
    saved_ids = [item["id"] for item in result["results"] if item["status"] == "saved"]
    assert len(saved_ids) == 3
    assert result["overflow_archived_ids"] == seeded
    assert [item["overflow_archived_ids"] for item in result["results"]] == [[s] for s in seeded]
    assert _active_ids(root, kind) == saved_ids


@pytest.mark.parametrize("kind", KINDS)
def test_a_reviewed_batch_moves_nothing_and_keeps_write_order(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    result = _bulk(engram, kind, [_row(kind, i, "ORDER", tier="verified") for i in range(5)])
    saved_ids = [item["id"] for item in result["results"]]
    assert "overflow_archived_ids" not in result
    assert _active_ids(root, kind) == seeded + saved_ids


@pytest.mark.parametrize("kind", KINDS)
def test_a_reviewed_batch_beyond_the_hard_cap_sends_the_excess_to_the_queue(tmp_path, monkeypatch, kind):
    root, engram = _fresh(tmp_path, monkeypatch)
    result = _bulk(engram, kind, [_row(kind, i, "BIG", tier="verified") for i in range(10)])
    saved_ids = [item["id"] for item in result["results"] if item["status"] == "saved"]
    assert len(saved_ids) == 10
    rows = _read_json(_active_path(root, kind))
    assert [r["id"] for r in rows] == saved_ids
    assert [r["tier"] for r in rows] == ["verified"] * 8 + ["staging"] * 2
    assert {r.get("approval_reason") for r in rows[8:]} == {"capacity"}
    assert "overflow_archived_ids" not in result


@pytest.mark.parametrize("kind", KINDS)
def test_an_oversized_staging_batch_archives_its_oldest_rows(tmp_path, monkeypatch, kind):
    root, engram = _fresh(tmp_path, monkeypatch, approval="strict")
    result = _bulk(engram, kind, [_row(kind, i, "BIGS") for i in range(QUOTA + 3)])
    saved_ids = [item["id"] for item in result["results"] if item["status"] == "saved"]
    assert len(saved_ids) == QUOTA + 3
    assert {row.get("tier") for row in _read_json(_active_path(root, kind))} == {"staging"}
    assert _active_ids(root, kind) == saved_ids[3:]
    assert result["overflow_archived_ids"] == saved_ids[:3]


@pytest.mark.parametrize("kind", KINDS)
def test_a_mixed_batch_moves_only_queue_rows_and_never_the_current_row(tmp_path, monkeypatch, kind):
    root, engram = _fresh(tmp_path, monkeypatch)
    rows = ([_row(kind, 0, "MIX", tier="staging")]
            + [_row(kind, i, "MIX", tier="verified") for i in range(1, 5)]
            + [_row(kind, i, "MIX", tier="staging") for i in range(5, 8)])
    result = _bulk(engram, kind, rows)
    saved_ids = [item["id"] for item in result["results"] if item["status"] == "saved"]
    assert len(saved_ids) == 8
    assert result["overflow_archived_ids"] == [saved_ids[0]]
    assert _active_ids(root, kind) == saved_ids[1:]


def _in_both_files(root: Path, kind: str) -> set[str]:
    return set(_active_ids(root, kind)) & set(_archived_ids(root, kind))


@pytest.mark.parametrize("kind", KINDS)
def test_a_batch_does_not_write_again_what_it_just_archived(tmp_path, monkeypatch, kind):
    root, engram = _fresh(tmp_path, monkeypatch, approval="strict")
    repeat = _row(kind, 0, "TWIN")
    rows = [repeat] + [_row(kind, i, "TWIN") for i in range(1, QUOTA + 1)] + [dict(repeat)]
    result = _bulk(engram, kind, rows)
    first, last = result["results"][0], result["results"][-1]
    assert first["status"] == "saved"
    assert first["id"] in _archived_ids(root, kind)
    assert last["status"] == "duplicate"
    assert _in_both_files(root, kind) == set()
    assert _archived_ids(root, kind).count(first["id"]) == 1


def test_a_batch_still_saves_a_revised_decision_or_another_projects_row(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "decision", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    first = _row("decision", 0, "REV")
    revised = dict(first, choice=_words(0, "REV-other"))
    result = engram.bulk_add_decisions([first, _row("decision", 1, "REV"), revised])
    assert [item["status"] for item in result["results"]] == ["saved", "saved", "saved"]

    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path / "p", monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    shared = _row("lesson", 0, "PRJ")
    rows = [dict(shared, project="alpha-service"), _row("lesson", 1, "PRJ"), dict(shared, project="beta-service")]
    result = engram.bulk_add_lessons(rows)
    assert [item["status"] for item in result["results"]] == ["saved", "saved", "saved"]


def test_an_oversized_batch_does_not_write_a_repeat_again(tmp_path, monkeypatch):
    root, engram = _fresh(tmp_path, monkeypatch, approval="strict")
    rows = [_row("lesson", i, "BIGR", timestamp="2026-09-23T00:00:00Z") for i in range(QUOTA + 1)]
    result = engram.bulk_add_lessons(rows + [dict(rows[0])])
    assert result["results"][-1]["status"] == "duplicate"
    assert len(_active_ids(root, "lesson")) == QUOTA
    assert _in_both_files(root, "lesson") == set()
    assert result["overflow_archived_ids"] == [result["results"][0]["id"]]


def test_a_queue_row_with_supersedes_edges_is_moved_like_any_queue_row(tmp_path, monkeypatch):
    from piia_engram.governance_store import RelationStore

    root, engram = _fresh(tmp_path, monkeypatch, approval="strict")
    head = _add(engram, "lesson", _row("lesson", 0, "HEADQ"))["id"]
    older = _add(engram, "lesson", _row("lesson", 1, "HEADQ"))["id"]
    RelationStore(root).add_relation(head, "supersedes", older)
    moved = []
    for i in range(2, 4):
        moved += _add(engram, "lesson", _row("lesson", i, "HEADQ")).get("overflow_archived_ids", [])
    assert moved == [head]
    assert head in _archived_ids(root, "lesson")


def test_a_reviewed_version_chain_head_is_never_moved(tmp_path, monkeypatch):
    from piia_engram.governance_store import RelationStore

    root, engram = _fresh(tmp_path, monkeypatch)
    head = _add(engram, "lesson", _row("lesson", 0, "HEADV", tier="verified"))["id"]
    older = _add(engram, "lesson", _row("lesson", 1, "HEADV", tier="verified"))["id"]
    RelationStore(root).add_relation(head, "supersedes", older)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    for i in range(2, 2 + QUOTA + 2):
        _add(engram, "lesson", _row("lesson", i, "HEADV"))
    assert {head, older} <= set(_active_ids(root, "lesson"))
    assert head not in _archived_ids(root, "lesson")


def test_a_batch_keeps_its_pending_supersede_target(_full_stores, tmp_path, monkeypatch):
    from piia_engram.governance_store import RelationStore

    root, seeded, engram = _copy_store(_full_stores, "decision", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    rows = [_row("decision", 990, "PSB"), dict(_row("decision", 991, "PSB"), supersedes=seeded[1])]
    result = engram.bulk_add_decisions(rows)
    new_id = result["results"][1]["id"]
    # the first row moves the oldest queue row; the second skips its own supersede target
    assert result["overflow_archived_ids"] == [seeded[0], seeded[2]]
    assert {seeded[1], new_id} <= set(_active_ids(root, "decision"))
    # an unreviewed row records the supersede; the edge waits for its promotion
    assert RelationStore(root).all_edges() == []
    stored = next(r for r in _read_json(_active_path(root, "decision")) if r["id"] == new_id)
    assert stored["pending_supersedes"] == seeded[1]


def test_moves_keep_the_remaining_rows_in_write_order(tmp_path, monkeypatch):
    root, engram = _fresh(tmp_path, monkeypatch)
    ids = [
        _add(engram, "lesson", _row("lesson", i, "GRP", tier="staging" if i % 2 else "verified"))["id"]
        for i in range(7)
    ]
    new = _add(engram, "lesson", _row("lesson", 7, "GRP", tier="staging"))
    assert new["overflow_archived_ids"] == [ids[1]]
    assert _active_ids(root, "lesson") == [i for n, i in enumerate(ids) if n != 1] + [new["id"]]


def test_every_batch_writer_runs_as_one_batch():
    from piia_engram import compat, reconcile_apply

    for func in (Engram.bulk_add_lessons, Engram.bulk_add_decisions, Engram.ingest_notes,
                 Engram.commit_candidates, Engram.extract_session_insights, Engram.reconcile_memories,
                 Engram.reconcile_ai_configs, compat.migrate_from_oca_memory, compat.import_from_openclaw,
                 reconcile_apply.apply_reconcile):
        assert getattr(func, "__wrapped__", None) is not None, func.__qualname__


def _flush_queue(engram: Engram, kind: str, start: int) -> None:
    """Fill the review queue with new rows so every older queue row moves to the archive."""
    for i in range(QUOTA):
        _queue_write(engram, kind, start + i, "FLUSH")


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
    _flush_queue(engram, "lesson", 975)
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
    _flush_queue(engram, "lesson", 940)
    archived_before = len(_archive_lines(root, "lesson"))
    assert engram.reconcile_memories()["imported"] == 0
    assert len(_archive_lines(root, "lesson")) == archived_before


# -- reconcile apply against the archive -----------------------------------------------


def _candidate(kind: str, i: int, salt: str) -> dict:
    row = _row(kind, i, salt)
    return {"summary": row["summary"]} if kind == "lesson" else {"question": row["question"], "choice": row["choice"]}


def _apply_again_after_the_import_was_archived(engram: Engram, root: Path, kind: str):
    from piia_engram.reconcile_apply import apply_reconcile

    candidate = _candidate(kind, 980, "RA")
    first = apply_reconcile(engram, [candidate], dry_run=False, confirm=True)
    assert first["counts"]["imported"] == 1
    imported_id = first["items"][0]["imported_id"]
    # the import is unreviewed, so filling the queue moves it to the archive
    _flush_queue(engram, kind, 981)
    assert imported_id in _archived_ids(root, kind)
    lines_before = len(_archive_lines(root, kind))
    plan = apply_reconcile(engram, [candidate])
    second = apply_reconcile(engram, [candidate], dry_run=False, confirm=True)
    return imported_id, lines_before, plan, second


@pytest.mark.parametrize("kind", KINDS)
def test_reconcile_apply_treats_an_archived_row_as_a_duplicate(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    imported_id, lines_before, plan, second = _apply_again_after_the_import_was_archived(engram, root, kind)
    assert [(item["action"], item["match_id"]) for item in plan["items"]] == [("duplicate", imported_id)]
    assert (second["counts"]["imported"], second["counts"]["duplicate"]) == (0, 1)
    assert len(_archive_lines(root, kind)) == lines_before


def test_reconcile_apply_reads_the_archive_of_an_encrypted_store(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    monkeypatch.setenv("ENGRAM_SECRET", "reconcile-archive-test-key")
    root, engram = _fresh(tmp_path, monkeypatch)
    if not engram._corpus_key:
        pytest.skip("corpus encryption not active in this environment")
    imported_id, lines_before, plan, second = _apply_again_after_the_import_was_archived(engram, root, "lesson")
    assert _candidate("lesson", 980, "RA")["summary"] not in _archive_path(root, "lesson").read_text(encoding="utf-8")
    assert [(item["action"], item["match_id"]) for item in plan["items"]] == [("duplicate", imported_id)]
    assert second["counts"]["imported"] == 0
    assert len(_archive_lines(root, "lesson")) == lines_before


def test_reconcile_conflict_preview_sees_an_archived_decision(_full_stores, tmp_path, monkeypatch):
    from piia_engram.reconcile_apply import preview_reconcile_conflicts

    root, seeded, engram = _copy_store(_full_stores, "decision", tmp_path, monkeypatch)
    _queue_write(engram, "decision", 982)
    archived = engram.get_overflow_archived("decision", seeded[0])
    preview = preview_reconcile_conflicts(engram, [{"question": archived["question"], "choice": _words(983, "OTHER")}])
    assert [(item["action"], item["match_id"]) for item in preview["items"]] == [("conflict", seeded[0])]


def test_reconcile_counts_only_archived_rows_the_list_calls_would_return(tmp_path, monkeypatch):
    from piia_engram.reconcile_apply import _archived_existing, apply_reconcile

    root, engram = _fresh(tmp_path, monkeypatch)
    q = [_words(i, "FQ") for i in range(4)]
    c = [_words(i, "FC") for i in range(4)]
    engram._archive_overflow_rows("decision", [
        {"id": "d-live", "question": q[0], "choice": c[0], "status": "active"},
        {"id": "d-superseded", "question": q[1], "choice": c[1], "status": "superseded"},
        {"id": "d-retired", "question": q[2], "choice": c[2], "status": "archived"},
        {"id": "d-project", "question": q[3], "choice": c[3], "status": "active", "project": "delta-service"},
    ])
    engram._archive_overflow_rows("lesson", [
        {"id": "l-live", "summary": _words(0, "FL"), "status": "active"},
        {"id": "l-project", "summary": _words(1, "FL"), "status": "active", "project": "delta-service"},
        {"id": "l-superseded", "summary": _words(2, "FL"), "status": "superseded"},
    ])
    assert sorted(row["id"] for row in _archived_existing(engram)) == ["d-live", "l-live"]
    candidates = [
        {"question": q[0], "choice": c[0]},
        {"question": q[1], "choice": _words(1, "NEW")},
        {"question": q[2], "choice": _words(2, "NEW")},
        {"question": q[3], "choice": _words(3, "NEW")},
        {"summary": _words(0, "FL")},
        {"summary": _words(1, "FL")},
        {"summary": _words(2, "FL")},
    ]
    plan = apply_reconcile(engram, candidates)
    assert [item["action"] for item in plan["items"]] == [
        "duplicate", "import", "import", "import", "duplicate", "import", "import"]


def test_reconcile_apply_does_not_import_again_what_it_just_archived(_full_stores, tmp_path, monkeypatch):
    from piia_engram.reconcile_apply import apply_reconcile

    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    a, b = _candidate("lesson", 986, "TW"), _candidate("lesson", 987, "TW")
    payload = apply_reconcile(engram, [a, b, dict(a)], dry_run=False, confirm=True)
    assert payload["counts"]["imported"] == 2
    assert payload["items"][2]["imported_id"] == ""
    assert _in_both_files(root, "lesson") == set()


def test_reconcile_skips_an_archived_row_it_cannot_classify(tmp_path, monkeypatch):
    from piia_engram.reconcile_apply import _archived_existing, apply_reconcile

    root, engram = _fresh(tmp_path, monkeypatch)
    engram._archive_overflow_rows("lesson", [
        {"id": "l-bad", "summary": _words(0, "ODD"), "status": "active", "project_folder": "E:/proj\x00x"},
        {"id": "l-good", "summary": _words(1, "ODD"), "status": "active"},
    ])
    assert [row["id"] for row in _archived_existing(engram)] == ["l-good"]
    plan = apply_reconcile(engram, [{"summary": _words(1, "ODD")}])
    assert [item["action"] for item in plan["items"]] == ["duplicate"]


def test_dashboard_reconcile_report_counts_an_archived_row_as_present(_full_stores, tmp_path, monkeypatch, capsys):
    from piia_engram.setup_wizard import _run_dashboard

    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    oldest = engram.get_lessons(limit=None, _update_access=False)[0]
    assert oldest["id"] == seeded[0]
    mem_dir = tmp_path / "fake_claude" / "projects" / "p" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "note.md").write_text(
        f"---\nname: n\ndescription: d\ntype: feedback\n---\n\n{oldest['summary']}\n", encoding="utf-8")
    monkeypatch.setattr(Engram, "_CLAUDE_MEMORY_GLOBS", [str(mem_dir / "*.md")])
    _queue_write(engram, "lesson", 988)
    assert seeded[0] in _archived_ids(root, "lesson")
    capsys.readouterr()
    assert _run_dashboard(["--json"]) == 0
    dash = json.loads(capsys.readouterr().out)
    assert dash["readiness"]["reconcile"] == {"import": 0, "duplicate": 1, "conflict": 0}


def test_reconcile_apply_digest_mentions_rows_moved_to_the_archive(_full_stores, tmp_path, monkeypatch):
    from piia_engram.reconcile_apply import apply_reconcile, render_reconcile_apply_text

    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    payload = apply_reconcile(engram, [_candidate("lesson", 984, "DG")], dry_run=False, confirm=True)
    assert payload["overflow_archived_ids"] == [seeded[0]]
    assert f"moved to the overflow archive by the capacity cap: 1 ({seeded[0]})" in render_reconcile_apply_text(payload)
    quiet = apply_reconcile(Engram(root=tmp_path / "small"), [_candidate("lesson", 985, "DG")],
                            dry_run=False, confirm=True)
    assert "overflow archive" not in render_reconcile_apply_text(quiet)


def test_startup_sync_message_counts_rows_moved_to_the_archive(_full_stores, tmp_path, monkeypatch, capsys):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_RECONCILE", "1")
    mem_dir = tmp_path / "fake_claude" / "projects" / "p" / "memory"
    mem_dir.mkdir(parents=True)
    for i in range(2):
        (mem_dir / f"note_{i}.md").write_text(
            f"---\nname: note {i}\ndescription: d\ntype: feedback\n---\n\n{_words(i, 'SYNC')}\n", encoding="utf-8")
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text(
        "# Rules\n\n## Formatting\nAlways run the formatter on every changed Python file before review.\n\n"
        "## Tests\nRun the full test suite before merging any change into the main branch.\n",
        encoding="utf-8")
    engram._discover_project_roots = lambda: [project]
    engram._AI_GLOBAL_CONFIGS = []
    server = _serve(engram, monkeypatch)
    capsys.readouterr()
    server._run_startup_sync()
    err = capsys.readouterr().err
    configs = int(err.split("configs=")[1].split(",")[0])
    assert "memories=2" in err and configs >= 1
    # the queue is full, so each unreviewed capture moves exactly one row
    assert f"moved to overflow archive={2 + configs}" in err
    assert len(_archive_lines(root, "lesson")) == 2 + configs


# -- MCP replies -----------------------------------------------------------------------


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
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    server = _serve(engram, monkeypatch)
    if kind == "lesson":
        reply = asyncio.run(server.add_lesson(summary=_words(950, "M"), domain="cap-test", user_confirmed=True))
    else:
        reply = asyncio.run(server.add_decision(question=_words(950, "M"), choice="c", user_confirmed=True))
    assert "1 条较早的条目已移入溢出归档" in reply
    assert "本条已直接放入溢出归档" not in reply
    assert seeded[0] in _archived_ids(root, kind)

    server = _serve(Engram(root=tmp_path / "small"), monkeypatch)
    quiet = asyncio.run(server.add_lesson(summary=_words(951, "M"), domain="cap-test", user_confirmed=True))
    assert "溢出归档" not in quiet


def test_add_tool_reply_says_when_the_new_row_went_straight_to_the_archive(tmp_path, monkeypatch):
    root, engram = _fresh(tmp_path, monkeypatch, approval="strict", ENGRAM_REVIEW_MIN_STAY_DAYS=7)
    for i in range(5):
        _add(engram, "lesson", _row("lesson", i, "CEIL"))
    server = _serve(engram, monkeypatch)
    reply = asyncio.run(server.add_lesson(summary=_words(99, "CEIL"), domain="cap-test", user_confirmed=True))
    assert "待审队列已满：本条已直接放入溢出归档" in reply
    assert "较早的条目" not in reply
    assert len(_active_ids(root, "lesson")) == 5


@pytest.mark.parametrize("kind", KINDS)
def test_memory_store_replies_mention_archived_rows(_full_stores, tmp_path, monkeypatch, kind):
    root, seeded, engram = _copy_store(_full_stores, kind, tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    server = _serve(engram, monkeypatch)
    content = json.dumps(_row(kind, 952, "M"), ensure_ascii=False)
    reply = asyncio.run(server.memory_store(kind=kind, content_json=content, user_confirmed=True))
    assert "溢出归档" in reply
    assert seeded[0] in _archived_ids(root, kind)


def test_memory_store_batch_reply_lists_archived_rows(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lesson", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    server = _serve(engram, monkeypatch)
    items = json.dumps([_row("lesson", 960 + i, "MB") for i in range(2)], ensure_ascii=False)
    reply = json.loads(asyncio.run(server.memory_store(kind="lesson", items_json=items, user_confirmed=True)))
    assert reply["overflow_archived_ids"] == seeded[:2]
