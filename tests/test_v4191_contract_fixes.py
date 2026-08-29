"""v4.19.1 contract-fidelity fixes: negative controls from the Codex final review.

All cases here pin the frozen state-machine contract that the v4.19.0
implementation drifted from. Red on v4.19.0 (main ec4178d) by construction;
each pairs with a fix in this release:

  1. snapshots are IMMUTABLE and not update targets (error snapshot_immutable)
  2. caller lineage fields in update payloads are REJECTED, not silently dropped
  3. caller-facing add_relation refuses rel="supersedes" (version lineage is
     generated only by the revision primitive)
  4. fuzzy duplicate hits never auto-fill guidance.revision.target_id
  5. get_playbook-by-id refuses snapshot ids (history lives behind
     get_knowledge_history only)
  6. export surfaces active HEADs only — no snapshots
  7. the all-zeros event baseline goes through the SAME bounded merge-base
     fallback (fetch + bound + unconditional HEAD evidence)
  8. the canonical checker clears a pre-existing --patch-output file
  9. in-lock no-op recheck: a lost-race no-op revision leaves no orphan
     snapshot and does not double-bump the version (barrier concurrency)
 10. history payloads carry total_body_size and complete playbook body fields
 11. memory_store passes allow_similar_new through and surfaces duplicate
     guidance for playbook kind
 12. encrypted store: a not-found update rewrites ZERO bytes (SkipWrite)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore
# enter via mcp_server (it re-exports memory_store); importing
# mcp_tools_write directly first hits a partially-initialized-module cycle
from piia_engram.mcp_server import memory_store  # noqa: F401 (signature check)

STEPS_A = [{"order": 1, "action": "build", "detail": "run the build"}]
STEPS_B = [{"order": 1, "action": "test", "detail": "run the tests"}]


def _pb(title: str, steps: list[dict]) -> dict:
    return {"title": title, "triggers": "t", "steps": steps, "domain": "ops"}


def _raw_lessons(root: Path) -> list[dict]:
    return json.loads((root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1
def test_update_lesson_snapshot_refused(tmp_path: Path):
    eng = Engram(tmp_path)
    lesson = eng.add_lesson({"summary": "s", "detail": "d1", "domain": "release"})
    eng.update_lesson(lesson["id"], {"detail": "d2"}, expected_version=1)
    snap_id = eng.get_knowledge_history(lesson["id"])["snapshots"][0]["id"]

    res = eng.update_knowledge(snap_id, {"detail": "tampered"})

    assert res.get("error") == "snapshot_immutable"
    bodies = eng.get_knowledge_history(lesson["id"], include_bodies=True)["snapshots"]
    assert bodies[0]["detail"] == "d1"


def test_update_playbook_snapshot_refused(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_pb("Handbook one", STEPS_A))
    eng.update_knowledge(created["id"], {"steps": STEPS_B}, expected_version=1)
    snap_id = eng.get_knowledge_history(created["id"])["snapshots"][0]["id"]

    res = eng.update_playbook(snap_id, {"description": "tampered"})

    assert res.get("error") == "snapshot_immutable"


def test_snapshot_revision_creates_no_nested_snapshot(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_pb("Handbook two", STEPS_A))
    eng.update_knowledge(created["id"], {"steps": STEPS_B}, expected_version=1)
    snap_id = eng.get_knowledge_history(created["id"])["snapshots"][0]["id"]

    eng.update_knowledge(snap_id, {"description": "tampered"})

    # exactly ONE snapshot, and its id is a direct child of the HEAD
    hist = eng.get_knowledge_history(created["id"])
    assert hist["total"] == 1
    assert hist["snapshots"][0]["id"].startswith(f"{created['id']}-prev-")
    edges = RelationStore(tmp_path).all_edges()
    assert all(e["src"] == created["id"] for e in edges)


# ---------------------------------------------------------------- 2
def test_caller_lineage_fields_rejected_on_update(tmp_path: Path):
    eng = Engram(tmp_path)
    lesson = eng.add_lesson({"summary": "s", "detail": "d1", "domain": "release"})
    created = eng.add_playbook(_pb("Handbook three", STEPS_A))

    for target_id, payload in (
        (lesson["id"], {"detail": "d2", "snapshot_of": "x", "version": 42}),
        (created["id"], {"description": "d", "superseded_by": "x", "snapshot_version": 9}),
    ):
        res = eng.update_knowledge(target_id, payload)
        assert res.get("error") == "lineage_fields_rejected", (target_id, res)


# ---------------------------------------------------------------- 3
def test_caller_add_relation_supersedes_refused(tmp_path: Path):
    eng = Engram(tmp_path)
    a = eng.add_lesson({"summary": "lesson A unique", "domain": "release", "tier": "verified"})
    b = eng.add_lesson({"summary": "lesson B distinct", "domain": "release", "tier": "verified"})

    res = eng.add_relation(a["id"], "supersedes", b["id"])

    assert res.get("added") is False
    assert res.get("reason") == "supersedes_is_internal"
    assert RelationStore(tmp_path).all_edges() == []


# ---------------------------------------------------------------- 4
def test_fuzzy_playbook_guidance_has_no_target_id(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_pb("Release flow handbook alpha edition", STEPS_A))

    res = eng.add_playbook(_pb("Release flow handbook beta edition", STEPS_B))

    assert res.get("status") == "duplicate"
    guidance = res.get("guidance") or {}
    assert "revision" not in guidance or "target_id" not in (guidance.get("revision") or {})
    assert guidance.get("new_entry", {}).get("param") == "allow_similar_new"


def test_fuzzy_lesson_guidance_has_no_target_id(tmp_path: Path):
    eng = Engram(tmp_path)
    base = ("Before every release pin the mcp dependency below version two "
            "and run the full sanity suite on a clean checkout")
    eng.add_lesson({"summary": base, "detail": "d1", "domain": "release"})

    res = eng.add_lesson({"summary": base + " twice", "detail": "d2", "domain": "release"})

    assert res.get("status") == "duplicate"  # sim ~0.97 >= 0.95, not identical
    guidance = res.get("guidance") or {}
    assert "target_id" not in (guidance.get("revision") or {})


def test_exact_identity_guidance_keeps_target_id(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_pb("Identical title only", STEPS_A))

    res = eng.add_playbook(_pb("Identical title only", STEPS_B))

    assert res.get("status") == "duplicate"
    revision = (res.get("guidance") or {}).get("revision") or {}
    assert revision.get("target_id") == created["id"]


# ---------------------------------------------------------------- 5
def test_get_playbook_by_id_refuses_snapshot(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_pb("Handbook four", STEPS_A))
    eng.update_knowledge(created["id"], {"steps": STEPS_B}, expected_version=1)
    snap_id = eng.get_knowledge_history(created["id"])["snapshots"][0]["id"]

    res = eng.get_playbook(playbook_id=snap_id)

    assert res.get("error") == "snapshot_immutable"


# ---------------------------------------------------------------- 6
def test_export_contains_no_snapshots(tmp_path: Path):
    eng = Engram(tmp_path)
    lesson = eng.add_lesson({"summary": "s", "detail": "d1", "domain": "release"})
    eng.update_lesson(lesson["id"], {"detail": "d2"}, expected_version=1)
    created = eng.add_playbook(_pb("Handbook five", STEPS_A))
    eng.update_knowledge(created["id"], {"steps": STEPS_B}, expected_version=1)

    out = tmp_path / "export.json"
    eng.export_all(str(out))
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert all(l.get("status") != "superseded" for l in payload["knowledge"]["lessons"])
    assert all("snapshot_of" not in p for p in payload["knowledge"]["playbooks"])


# ---------------------------------------------------------------- 7
_ALL_ZEROS = "0" * 40


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_all_zeros_baseline_goes_through_bounded_fallback(tmp_path: Path):
    from tests.test_check_release_preflight import (
        _REQUIRED_EVIDENCE,
        _write_version_files,
        make_repo,
    )

    preflight = _load_preflight()
    repo = make_repo(tmp_path, "4.12.0")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    # two commits past the origin/main base with evidence present
    _write_version_files(repo, "4.13.0")
    (repo / "release-evidence" / "v4.13.0.md").write_text(
        _REQUIRED_EVIDENCE.format(v="4.13.0"), encoding="utf-8"
    )
    allow = (repo / ".publishallow").read_text(encoding="utf-8")
    (repo / ".publishallow").write_text(
        allow + "release-evidence/v4.13.0.md\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "bump 4.13.0")
    (repo / "README.md").write_text(
        (repo / "README.md").read_text(encoding="utf-8") + "\nx\n", encoding="utf-8"
    )
    _git(repo, "commit", "-aqm", "docs")

    # all-zeros + bound=1 and 2 commits of distance -> MUST fail closed on the
    # bound (proves the all-zeros path runs the bounded fallback, not the old
    # cached origin/main shortcut)
    result = preflight.preflight(repo, since=_ALL_ZEROS, base_required=True, fallback_bound=1)

    assert not result.ok
    assert any("exceeds bound" in e for e in result.errors)
    assert preflight.preflight(
        repo, since=_ALL_ZEROS, base_required=True
    ).ok


def _load_preflight():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "check_release_preflight.py"
    spec = importlib.util.spec_from_file_location("crp", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 8
def _py() -> str:
    import sys

    return sys.executable


def test_checker_clears_preexisting_patch_on_no_drift(tmp_path: Path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_canonical_counts.py"

    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite tests="10" failures="0" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    manifest = tmp_path / "facts.json"
    manifest.write_text(
        json.dumps({"facts": {"test_passed": 10, "test_skipped": 0, "test_collected": 10}}),
        encoding="utf-8",
    )
    patch = tmp_path / "stale.patch"
    patch.write_text("STALE", encoding="utf-8")

    proc = subprocess.run(
        [str(_py()), str(script), str(junit), "--manifest", str(manifest),
         "--patch-output", str(patch)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert not patch.exists()


def test_checker_clears_preexisting_patch_on_red_suite(tmp_path: Path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_canonical_counts.py"
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite tests="10" failures="2" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    manifest = tmp_path / "facts.json"
    manifest.write_text(
        json.dumps({"facts": {"test_passed": 10, "test_skipped": 0, "test_collected": 10}}),
        encoding="utf-8",
    )
    patch = tmp_path / "stale.patch"
    patch.write_text("STALE", encoding="utf-8")

    proc = subprocess.run(
        [str(_py()), str(script), str(junit), "--manifest", str(manifest),
         "--patch-output", str(patch)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert not patch.exists()


def _py() -> str:
    import sys
    return sys.executable


# ---------------------------------------------------------------- 9
def test_barrier_race_no_op_leaves_no_orphan_and_single_bump(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_pb("Handbook six", STEPS_A))
    pid = created["id"]
    new_steps = [{"order": 1, "action": "ship", "detail": "same for both"}]
    n_threads = 4
    barrier = threading.Barrier(n_threads)
    results: list[dict] = []

    def _worker():
        barrier.wait()
        results.append(eng.update_knowledge(pid, {"steps": new_steps}))

    threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stored = eng._read_playbook_by_id(pid)
    hist = eng.get_knowledge_history(pid)
    # exactly one snapshot survives, head bumped exactly once, no orphans
    assert hist["total"] == 1
    assert stored["version"] == 2
    assert stored["steps"] == new_steps
    edges = RelationStore(tmp_path).all_edges()
    assert len(edges) == 1


# ---------------------------------------------------------------- 10
def test_history_reports_total_body_size_and_full_playbook_fields(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook({
        "title": "Handbook seven",
        "triggers": "t",
        "steps": STEPS_A,
        "domain": "ops",
        "description": "desc",
        "parameters": ["${TARGET}"],
        "required_tools": [{"name": "gh", "purpose": "github"}],
    })
    pid = created["id"]

    res = eng.update_knowledge(pid, {"steps": STEPS_B}, expected_version=1)

    assert res.get("version") == 2
    hist = eng.get_knowledge_history(pid)
    assert isinstance(hist.get("total_body_size"), int) and hist["total_body_size"] > 0
    with_bodies = eng.get_knowledge_history(pid, include_bodies=True)
    snap = with_bodies["snapshots"][0]
    assert snap["steps"] == STEPS_A
    assert snap["description"] == "desc"
    assert snap["parameters"] == ["${TARGET}"]
    # required_tools entries are normalized (optional/min_version/query added)
    assert snap["required_tools"][0]["name"] == "gh"
    assert snap["required_tools"][0]["purpose"] == "github"


# ---------------------------------------------------------------- 11
def test_playbook_duplicate_guidance_and_memory_store_passthrough(tmp_path: Path):
    # the playbook duplicate payload must carry revision guidance (core layer;
    # the MCP shell is a thin pass-through)
    eng = Engram(tmp_path)
    eng.add_playbook(_pb("Guidance surfacing handbook", STEPS_A))
    res = eng.add_playbook(_pb("Guidance surfacing handbook", STEPS_B))
    assert res.get("status") == "duplicate"
    assert (res.get("guidance") or {}).get("revision", {}).get("target_id")
    # memory_store single-item path forwards allow_similar_new (module-level
    # import above — importing inside a test triggers an import cycle)
    import inspect

    sig = inspect.signature(memory_store)
    assert "allow_similar_new" in sig.parameters


# ---------------------------------------------------------------- 12
def test_encrypted_store_not_found_update_writes_zero_bytes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENGRAM_SECRET", "unit-test-secret")
    eng = Engram(tmp_path)
    eng.add_lesson({"summary": "seed lesson for encrypted zero-write", "domain": "release"})
    eng.add_decision({"question": "seed q", "choice": "seed c"})
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    dec_path = tmp_path / "knowledge" / "decisions.json"
    assert lessons_path.is_file() and dec_path.is_file()
    before_l = hashlib.sha256(lessons_path.read_bytes()).hexdigest()
    before_d = hashlib.sha256(dec_path.read_bytes()).hexdigest()

    eng.update_lesson("no-such-id", {"detail": "x"})
    eng.update_decision("no-such-id", {"choice": "x"})

    assert hashlib.sha256(lessons_path.read_bytes()).hexdigest() == before_l, (
        "encrypted lessons.json was rewritten by a not-found update")
    assert hashlib.sha256(dec_path.read_bytes()).hexdigest() == before_d, (
        "encrypted decisions.json was rewritten by a not-found update")
