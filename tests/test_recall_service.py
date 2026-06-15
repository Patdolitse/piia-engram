"""Tests for the recall gather/render service (Phase 6).

Uses a lightweight duck-typed fake Engram so the gather layer is tested without
touching a real store (the underlying read methods have their own tests).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from piia_engram import recall_service as rs


class FakeRelationStore:
    def __init__(self, edges):
        self._edges = edges

    def all_edges(self):
        return self._edges


class FakeEngram:
    def __init__(self, *, profile=None, recent=None, relevant=None,
                 search=None, root=None):
        self._profile = profile or {}
        self._recent = recent or []
        self._relevant = relevant or []
        self._search = search or {}
        self.root = root

    def get_safe_profile(self):
        return self._profile

    def get_recent_context(self, limit=1):
        return self._recent[:limit]

    def get_relevant_lessons(self, project_folder=None, limit=8, _update_access=True):
        return list(self._relevant)

    def search_knowledge(self, query, scope="all", limit=10):
        return self._search


def _now():
    return datetime(2026, 6, 3, tzinfo=timezone.utc)


def test_identity_slice_keeps_stable_fields_only():
    eng = FakeEngram(profile={
        "role": "codex_tester",
        "language": "zh",
        "technical_level": "non-technical",
        "preferences": ["GUI", "fast iteration"],
        "secret_api_key": "should-not-appear",
    })
    payload = rs.gather_recall(eng, now=_now())
    ident = payload["identity"]
    assert ident["role"] == "codex_tester"
    assert ident["language"] == "zh"
    assert ident["preferences"] == ["GUI", "fast iteration"]
    assert "secret_api_key" not in ident
    assert "should-not-appear" not in repr(payload)


class RichIdentityEngram(FakeEngram):
    """Fake with the dedicated identity sub-stores (preferences / quality)."""

    def __init__(self, *, preferences=None, quality=None, boundaries=None, **kw):
        super().__init__(**kw)
        self._preferences = preferences or {}
        self._quality = quality or {}
        self._boundaries = boundaries or {}

    def get_preferences(self):
        return self._preferences

    def get_quality_standards(self):
        return self._quality

    def get_trust_boundaries(self):
        return self._boundaries


def test_identity_slice_merges_dedicated_identity_stores():
    eng = RichIdentityEngram(
        profile={"role": "builder", "language": "zh"},
        preferences={
            "work_patterns": {"语言": "中文沟通", "界面": "偏好GUI"},
            "communication": "直接简洁",
            "tool_preferences": {"codex": "primary"},
        },
        quality={"acceptance_threshold": 4, "rules": ["验证后再上线"]},
    )
    payload = rs.gather_recall(eng, now=_now())
    ident = payload["identity"]
    assert ident["work_patterns"] == ["语言: 中文沟通", "界面: 偏好GUI"]
    assert "codex: primary" in ident["preferences"]
    assert "communication: 直接简洁" in ident["preferences"]
    assert ident["quality_standards"] == ["验证后再上线"]


def test_identity_merge_never_overwrites_profile_values():
    eng = RichIdentityEngram(
        profile={"role": "builder", "work_patterns": ["profile wins"]},
        preferences={"work_patterns": {"界面": "should not replace"}},
    )
    payload = rs.gather_recall(eng, now=_now())
    assert payload["identity"]["work_patterns"] == ["profile wins"]


def test_identity_merge_honors_trust_boundary_restrictions():
    eng = RichIdentityEngram(
        profile={"role": "builder"},
        preferences={"work_patterns": {"界面": "GUI"}},
        quality={"rules": ["rule"]},
        boundaries={"restricted_fields": ["work_patterns", "quality_standards"]},
    )
    payload = rs.gather_recall(eng, now=_now())
    assert "work_patterns" not in payload["identity"]
    assert "quality_standards" not in payload["identity"]


def test_recent_activity_digest_no_body():
    eng = FakeEngram(recent=[{
        "tool": "claude_code",
        "session_id": "s1",
        "modified_at": "2026-06-03T03:08:00",
        "content": "FULL SESSION BODY SHOULD NOT LEAK",
    }])
    payload = rs.gather_recall(eng, now=_now())
    act = payload["recent_activity"]
    assert act["last_tool"] == "claude_code"
    assert act["session_id"] == "s1"
    assert "FULL SESSION BODY" not in repr(payload)


def test_query_folds_in_search_results():
    eng = FakeEngram(
        relevant=[{"id": "L1", "summary": "relevant lesson"}],
        search={"lessons": [{"id": "L2", "summary": "query hit"}],
                "decisions": [{"id": "D1", "question": "q", "choice": "c"}]},
    )
    payload = rs.gather_recall(eng, query="anything", now=_now())
    summaries = {k.get("summary") or k.get("choice") for k in payload["knowledge"]}
    assert "relevant lesson" in summaries
    assert "query hit" in summaries
    assert "c" in summaries


def test_no_query_skips_search():
    eng = FakeEngram(
        relevant=[{"id": "L1", "summary": "relevant"}],
        search={"lessons": [{"id": "L2", "summary": "should not appear"}]},
    )
    payload = rs.gather_recall(eng, now=_now())
    assert "should not appear" not in repr(payload)


def test_version_collapse_hides_superseded():
    eng = FakeEngram(
        relevant=[{"id": "v1", "summary": "old version"},
                  {"id": "v2", "summary": "new version"}],
        root="/fake/root",
    )
    edges = [{"src": "v2", "rel": "supersedes", "dst": "v1"}]
    # Patch the relation loader to return our edges.
    import piia_engram.governance_store as gstore
    orig = gstore.RelationStore
    gstore.RelationStore = lambda root: FakeRelationStore(edges)
    try:
        payload = rs.gather_recall(eng, now=_now())
    finally:
        gstore.RelationStore = orig
    summaries = {k.get("summary") for k in payload["knowledge"]}
    assert "new version" in summaries
    assert "old version" not in summaries
    assert payload["meta"]["collapsed_versions"] == 1


def test_collapse_disabled_keeps_both():
    eng = FakeEngram(
        relevant=[{"id": "v1", "summary": "old"}, {"id": "v2", "summary": "new"}],
        root="/fake/root",
    )
    payload = rs.gather_recall(eng, collapse_versions=False, now=_now())
    assert payload["meta"]["collapsed_versions"] == 0


def test_gather_is_resilient_to_failing_methods():
    class Broken:
        root = None

        def get_safe_profile(self):
            raise RuntimeError("boom")

        def get_recent_context(self, limit=1):
            raise RuntimeError("boom")

        def get_relevant_lessons(self, **kw):
            raise RuntimeError("boom")

    payload = rs.gather_recall(Broken(), now=_now())
    # Degrades to empty slices rather than raising.
    assert payload["identity"] == {}
    assert payload["recent_activity"] == {}
    assert payload["knowledge"] == []


def test_render_text_smoke():
    eng = FakeEngram(
        profile={"role": "tester", "language": "zh"},
        relevant=[{"id": "L1", "summary": "a lesson", "domain": "python"}],
    )
    payload = rs.gather_recall(eng, project_folder="proj", now=_now())
    text = rs.render_recall_text(payload)
    assert "Recall digest" in text
    assert "tester" in text
    assert "a lesson" in text


def test_render_text_includes_context_usage_footer():
    eng = FakeEngram(
        relevant=[{"id": f"L{i}", "summary": "x" * 200} for i in range(4)],
    )
    payload = rs.gather_recall(eng, token_budget=60, now=_now())
    text = rs.render_recall_text(payload)

    assert "context usage:" in text
    assert "returned=" in text
    assert "trimmed=" in text


def test_render_text_includes_labeling_state():
    eng = FakeEngram(
        relevant=[{
            "id": "L1",
            "summary": "a validated lesson",
            "labeling": {
                "source_kind": "agent",
                "annotation_quality": "mature",
                "validation_state": "validated",
                "signals": ["has_last_validated_at"],
            },
        }],
    )
    payload = rs.gather_recall(eng, now=_now())
    text = rs.render_recall_text(payload)

    assert "validated/mature" in text


def test_role_scoped_memory_filters_owner_recall_when_governance_enabled(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
    monkeypatch.setenv("ENGRAM_CALLER_ROLE", "reviewer")

    eng = FakeEngram(
        root=tmp_path,
        relevant=[
            {"id": "pub", "summary": "public ok", "sensitivity": "public"},
            {"id": "work", "summary": "work ok", "sensitivity": "work"},
            {"id": "secret", "summary": "secret hidden", "sensitivity": "secret"},
        ],
    )

    payload = rs.gather_recall(
        eng,
        role_scoped_memory=True,
        now=_now(),
    )

    assert [item["summary"] for item in payload["knowledge"]] == [
        "public ok",
        "work ok",
    ]
    usage = payload["meta"]["context_usage"]
    assert usage["role_scope"]["enabled"] is True
    assert usage["role_scope"]["filtered"] == 1
    assert usage["role_scope"]["max_sensitivity"] == "work"
    assert not any(item.get("governance_withheld") for item in payload["knowledge"])


def test_role_scoped_memory_is_noop_when_governance_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
    monkeypatch.setenv("ENGRAM_CALLER_ROLE", "reviewer")
    eng = FakeEngram(
        root=tmp_path,
        relevant=[
            {"id": "pub", "summary": "public ok", "sensitivity": "public"},
            {"id": "secret", "summary": "secret still visible", "sensitivity": "secret"},
        ],
    )

    base = rs.gather_recall(eng, role_scoped_memory=False, now=_now())
    scoped = rs.gather_recall(eng, role_scoped_memory=True, now=_now())

    assert scoped == base
    assert scoped["meta"]["context_usage"]["role_scope"]["enabled"] is False
    assert not (tmp_path / "governance_ledger.jsonl").exists()


def test_same_store_role_scope_changes_only_when_governance_enabled(
    tmp_path, monkeypatch
):
    eng = FakeEngram(
        root=tmp_path,
        relevant=[
            {"id": "pub", "summary": "public remains", "sensitivity": "public"},
            {"id": "work", "summary": "work remains", "sensitivity": "work"},
            {"id": "secret", "summary": "secret filtered", "sensitivity": "secret"},
        ],
    )
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
    monkeypatch.setenv("ENGRAM_CALLER_ROLE", "reviewer")

    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    governance_off = rs.gather_recall(
        eng,
        role_scoped_memory=True,
        now=_now(),
    )

    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    governance_on = rs.gather_recall(
        eng,
        role_scoped_memory=True,
        now=_now(),
    )

    assert [item["summary"] for item in governance_off["knowledge"]] == [
        "public remains",
        "work remains",
        "secret filtered",
    ]
    assert [item["summary"] for item in governance_on["knowledge"]] == [
        "public remains",
        "work remains",
    ]
    assert governance_off["meta"]["context_usage"]["role_scope"]["enabled"] is False
    assert governance_on["meta"]["context_usage"]["role_scope"]["enabled"] is True
    assert governance_on["meta"]["context_usage"]["role_scope"]["filtered"] == 1


def test_role_scoped_memory_disabled_writes_no_disclosure_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "cli")
    monkeypatch.setenv("ENGRAM_CALLER_ROLE", "reviewer")
    eng = FakeEngram(
        root=tmp_path,
        relevant=[{"id": "secret", "summary": "secret", "sensitivity": "secret"}],
    )

    payload = rs.gather_recall(eng, role_scoped_memory=False, now=_now())

    assert payload["knowledge"][0]["summary"] == "secret"
    assert payload["meta"]["context_usage"]["role_scope"]["enabled"] is False
    assert not (tmp_path / "governance_ledger.jsonl").exists()
