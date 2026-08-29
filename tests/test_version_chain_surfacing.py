"""Tests for version-chain HEAD surfacing (N5) - guarded / render-only.

The version-chain read layer already collapses superseded knowledge to its HEAD
during recall. This suite covers the *surfacing* of that HEAD state in the
owner-facing outputs (recall meta + render, resume brief), as additive,
render-only annotations that never change what is stored and never leak bodies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import piia_engram.governance_store as gstore
from piia_engram import recall_service as rs
from piia_engram import version_chain as vc


NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


# --- pure helper -----------------------------------------------------------


def test_head_ids_returns_current_versions_only():
    edges = [
        {"src": "b", "rel": "supersedes", "dst": "a"},
        {"src": "c", "rel": "supersedes", "dst": "b"},
        {"src": "y", "rel": "led_to", "dst": "z"},
    ]
    heads = vc.head_ids(edges)
    # c is the latest in a->b->c; z is the forward end of y->z.
    assert heads == {"c", "z"}
    assert vc.head_ids([]) == set()


# --- recall meta + render --------------------------------------------------


class _FakeRelationStore:
    def __init__(self, edges):
        self._edges = edges

    def all_edges(self):
        return list(self._edges)


class _FakeEngram:
    def __init__(self, *, relevant, edges, root="/fake/root"):
        self._relevant = relevant
        self.root = root

    def get_safe_profile(self):
        return {"role": "codex_tester"}

    def get_recent_context(self, limit=1):
        return []

    def get_relevant_lessons(self, project_folder=None, limit=8, _update_access=True):
        return [dict(e) for e in self._relevant]

    def search_knowledge(self, query, scope="all", limit=10):
        return {}


def _gather(eng, edges, **kw):
    orig = gstore.RelationStore
    gstore.RelationStore = lambda root: _FakeRelationStore(edges)
    try:
        return rs.gather_recall(eng, now=NOW, **kw)
    finally:
        gstore.RelationStore = orig


def _fixture():
    relevant = [
        {"id": "v1", "summary": "first take", "tier": "verified", "access_count": 5},
        {"id": "v2", "summary": "current take", "tier": "verified", "access_count": 2},
        {"id": "k3", "summary": "unrelated lesson", "tier": "staging"},
    ]
    edges = [{"src": "v2", "rel": "supersedes", "dst": "v1"}]
    return _FakeEngram(relevant=relevant, edges=edges), edges


def test_recall_meta_surfaces_version_chain_heads():
    eng, edges = _fixture()
    payload = _gather(eng, edges)
    vcmeta = payload["meta"]["version_chain"]
    assert vcmeta["collapsed"] == 1          # v1 hidden behind v2
    assert vcmeta["heads_present"] == 1      # v2 is a surfaced HEAD


def test_recall_render_shows_head_count_no_leak():
    eng, edges = _fixture()
    payload = _gather(eng, edges)
    text = rs.render_recall_text(payload)
    assert "HEAD" in text or "current version" in text.lower()
    # Render stays metadata-only - no internal id/tier dumps for hidden versions.
    assert "first take" not in text  # superseded body never surfaces


def test_recall_no_edges_is_safe_zero():
    eng, _ = _fixture()
    payload = _gather(eng, [])
    vcmeta = payload["meta"]["version_chain"]
    assert vcmeta["collapsed"] == 0
    assert vcmeta["heads_present"] == 0


# --- resume brief annotation (real Engram) ---------------------------------


def _make_engine(tmp_path: Path):
    from piia_engram.core import Engram

    return Engram(root=tmp_path)


def test_resume_brief_annotates_version_chains(tmp_path):
    eng = _make_engine(tmp_path)
    a = eng.add_lesson(
        "initial approach to recall collapse",
        tier="verified",
        project_folder=str(tmp_path),
    )
    b = eng.add_lesson(
        "revised approach to recall head selection logic",
        tier="verified",
        project_folder=str(tmp_path),
    )
    # v4.19.1: version lineage is internal-only — seed the chain directly
    gstore.RelationStore(tmp_path).add_relation(b["id"], "supersedes", a["id"])

    brief = eng.get_resume_brief(project_folder=str(tmp_path))
    md = brief["markdown"]
    # An additive, metadata-only annotation noting a version chain exists.
    assert "version" in md.lower()
    assert "superseded" in md.lower() or "HEAD" in md
    assert "initial approach to recall collapse" not in md
    assert "[HEAD] revised approach to recall head selection logic" in md


def test_resume_brief_no_chains_omits_annotation(tmp_path):
    eng = _make_engine(tmp_path)
    eng.add_lesson("a standalone lesson with no version chain at all", tier="verified")
    brief = eng.get_resume_brief(project_folder=str(tmp_path))
    # No supersedes edges -> no version-chain handoff line.
    assert "superseded older version" not in brief["markdown"].lower()
