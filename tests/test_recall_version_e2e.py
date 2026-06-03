"""L4: recall + version-chain end-to-end determinism & source-explainability.

Proves, on realistic fixture data, that:
- recall ordering and budget-trim are deterministic (byte-identical across runs);
- version-chain reconstruction is deterministic and metadata-only;
- the recall surface projects knowledge to a stable, leak-free view (no internal
  bookkeeping fields like id/access_count/tier/embedding escape).

CLI/helper level only — no MCP tool surface is touched (it stays deferred).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import piia_engram.governance_store as gstore
from piia_engram import recall_service as rs
from piia_engram import version_chain as vc


NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


class _FakeRelationStore:
    def __init__(self, edges):
        self._edges = edges

    def all_edges(self):
        return list(self._edges)


class _FakeEngram:
    """Duck-typed Engram over fixed fixture data (mirrors test_recall_service)."""

    def __init__(self, *, profile, recent, relevant, search, root="/fake/root"):
        self._profile = profile
        self._recent = recent
        self._relevant = relevant
        self._search = search
        self.root = root

    def get_safe_profile(self):
        return dict(self._profile)

    def get_recent_context(self, limit=1):
        return [dict(r) for r in self._recent[:limit]]

    def get_relevant_lessons(self, project_folder=None, limit=8, _update_access=True):
        return [dict(e) for e in self._relevant]

    def search_knowledge(self, query, scope="all", limit=10):
        return json.loads(json.dumps(self._search))  # deep copy


def _fixture_engram():
    profile = {
        "role": "codex_tester", "language": "zh", "technical_level": "non-technical",
        "preferences": ["GUI", "fast iteration"],
        "secret_api_key": "MUST-NOT-LEAK",
    }
    recent = [{
        "tool": "claude_code", "session_id": "s-16",
        "modified_at": "2026-06-03T06:38:00",
        "content": "FULL SESSION BODY MUST NOT LEAK",
    }]
    # v2 supersedes v1; both are "relevant", so collapse must prefer v2.
    relevant = [
        {"id": "v1", "summary": "first take", "domain": "ai",
         "tier": "verified", "access_count": 7,
         "provenance": {"source_agent": "claude_code", "run_id": "r-1"}},
        {"id": "v2", "summary": "current take", "domain": "ai",
         "tier": "verified", "access_count": 2,
         "provenance": {"source_agent": "codex", "run_id": "r-2"}},
        {"id": "k3", "summary": "another lesson", "domain": "engram",
         "tier": "staging", "access_count": 0},
    ]
    search = {
        "lessons": [{"id": "q1", "summary": "query hit lesson", "domain": "infra"}],
        "decisions": [{"id": "qd1", "question": "deploy now?", "choice": "no",
                       "domain": "infra"}],
    }
    edges = [{"src": "v2", "rel": "supersedes", "dst": "v1"}]
    return _FakeEngram(profile=profile, recent=recent, relevant=relevant,
                       search=search), edges


def _gather(eng, edges, **kw):
    orig = gstore.RelationStore
    gstore.RelationStore = lambda root: _FakeRelationStore(edges)
    try:
        return rs.gather_recall(eng, query="deploy", now=NOW, **kw)
    finally:
        gstore.RelationStore = orig


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_recall_payload_is_byte_identical_across_runs():
    eng, edges = _fixture_engram()
    runs = [json.dumps(_gather(eng, edges), ensure_ascii=False, sort_keys=True)
            for _ in range(5)]
    assert len(set(runs)) == 1, "recall payload must be deterministic"


def test_recall_ordering_is_stable_and_collapses_to_head():
    eng, edges = _fixture_engram()
    payload = _gather(eng, edges)
    labels = [k.get("summary") or k.get("choice") for k in payload["knowledge"]]
    # v1 is superseded by v2 → dropped; order is relevant-first then query-only.
    assert labels == ["current take", "another lesson", "query hit lesson", "no"]
    assert payload["meta"]["collapsed_versions"] == 1


def test_budget_trim_is_deterministic():
    eng, edges = _fixture_engram()
    # A tiny budget forces trimming; the kept set + excluded count must be stable.
    runs = []
    for _ in range(4):
        p = _gather(eng, edges, token_budget=20)
        runs.append(([k.get("summary") or k.get("choice") for k in p["knowledge"]],
                     p["meta"]["governance"]["excluded_count"]))
    assert all(r == runs[0] for r in runs)
    kept, excluded = runs[0]
    assert len(kept) >= 1          # always at least one item survives
    assert excluded >= 1           # and the tiny budget did exclude some


# ---------------------------------------------------------------------------
# Source-explainability without internal-field leakage
# ---------------------------------------------------------------------------

_ALLOWED_ITEM_KEYS = {"type", "summary", "question", "choice", "domain",
                      "provenance", "freshness"}
_ALLOWED_PROV_KEYS = {"source_agent", "run_id", "last_validated_at"}
_INTERNAL_FIELDS = ("access_count", "tier", "id", "embedding", "secret_api_key")


def test_recall_items_expose_only_projected_fields():
    eng, edges = _fixture_engram()
    payload = _gather(eng, edges)
    blob = json.dumps(payload, ensure_ascii=False)
    assert "MUST-NOT-LEAK" not in blob
    assert "FULL SESSION BODY" not in blob
    for item in payload["knowledge"]:
        assert set(item) <= _ALLOWED_ITEM_KEYS, set(item) - _ALLOWED_ITEM_KEYS
        prov = item.get("provenance", {})
        assert set(prov) <= _ALLOWED_PROV_KEYS, set(prov) - _ALLOWED_PROV_KEYS
        for internal in _INTERNAL_FIELDS:
            assert internal not in item


def test_provenance_is_source_explainable():
    eng, edges = _fixture_engram()
    payload = _gather(eng, edges)
    head = next(k for k in payload["knowledge"] if k.get("summary") == "current take")
    # The surviving head keeps its own provenance, explaining where it came from.
    assert head["provenance"]["source_agent"] == "codex"
    assert head["provenance"]["run_id"] == "r-2"


# ---------------------------------------------------------------------------
# Version-chain reconstruction determinism (pure layer)
# ---------------------------------------------------------------------------

def _version_edges():
    return [
        {"src": "b", "rel": "supersedes", "dst": "a"},
        {"src": "c", "rel": "supersedes", "dst": "b"},
        {"src": "y", "rel": "led_to", "dst": "z"},
    ]


def test_version_report_is_deterministic_and_metadata_only():
    edges = _version_edges()
    reports = [json.dumps(vc.build_version_report(edges), sort_keys=True)
               for _ in range(5)]
    assert len(set(reports)) == 1
    report = vc.build_version_report(edges)
    # Topics are keyed by the smallest id and sorted → stable seeds.
    assert [t["seed"] for t in report["topics"]] == ["a", "y"]
    # Metadata-only: ids + counts, no content fields anywhere.
    blob = json.dumps(report)
    for marker in ("summary", "choice", "question", "body", "content"):
        assert marker not in blob


def test_lineage_reconstruction_is_deterministic():
    edges = _version_edges()
    runs = [json.dumps(vc.lineage("a", edges), sort_keys=True) for _ in range(4)]
    assert len(set(runs)) == 1
    head = vc.resolve_heads("a", edges)
    assert head == ["c"]
