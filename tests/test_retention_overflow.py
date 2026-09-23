"""Knowledge cap overflow: rows pushed out by the per-type cap are archived, never dropped silently."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from piia_engram import Engram
from piia_engram import core as core_mod
from piia_engram.storage import MAX_KNOWLEDGE_ENTRIES, _read_json


def _words(i: int, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}-{i}".encode()).hexdigest()
    return " ".join("w" + digest[k:k + 7] for k in range(0, 56, 7))


def _ids(path: Path) -> list[str]:
    if not path.is_file():
        return []
    data = _read_json(path)
    return [row.get("id") for row in data if isinstance(row, dict)]


def _active_ids(root: Path, name: str) -> list[str]:
    return _ids(root / "knowledge" / f"{name}.json")


def _archived_ids(root: Path, name: str) -> list[str]:
    return _ids(root / "knowledge" / "overflow_archive" / f"{name}.json")


@pytest.fixture(scope="module")
def _full_stores(tmp_path_factory):
    """One store with a full lesson file and one with a full decision file (all verified)."""
    base = tmp_path_factory.mktemp("full-stores")
    lessons_root = base / "lessons"
    engram = Engram(root=lessons_root)
    lesson_ids = [
        engram.add_lesson({"summary": _words(i, "L"), "tier": "verified"}, domain="cap-test")["id"]
        for i in range(MAX_KNOWLEDGE_ENTRIES)
    ]
    decisions_root = base / "decisions"
    engram = Engram(root=decisions_root)
    decision_ids = [
        engram.add_decision(
            {"question": _words(i, "Q"), "choice": _words(i, "C"), "tier": "verified"}
        )["id"]
        for i in range(MAX_KNOWLEDGE_ENTRIES)
    ]
    return {"lessons": (lessons_root, lesson_ids), "decisions": (decisions_root, decision_ids)}


def _copy_store(full_stores, name: str, tmp_path: Path, monkeypatch) -> tuple[Path, list[str], Engram]:
    source, seeded_ids = full_stores[name]
    root = tmp_path / name
    shutil.copytree(source, root)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return root, list(seeded_ids), Engram(root=root)


# -- no silent loss on a full store -------------------------------------------------


def test_verified_write_into_full_store_keeps_the_oldest_row(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    new = engram.add_lesson({"summary": _words(900, "L"), "tier": "verified"}, domain="cap-test")
    active = _active_ids(root, "lessons")
    assert new["id"] in active
    assert len(active) == MAX_KNOWLEDGE_ENTRIES
    assert seeded[0] in active or seeded[0] in _archived_ids(root, "lessons")


def test_strict_write_into_full_store_keeps_the_new_row(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    new = engram.add_lesson({"summary": _words(901, "L")}, domain="cap-test")
    assert new["tier"] == "staging"
    active = _active_ids(root, "lessons")
    assert new["id"] in active
    assert seeded[0] in _archived_ids(root, "lessons")
    assert len(active) == MAX_KNOWLEDGE_ENTRIES


def test_strict_decision_into_full_store_keeps_the_new_row(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "decisions", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    new = engram.add_decision({"question": _words(902, "Q"), "choice": _words(902, "C")})
    active = _active_ids(root, "decisions")
    assert new["id"] in active
    assert seeded[0] in _archived_ids(root, "decisions")
    assert len(active) == MAX_KNOWLEDGE_ENTRIES


def _backup_file(tmp_path: Path, lessons: list[dict]) -> Path:
    path = tmp_path / "backup.json"
    path.write_text(
        json.dumps({"schema_version": "1.0", "knowledge": {"lessons": lessons}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_merge_import_over_the_cap_archives_pushed_out_rows(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    incoming = [
        {"id": f"imported-{i:03d}", "summary": _words(i, "IMP"), "tier": "verified"} for i in range(5)
    ]
    result = engram.import_all(str(_backup_file(tmp_path, incoming)), merge=True)
    active = _active_ids(root, "lessons")
    archived = _archived_ids(root, "lessons")
    assert len(active) == MAX_KNOWLEDGE_ENTRIES
    for oldest in seeded[:5]:
        assert oldest in active or oldest in archived
    assert "lessons(+5, archived 5)" in result["imported"]


def test_replace_import_over_the_cap_archives_the_extra_rows(tmp_path, monkeypatch):
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    engram = Engram(root=root)
    incoming = [
        {"id": f"imported-{i:03d}", "summary": _words(i, "REP"), "tier": "verified"}
        for i in range(MAX_KNOWLEDGE_ENTRIES + 5)
    ]
    result = engram.import_all(str(_backup_file(tmp_path, incoming)), merge=False)
    assert _archived_ids(root, "lessons") == [f"imported-{i:03d}" for i in range(5)]
    assert len(_active_ids(root, "lessons")) == MAX_KNOWLEDGE_ENTRIES
    assert f"lessons({MAX_KNOWLEDGE_ENTRIES}, archived 5)" in result["imported"]


# -- explicit result, read-back and reachability ------------------------------------


def test_overflow_result_names_the_archived_rows(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    new = engram.add_lesson({"summary": _words(903, "L"), "tier": "verified"}, domain="cap-test")
    assert new["overflow_archived_ids"] == [seeded[0]]
    stored = next(r for r in _read_json(root / "knowledge" / "lessons.json") if r["id"] == new["id"])
    assert "overflow_archived_ids" not in stored
    row = engram.get_overflow_archived("lesson", seeded[0])
    assert row is not None
    assert row["summary"] == _words(0, "L")
    assert row["overflow_archive_reason"] == "capacity_overflow"
    assert row["overflow_archived_at"]
    assert engram.get_overflow_archived("lesson", "no-such-id") is None


def test_decision_overflow_result_names_the_archived_rows(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "decisions", tmp_path, monkeypatch)
    new = engram.add_decision({"question": _words(904, "Q"), "choice": _words(904, "C"), "tier": "verified"})
    assert new["overflow_archived_ids"] == [seeded[0]]
    assert engram.get_overflow_archived("decision", seeded[0])["question"] == _words(0, "Q")


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
    lesson = engram.add_lesson({"summary": _words(1, "B"), "tier": "verified"}, domain="cap-test")
    decision = engram.add_decision({"question": _words(1, "BQ"), "choice": _words(1, "BC"), "tier": "verified"})
    assert "overflow_archived_ids" not in lesson
    assert "overflow_archived_ids" not in decision
    assert not (root / "knowledge" / "overflow_archive").exists()


def test_archived_rows_leave_regular_reads(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    engram.add_lesson({"summary": _words(905, "L"), "tier": "verified"}, domain="cap-test")
    listed = [row["id"] for row in engram.get_lessons(limit=None, _update_access=False)]
    assert seeded[0] not in listed
    hits = engram.search_knowledge(_words(0, "L"), scope="lessons", limit=50)
    assert seeded[0] not in json.dumps(hits, ensure_ascii=False)


def test_export_includes_the_overflow_archive(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    engram.add_lesson({"summary": _words(906, "L"), "tier": "verified"}, domain="cap-test")
    out = Path(engram.export_all(str(tmp_path / "export.json")))
    exported = json.loads(out.read_text(encoding="utf-8"))
    assert [row["id"] for row in exported["overflow_archive"]["lessons"]] == [seeded[0]]
    assert exported["overflow_archive"]["decisions"] == []


def test_backup_plan_lists_the_overflow_archive(_full_stores, tmp_path, monkeypatch):
    from piia_engram.recovery import build_backup_plan

    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    engram.add_lesson({"summary": _words(907, "L"), "tier": "verified"}, domain="cap-test")
    plan = build_backup_plan(root)
    datasets = {item["dataset"]: item for item in plan["knowledge_datasets"]}
    assert datasets["lessons"]["entries"] == MAX_KNOWLEDGE_ENTRIES
    assert datasets["lessons_overflow_archive"]["entries"] == 1
    assert datasets["lessons_overflow_archive"]["file_name"] == "overflow_archive/lessons.json"


# -- ordering, audit, encryption, reply text ----------------------------------------


def test_archive_is_written_before_the_active_file(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    real_update_json = core_mod._update_json

    def _fail_active_write(path, mutator, *, default=None):
        if path.name == "lessons.json" and path.parent.name == "knowledge":
            mutator(_read_json(path))
            raise OSError("simulated failure before the active file is replaced")
        return real_update_json(path, mutator, default=default)

    monkeypatch.setattr(core_mod, "_update_json", _fail_active_write)
    with pytest.raises(OSError):
        engram.add_lesson({"summary": _words(908, "L"), "tier": "verified"}, domain="cap-test")
    monkeypatch.setattr(core_mod, "_update_json", real_update_json)
    assert seeded[0] in _active_ids(root, "lessons")
    assert seeded[0] in _archived_ids(root, "lessons")


def test_each_archived_row_is_audited_without_content(_full_stores, tmp_path, monkeypatch):
    root, seeded, _ = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    engram = Engram(root=root)
    engram.add_lesson(
        {"summary": _words(909, "L"), "tier": "verified"}, domain="cap-test", _audit_metadata_only=True
    )
    lines = [json.loads(line) for line in (root / "audit.log").read_text(encoding="utf-8").splitlines() if line]
    archive_events = [e for e in lines if e.get("action") == "archive"]
    assert len(archive_events) == 1
    assert archive_events[0]["resource"] == "knowledge/lessons"
    assert archive_events[0]["detail"] == f"capacity_overflow id={seeded[0]}"


def test_archive_keeps_the_at_rest_form_of_an_encrypted_store(tmp_path, monkeypatch):
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
        row = engram.add_lesson({"summary": _words(i, "ENC"), "tier": "verified"}, domain="cap-test")
        first = first or row["id"]
    raw = _read_json(root / "knowledge" / "overflow_archive" / "lessons.json")
    assert [r["id"] for r in raw] == [first]
    assert raw[0]["summary"] != _words(0, "ENC")
    assert engram.get_overflow_archived("lesson", first)["summary"] == _words(0, "ENC")


def _serve(engram: Engram, monkeypatch):
    import asyncio

    from piia_engram import mcp_server

    monkeypatch.setattr(mcp_server, "_engram", engram)
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    old_session = mcp_server._session
    old_session._stop_event.set()
    if old_session._heartbeat_thread is not None:
        old_session._heartbeat_thread.join(timeout=2.0)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    return mcp_server, asyncio.run


def test_write_replies_mention_archived_rows_only_when_there_are_some(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "lessons", tmp_path, monkeypatch)
    server, run = _serve(engram, monkeypatch)
    reply = run(server.add_lesson(summary=_words(910, "L"), domain="cap-test", user_confirmed=True))
    assert "1 条较早的条目已移入溢出归档" in reply
    assert seeded[0] in _archived_ids(root, "lessons")

    small_root = tmp_path / "small"
    small = Engram(root=small_root)
    server, run = _serve(small, monkeypatch)
    quiet = run(server.add_lesson(summary=_words(911, "L"), domain="cap-test", user_confirmed=True))
    assert "溢出归档" not in quiet


def test_memory_store_decision_reply_mentions_archived_rows(_full_stores, tmp_path, monkeypatch):
    root, seeded, engram = _copy_store(_full_stores, "decisions", tmp_path, monkeypatch)
    server, run = _serve(engram, monkeypatch)
    content = json.dumps({"question": _words(912, "Q"), "choice": _words(912, "C")}, ensure_ascii=False)
    reply = run(server.memory_store(kind="decision", content_json=content, user_confirmed=True))
    assert "溢出归档" in reply
    assert seeded[0] in _archived_ids(root, "decisions")
