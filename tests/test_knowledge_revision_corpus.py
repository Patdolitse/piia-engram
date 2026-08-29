"""v4.19.0 knowledge-revision regression corpus (behavior assertions only).

Frozen by the dual-reviewed v4.19 proposal (PROPOSAL_v419_v2 + state-machine
addendum v2.2). Every case asserts OBSERVABLE BEHAVIOR — entry created / not,
version bumped / not, history readable / not — never a similarity number.

Cases 1/4/9 guard behavior that must NOT change; the rest are red on the
pre-fix ref (5b114f0) by construction.

v4.19.1 amendment: case 9 was strengthened — caller lineage fields are now
explicitly REJECTED (lineage_fields_rejected), not silently dropped; original
red receipt (13 red / 3 green on 5b114f0) unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from piia_engram.core import Engram
from piia_engram.governance_store import RelationStore

STEPS_A = [{"order": 1, "action": "build", "detail": "run the build"}]
STEPS_B = [{"order": 1, "action": "test", "detail": "run the tests"}]
STEPS_C = [{"order": 1, "action": "deploy", "detail": "run the deploy"}]

TITLE = "Release flow handbook"
TITLE_SIMILAR = "Release flow handbook two"


def _playbook(title: str, steps: list[dict]) -> dict:
    return {"title": title, "triggers": "release,发布", "steps": steps, "domain": "ops"}


def _raw_lessons(root: Path) -> list[dict]:
    return json.loads((root / "knowledge" / "lessons.json").read_text(encoding="utf-8"))


def _raw_index(root: Path) -> list[dict]:
    return json.loads((root / "playbooks" / "_index.json").read_text(encoding="utf-8"))


def _active_playbook_titles(root: Path) -> list[str]:
    return sorted(
        e.get("title", "") for e in _raw_index(root) if e.get("status") == "active"
    )


def _edges(root: Path) -> list[dict]:
    return RelationStore(root).all_edges()


# ---------------------------------------------------------------- case 1
def test_case1_same_title_same_body_add_playbook_still_rejects(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_playbook(TITLE, STEPS_A))

    second = eng.add_playbook(_playbook(TITLE, STEPS_A))

    assert second.get("status") == "duplicate"
    assert _active_playbook_titles(tmp_path) == [TITLE]


# ---------------------------------------------------------------- case 2
def test_case2a_same_title_different_body_playbook_returns_revision_guidance(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_playbook(TITLE, STEPS_A))

    second = eng.add_playbook(_playbook(TITLE, STEPS_B))

    # NOT a silent swallow: the rejection must carry an explicit revision path.
    assert second.get("status") == "duplicate"
    assert second.get("likely_revision") is True
    guidance = second.get("guidance") or {}
    revision = guidance.get("revision") or {}
    assert revision.get("target_id") == created["id"]
    assert revision.get("tool_hint") == "update_knowledge"
    assert isinstance(revision.get("expected_version"), int)
    assert guidance.get("new_entry", {}).get("param") == "allow_similar_new"
    # and the store must NOT have created a second active entry
    assert _active_playbook_titles(tmp_path) == [TITLE]


def test_case2b_same_summary_different_detail_lesson_returns_revision_guidance(tmp_path: Path):
    eng = Engram(tmp_path)
    summary = "Pin mcp below 2 before releasing piia-engram"
    eng.add_lesson({"summary": summary, "detail": "old detail", "domain": "release"})

    second = eng.add_lesson({"summary": summary, "detail": "new detail", "domain": "release"})

    assert second.get("status") == "duplicate"
    assert second.get("likely_revision") is True
    revision = (second.get("guidance") or {}).get("revision") or {}
    assert revision.get("tool_hint") == "update_knowledge"
    assert len(_raw_lessons(tmp_path)) == 1


# ---------------------------------------------------------------- case 3
def test_case3a_similar_title_new_entry_mode_creates_playbook(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_playbook(TITLE, STEPS_A))

    second = eng.add_playbook(
        _playbook(TITLE_SIMILAR, STEPS_B), allow_similar_new=True
    )

    assert second.get("status") != "duplicate"
    assert "error" not in second
    assert _active_playbook_titles(tmp_path) == sorted([TITLE, TITLE_SIMILAR])


def test_case3b_similar_summary_new_entry_mode_creates_related_lesson(tmp_path: Path):
    eng = Engram(tmp_path)
    first = eng.add_lesson(
        {"summary": "Pin mcp below 2 before releasing piia-engram", "detail": "d1", "domain": "release"}
    )

    second = eng.add_lesson(
        {"summary": "Pin mcp below 2 before releasing piia-engram today", "detail": "d2", "domain": "release"},
        allow_similar_new=True,
    )

    assert second.get("status") != "duplicate"
    assert "error" not in second
    active = [l for l in _raw_lessons(tmp_path) if l.get("status") == "active"]
    assert len(active) == 2
    assert second["id"] in (active[0].get("related_ids") or [])
    assert first["id"] in (active[1].get("related_ids") or [])


# ---------------------------------------------------------------- case 4
def test_case4_similar_title_plain_add_still_rejects(tmp_path: Path):
    eng = Engram(tmp_path)
    eng.add_playbook(_playbook(TITLE, STEPS_A))

    second = eng.add_playbook(_playbook(TITLE_SIMILAR, STEPS_B))

    assert second.get("status") == "duplicate"
    assert _active_playbook_titles(tmp_path) == [TITLE]


# ---------------------------------------------------------------- case 5
def test_case5a_playbook_revision_updates_head_and_keeps_history(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_playbook(TITLE, STEPS_A))
    pid = created["id"]

    rev1 = eng.update_knowledge(pid, {"steps": STEPS_B}, expected_version=1)

    assert rev1.get("version") == 2
    assert rev1.get("steps") == STEPS_B

    history = eng.get_knowledge_history(pid)
    snapshots = history["snapshots"]
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap["id"].startswith(f"{pid}-prev-")
    assert snap["snapshot_version"] == 1
    assert _edges(tmp_path) == [{"src": pid, "rel": "supersedes", "dst": snap["id"]}]

    with_bodies = eng.get_knowledge_history(pid, include_bodies=True)
    assert with_bodies["snapshots"][0]["steps"] == STEPS_A
    # active reads show the new body only
    active = [p for p in eng.get_playbooks(limit=None) if p.get("id") == pid]
    assert active and active[0].get("steps") == STEPS_B

    # second revision -> star topology: 2 snapshots, 2 edges, both from HEAD
    rev2 = eng.update_knowledge(pid, {"steps": STEPS_C}, expected_version=2)
    assert rev2.get("version") == 3
    history2 = eng.get_knowledge_history(pid)
    assert len(history2["snapshots"]) == 2
    versions = [s["snapshot_version"] for s in history2["snapshots"]]
    assert versions == sorted(versions, reverse=True)
    all_edges = _edges(tmp_path)
    assert len(all_edges) == 2
    assert all(e["src"] == pid for e in all_edges)


def test_case5b_lesson_revision_versions_and_history(tmp_path: Path):
    eng = Engram(tmp_path)
    lesson = eng.add_lesson(
        {"summary": "Pin mcp below 2 before releasing piia-engram", "detail": "d1", "domain": "release"}
    )
    lid = lesson["id"]

    rev = eng.update_lesson(lid, {"detail": "d2"}, expected_version=1)

    assert rev.get("version") == 2
    history = eng.get_knowledge_history(lid)
    assert len(history["snapshots"]) == 1
    assert history["snapshots"][0]["snapshot_version"] == 1
    with_bodies = eng.get_knowledge_history(lid, include_bodies=True)
    assert with_bodies["snapshots"][0]["detail"] == "d1"


def test_case5c_metadata_only_update_does_not_bump_version(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_playbook(TITLE, STEPS_A))
    pid = created["id"]

    res = eng.update_knowledge(pid, {"status": "outdated"})

    assert res.get("status") == "outdated"
    assert res.get("version") == 1
    assert eng.get_knowledge_history(pid)["snapshots"] == []
    assert _edges(tmp_path) == []


# ---------------------------------------------------------------- case 6
def test_case6a_stale_expected_version_fails_zero_writes(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_playbook(TITLE, STEPS_A))
    pid = created["id"]

    res = eng.update_knowledge(pid, {"steps": STEPS_B}, expected_version=7)

    assert res.get("error") == "version_conflict"
    assert res.get("actual_version") == 1
    stored = eng._read_playbook_by_id(pid)
    assert stored["version"] == 1
    assert stored["steps"] == STEPS_A
    assert eng.get_knowledge_history(pid)["snapshots"] == []
    assert _edges(tmp_path) == []


def test_case6b_lesson_stale_expected_version_fails_zero_writes(tmp_path: Path):
    eng = Engram(tmp_path)
    lesson = eng.add_lesson({"summary": "s", "detail": "d1", "domain": "release"})
    lid = lesson["id"]

    res = eng.update_lesson(lid, {"detail": "d2"}, expected_version=9)

    assert res.get("error") == "version_conflict"
    raw = _raw_lessons(tmp_path)
    assert len(raw) == 1
    assert raw[0]["detail"] == "d1"
    assert _edges(tmp_path) == []


# ---------------------------------------------------------------- case 7
def test_case7_identical_body_revision_is_noop(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_playbook(TITLE, STEPS_A))
    pid = created["id"]

    res = eng.update_knowledge(pid, {"steps": STEPS_A}, expected_version=1)

    assert res.get("revision_outcome") == "noop"
    assert res.get("version") == 1
    assert eng.get_knowledge_history(pid)["snapshots"] == []
    assert _edges(tmp_path) == []


# ---------------------------------------------------------------- case 8
def test_case8_second_cas_with_stale_version_fails_single_winner(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_playbook(TITLE, STEPS_A))
    pid = created["id"]

    first = eng.update_knowledge(pid, {"steps": STEPS_B}, expected_version=1)
    second = eng.update_knowledge(pid, {"steps": STEPS_C}, expected_version=1)

    assert first.get("version") == 2
    assert second.get("error") == "version_conflict"
    stored = eng._read_playbook_by_id(pid)
    assert stored["version"] == 2
    assert stored["steps"] == STEPS_B
    assert len(eng.get_knowledge_history(pid)["snapshots"]) == 1


# ---------------------------------------------------------------- case 9
def test_case9_caller_lineage_fields_rejected_explicitly(tmp_path: Path):
    # v4.19.1 contract strengthening: lineage fields are no longer silently
    # dropped — the update is REJECTED so silence can never look like success.
    eng = Engram(tmp_path)
    created = eng.add_playbook(_playbook(TITLE, STEPS_A))
    pid = created["id"]

    res = eng.update_knowledge(
        pid,
        {
            "steps": STEPS_B,
            "version": 42,
            "snapshot_of": "attacker-node",
            "superseded_by": "attacker-node",
            "supersedes": "attacker-node",
            "snapshot_version": 99,
        },
    )

    assert res.get("error") == "lineage_fields_rejected"
    assert set(res.get("fields") or []) == {
        "snapshot_of", "superseded_by", "supersedes", "snapshot_version", "version",
    }
    stored = eng._read_playbook_by_id(pid)
    assert stored["version"] == 1  # nothing applied
    assert stored["steps"] == STEPS_A
    assert _edges(tmp_path) == []

    lesson = eng.add_lesson({"summary": "lesson lineage guard", "detail": "d1", "domain": "release"})
    lres = eng.update_lesson(
        lesson["id"],
        {"detail": "d2", "version": 42, "snapshot_of": "attacker-node", "snapshot_version": 99},
    )
    assert lres.get("error") == "lineage_fields_rejected"
    assert len(_raw_lessons(tmp_path)) == 1  # zero writes


# ---------------------------------------------------------------- case 10
def test_case10_cycle_guard_on_version_edges(tmp_path: Path):
    eng = Engram(tmp_path)
    # pre-seed one edge a -[supersedes]-> b
    RelationStore(tmp_path).add_relation("a", "supersedes", "b")

    # adding b -> a would close the cycle a -> b -> a: must be refused
    assert eng._commit_version_edge("b", "a") is False
    # self edge is structurally invalid
    assert eng._commit_version_edge("x", "x") is False
    # a fresh target that closes no cycle is accepted
    assert eng._commit_version_edge("c", "a") is True
    edges = _edges(tmp_path)
    assert {"src": "b", "rel": "supersedes", "dst": "a"} not in edges
    assert {"src": "c", "rel": "supersedes", "dst": "a"} in edges


# ---------------------------------------------------------------- history reads
def test_history_by_version_exact_match_only(tmp_path: Path):
    eng = Engram(tmp_path)
    created = eng.add_playbook(_playbook(TITLE, STEPS_A))
    pid = created["id"]
    eng.update_knowledge(pid, {"steps": STEPS_B}, expected_version=1)
    eng.update_knowledge(pid, {"steps": STEPS_C}, expected_version=2)

    hit = eng.get_knowledge_history(pid, version=1)
    assert hit["snapshot"]["snapshot_version"] == 1
    miss = eng.get_knowledge_history(pid, version=99)
    assert miss.get("error") == "version_not_found"
