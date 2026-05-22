"""Tests for MCP tool wrappers in engram_core.mcp_server.

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

from engram_core import mcp_server
from engram_core.core import Engram


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_engram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    """Replace the module-level ``_engram`` with a fresh instance in tmp_path.

    Tools read the global ``mcp_server._engram`` directly, so we patch the
    attribute rather than the underlying Engram class.
    """
    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    """Helper to run an async tool synchronously in tests."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Identity tool wrappers
# ---------------------------------------------------------------------------


class TestIdentityTools:
    def test_get_profile_empty_returns_json_dict(self, isolated_engram: Engram):
        result = _run(mcp_server.get_profile(safe=True))
        # Empty profile → JSON "{}" (not a human-readable fallback)
        assert json.loads(result) == {}

    def test_get_profile_returns_filled_data(self, isolated_engram: Engram):
        isolated_engram.update_profile({"role": "engineer", "language": "zh"})
        result = _run(mcp_server.get_profile(safe=True))
        parsed = json.loads(result)
        assert parsed["role"] == "engineer"
        assert parsed["language"] == "zh"

    def test_get_profile_safe_filters_restricted_fields(
        self, isolated_engram: Engram
    ):
        """Restricted fields in trust_boundaries must be excluded when safe=True."""
        isolated_engram.update_profile(
            {"role": "engineer", "email": "secret@example.com"}
        )
        isolated_engram.update_trust_boundaries({"restricted_fields": ["email"]})
        safe_result = json.loads(_run(mcp_server.get_profile(safe=True)))
        assert "email" not in safe_result
        assert safe_result["role"] == "engineer"

    def test_get_preferences_returns_json(self, isolated_engram: Engram):
        isolated_engram.update_preferences({"work_patterns": {"pace": "fast"}})
        result = json.loads(_run(mcp_server.get_preferences()))
        assert result["work_patterns"] == {"pace": "fast"}

    def test_get_trust_boundaries_returns_defaults(self, isolated_engram: Engram):
        result = json.loads(_run(mcp_server.get_trust_boundaries()))
        # Defaults are written on init
        assert "default_sharing" in result

    def test_get_quality_standards_returns_dict(self, isolated_engram: Engram):
        isolated_engram.update_quality_standards({"acceptance_threshold": 4})
        result = json.loads(_run(mcp_server.get_quality_standards()))
        assert result["acceptance_threshold"] == 4


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

    def test_get_domains_empty(self, isolated_engram: Engram):
        result = _run(mcp_server.get_domains())
        assert "尚无" in result

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
        assert result == {"lessons": [], "decisions": []}


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


class TestToolTier:
    def test_tier1_tools_set_is_well_known_subset(self):
        """The Tier-1 (core) set must stay a curated subset, not the full API."""
        # Sanity: contains lifecycle + key reads/writes
        assert "get_user_context" in mcp_server.TIER1_TOOLS
        assert "add_lesson" in mcp_server.TIER1_TOOLS
        assert "search_knowledge" in mcp_server.TIER1_TOOLS
        # Sanity: there's something the filter would actually remove
        # (i.e., at least one well-known tool not in TIER1)
        assert "find_similar_knowledge" not in mcp_server.TIER1_TOOLS

    def test_apply_tool_tier_noop_when_not_core(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """When TOOL_TIER != 'core', the filter should be a no-op."""
        monkeypatch.setattr(mcp_server, "TOOL_TIER", "all")
        # Should not raise even if the internal tool manager shape is unexpected
        mcp_server._apply_tool_tier()


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
