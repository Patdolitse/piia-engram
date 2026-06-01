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
        assert result["lessons"] == []
        assert result["decisions"] == []
        assert result["playbooks"] == []
        # a3: permissions metadata is always present
        assert "_caller_permissions" in result


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
    def test_get_work_style_returns_json(self, isolated_engram: Engram):
        """Line 275: get_work_style returns JSON of work_style data."""
        result = _run(mcp_server.get_work_style())
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
            raise RuntimeError(r"update boom at C:\Users\pp3x3\secret.json")

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
        result = _run(mcp_server.export_engram_to_openclaw())
        assert "OpenClaw 兼容格式失败" in result

    def test_import_openclaw_exception(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Lines 1093-1094: import_from_openclaw raises -> error message."""

        def explode(*a, **kw):
            raise RuntimeError("import boom")

        monkeypatch.setattr(mcp_server, "import_from_openclaw", explode)
        result = _run(mcp_server.import_engram_from_openclaw())
        assert "OpenClaw 兼容格式导入失败" in result

    def test_export_openclaw_non_success_status(
        self, isolated_engram: Engram, monkeypatch: pytest.MonkeyPatch
    ):
        """Line 1066: export_to_openclaw returns non-success status -> return full result."""

        def fake_export(*a, **kw):
            return {"status": "partial", "message": "some files missing"}

        monkeypatch.setattr(mcp_server, "export_to_openclaw", fake_export)
        result = json.loads(_run(mcp_server.export_engram_to_openclaw()))
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


def test_get_user_context_no_user_prompt_omits_section(isolated_engram: Engram):
    """MCP get_user_context 不传 user_prompt 时不应有「当前用户提问」section。"""
    isolated_engram.update_profile({"role": "developer"})
    result = _run(mcp_server.get_user_context())
    assert "当前用户提问" not in result


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


def test_mcp_apply_legacy_playbook_scope_suggestions_preview(
    isolated_engram: Engram, tmp_path: Path,
):
    """MCP wrapper should expose a safe preview for legacy Playbook migration."""
    project = str(tmp_path / "engram")
    isolated_engram.save_project_snapshot(project, {"title": "Engram"})
    pb = isolated_engram.add_playbook({
        "title": "Engram release checklist",
        "triggers": ["engram", "release"],
    })

    result = json.loads(_run(
        mcp_server.apply_legacy_playbook_scope_suggestions(
            dry_run=True,
            confirm=False,
        )
    ))

    assert result["dry_run"] is True
    assert [item["id"] for item in result["would_apply"]] == [pb["id"]]


def test_mcp_rollback_playbook_scope_migration_preview(
    isolated_engram: Engram, tmp_path: Path,
):
    """MCP wrapper should preview rollback without changing scope."""
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

    result = json.loads(_run(
        mcp_server.rollback_playbook_scope_migration(
            playbook_ids_json=json.dumps([pb["id"]]),
            dry_run=True,
            confirm=False,
        )
    ))

    assert result["dry_run"] is True
    assert [item["id"] for item in result["would_rollback"]] == [pb["id"]]
    assert isolated_engram.get_playbook(
        pb["id"], _update_access=False,
    )["scope"]["type"] == "project"


def test_mcp_playbook_scope_migration_refuses_web_caller_before_write(
    isolated_engram: Engram, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Low-trust web callers must not apply Playbook scope migrations."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    project = str(tmp_path / "engram")
    isolated_engram.save_project_snapshot(project, {"title": "Engram"})
    pb = isolated_engram.add_playbook({
        "title": "Engram release checklist",
        "triggers": ["engram", "release"],
    })

    result = _run(mcp_server.apply_legacy_playbook_scope_suggestions(
        dry_run=False,
        confirm=True,
    ))

    assert "Governance" in result or "治理" in result
    stored = isolated_engram.get_playbook(pb["id"], _update_access=False)
    assert stored["scope"]["type"] == "global"
    assert "scope_migration_history" not in stored


def test_mcp_get_playbook_scope_review_queue(
    isolated_engram: Engram,
):
    """MCP wrapper should expose unresolved Playbook scope review items."""
    pb = isolated_engram.add_playbook({"title": "Daily cleanup", "triggers": ["notes"]})

    result = json.loads(_run(mcp_server.get_playbook_scope_review_queue()))

    assert result["total"] == 1
    assert result["items"][0]["id"] == pb["id"]
    assert result["items"][0]["suggested_scope"]["type"] == "needs_review"


def test_mcp_resolve_playbook_scope_review_accept_shared_preview(
    isolated_engram: Engram, tmp_path: Path,
):
    """MCP wrapper should pass explicit shared project folders to review resolution."""
    project_a = str(tmp_path / "engram")
    project_b = str(tmp_path / "atlas")
    pb = isolated_engram.add_playbook({"title": "Daily cleanup", "triggers": ["notes"]})

    result = json.loads(_run(mcp_server.resolve_playbook_scope_review(
        playbook_id=pb["id"],
        action="accept_shared",
        project_folders_json=json.dumps([project_a, project_b]),
        dry_run=True,
        confirm=False,
    )))

    assert result["dry_run"] is True
    assert result["would_update"]["to_scope"]["type"] == "shared"
    assert result["would_update"]["to_scope"]["project_folders"] == [project_a, project_b]
    stored = isolated_engram.get_playbook(pb["id"], _update_access=False)
    assert stored["scope"]["type"] == "global"


def test_mcp_resolve_playbook_scope_review_refuses_web_caller_before_write(
    isolated_engram: Engram, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Low-trust web callers must not resolve Playbook scope review items."""
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "web")
    project = str(tmp_path / "engram")
    pb = isolated_engram.add_playbook({"title": "Daily cleanup", "triggers": ["notes"]})

    result = _run(mcp_server.resolve_playbook_scope_review(
        playbook_id=pb["id"],
        action="accept_project",
        project_folder=project,
        dry_run=False,
        confirm=True,
    ))

    assert "Governance" in result or "治理" in result
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

    result = json.loads(_run(mcp_server.list_playbooks_for_management(status="all")))

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

    result = json.loads(_run(mcp_server.list_playbooks_for_management(status="all")))
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

    delete_preview = _run(mcp_server.delete_playbook(
        playbook_id=pb["id"],
        reason=f"{secret} dry reason",
        dry_run=True,
        confirm=False,
    ))
    deleted = _run(mcp_server.delete_playbook(
        playbook_id=pb["id"],
        reason=f"{secret} applied reason",
        dry_run=False,
        confirm=True,
    ))
    restore_preview = _run(mcp_server.restore_playbook(
        playbook_id=pb["id"],
        dry_run=True,
        confirm=False,
    ))
    restored = _run(mcp_server.restore_playbook(
        playbook_id=pb["id"],
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

    result = _run(mcp_server.archive_playbook(pb["id"]))

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

    result = _run(mcp_server.delete_playbook(
        playbook_id=pb["id"],
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

    result = _run(mcp_server.restore_playbook(
        playbook_id=pb["id"],
        dry_run=False,
        confirm=True,
    ))

    assert "Governance" in result or "治理" in result
    assert isolated_engram.get_playbook(
        pb["id"], _update_access=False,
    )["status"] == "deleted"
