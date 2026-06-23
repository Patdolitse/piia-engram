"""Tests for the integrity scan + self-heal proposals (Phase 9).

Builds a temp Engram root with hand-written knowledge files so we can inject the
exact corruption/drift cases (interrupted/partial write, duplicate ids, stale
index, dangling relations) without a live store.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from piia_engram import integrity


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    knowledge = tmp_path / "knowledge"
    _write_json(knowledge / "lessons.json", [
        {"id": "L1", "summary": "a"}, {"id": "L2", "summary": "b"}])
    _write_json(knowledge / "decisions.json", [
        {"id": "D1", "question": "q", "choice": "c"}])
    _write_json(knowledge / "playbooks.json", [])
    return tmp_path


def test_healthy_store_reports_no_problems(root):
    report = integrity.scan_integrity(root)
    assert report["healthy"] is True
    assert report["problems"] == []
    assert report["live_store_modified"] is False
    # sha summaries present for change-detection.
    assert all(ds["sha256_12"] for ds in report["datasets"] if ds["status"] == "ok")


def test_interrupted_partial_write_detected_as_corrupt(root):
    # Simulate a half-flushed write: truncated JSON.
    (root / "knowledge" / "lessons.json").write_text('[{"id": "L1", "summ',
                                                      encoding="utf-8")
    report = integrity.scan_integrity(root)
    codes = {p["code"] for p in report["problems"]}
    assert "dataset_corrupt" in codes
    assert report["healthy"] is False
    proposals = integrity.build_self_heal_proposals(report)
    heal = {p["problem_code"]: p for p in proposals}
    assert "dataset_corrupt" in heal
    assert "recover-json" in heal["dataset_corrupt"]["command"]
    assert heal["dataset_corrupt"]["destructive"] is False


def test_duplicate_ids_detected(root):
    _write_json(root / "knowledge" / "lessons.json", [
        {"id": "L1", "summary": "a"}, {"id": "L1", "summary": "dup"}])
    report = integrity.scan_integrity(root)
    codes = {p["code"] for p in report["problems"]}
    assert "duplicate_ids" in codes
    ds = next(d for d in report["datasets"] if d["dataset"] == "lessons")
    assert ds["duplicate_ids"] == ["L1"]


def test_index_drift_detected(root):
    # Create an index file older than the store (store touched after).
    idx = root / "search_index.db"
    idx.write_bytes(b"fake-index")
    old = time.time() - 10_000
    os.utime(idx, (old, old))
    # Touch the store so it is newer than the index.
    lessons = root / "knowledge" / "lessons.json"
    now = time.time()
    os.utime(lessons, (now, now))
    report = integrity.scan_integrity(root)
    assert report["index"]["present"] is True
    assert report["index"]["stale"] is True
    codes = {p["code"] for p in report["problems"]}
    assert "index_stale" in codes


def test_dangling_relations_detected(root):
    _write_json(root / "knowledge" / "relations.json", [
        {"src": "L1", "rel": "led_to", "dst": "GHOST"}])  # GHOST not in store
    report = integrity.scan_integrity(root)
    assert report["relations"]["dangling_edges"] == 1
    codes = {p["code"] for p in report["problems"]}
    assert "dangling_relations" in codes


def test_relation_cycle_detected(root):
    # a<->b cycle; both ids exist so it's not dangling, just a cycle.
    _write_json(root / "knowledge" / "lessons.json", [
        {"id": "a", "summary": "x"}, {"id": "b", "summary": "y"}])
    _write_json(root / "knowledge" / "relations.json", [
        {"src": "a", "rel": "led_to", "dst": "b"},
        {"src": "b", "rel": "led_to", "dst": "a"}])
    report = integrity.scan_integrity(root)
    assert report["relations"]["cycles"] == 1
    codes = {p["code"] for p in report["problems"]}
    assert "relation_cycle" in codes


def test_report_is_metadata_only(root):
    # Body text never appears in the report. (The healthy report is even more
    # minimal — it carries counts/hashes only; ids surface only as a duplicate
    # or dangling-edge finding, asserted in their own tests.)
    _write_json(root / "knowledge" / "lessons.json", [
        {"id": "DUPID", "summary": "SUPER-SECRET-BODY-TEXT"},
        {"id": "DUPID", "summary": "ANOTHER-SECRET-BODY"}])
    report = integrity.scan_integrity(root)
    blob = repr(report)
    assert "SUPER-SECRET-BODY-TEXT" not in blob
    assert "ANOTHER-SECRET-BODY" not in blob
    assert "DUPID" in blob  # id surfaces as duplicate-finding metadata


def test_missing_root_no_crash(tmp_path):
    report = integrity.scan_integrity(tmp_path / "does-not-exist")
    assert report["exists"] is False
    # Missing datasets are reported, not crashed on.
    assert all(d["status"] == "missing" for d in report["datasets"])


def test_render_text_smoke(root):
    (root / "knowledge" / "lessons.json").write_text("{bad", encoding="utf-8")
    report = integrity.scan_integrity(root)
    proposals = integrity.build_self_heal_proposals(report)
    text = integrity.render_integrity_text(report, proposals)
    assert "integrity scan" in text
    assert "live store modified: false" in text


def test_no_repair_side_effects(root):
    # The scan must not create, rebuild, or modify any files.
    before = {p.name: p.read_bytes() for p in (root / "knowledge").iterdir()}
    files_before = set(os.listdir(root))
    integrity.scan_integrity(root)
    after = {p.name: p.read_bytes() for p in (root / "knowledge").iterdir()}
    assert before == after
    assert set(os.listdir(root)) == files_before


# -- Playbook split-file orphan / dangling detection (1-1) -------------------


def _setup_split_playbooks(root):
    """Create a split playbooks/ layout with body files + _index.json."""
    pb_dir = root / "playbooks"
    pb_dir.mkdir(parents=True, exist_ok=True)
    # Body files
    _write_json(pb_dir / "aaa111.json", {"id": "aaa111", "title": "Alpha"})
    _write_json(pb_dir / "bbb222.json", {"id": "bbb222", "title": "Beta"})
    _write_json(pb_dir / "ccc333.json", {"id": "ccc333", "title": "Gamma"})
    # Index only references aaa111 and bbb222 — ccc333 is orphaned
    _write_json(pb_dir / "_index.json", [
        {"id": "aaa111", "title": "Alpha", "status": "active"},
        {"id": "bbb222", "title": "Beta", "status": "active"},
        {"id": "ddd444", "title": "Delta", "status": "active"},  # dangling: no body
    ])


def test_orphaned_playbook_body_detected(tmp_path):
    """Body file without index entry should be flagged as orphan."""
    _setup_split_playbooks(tmp_path)
    report = integrity.scan_integrity(tmp_path)
    problems = [p for p in report.get("problems", [])
                if p.get("type") == "orphaned_playbook_body"]
    assert len(problems) >= 1
    orphan_ids = {p.get("playbook_id") for p in problems}
    assert "ccc333" in orphan_ids


def test_dangling_index_entry_detected(tmp_path):
    """Index entry without body file should be flagged as dangling."""
    _setup_split_playbooks(tmp_path)
    report = integrity.scan_integrity(tmp_path)
    problems = [p for p in report.get("problems", [])
                if p.get("type") == "dangling_playbook_index"]
    assert len(problems) >= 1
    dangling_ids = {p.get("playbook_id") for p in problems}
    assert "ddd444" in dangling_ids


def test_consistent_playbooks_no_problems(tmp_path):
    """Fully consistent split playbooks should report zero orphan/dangling."""
    pb_dir = tmp_path / "playbooks"
    pb_dir.mkdir(parents=True, exist_ok=True)
    _write_json(pb_dir / "aaa111.json", {"id": "aaa111", "title": "Alpha"})
    _write_json(pb_dir / "_index.json", [
        {"id": "aaa111", "title": "Alpha", "status": "active"},
    ])
    report = integrity.scan_integrity(tmp_path)
    pb_problems = [p for p in report.get("problems", [])
                   if p.get("type") in ("orphaned_playbook_body", "dangling_playbook_index")]
    assert len(pb_problems) == 0
