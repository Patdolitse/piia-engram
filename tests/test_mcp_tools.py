"""Tests for MCP tool wrappers in piia_engram.mcp_server.

Each ``@mcp.tool()`` is a thin async wrapper around an Engram method. These
tests verify:
- the wrapper actually invokes the Engram method
- empty results return user-friendly strings (not raw "[]" / "{}")
- error paths inside the wrapper are caught and surface a readable error
- ``_apply_tool_tier`` correctly filters tools when ``ENGRAM_TOOLS=core``
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_engram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    """Replace the module-level ``_engram`` with a fresh instance in tmp_path.

    Tools read the global ``mcp_server._engram`` directly, so we patch the
    attribute rather than the underlying Engram class.

    Also resets ``_session`` to prevent test tool calls from leaking into the
    real ``~/.engram/`` directory via the atexit handler.

    M6 fix: stop the OLD tracker's heartbeat thread before replacing it,
    and disable heartbeat on the new one to keep tests deterministic.
    """
    # Stop old heartbeat thread to prevent cross-test leaks (M6).
    old_session = mcp_server._session
    old_session._stop_event.set()
    if old_session._heartbeat_thread is not None:
        old_session._heartbeat_thread.join(timeout=2.0)

    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    # Disable heartbeat for test tracker to avoid daemon thread noise.
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    # Prevent auto-bootstrap from scanning the real home dir in tests.
    (tmp_path / ".bootstrap_done").write_text("1", encoding="utf-8")
    return engram


def _run(coro):
    """Helper to run an async tool synchronously in tests."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Identity tool wrappers
# ---------------------------------------------------------------------------


class TestIdentityTools:
    def test_profile_facet_empty_returns_json_dict(self, isolated_engram: Engram):
        result = _run(mcp_server.get_identity_facets(facet="profile", safe=True))
        # Empty profile → JSON "{}" (not a human-readable fallback)
        assert json.loads(result) == {}

    def test_profile_facet_returns_filled_data(self, isolated_engram: Engram):
        isolated_engram.update_profile({"role": "engineer", "language": "zh"})
        result = _run(mcp_server.get_identity_facets(facet="profile", safe=True))
        parsed = json.loads(result)
        assert parsed["role"] == "engineer"
        assert parsed["language"] == "zh"

    def test_profile_facet_safe_filters_restricted_fields(
        self, isolated_engram: Engram
    ):
        """Restricted fields in trust_boundaries must be excluded when safe=True."""
        isolated_engram.update_profile(
            {"role": "engineer", "email": "secret@example.com"}
        )
        isolated_engram.update_trust_boundaries({"restricted_fields": ["email"]})
        safe_result = json.loads(
            _run(mcp_server.get_identity_facets(facet="profile", safe=True))
        )
        assert "email" not in safe_result
        assert safe_result["role"] == "engineer"

    def test_preferences_facet_returns_json(self, isolated_engram: Engram):
        isolated_engram.update_preferences({"work_patterns": {"pace": "fast"}})
        result = json.loads(
            _run(mcp_server.get_identity_facets(facet="preferences"))
        )
        assert result["work_patterns"] == {"pace": "fast"}

    def test_trust_boundaries_facet_returns_defaults(self, isolated_engram: Engram):
        result = json.loads(
            _run(mcp_server.get_identity_facets(facet="trust_boundaries"))
        )
        # Defaults are written on init
        assert "default_sharing" in result

    def test_quality_standards_facet_returns_dict(self, isolated_engram: Engram):
        isolated_engram.update_quality_standards({"acceptance_threshold": 4})
        result = json.loads(
            _run(mcp_server.get_identity_facets(facet="quality_standards"))
        )
        assert result["acceptance_threshold"] == 4

    def test_all_facets_aggregate(self, isolated_engram: Engram):
        """facet="all" (default) returns one dict with every facet keyed by name."""
        isolated_engram.update_profile({"role": "engineer"})
        result = json.loads(_run(mcp_server.get_identity_facets()))
        assert set(result) == {
            "profile",
            "preferences",
            "trust_boundaries",
            "work_style",
            "quality_standards",
            "domains",
        }
        assert result["profile"]["role"] == "engineer"

    def test_unknown_facet_returns_error_hint(self, isolated_engram: Engram):
        result = _run(mcp_server.get_identity_facets(facet="nope"))
        assert "unknown facet" in result
        assert "profile" in result  # hint lists valid facet names

    def test_domains_facet_empty_friendly_message(self, isolated_engram: Engram):
        result = _run(mcp_server.get_identity_facets(facet="domains"))
        assert "尚无" in result


# ---------------------------------------------------------------------------
# Knowledge read tool wrappers
# ---------------------------------------------------------------------------


class TestKnowledgeReadTools:
    def test_get_lessons_empty_returns_friendly_message(
        self, isolated_engram: Engram
    ):
        result = _run(mcp_server.get_lessons())
        assert "尚无" in result  # friendly empty message, not "[]"
        assert not result.startswith("[")

    def test_get_lessons_returns_added_lesson(self, isolated_engram: Engram):
        isolated_engram.add_lesson({"summary": "测试经验", "domain": "test"})
        result = _run(mcp_server.get_lessons(limit=10))
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert any(l.get("summary") == "测试经验" for l in parsed)

    def test_get_decisions_empty_returns_friendly_message(
        self, isolated_engram: Engram
    ):
        result = _run(mcp_server.get_decisions())
        assert "尚无" in result
        assert not result.startswith("[")

    def test_get_decisions_filters_by_domain(self, isolated_engram: Engram):
        isolated_engram.add_decision(
            {"question": "选 A 还是 B?", "choice": "A", "domain": "architecture"}
        )
        isolated_engram.add_decision(
            {"question": "用 X 库", "choice": "X", "domain": "python"}
        )
        result = _run(mcp_server.get_decisions(domain="architecture"))
        parsed = json.loads(result)
        assert all("architecture" in d.get("domain", "") for d in parsed)

    def test_get_project_context_missing_returns_friendly_message(
        self, isolated_engram: Engram
    ):
        result = _run(mcp_server.get_project_context(project_folder="/no/such"))
        assert "未找到" in result

    def test_get_project_context_returns_snapshot(self, isolated_engram: Engram):
        isolated_engram.save_project_snapshot(
            "/path/to/proj", {"title": "MyProj", "tech_stack": ["python"]}
        )
        result = json.loads(_run(mcp_server.get_project_context("/path/to/proj")))
        assert result["title"] == "MyProj"

    def test_list_projects_empty(self, isolated_engram: Engram):
        result = _run(mcp_server.list_projects())
        assert "尚无" in result


# ---------------------------------------------------------------------------
# Context tool wrappers
# ---------------------------------------------------------------------------


class TestContextTools:
    def test_get_user_context_empty_returns_hint(self, isolated_engram: Engram):
        """Empty Engram should hint that the user is new — not empty string."""
        result = _run(mcp_server.get_user_context())
        # Either the cold-start hint or the explicit "new user" sentinel
        assert "Engram" in result or "用户" in result

    def test_get_user_context_after_setup_includes_profile(
        self, isolated_engram: Engram
    ):
        isolated_engram.update_profile({"role": "engineer", "language": "zh"})
        result = _run(mcp_server.get_user_context())
        assert "engineer" in result

    def test_get_identity_card_empty_returns_message(
        self, isolated_engram: Engram
    ):
        """Identity card with no data still produces a card frame (export writes a file).

        It should at least be a non-empty string.
        """
        result = _run(mcp_server.get_identity_card())
        # Card frame is always emitted (just headers), should not be the
        # "尚未积累足够" sentinel unless export returns empty string
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Knowledge write tool wrappers
# ---------------------------------------------------------------------------


class TestKnowledgeWriteTools:
    def test_add_lesson_persists(self, isolated_engram: Engram):
        result = _run(
            mcp_server.add_lesson(
                summary="测试要点", detail="详情", domain="python"
            )
        )
        assert "测试要点" in result
        # Verify it actually landed in the Engram
        lessons = isolated_engram.get_lessons()
        assert any(l.get("summary") == "测试要点" for l in lessons)

    def test_add_lesson_duplicate_returns_status(self, isolated_engram: Engram):
        _run(mcp_server.add_lesson(summary="独特的测试经验内容来防止误判"))
        result2 = _run(mcp_server.add_lesson(summary="独特的测试经验内容来防止误判"))
        parsed = json.loads(result2)
        assert parsed.get("status") == "duplicate"

    def test_add_decision_persists(self, isolated_engram: Engram):
        result = _run(
            mcp_server.add_decision(
                question="使用什么库?", choice="library-X", reasoning="性能更好"
            )
        )
        assert isinstance(result, str)
        decisions = isolated_engram.get_decisions()
        assert any(d.get("question") == "使用什么库?" for d in decisions)


# ---------------------------------------------------------------------------
# Search tool wrappers
# ---------------------------------------------------------------------------


class TestSearchTools:
    def test_search_knowledge_finds_lesson(self, isolated_engram: Engram):
        isolated_engram.add_lesson(
            {"summary": "pytest fixture 复用很重要", "domain": "python"}
        )
        result = json.loads(_run(mcp_server.search_knowledge("pytest")))
        assert isinstance(result, dict)
        assert result.get("lessons")
        assert any("pytest" in l.get("summary", "") for l in result["lessons"])

    def test_search_knowledge_empty_query_returns_empty_results(
        self, isolated_engram: Engram
    ):
        isolated_engram.add_lesson({"summary": "some lesson"})
        result = json.loads(_run(mcp_server.search_knowledge("")))
        assert result["lessons"] == []
        assert result["decisions"] == []
        assert result["playbooks"] == []
        # a3: permissions metadata is always present
        assert "_caller_permissions" in result

    def test_search_knowledge_playbooks_include_usage_policy(
        self, isolated_engram: Engram
    ):
        isolated_engram.add_playbook({
            "title": "Release checklist",
            "triggers": ["release", "publish"],
            "steps": ["Run tests", "Verify package"],
        })

        result = json.loads(_run(
            mcp_server.search_knowledge("release", scope="playbooks")
        ))

        assert result["playbooks"]
        policy = result["playbooks"][0]["usage_policy"]
        assert "被动参考" in policy
        assert "passive reference" in policy
        assert "Do not auto-drive" in policy

    def test_mcp_execution_status_exposes_outcome_rollup(
        self, isolated_engram: Engram
    ):
        pb = isolated_engram.add_playbook({
            "title": "MCP outcome flow",
            "triggers": ["outcome"],
            "steps": ["Prepare", "Optional cleanup", "Verify"],
        })

        plan = json.loads(_run(
            mcp_server.playbook_execution("prepare", pb["id"])
        ))
        assert "step-by-step confirmation" in plan["usage_policy"]

        update = json.loads(_run(
            mcp_server.playbook_execution(
                "update_step", pb["id"], step_order=1, step_status="completed"
            )
        ))
        assert update["outcome"]["status"] == "partial"

        json.loads(_run(
            mcp_server.playbook_execution(
                "update_step",
                pb["id"],
                step_order=2,
                step_status="skipped",
                notes="Not needed",
            )
        ))
        status = json.loads(_run(
            mcp_server.playbook_execution("status", pb["id"])
        ))

        assert status["outcome"] == {
            "status": "partial",
            "completed": 1,
            "skipped": 1,
            "failed": 0,
            "pending": 1,
            "total": 3,
        }
        assert "step-by-step confirmation" in status["usage_policy"]

    def test_mcp_prepare_playbook_execution_returns_resolved_tools(
        self, isolated_engram: Engram
    ):
        registered = isolated_engram.register_tool({
            "name": "gh",
            "path": "/tools/gh",
            "version": "2.88.1",
            "purpose": "GitHub CLI",
        })
        pb = isolated_engram.add_playbook({
            "title": "MCP tool-aware flow",
            "triggers": ["tools"],
            "required_tools": [{"name": "gh", "purpose": "GitHub release"}],
            "steps": ["Prepare", "Verify"],
        })

        result = json.loads(_run(
            mcp_server.playbook_execution("prepare", pb["id"])
        ))

        assert result["tools_ready"] is True
        assert result["missing_tools"] == []
        assert result["resolved_tools"][0]["status"] == "resolved"
        assert result["resolved_tools"][0]["tool_id"] == registered["id"]
        assert result["resolved_tools"][0]["path"] == "/tools/gh"
        assert "step-by-step confirmation" in result["usage_policy"]

    def test_mcp_add_playbook_accepts_required_tools_json_and_tool_refs(
        self, isolated_engram: Engram
    ):
        result = _run(mcp_server.add_playbook(
            "MCP declared tool flow",
            "tools",
            steps_json='["Prepare", "Verify"]',
            required_tools_json='[{"name": "gh", "purpose": "GitHub release"}]',
            tool_refs="Node.js",
        ))

        assert "Playbook 已记录" in result
        stored = isolated_engram.get_playbooks()[0]
        assert stored["required_tools"] == [
            {
                "name": "gh",
                "purpose": "GitHub release",
                "optional": False,
                "min_version": "",
                "query": "",
            },
            {
                "name": "Node.js",
                "purpose": "",
                "optional": False,
                "min_version": "",
                "query": "",
            },
        ]

    def test_mcp_update_playbook_accepts_required_tools_json_and_tool_refs(
        self, isolated_engram: Engram
    ):
        pb = isolated_engram.add_playbook({
            "title": "MCP update tool flow",
            "triggers": ["tools"],
            "steps": ["Prepare", "Verify"],
        })

        result = _run(mcp_server.manage_playbook(
            "update",
            pb["id"],
            required_tools_json='[{"name": "mcp-publisher"}]',
            tool_refs="gh",
        ))

        assert "Playbook 已更新" in result
        stored = isolated_engram.get_playbook(pb["id"], _update_access=False)
        assert [tool["name"] for tool in stored["required_tools"]] == [
            "mcp-publisher",
            "gh",
        ]


class TestSearchKnowledgeResultSize:
    """Round 2: result-size discipline for the search_knowledge MCP wrapper.

    A few large knowledge bodies must not blow up the MCP client (this dev
    session hit a ~68 KB result twice). Truncation lives at the MCP boundary
    only — ``Engram.search_knowledge`` (reused by the CLI and recall_service)
    stays full-fidelity. ``max_field_chars=0`` is a same-tier escape hatch.
    """

    # ---- pure helper unit tests ----

    def test_truncate_helper_leaves_short_strings(self):
        from piia_engram import mcp_tools_read as m
        obj = {"summary": "short", "n": 3, "ok": True}
        assert m._truncate_long_strings(obj, 400) == obj

    def test_truncate_helper_clips_long_string_with_marker(self):
        from piia_engram import mcp_tools_read as m
        body = "x" * 5000
        out = m._truncate_long_strings({"detail": body}, 400)
        assert out["detail"] != body
        assert len(out["detail"]) < 600          # 400 head + short marker
        assert "truncated" in out["detail"]
        assert out["detail"].startswith("x" * 400)

    def test_truncate_helper_recurses_lists_and_nested_dicts(self):
        from piia_engram import mcp_tools_read as m
        body = "y" * 5000
        out = m._truncate_long_strings(
            {"steps": [{"action": body}, body], "meta": {"note": body}}, 400
        )
        assert "truncated" in out["steps"][0]["action"]
        assert "truncated" in out["steps"][1]
        assert "truncated" in out["meta"]["note"]

    def test_truncate_helper_preserves_dict_keys(self):
        from piia_engram import mcp_tools_read as m
        long_key = "k" * 5000          # key itself is long
        body = "z" * 5000
        out = m._truncate_long_strings({long_key: body}, 400)
        assert long_key in out                    # key untouched, never truncated
        assert "truncated" in out[long_key]       # only the value is clipped

    def test_truncate_helper_zero_means_unlimited(self):
        from piia_engram import mcp_tools_read as m
        obj = {"detail": "w" * 5000}
        assert m._truncate_long_strings(obj, 0) == obj

    def test_truncate_helper_does_not_mutate_input(self):
        from piia_engram import mcp_tools_read as m
        body = "x" * 5000
        original = {"detail": body, "steps": [{"action": body}]}
        out = m._truncate_long_strings(original, 400)
        # input untouched at every depth; output is a fresh, clipped structure
        assert original["detail"] == body
        assert original["steps"][0]["action"] == body
        assert out is not original
        assert "truncated" in out["detail"]
        assert "truncated" in out["steps"][0]["action"]

    # ---- integration via the MCP wrapper ----

    def test_search_knowledge_truncates_long_detail_by_default(
        self, isolated_engram: Engram
    ):
        isolated_engram.add_lesson({
            "summary": "huge body lesson about pytest",
            "detail": "D" * 8000,
            "domain": "pytest",
        })
        result = json.loads(_run(mcp_server.search_knowledge("pytest")))
        lesson = result["lessons"][0]
        assert lesson["summary"] == "huge body lesson about pytest"   # headline kept
        assert "truncated" in lesson["detail"]                        # body clipped
        assert len(lesson["detail"]) < 1000

    def test_search_knowledge_escape_hatch_returns_full_body(
        self, isolated_engram: Engram
    ):
        isolated_engram.add_lesson({
            "summary": "huge body lesson about pytest",
            "detail": "D" * 8000,
            "domain": "pytest",
        })
        result = json.loads(_run(
            mcp_server.search_knowledge("pytest", max_field_chars=0)
        ))
        assert result["lessons"][0]["detail"] == "D" * 8000          # full fidelity

    def test_search_knowledge_truncates_playbook_steps(
        self, isolated_engram: Engram
    ):
        isolated_engram.add_playbook({
            "title": "Release checklist",
            "triggers": ["release"],
            "steps": ["S" * 4000, "T" * 4000],
        })
        result = json.loads(_run(
            mcp_server.search_knowledge("release", scope="playbooks")
        ))
        steps = result["playbooks"][0]["steps"]
        assert steps
        # steps are stored as list[dict] ({order, action, detail}); assert on
        # the serialized step so the check is robust to that shape — each long
        # step content must be clipped and the whole step bounded.
        for step in steps:
            blob = json.dumps(step, ensure_ascii=False)
            assert "truncated" in blob
            assert len(blob) < 1000
        # usage_policy is injected AFTER truncation → must survive verbatim
        assert "Do not auto-drive" in result["playbooks"][0]["usage_policy"]

    def test_search_knowledge_default_result_bytes_bounded(
        self, isolated_engram: Engram
    ):
        # Fill all three buckets with large bodies — the real 68 KB failure mode.
        for i in range(8):
            isolated_engram.add_lesson({
                "summary": f"perf lesson {i}",
                "detail": "L" * 6000,
                "domain": "perf",
            })
            isolated_engram.add_decision({
                "question": f"perf decision {i}",
                "choice": "do it",
                "reasoning": "R" * 6000,
                "domain": "perf",
            })
        isolated_engram.add_playbook({
            "title": "perf playbook",
            "triggers": ["perf"],
            "steps": ["P" * 3000, "Q" * 3000, "U" * 3000],
            "description": "B" * 6000,
        })
        truncated = _run(mcp_server.search_knowledge("perf"))
        full = _run(mcp_server.search_knowledge("perf", max_field_chars=0))
        # measure real UTF-8 bytes (bilingual usage_policy makes char count
        # underestimate the wire size the MCP client must absorb)
        truncated_bytes = len(truncated.encode("utf-8"))
        full_bytes = len(full.encode("utf-8"))
        assert truncated_bytes < full_bytes * 0.5   # discipline shrinks the result
        assert truncated_bytes < 50_000             # well under the 68 KB pain point
        td = json.loads(truncated)
        assert td["lessons"] and td["decisions"] and td["playbooks"]
        assert "_caller_permissions" in td          # metadata preserved
        assert all("id" in item for item in td["lessons"])


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_get_user_context_catches_engram_error(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """generate_context that raises must be caught and surface a string error."""

        def explode(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(isolated_engram, "generate_context", explode)
        result = _run(mcp_server.get_user_context())
        assert "失败" in result or "synthetic failure" in result

    def test_get_identity_card_catches_engram_error(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        def explode(*args, **kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(isolated_engram, "export_identity_card", explode)
        result = _run(mcp_server.get_identity_card())
        assert "失败" in result or "synthetic failure" in result


# ---------------------------------------------------------------------------
# Tool tier filtering
# ---------------------------------------------------------------------------


class TestCapabilityModeResolution:
    def test_resolve_capability_modes_defaults_to_core(self):
        assert mcp_server.resolve_capability_modes("") == mcp_server.TIER1_TOOLS
        assert mcp_server.resolve_capability_modes("   ") == mcp_server.TIER1_TOOLS

    def test_resolve_capability_modes_accepts_core_case_and_duplicates(self):
        result = mcp_server.resolve_capability_modes(" core + CORE + Core ")

        assert result == mcp_server.TIER1_TOOLS

    def test_resolve_capability_modes_combines_groups_with_implicit_core(self):
        expected = (
            mcp_server.TIER1_TOOLS
            | mcp_server.CAPABILITY_GROUPS["knowledge"]
            | mcp_server.CAPABILITY_GROUPS["integrations"]
        )

        result = mcp_server.resolve_capability_modes("knowledge + integrations")

        assert result == expected

    def test_resolve_capability_modes_all_overrides_other_tokens(self):
        all_tools = mcp_server.TIER1_TOOLS | frozenset().union(
            *mcp_server.CAPABILITY_GROUPS.values()
        )

        assert mcp_server.resolve_capability_modes("all") == all_tools
        assert mcp_server.resolve_capability_modes("core+governance+all") == all_tools

    def test_resolve_capability_modes_ignores_unknown_tokens(self, capsys):
        expected = mcp_server.TIER1_TOOLS | mcp_server.CAPABILITY_GROUPS["governance"]

        result = mcp_server.resolve_capability_modes("governance + mystery")

        assert result == expected
        err = capsys.readouterr().err
        assert "Unknown ENGRAM_TOOLS token(s)" in err
        assert "mystery" in err
        assert "合法值" in err

    def test_resolve_capability_modes_falls_back_to_core_when_all_tokens_unknown(
        self, capsys
    ):
        result = mcp_server.resolve_capability_modes("mystery + nope")

        assert result == mcp_server.TIER1_TOOLS
        err = capsys.readouterr().err
        assert "falling back to core" in err
        assert "回落 core" in err


class TestToolTier:
    def _filtered_tool_names(
        self,
        raw_mode: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> set[str]:
        fake_tools = {name: object() for name in mcp_server.ALL_CAPABILITY_TOOLS}
        import types

        fake_manager = types.SimpleNamespace(_tools=fake_tools)
        monkeypatch.setattr(mcp_server.mcp, "_tool_manager", fake_manager)
        monkeypatch.setattr(mcp_server, "TOOL_TIER", raw_mode)
        monkeypatch.setattr(
            mcp_server.mcp,
            "remove_tool",
            lambda name: fake_tools.pop(name, None),
        )

        mcp_server._apply_tool_tier()

        return set(fake_tools)

    def test_tier1_tools_set_is_well_known_subset(self):
        """The Tier-1 (core) set must stay a curated subset, not the full API."""
        # Sanity: contains lifecycle + key reads/writes
        assert "get_user_context" in mcp_server.TIER1_TOOLS
        assert "add_lesson" in mcp_server.TIER1_TOOLS
        assert "search_knowledge" in mcp_server.TIER1_TOOLS
        # Sanity: there's something the filter would actually remove
        # (i.e., at least one well-known tool not in TIER1)
        assert "explore_knowledge" not in mcp_server.TIER1_TOOLS

    def test_apply_tool_tier_noop_when_not_core(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """When TOOL_TIER != 'core', the filter should be a no-op."""
        monkeypatch.setattr(mcp_server, "TOOL_TIER", "all")
        # Should not raise even if the internal tool manager shape is unexpected
        mcp_server._apply_tool_tier()

    def test_capability_groups_cover_all_non_core_tools_without_overlap(self):
        grouped_tools = frozenset().union(*mcp_server.CAPABILITY_GROUPS.values())

        assert mcp_server.TIER1_TOOLS | grouped_tools == frozenset(
            mcp_server.TOOL_GOVERNANCE_CLASS
        )
        assert mcp_server.TIER1_TOOLS.isdisjoint(grouped_tools)
        names = sorted(mcp_server.CAPABILITY_GROUPS)
        for left_index, left in enumerate(names):
            for right in names[left_index + 1:]:
                assert mcp_server.CAPABILITY_GROUPS[left].isdisjoint(
                    mcp_server.CAPABILITY_GROUPS[right]
                )

    def test_capability_groups_pin_sensitive_governance_membership(self):
        assert {
            "export_engram",
            "import_engram",
            "export_feedback_report",
        } <= mcp_server.CAPABILITY_GROUPS["admin"]
        assert "manage_caller_trust" in mcp_server.CAPABILITY_GROUPS["governance"]
        assert "onboard_repo" in mcp_server.CAPABILITY_GROUPS["knowledge"]
        assert "onboard_accept" in mcp_server.CAPABILITY_GROUPS["knowledge"]

    def test_core_mode_hides_non_core_owner_only_and_export_owner_only_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        visible = self._filtered_tool_names("core", monkeypatch)
        sensitive = {
            name
            for name, klass in mcp_server.TOOL_GOVERNANCE_CLASS.items()
            if klass in {"owner_only_write", "export_owner_only"}
        }
        non_core_sensitive = sensitive - mcp_server.TIER1_TOOLS

        assert non_core_sensitive
        assert non_core_sensitive.isdisjoint(visible)

    @pytest.mark.parametrize("group_name", sorted(mcp_server.CAPABILITY_GROUPS))
    def test_single_group_modes_register_core_plus_group_count(
        self,
        group_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        visible = self._filtered_tool_names(group_name, monkeypatch)

        assert visible == mcp_server.TIER1_TOOLS | mcp_server.CAPABILITY_GROUPS[group_name]
        assert len(visible) == 17 + len(mcp_server.CAPABILITY_GROUPS[group_name])

    @pytest.mark.parametrize(
        ("raw_mode", "group_names"),
        [
            ("knowledge+governance", ("knowledge", "governance")),
            ("admin + integrations", ("admin", "integrations")),
        ],
    )
    def test_multi_group_modes_register_expected_union(
        self,
        raw_mode: str,
        group_names: tuple[str, ...],
        monkeypatch: pytest.MonkeyPatch,
    ):
        expected = mcp_server.TIER1_TOOLS
        for group_name in group_names:
            expected = expected | mcp_server.CAPABILITY_GROUPS[group_name]

        visible = self._filtered_tool_names(raw_mode, monkeypatch)

        assert visible == expected

    @pytest.mark.parametrize(
        ("raw_mode", "expected"),
        [
            ("", mcp_server.TIER1_TOOLS),
            ("core", mcp_server.TIER1_TOOLS),
            ("all", mcp_server.ALL_CAPABILITY_TOOLS),
        ],
    )
    def test_legacy_core_and_all_modes_keep_v4_registered_sets(
        self,
        raw_mode: str,
        expected: frozenset[str],
        monkeypatch: pytest.MonkeyPatch,
    ):
        visible = self._filtered_tool_names(raw_mode, monkeypatch)

        assert visible == expected


# ---------------------------------------------------------------------------
# Path validation (Phase 3.6)
# ---------------------------------------------------------------------------


class TestPathValidation:
    """``_validate_path`` is the choke point for user-supplied filesystem paths.

    Engram is local-first, so this is NOT a sandboxing boundary — it's a thin
    hygiene check that rejects inputs which silently break downstream OS calls
    (null bytes) or are obvious programming errors (empty / wrong type).
    """

    def test_valid_path_returns_none(self):
        assert mcp_server._validate_path("/tmp/file.json") is None
        assert mcp_server._validate_path("C:\\Users\\me\\engram.json") is None
        assert mcp_server._validate_path("relative/path.json") is None

    def test_null_byte_rejected(self):
        err = mcp_server._validate_path("/tmp/file\x00.json")
        assert err and "NUL" in err

    def test_empty_string_rejected_by_default(self):
        err = mcp_server._validate_path("")
        assert err and "空" in err

    def test_whitespace_only_rejected(self):
        err = mcp_server._validate_path("   ")
        assert err and "空" in err

    def test_empty_allowed_with_flag(self):
        """``allow_empty=True`` permits None/empty (used by optional path args)."""
        assert mcp_server._validate_path(None, allow_empty=True) is None
        assert mcp_server._validate_path("", allow_empty=True) is None

    def test_none_rejected_by_default(self):
        err = mcp_server._validate_path(None)
        assert err and "缺失" in err

    def test_wrong_type_rejected(self):
        err = mcp_server._validate_path(123)  # type: ignore[arg-type]
        assert err and ("字符串" in err or "string" in err)

    def test_import_engram_rejects_null_byte(self, isolated_engram: Engram):
        """The import_engram tool must surface a path error instead of crashing."""
        result = _run(mcp_server.import_engram(input_path="/tmp/x\x00.json"))
        parsed = json.loads(result)
        assert "error" in parsed and "NUL" in parsed["error"]

    def test_import_engram_dry_run_returns_preview_without_mutating(
        self,
        isolated_engram: Engram,
        tmp_path: Path,
    ):
        """MCP import dry-run should expose a metadata-only preview."""
        source = Engram(root=tmp_path / "source")
        source.update_profile({"role": "MCP_INCOMING_SECRET"})
        export_path = source.export_all(str(tmp_path / "backup.json"))

        isolated_engram.update_profile({"role": "MCP_LOCAL_SECRET"})
        result = _run(
            mcp_server.import_engram(
                input_path=export_path,
                merge=True,
                dry_run=True,
            )
        )
        parsed = json.loads(result)
        serialized = json.dumps(parsed, ensure_ascii=False)

        assert parsed["status"] == "preview"
        assert parsed["dry_run"] is True
        assert any(c["section"] == "profile" and c["field"] == "role" for c in parsed["conflicts"])
        assert "MCP_INCOMING_SECRET" not in serialized
        assert "MCP_LOCAL_SECRET" not in serialized
        assert isolated_engram.get_profile()["role"] == "MCP_LOCAL_SECRET"

    def test_save_project_snapshot_rejects_null_byte(
        self, isolated_engram: Engram
    ):
        result = _run(
            mcp_server.save_project_snapshot(
                project_folder="/tmp/proj\x00", data_json="{}"
            )
        )
        assert "错误" in result and "NUL" in result

    def test_export_engram_rejects_null_byte(self, isolated_engram: Engram):
        result = _run(mcp_server.export_engram(output_path="/tmp/x\x00.json"))
        assert "错误" in result and "NUL" in result

    def test_export_engram_empty_path_is_valid(self, isolated_engram: Engram):
        """No output_path means "use default" — must NOT be rejected as empty."""
        result = _run(mcp_server.export_engram(output_path=None))
        assert "导出成功" in result


# ---------------------------------------------------------------------------
# Coverage boost: _apply_tool_tier edge cases (lines 116, 123-124)
# ---------------------------------------------------------------------------


class TestApplyToolTierEdgeCases:
    def test_apply_tool_tier_returns_early_when_tools_not_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Line 116: _tools is not a dict -> early return without error."""
        monkeypatch.setattr(mcp_server, "TOOL_TIER", "core")
        # Create a mock tool_manager where _tools is None (not a dict)
        import types

        fake_manager = types.SimpleNamespace(_tools=None)
        monkeypatch.setattr(mcp_server.mcp, "_tool_manager", fake_manager)
        # Should return without error
        mcp_server._apply_tool_tier()

    def test_apply_tool_tier_fallback_pop_on_remove_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 123-124: mcp.remove_tool raises -> fallback to tools.pop."""
        monkeypatch.setattr(mcp_server, "TOOL_TIER", "core")

        # Create a fake tools dict with a non-tier1 tool
        fake_tools = {"get_user_context": "t1", "some_extra_tool": "t2"}
        import types

        fake_manager = types.SimpleNamespace(_tools=fake_tools)
        monkeypatch.setattr(mcp_server.mcp, "_tool_manager", fake_manager)

        def failing_remove(name):
            raise RuntimeError("cannot remove")

        monkeypatch.setattr(mcp_server.mcp, "remove_tool", failing_remove)
        mcp_server._apply_tool_tier()
        # "some_extra_tool" should have been popped from the dict
        assert "some_extra_tool" not in fake_tools
        assert "get_user_context" in fake_tools


# ---------------------------------------------------------------------------
# Coverage boost: empty context returns (lines 223, 245)
# ---------------------------------------------------------------------------


class TestEmptyContextReturns:
    def test_get_user_context_returns_empty_sentinel(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Line 223: generate_context returns '' -> 'Engram 为空' message."""
        monkeypatch.setattr(isolated_engram, "generate_context", lambda *a, **kw: "")
        result = _run(mcp_server.get_user_context())
        assert "Engram 为空" in result

    def test_get_identity_card_returns_empty_sentinel(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Line 245: export_identity_card returns '' -> '身份卡为空' message."""
        monkeypatch.setattr(
            isolated_engram, "export_identity_card", lambda *a, **kw: ""
        )
        result = _run(mcp_server.get_identity_card())
        assert "身份卡为空" in result


# ---------------------------------------------------------------------------
# Coverage boost: get_work_style (line 275)
# ---------------------------------------------------------------------------


class TestGetWorkStyle:
    def test_work_style_facet_returns_json(self, isolated_engram: Engram):
        """work_style facet of get_identity_facets returns JSON of work_style data."""
        result = _run(mcp_server.get_identity_facets(facet="work_style"))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Coverage boost: exception handlers in project/knowledge tools (lines 406-408, 449-451)
# ---------------------------------------------------------------------------


class TestProjectKnowledgeExceptions:
    def test_get_project_context_exception_propagates(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 406-408: get_project_snapshot raises -> exception re-raised."""

        def explode(*args, **kwargs):
            raise RuntimeError("snapshot boom")

        monkeypatch.setattr(isolated_engram, "get_project_snapshot", explode)
        with pytest.raises(RuntimeError, match="snapshot boom"):
            _run(mcp_server.get_project_context(project_folder="/some/path"))

    def test_get_relevant_knowledge_exception_propagates(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 449-451: get_relevant_lessons raises -> exception re-raised."""

        def explode(*args, **kwargs):
            raise RuntimeError("relevance boom")

        monkeypatch.setattr(isolated_engram, "get_relevant_lessons", explode)
        with pytest.raises(RuntimeError, match="relevance boom"):
            _run(
                mcp_server.get_relevant_knowledge(
                    project_folder="/some/path", limit=5
                )
            )


# ---------------------------------------------------------------------------
# Coverage boost: update_identity exception (lines 923-925)
# ---------------------------------------------------------------------------


class TestUpdateIdentityException:
    def test_update_identity_exception_returns_safe_json(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Internal identity write failures should not bubble raw MCP exceptions."""

        def explode(*args, **kwargs):
            raise RuntimeError(r"update boom at C:\Users\someone\secret.json")

        monkeypatch.setattr(isolated_engram, "update_profile", explode)
        result = _run(
            mcp_server.update_identity(
                field="profile", updates_json='{"role": "test"}'
            )
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["field"] == "profile"
        assert parsed["error"] == "update_identity failed: update boom at <path>"
        assert "C:\\Users" not in parsed["error"]


# ---------------------------------------------------------------------------
# Coverage boost: read_web_content (lines 973-995)
# ---------------------------------------------------------------------------


class TestReadWebContent:
    def test_read_web_content_success(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 973-991: successful extraction returns formatted content."""
        import urllib.request

        response_data = json.dumps(
            {"content": "Hello World", "source": "test", "error": None}
        ).encode("utf-8")

        class FakeResponse:
            def read(self):
                return response_data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **kw: FakeResponse()
        )
        result = _run(mcp_server.read_web_content(url="http://example.com"))
        assert "[来源: test]" in result
        assert "Hello World" in result

    def test_read_web_content_extraction_error(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Line 986: API returns error field -> '提取失败'."""
        import urllib.request

        response_data = json.dumps(
            {"error": "page not found", "content": ""}
        ).encode("utf-8")

        class FakeResponse:
            def read(self):
                return response_data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **kw: FakeResponse()
        )
        result = _run(mcp_server.read_web_content(url="http://example.com"))
        assert "提取失败" in result

    def test_read_web_content_no_content(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 989-990: content is empty -> fallback message."""
        import urllib.request

        response_data = json.dumps(
            {"content": "", "source": "test"}
        ).encode("utf-8")

        class FakeResponse:
            def read(self):
                return response_data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **kw: FakeResponse()
        )
        result = _run(mcp_server.read_web_content(url="http://example.com"))
        assert "未能提取到内容" in result

    def test_read_web_content_url_error(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 992-993: URLError -> service not running message."""
        import urllib.request
        import urllib.error

        def raise_url_error(*a, **kw):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)
        result = _run(mcp_server.read_web_content(url="http://example.com"))
        assert "Reader 服务未运行" in result

    def test_read_web_content_generic_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 994-995: generic Exception -> '读取失败'."""
        import urllib.request

        def raise_generic(*a, **kw):
            raise ValueError("unexpected error")

        monkeypatch.setattr(urllib.request, "urlopen", raise_generic)
        result = _run(mcp_server.read_web_content(url="http://example.com"))
        assert "读取失败" in result


# ---------------------------------------------------------------------------
# Coverage boost: export/import exception handlers (lines 1022-1023, 1066-1068, 1093-1094)
# ---------------------------------------------------------------------------


class TestExportImportExceptions:
    def test_export_engram_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1022-1023: export_all raises -> '导出失败'."""

        def explode(*args, **kwargs):
            raise RuntimeError("export boom")

        monkeypatch.setattr(isolated_engram, "export_all", explode)
        result = _run(mcp_server.export_engram(output_path=None))
        assert "导出失败" in result

    def test_export_openclaw_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1066-1068: export_to_openclaw raises -> error message."""
        monkeypatch.setattr(
            mcp_server,
            "export_to_openclaw",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("openclaw boom")),
        )
        # Simpler: patch to a function that raises
        def explode(*a, **kw):
            raise RuntimeError("openclaw boom")

        monkeypatch.setattr(mcp_server, "export_to_openclaw", explode)
        result = _run(mcp_server.export_engram(format="openclaw"))
        assert "OpenClaw 兼容格式失败" in result

    def test_import_openclaw_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1093-1094: import_from_openclaw raises -> error message."""

        def explode(*a, **kw):
            raise RuntimeError("import boom")

        monkeypatch.setattr(mcp_server, "import_from_openclaw", explode)
        result = _run(mcp_server.import_engram(format="openclaw"))
        assert "OpenClaw 兼容格式导入失败" in result

    def test_export_openclaw_non_success_status(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Line 1066: export_to_openclaw returns non-success status -> return full result."""

        def fake_export(*a, **kw):
            return {"status": "partial", "message": "some files missing"}

        monkeypatch.setattr(mcp_server, "export_to_openclaw", fake_export)
        result = json.loads(_run(mcp_server.export_engram(format="openclaw")))
        assert result["status"] == "partial"


# ---------------------------------------------------------------------------
# Coverage boost: get_audit_log with bad JSON (lines 1120-1121)
# ---------------------------------------------------------------------------


class TestAuditLogBadJSON:
    def test_audit_log_skips_corrupt_lines(self, isolated_engram: Engram):
        """Lines 1120-1121: JSONDecodeError on a line -> skip it, continue."""
        log_path = isolated_engram.root / "audit.log"
        log_path.write_text(
            '{"action":"read","target":"profile"}\n'
            "NOT_JSON_AT_ALL\n"
            '{"action":"write","target":"lesson"}\n',
            encoding="utf-8",
        )
        result = json.loads(_run(mcp_server.get_audit_log(limit=50)))
        entries = result["entries"]
        # Only the two valid JSON lines should be parsed
        assert len(entries) == 2
        assert result["total"] == 2  # total parsed entries (corrupt lines skipped)


# ---------------------------------------------------------------------------
# Coverage boost: wrap_up_session error paths (lines 1162-1241)
# ---------------------------------------------------------------------------


class TestWrapUpSessionErrors:
    """Cover all exception handlers in wrap_up_session."""

    def test_extract_insights_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1162-1164: extract_session_insights raises -> error in results."""

        def explode(*a, **kw):
            raise RuntimeError("extract boom")

        monkeypatch.setattr(isolated_engram, "extract_session_insights", explode)
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert "error" in result["insights"]
        assert "extract boom" in result["insights"]["error"]

    def test_snapshot_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1178-1180: save_project_snapshot raises -> error in results."""
        # Let extract succeed
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )

        def explode(*a, **kw):
            raise RuntimeError("snapshot boom")

        monkeypatch.setattr(isolated_engram, "save_project_snapshot", explode)
        result = json.loads(
            _run(
                mcp_server.wrap_up_session(
                    summary="test", project_folder="/some/proj"
                )
            )
        )
        assert "error" in result["project_snapshot"]
        assert "snapshot boom" in result["project_snapshot"]["error"]

    def test_wrap_up_session_calibrates_project_snapshot_current_state(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )
        monkeypatch.setattr(
            mcp_server,
            "_collect_project_info",
            lambda folder: {
                "version": "9.9.9",
                "test_count": 42,
                "mcp_tool_definitions": 7,
            },
        )

        project = str(isolated_engram.root / "project")
        result = json.loads(_run(mcp_server.wrap_up_session(
            summary="finished current labeling loop",
            project_folder=project,
            project_title="Piia Engram",
        )))
        snapshot = isolated_engram.get_project_snapshot(project)

        assert result["project_snapshot"]["saved"] is True
        assert snapshot["title"] == "Piia Engram"
        assert snapshot["version"] == "9.9.9"
        assert snapshot["test_count"] == 42
        assert snapshot["current_state"]["version"] == "9.9.9"
        assert snapshot["current_state"]["test_count"] == 42
        assert snapshot["current_state"]["mcp_tool_definitions"] == 7
        assert "verified_at" in snapshot["current_state"]

    def test_reconcile_memories_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1187-1188: reconcile_memories raises -> silently caught."""
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )

        def explode(*a, **kw):
            raise RuntimeError("reconcile boom")

        monkeypatch.setattr(isolated_engram, "reconcile_memories", explode)
        # Should not raise — error is logged and swallowed
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert "insights" in result

    def test_reconcile_ai_configs_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1194-1195: reconcile_ai_configs raises -> silently caught."""
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_memories",
            lambda *a, **kw: {"imported": 0},
        )

        def explode(*a, **kw):
            raise RuntimeError("config boom")

        monkeypatch.setattr(isolated_engram, "reconcile_ai_configs", explode)
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert "insights" in result

    def test_evaluate_tiers_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1201-1203: evaluate_tiers raises -> silently caught."""
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_memories",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_ai_configs",
            lambda *a, **kw: {"imported": 0},
        )

        def explode(*a, **kw):
            raise RuntimeError("tier boom")

        monkeypatch.setattr(isolated_engram, "evaluate_tiers", explode)
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert "insights" in result

    def test_get_staging_summary_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1218-1219: get_staging_summary raises -> silently caught."""
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_memories",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_ai_configs",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "evaluate_tiers",
            lambda *a, **kw: {"promoted": 0},
        )

        def explode(*a, **kw):
            raise RuntimeError("staging boom")

        monkeypatch.setattr(isolated_engram, "get_staging_summary", explode)
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert "insights" in result

    def test_evaluate_tiers_suggested(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """evaluate_tiers suggestions are surfaced without implying promotion."""
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_memories",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_ai_configs",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "evaluate_tiers",
            lambda *a, **kw: {"promoted": 0, "suggested": 2, "details": ["a", "b"]},
        )
        monkeypatch.setattr(
            isolated_engram,
            "get_staging_summary",
            lambda *a, **kw: {"total_staging": 0, "staging_lessons": 0, "staging_decisions": 0},
        )
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert result["promotion_suggestions"]["suggested"] == 2

    def test_pkg_version_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1230-1231: _pkg_version raises -> falls back to 'dev'."""
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_memories",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_ai_configs",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "evaluate_tiers",
            lambda *a, **kw: {"promoted": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "get_staging_summary",
            lambda *a, **kw: {"total_staging": 0, "staging_lessons": 0, "staging_decisions": 0},
        )
        # Ensure _tracker is set so the pkg_version path runs
        import importlib.metadata

        original_version = importlib.metadata.version
        monkeypatch.setattr(
            importlib.metadata,
            "version",
            lambda name: (_ for _ in ()).throw(Exception("no package")),
        )
        # The function should still succeed — _ver falls back to "dev"
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert "insights" in result
        monkeypatch.setattr(importlib.metadata, "version", original_version)

    def test_k_counts_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1237-1238: get_lessons/get_decisions raises -> k_counts stays empty."""
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_memories",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_ai_configs",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "evaluate_tiers",
            lambda *a, **kw: {"promoted": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "get_staging_summary",
            lambda *a, **kw: {"total_staging": 0, "staging_lessons": 0, "staging_decisions": 0},
        )

        def explode(*a, **kw):
            raise RuntimeError("count boom")

        monkeypatch.setattr(isolated_engram, "get_lessons", explode)
        # Should not raise — the exception is caught in the inner try
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert "insights" in result

    def test_flush_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1240-1241: _tracker.flush raises -> silently caught."""
        monkeypatch.setattr(
            isolated_engram,
            "extract_session_insights",
            lambda *a, **kw: {"lessons": [], "decisions": []},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_memories",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "reconcile_ai_configs",
            lambda *a, **kw: {"imported": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "evaluate_tiers",
            lambda *a, **kw: {"promoted": 0},
        )
        monkeypatch.setattr(
            isolated_engram,
            "get_staging_summary",
            lambda *a, **kw: {"total_staging": 0, "staging_lessons": 0, "staging_decisions": 0},
        )

        # Create a fake tracker that raises on flush
        class ExplodingTracker:
            def record(self, *a, **kw):
                pass

            def flush(self, *a, **kw):
                raise RuntimeError("flush boom")

        monkeypatch.setattr(mcp_server, "_tracker", ExplodingTracker())
        result = json.loads(_run(mcp_server.wrap_up_session(summary="test")))
        assert "insights" in result


# ---------------------------------------------------------------------------
# _collect_project_info tests
# ---------------------------------------------------------------------------


class TestCollectProjectInfo:
    """Tests for the _collect_project_info helper."""

    def test_empty_folder_returns_empty(self):
        assert mcp_server._collect_project_info("") == {}

    def test_no_pyproject_returns_empty(self, tmp_path: Path):
        assert mcp_server._collect_project_info(str(tmp_path)) == {}

    def test_collects_version(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nversion = "1.2.3"\n', encoding="utf-8",
        )
        info = mcp_server._collect_project_info(str(tmp_path))
        assert info["version"] == "1.2.3"

    def test_collects_module_count(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nversion = "0.1.0"\n', encoding="utf-8",
        )
        src = tmp_path / "src" / "mypkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("", encoding="utf-8")
        (src / "core.py").write_text("pass", encoding="utf-8")
        info = mcp_server._collect_project_info(str(tmp_path))
        assert info["module_count"] == 2

    def test_collects_test_count(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nversion = "0.1.0"\n', encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text(
            "def test_one(): pass\ndef test_two(): pass\n", encoding="utf-8",
        )
        info = mcp_server._collect_project_info(str(tmp_path))
        assert info["test_count"] == 2

    def test_collects_mcp_tool_count(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nversion = "0.1.0"\n', encoding="utf-8",
        )
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "mcp_server.py").write_text(
            "@mcp.tool()\nasync def a(): ...\n@mcp.tool()\nasync def b(): ...\n",
            encoding="utf-8",
        )
        info = mcp_server._collect_project_info(str(tmp_path))
        assert info["mcp_tool_definitions"] == 2

    def test_no_crash_on_missing_dirs(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nversion = "0.1.0"\n', encoding="utf-8",
        )
        # No src/ or tests/ dirs
        info = mcp_server._collect_project_info(str(tmp_path))
        assert info.get("version") == "0.1.0"
        assert "module_count" not in info
        assert "test_count" not in info


# ---------------------------------------------------------------------------
# Provider 兼容层参数测试
# ---------------------------------------------------------------------------


def test_mcp_search_knowledge_filters_json_passes_filters(isolated_engram: Engram):
    """MCP search_knowledge 的 filters_json 应正确解析并过滤结果。"""
    isolated_engram.add_lesson({"summary": "staging tip about caching", "tier": "staging"})
    isolated_engram.add_lesson({"summary": "verified tip about caching", "tier": "verified"})

    result = _run(mcp_server.search_knowledge(
        query="caching", filters_json='{"tier": "staging"}',
    ))
    parsed = json.loads(result)
    assert len(parsed["lessons"]) >= 1
    assert all(l.get("tier") == "staging" for l in parsed["lessons"])


def test_mcp_search_knowledge_invalid_filters_json(isolated_engram: Engram):
    """MCP search_knowledge 非法 filters_json 应返回友好错误。"""
    result = _run(mcp_server.search_knowledge(
        query="anything", filters_json="not valid json{",
    ))
    assert "filters_json 格式错误" in result


def test_review_staging_list_is_metadata_only(isolated_engram: Engram):
    """action=list returns id/type/domain metadata, never the item body."""
    secret = "ZZ_MCP_STAGING_QUEUE_SECRET"
    isolated_engram.add_lesson({
        "summary": f"staging lesson {secret}",
        "domain": "release",
        "tier": "staging",
    })

    result = json.loads(_run(mcp_server.review_staging(
        action="list",
        filters_json='{"domain":"release"}',
        limit=5,
    )))

    assert result["status"] == "listed"
    assert result["counts"]["listed"] == 1
    assert result["items"][0]["type"] == "lesson"
    assert result["items"][0]["domain"] == "release"
    assert secret not in json.dumps(result, ensure_ascii=False)


def test_review_staging_list_refused_for_web_caller(
    isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
):
    """v4.0.0 tightening: the merged review_staging is write-gated for ALL
    actions — a web caller is refused even for action="list" (the old
    read-only list_pending_staging allowed it metadata-only)."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")

    result = _run(mcp_server.review_staging(action="list"))

    assert "ENGRAM_GOVERNANCE_REFUSAL" in result


def test_review_staging_list_surfaces_other_review_queues(
    isolated_engram: Engram,
):
    """An empty staging queue must not hide pending playbook scope reviews."""
    isolated_engram.add_playbook({"title": "Daily cleanup", "triggers": ["notes"]})

    result = json.loads(_run(mcp_server.review_staging(action="list")))

    assert result["counts"]["total_pending"] == 0
    other = result["other_queues"]["playbook_scope_review"]
    assert other["pending"] == 1
    # v4.0.0: scope review moved out of MCP — the hint points at the owner CLI
    assert "engram playbook scope resolve" in other["hint"]


def test_review_staging_list_other_queues_empty_when_nothing_pending(
    isolated_engram: Engram,
):
    """No pending backlogs anywhere → other_queues stays an empty dict."""
    result = json.loads(_run(mcp_server.review_staging(action="list")))

    assert result["other_queues"] == {}


def test_review_staging_batch_still_write_gated_for_external(
    isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
):
    lesson = isolated_engram.add_lesson("needs owner review", tier="staging")
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")

    result = _run(mcp_server.review_staging(
        action="batch",
        actions_json=json.dumps([{"id": lesson["id"], "action": "approve"}]),
        dry_run=False,
        confirm=True,
    ))

    assert "ENGRAM_GOVERNANCE_REFUSAL" in result


def test_get_user_context_passes_token_budget(
    isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch,
):
    """MCP get_user_context 的 token_budget 应传为 generate_context(max_tokens=...)。"""
    captured = {}
    original = isolated_engram.generate_context

    def spy(project_folder=None, max_tokens=None, level="full"):
        captured["max_tokens"] = max_tokens
        return original(project_folder, max_tokens=max_tokens, level=level)

    monkeypatch.setattr(isolated_engram, "generate_context", spy)
    _run(mcp_server.get_user_context(token_budget=42))
    assert captured.get("max_tokens") == 42


def test_get_user_context_appends_user_prompt(isolated_engram: Engram):
    """MCP get_user_context 传 user_prompt 时应追加到输出末尾。"""
    isolated_engram.update_profile({"role": "developer"})
    result = _run(mcp_server.get_user_context(user_prompt="如何优化启动速度？"))
    assert "## 当前用户提问" in result
    assert "如何优化启动速度？" in result


def test_get_user_context_truncates_user_prompt_to_token_budget(
    isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch,
):
    """user_prompt 过长且设置 token_budget 时应裁剪追加内容。"""
    monkeypatch.setattr(
        isolated_engram,
        "generate_context",
        lambda project_folder=None, level="standard", max_tokens=None: "context-body-" * 3,
    )
    prompt = "这是一个很长的问题" * 30

    result = _run(mcp_server.get_user_context(token_budget=25, user_prompt=prompt))

    assert "## 当前用户提问" in result
    assert prompt not in result
    assert "…" in result


def test_get_user_context_no_user_prompt_omits_section(isolated_engram: Engram):
    """MCP get_user_context 不传 user_prompt 时不应有「当前用户提问」section。"""
    isolated_engram.update_profile({"role": "developer"})
    result = _run(mcp_server.get_user_context())
    assert "当前用户提问" not in result


# ---------------------------------------------------------------------------
# Cold-start playbook trigger matching ("playbook finds you")
# ---------------------------------------------------------------------------


class TestGetUserContextPlaybookMatching:
    # The heading is unique to the trigger-matching section — the baseline
    # cold-start context has its own "最近操作手册" section that also lists
    # playbook titles, so assertions must key on THIS heading, not titles.
    _SECTION = "相关 Playbook（与当前提问匹配）"

    @pytest.fixture(autouse=True)
    def _pin_lang_zh(self, monkeypatch: pytest.MonkeyPatch):
        """Pin the runtime language so the section heading is deterministic
        regardless of the developer's real ~/.engram profile."""
        from piia_engram import i18n

        monkeypatch.setattr(i18n, "_runtime_lang", "zh")

    @staticmethod
    def _seed(engram: Engram) -> str:
        engram.update_profile({"role": "developer"})
        result = engram.add_playbook({
            "title": "MCP Registry 发布流程",
            "triggers": ["发布", "registry"],
            "steps": [
                {"order": 1, "action": "build", "detail": "python -m build"},
                {"order": 2, "action": "publish", "detail": "twine upload"},
            ],
        })
        return result.get("id", "")

    def test_prompt_hitting_trigger_surfaces_playbook_section(
        self, isolated_engram: Engram,
    ):
        """user_prompt 命中 trigger 时，冷启动上下文应浮现 Playbook 指针。"""
        pb_id = self._seed(isolated_engram)
        result = _run(mcp_server.get_user_context(
            user_prompt="帮我把新版本发布到 registry",
        ))
        assert self._SECTION in result
        assert "MCP Registry 发布流程" in result
        assert pb_id in result
        assert 'get_playbooks(mode="get"' in result

    def test_prompt_without_trigger_hit_omits_section(
        self, isolated_engram: Engram,
    ):
        """无 trigger 命中时不应有匹配小节（精确优先）。"""
        self._seed(isolated_engram)
        result = _run(mcp_server.get_user_context(
            user_prompt="解释一下这段排序算法的复杂度",
        ))
        assert self._SECTION not in result

    def test_no_user_prompt_skips_matching_entirely(
        self, isolated_engram: Engram,
    ):
        """不传 user_prompt 时完全不做匹配，也不浮现匹配小节。"""
        self._seed(isolated_engram)
        result = _run(mcp_server.get_user_context())
        assert self._SECTION not in result

    def test_tight_token_budget_drops_playbook_section(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch,
    ):
        """token_budget 不够时匹配小节应整体丢弃（最低优先级）。"""
        self._seed(isolated_engram)
        monkeypatch.setattr(
            isolated_engram,
            "generate_context",
            lambda project_folder=None, level="standard", max_tokens=None: "ctx-" * 20,
        )
        result = _run(mcp_server.get_user_context(
            token_budget=30, user_prompt="发布到 registry",
        ))
        assert self._SECTION not in result

    def test_surfacing_does_not_bump_access_stats(
        self, isolated_engram: Engram,
    ):
        """冷启动浮现不算使用——access_count / last_reviewed 不应被改动。"""
        pb_id = self._seed(isolated_engram)
        before = isolated_engram.get_playbook(pb_id, _update_access=False)
        _run(mcp_server.get_user_context(user_prompt="发布到 registry"))
        after = isolated_engram.get_playbook(pb_id, _update_access=False)
        assert after.get("access_count", 0) == before.get("access_count", 0)
        assert after.get("last_reviewed") == before.get("last_reviewed")

    def test_matching_failure_does_not_break_cold_start(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch,
    ):
        """匹配链路抛异常时，冷启动上下文必须照常返回。"""
        self._seed(isolated_engram)

        def boom(*args, **kwargs):
            raise RuntimeError("playbook store unavailable")

        monkeypatch.setattr(isolated_engram, "get_playbooks", boom)
        result = _run(mcp_server.get_user_context(user_prompt="发布到 registry"))
        assert "## 当前用户提问" in result
        assert "发布到 registry" in result


# ---------------------------------------------------------------------------
# M11: MCP wrapper + doctor WARN coverage (Codex review)
# ---------------------------------------------------------------------------


class TestResumeBriefWrapper:
    def test_mcp_get_resume_brief_wrapper(self, isolated_engram: Engram, tmp_path: Path):
        """M11-1: get_resume_brief must return an <engram-resume …> XML block."""
        result = _run(mcp_server.get_resume_brief(project_folder=str(tmp_path)))
        assert "<engram-resume" in result, (
            f"Expected '<engram-resume' tag in resume brief output, got: {result[:200]}"
        )


class TestColdStartBootstrap:
    """Cold-start regression: bootstrap must be REACHABLE via get_user_context,
    not just unit-tested in isolation. The bug: bootstrap was gated behind
    ``if not context``, but generate_context returns a non-empty "identity not
    set" scaffold for an empty store, so a brand-new user with a discoverable
    CLAUDE.md got the scaffold instead of their auto-imported rules ("it already
    knows me"). Only get_resume_brief ran bootstrap unconditionally."""

    def test_get_user_context_imports_rule_files_on_cold_start(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # Re-enable bootstrap (the fixture disables it) + feed a synthetic rule
        # file so the scan never touches the real home dir.
        (isolated_engram.root / ".bootstrap_done").unlink()
        fake = tmp_path / "fake_CLAUDE.md"
        fake.write_text(
            "# Rules\n所有沟通使用中文。\n我是一名独立开发者。\n这个 repo 用 pytest 测试。\n",
            encoding="utf-8",
        )
        import piia_engram.bootstrap as bs
        monkeypatch.setattr(bs, "_scan_rule_files", lambda: [
            {"path": fake, "scope": "global",
             "lines": fake.read_text(encoding="utf-8").splitlines()},
        ])

        result = _run(mcp_server.get_user_context())

        # The new user must see imported content, NOT the generic scaffold.
        assert "身份画像未设置" not in result
        assert "首次连接自动导入" in result
        assert "zh-CN" in result or "沟通语言" in result

    def test_get_user_context_no_rule_files_still_gives_guidance(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        # No discoverable rules → import nothing, but the user still gets
        # actionable cold-start guidance (no crash, no empty output).
        (isolated_engram.root / ".bootstrap_done").unlink()
        import piia_engram.bootstrap as bs
        monkeypatch.setattr(bs, "_scan_rule_files", lambda: [])

        result = _run(mcp_server.get_user_context())

        assert result.strip()
        assert (
            "update_identity" in result
            or "engram setup" in result
            or "身份画像未设置" in result
        )

    def test_get_resume_brief_imports_rule_files_on_cold_start(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # get_resume_brief is the OTHER cold-start entry. The original bug was the
        # two entries behaving inconsistently (get_user_context unreachable while
        # get_resume_brief reached bootstrap); lock that this entry keeps surfacing
        # the import so it can't silently regress to an empty brief.
        (isolated_engram.root / ".bootstrap_done").unlink()
        fake = tmp_path / "fake_CLAUDE.md"
        fake.write_text(
            "# Rules\n所有沟通使用中文。\n我是一名独立开发者。\n这个 repo 用 pytest 测试。\n",
            encoding="utf-8",
        )
        import piia_engram.bootstrap as bs
        monkeypatch.setattr(bs, "_scan_rule_files", lambda: [
            {"path": fake, "scope": "global",
             "lines": fake.read_text(encoding="utf-8").splitlines()},
        ])

        result = _run(mcp_server.get_resume_brief())

        # Real entry must trigger bootstrap: detected language surfaces in the brief…
        assert "zh-CN" in result
        # …and the store actually received the imported rules (reachability proof).
        lessons = isolated_engram.get_lessons(limit=10, _update_access=False)
        assert any("首次连接自动导入" in l.get("summary", "") for l in lessons)


class TestRecallWrapper:
    def test_mcp_get_recall_wrapper_returns_recall_payload(
        self, isolated_engram: Engram, tmp_path: Path
    ):
        """get_recall should expose the structured Recall Surface v1 payload."""
        isolated_engram.update_profile({
            "role": "developer",
            "language": "zh",
            "technical_level": "senior",
        })
        isolated_engram.add_lesson({
            "summary": "Release gates should stay focused before publishing.",
            "domain": "release",
        })

        result = json.loads(_run(mcp_server.get_recall(
            project_folder=str(tmp_path),
            query="release gates",
            limit=3,
            token_budget=512,
        )))

        assert result["identity"]["role"] == "developer"
        assert result["identity"]["language"] == "zh"
        assert result["meta"]["project"] == str(tmp_path)
        assert result["meta"]["query"] == "release gates"
        assert result["meta"]["token_budget"] == 512
        assert result["meta"]["_caller_permissions"]["trust_level"] == "unrestricted"
        assert any(
            item.get("summary") == "Release gates should stay focused before publishing."
            for item in result["knowledge"]
        )

    def test_mcp_get_recall_passes_trust_only_for_owner(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        captured: dict = {}

        def _gather(*args, **kwargs):
            captured.update(kwargs)
            return {"identity": {}, "knowledge": [], "meta": {}}

        monkeypatch.setattr(mcp_server._gov_rt, "caller_is_owner", lambda root: True)
        monkeypatch.setattr(mcp_server._recall_service, "gather_recall", _gather)
        monkeypatch.setattr(mcp_server, "_track", lambda *args, **kwargs: None)

        result = json.loads(_run(mcp_server.get_recall()))

        assert result["knowledge"] == []
        assert captured["include_trust"] is True
        assert "include_trust" not in mcp_server.get_recall.__annotations__

    def test_mcp_get_recall_refuses_non_owner_before_trust_gather(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
        monkeypatch.setattr(mcp_server._gov_rt, "caller_is_owner", lambda root: False)

        def _boom(*args, **kwargs):
            raise AssertionError("get_recall should refuse before gather_recall")

        monkeypatch.setattr(mcp_server._recall_service, "gather_recall", _boom)

        result = _run(mcp_server.get_recall())

        assert "private-self only" in result
        assert "gather_recall" not in result


class TestContextGovernancePreviewWrapper:
    def test_mcp_preview_context_governance_returns_safe_context_proposal(
        self, isolated_engram: Engram
    ):
        result = json.loads(_run(mcp_server.preview_context_governance(
            mode="safe_context",
            payload_json=(
                '{"knowledge": [{"summary": '
                '"api key sk-test_1234567890abcdef1234567890abcdef"}]}'
            ),
            options_json='{"max_chars": 2000}',
        )))

        assert result["mode"] == "safe_context"
        assert result["applied"] is False
        assert result["invariant"] == "context_governance_preview_only"
        assert "sk-test_" not in repr(result)
        assert result["proposal"]["meta"]["safe_context"]["mode"] == "safe"

    def test_mcp_preview_context_governance_refuses_non_owner_before_gather(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
        monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")

        def _boom(*args, **kwargs):
            raise AssertionError("preview should refuse before building proposal")

        monkeypatch.setattr(
            mcp_server._context_governance,
            "build_context_governance_preview",
            _boom,
        )

        result = _run(mcp_server.preview_context_governance(
            mode="freshness_conflicts",
        ))

        assert "private-self only" in result
        assert "preview should refuse" not in result

    def test_mcp_preview_context_governance_other_modes_and_errors(
        self, isolated_engram: Engram
    ):
        isolated_engram.add_lesson({
            "summary": "Prefer local previews before publishing.",
            "domain": "governance",
        })
        freshness = json.loads(_run(mcp_server.preview_context_governance(
            mode="freshness_conflicts",
        )))
        replay = json.loads(_run(mcp_server.preview_context_governance(
            mode="replay_packet",
            payload_json='{"compact_summary": "resume from local draft"}',
        )))
        evidence = json.loads(_run(mcp_server.preview_context_governance(
            mode="external_evidence",
            payload_json=(
                '{"evidence": [{"label": "PyPI", "status": "verified", '
                '"checked_at": "2026-06-06", "url": "https://example.test"}]}'
            ),
        )))
        unknown = json.loads(_run(mcp_server.preview_context_governance(
            mode="nope",
        )))
        bad_json = json.loads(_run(mcp_server.preview_context_governance(
            mode="safe_context",
            payload_json="[1, 2]",
        )))

        assert freshness["mode"] == "freshness_conflicts"
        assert freshness["proposal"]["invariant"] == "proposal_only_metadata"
        assert replay["mode"] == "replay_packet"
        assert replay["proposal"]["applied"] is False
        assert evidence["mode"] == "external_evidence"
        assert "LOCAL DRAFT" in evidence["proposal"]["draft"]
        assert unknown["error"] == "unknown_mode"
        assert "context governance preview failed" in bad_json["error"]


class TestDoctorUncleanExitWarn:
    def test_doctor_surfaces_unclean_exit_warn(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """M11-2: When session_state.json shows an unclean prior exit,
        doctor's JSON output must contain a check with name='unclean_exit'
        and status='WARN'."""
        import os

        # 1. Write a session_state.json that simulates a prior unclean exit
        #    with a pid that is NOT this process (so _prev_unclean is populated).
        state_path = tmp_path / "session_state.json"
        fake_pid = 1 if os.getpid() != 1 else 2
        state_data = json.dumps({
            "pid": fake_pid,
            "last_clean_exit": False,
            "started_at": "2026-01-01T00:00:00",
            "last_seen_at": "2026-01-01T00:05:00",
            "last_session_id": "fake-prior-session",
            "session_nonce": "deadbeef12345678",
        })
        state_path.write_text(state_data, encoding="utf-8")

        # 2. Construct an Engram instance that reads the unclean breadcrumb.
        engram = Engram(root=tmp_path)
        assert engram._prev_unclean is not None, (
            "Engram should detect the unclean prior exit from session_state.json"
        )

        # 3. Patch the module-level _engram and stop old heartbeat.
        old_session = mcp_server._session
        old_session._stop_event.set()
        if old_session._heartbeat_thread is not None:
            old_session._heartbeat_thread.join(timeout=2.0)

        monkeypatch.setattr(mcp_server, "_engram", engram)
        monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
        monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())

        # 4. Run doctor in JSON mode and verify
        result = _run(mcp_server.doctor(output_format="json"))
        parsed = json.loads(result)
        checks = parsed.get("checks", [])
        unclean_checks = [c for c in checks if c.get("name") == "unclean_exit"]
        assert len(unclean_checks) == 1, (
            f"Expected exactly one 'unclean_exit' check, found: {unclean_checks}"
        )
        assert unclean_checks[0]["status"] == "WARN", (
            f"Expected status='WARN', got: {unclean_checks[0]['status']}"
        )


# ---------------------------------------------------------------------------
# v4.0.0: legacy Playbook scope migration moved out of MCP into the owner CLI
# (`engram playbook scope ...`). These tests exercise the CLI entry point
# against the same isolated root (via ENGRAM_DIR) the fixture engram uses.
# ---------------------------------------------------------------------------


def _run_scope_cli(args: list, capsys) -> tuple:
    """Invoke ``engram playbook scope <args>`` and return (rc, stdout)."""
    from piia_engram.setup_wizard import run_playbook

    rc = run_playbook(["scope", *args])
    return rc, capsys.readouterr().out


def _cli_json(out: str) -> dict:
    """Parse the JSON document the scope CLI prints (skip any preamble)."""
    return json.loads(out[out.index("{"):])


def test_cli_playbook_scope_apply_preview(
    isolated_engram: Engram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """`engram playbook scope apply` defaults to a safe dry-run preview."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    project = str(tmp_path / "engram")
    isolated_engram.save_project_snapshot(project, {"title": "Engram"})
    pb = isolated_engram.add_playbook({
        "title": "Engram release checklist",
        "triggers": ["engram", "release"],
    })

    rc, out = _run_scope_cli(["apply"], capsys)
    result = _cli_json(out)

    assert rc == 0
    assert result["dry_run"] is True
    assert [item["id"] for item in result["would_apply"]] == [pb["id"]]


def test_cli_playbook_scope_rollback_preview(
    isolated_engram: Engram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """`engram playbook scope rollback` previews without changing scope."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    project = str(tmp_path / "engram")
    isolated_engram.save_project_snapshot(project, {"title": "Engram"})
    pb = isolated_engram.add_playbook({
        "title": "Engram release checklist",
        "triggers": ["engram", "release"],
    })
    isolated_engram.apply_legacy_playbook_scope_suggestions(
        dry_run=False,
        confirm=True,
    )

    rc, out = _run_scope_cli(["rollback", "--playbook-ids", pb["id"]], capsys)
    result = _cli_json(out)

    assert rc == 0
    assert result["dry_run"] is True
    assert [item["id"] for item in result["would_rollback"]] == [pb["id"]]
    assert isolated_engram.get_playbook(
        pb["id"], _update_access=False,
    )["scope"]["type"] == "project"


def test_cli_playbook_scope_apply_requires_yes_before_write(
    isolated_engram: Engram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """--apply without --yes must be refused before any mutation."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    project = str(tmp_path / "engram")
    isolated_engram.save_project_snapshot(project, {"title": "Engram"})
    pb = isolated_engram.add_playbook({
        "title": "Engram release checklist",
        "triggers": ["engram", "release"],
    })

    rc, out = _run_scope_cli(["apply", "--apply"], capsys)

    assert rc == 2
    assert "--yes" in out
    stored = isolated_engram.get_playbook(pb["id"], _update_access=False)
    assert stored["scope"]["type"] == "global"
    assert "scope_migration_history" not in stored


def test_cli_playbook_scope_queue(
    isolated_engram: Engram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """`engram playbook scope queue` lists unresolved scope review items."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    pb = isolated_engram.add_playbook({"title": "Daily cleanup", "triggers": ["notes"]})

    rc, out = _run_scope_cli(["queue"], capsys)
    result = _cli_json(out)

    assert rc == 0
    assert result["total"] == 1
    assert result["items"][0]["id"] == pb["id"]
    assert result["items"][0]["suggested_scope"]["type"] == "needs_review"


def test_cli_playbook_scope_resolve_accept_shared_preview(
    isolated_engram: Engram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """resolve passes explicit shared project folders through to the core."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    project_a = str(tmp_path / "engram")
    project_b = str(tmp_path / "atlas")
    pb = isolated_engram.add_playbook({"title": "Daily cleanup", "triggers": ["notes"]})

    rc, out = _run_scope_cli([
        "resolve", pb["id"],
        "--action", "accept_shared",
        "--project-folders", f"{project_a},{project_b}",
    ], capsys)
    result = _cli_json(out)

    assert rc == 0
    assert result["dry_run"] is True
    assert result["would_update"]["to_scope"]["type"] == "shared"
    assert result["would_update"]["to_scope"]["project_folders"] == [project_a, project_b]
    stored = isolated_engram.get_playbook(pb["id"], _update_access=False)
    assert stored["scope"]["type"] == "global"


def test_cli_playbook_scope_resolve_requires_yes_before_write(
    isolated_engram: Engram, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """resolve --apply without --yes must be refused before any mutation."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    project = str(tmp_path / "engram")
    pb = isolated_engram.add_playbook({"title": "Daily cleanup", "triggers": ["notes"]})

    rc, out = _run_scope_cli([
        "resolve", pb["id"],
        "--action", "accept_project",
        "--project-folder", project,
        "--apply",
    ], capsys)

    assert rc == 2
    assert "--yes" in out
    stored = isolated_engram.get_playbook(pb["id"], _update_access=False)
    assert stored["scope"]["type"] == "global"
    assert "scope_review_history" not in stored


def test_mcp_list_playbooks_for_management_includes_hidden_items(
    isolated_engram: Engram,
):
    """Management list should expose archived/deleted Playbook metadata."""
    active = isolated_engram.add_playbook({"title": "Active flow", "triggers": ["active"]})
    deleted = isolated_engram.add_playbook({"title": "Deleted flow", "triggers": ["delete"]})
    isolated_engram.delete_playbook(deleted["id"], dry_run=False, confirm=True)

    result = json.loads(_run(
        mcp_server.get_playbooks(mode="management", status="all")
    ))

    by_id = {item["id"]: item for item in result["items"]}
    assert by_id[active["id"]]["status"] == "active"
    assert by_id[deleted["id"]]["status"] == "deleted"


def test_mcp_list_playbooks_for_management_default_is_metadata_only(
    isolated_engram: Engram,
):
    """The MCP management list default must not echo Playbook content."""
    secret = "ZZ_MCP_MANAGEMENT_SECRET"
    pb = isolated_engram.add_playbook({
        "title": f"{secret} title",
        "description": f"{secret} description",
        "domain": f"{secret} domain",
        "triggers": [f"{secret} trigger"],
        "steps": [f"{secret} step"],
    })
    isolated_engram.delete_playbook(
        pb["id"],
        reason=f"{secret} deletion reason",
        dry_run=False,
        confirm=True,
    )

    result = json.loads(_run(
        mcp_server.get_playbooks(mode="management", status="all")
    ))
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    item = result["items"][0]

    assert secret not in rendered
    assert "title" not in item
    assert "description" not in item
    assert "domain" not in item
    assert "triggers" not in item
    assert "steps" not in item
    assert "deletion_reason" not in item


def test_mcp_delete_restore_playbook_receipts_do_not_echo_content(
    isolated_engram: Engram,
):
    """MCP delete/restore receipts should be metadata-only even with governance off."""
    secret = "ZZ_MCP_DELETE_RECEIPT_SECRET"
    pb = isolated_engram.add_playbook({
        "title": f"{secret} title",
        "steps": [f"{secret} step"],
    })

    delete_preview = _run(mcp_server.manage_playbook(
        "delete",
        pb["id"],
        reason=f"{secret} dry reason",
        dry_run=True,
        confirm=False,
    ))
    deleted = _run(mcp_server.manage_playbook(
        "delete",
        pb["id"],
        reason=f"{secret} applied reason",
        dry_run=False,
        confirm=True,
    ))
    restore_preview = _run(mcp_server.manage_playbook(
        "restore",
        pb["id"],
        dry_run=True,
        confirm=False,
    ))
    restored = _run(mcp_server.manage_playbook(
        "restore",
        pb["id"],
        dry_run=False,
        confirm=True,
    ))
    rendered = "\n".join([delete_preview, deleted, restore_preview, restored])

    assert secret not in rendered
    assert "title" not in rendered
    assert "reason" not in rendered


def test_mcp_archive_playbook_ack_does_not_echo_title(
    isolated_engram: Engram,
):
    """Archive acknowledgement should not echo private Playbook titles."""
    secret = "ZZ_MCP_ARCHIVE_SECRET"
    pb = isolated_engram.add_playbook({
        "title": f"{secret} title",
        "steps": [f"{secret} step"],
    })

    result = _run(mcp_server.manage_playbook("archive", pb["id"]))

    assert secret not in result
    assert "title" not in result
    assert pb["id"] in result


def test_mcp_delete_playbook_refuses_web_caller_before_write(
    isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch,
):
    """Low-trust web callers must not soft-delete Playbooks."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    pb = isolated_engram.add_playbook({"title": "Do not delete", "triggers": ["safe"]})

    result = _run(mcp_server.manage_playbook(
        "delete",
        pb["id"],
        reason="web attempt",
        dry_run=False,
        confirm=True,
    ))

    assert "Governance" in result or "治理" in result
    assert isolated_engram.get_playbook(
        pb["id"], _update_access=False,
    )["status"] == "active"


def test_mcp_restore_playbook_refuses_web_caller_before_write(
    isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch,
):
    """Low-trust web callers must not restore Playbooks."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    pb = isolated_engram.add_playbook({"title": "Deleted flow", "triggers": ["safe"]})
    isolated_engram.delete_playbook(pb["id"], dry_run=False, confirm=True)

    result = _run(mcp_server.manage_playbook(
        "restore",
        pb["id"],
        dry_run=False,
        confirm=True,
    ))

    assert "Governance" in result or "治理" in result
    assert isolated_engram.get_playbook(
        pb["id"], _update_access=False,
    )["status"] == "deleted"
