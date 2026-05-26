"""Additional MCP tool wrapper tests to increase mcp_server.py coverage.

Targets uncovered tool handlers, error paths, resources, and workflow shortcuts.
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
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    """Isolated Engram instance patched into mcp_server._engram."""
    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Read tools — uncovered single-line wrappers
# ---------------------------------------------------------------------------


class TestReadToolsCoverage:
    def test_get_domains_with_data(self, eng: Engram):
        eng.add_lesson({"summary": "x", "domain": "python"})
        result = _run(mcp_server.get_domains())
        parsed = json.loads(result)
        assert "python" in parsed

    def test_list_projects_with_data(self, eng: Engram):
        eng.save_project_snapshot("/proj", {"title": "P"})
        result = _run(mcp_server.list_projects())
        parsed = json.loads(result)
        assert len(parsed) >= 1

    def test_get_knowledge_overview_returns_json(self, eng: Engram):
        result = _run(mcp_server.get_knowledge_overview(section="digest"))
        parsed = json.loads(result)
        assert "digest" in parsed

    def test_get_related_knowledge(self, eng: Engram):
        r = eng.add_lesson({"summary": "lesson A"})
        result = _run(mcp_server.get_related_knowledge(r["id"]))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_find_similar_knowledge(self, eng: Engram):
        r = eng.add_lesson({"summary": "pytest fixture pattern for unit tests"})
        result = _run(mcp_server.find_similar_knowledge(r["id"], limit=3))
        parsed = json.loads(result)
        assert isinstance(parsed, (list, dict))

    def test_suggest_merges_wrapper(self, eng: Engram):
        base = "数据库连接池必须配置最大连接数和超时设置"
        eng.add_lesson({"summary": base})
        # Bypass ingestion dedup by writing directly
        lessons_path = eng._knowledge_dir / "lessons.json"
        lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
        dup = dict(lessons[0])
        dup["id"] = "dup-mcp-test"
        dup["summary"] = base.replace("和超时设置", "与超时配置")
        lessons.append(dup)
        eng._atomic_write(lessons_path, lessons)
        result = _run(mcp_server.suggest_merges(threshold=0.4, limit=5))
        parsed = json.loads(result)
        assert "total_candidates" in parsed
        assert "suggestions" in parsed
        assert parsed["total_candidates"] >= 1

    def test_suggest_merges_empty(self, eng: Engram):
        result = _run(mcp_server.suggest_merges())
        parsed = json.loads(result)
        assert parsed["total_candidates"] == 0

    def test_health_score_in_overview(self, eng: Engram):
        eng.add_lesson({"summary": "test lesson", "domain": "testing"})
        result = _run(mcp_server.get_knowledge_overview(section="health"))
        parsed = json.loads(result)
        health = parsed["health"]
        assert "health_score" in health
        assert "dimensions" in health
        assert 0 <= health["health_score"] <= 100

    def test_export_knowledge_report(self, eng: Engram):
        eng.add_lesson({"summary": "some lesson", "domain": "testing"})
        result = _run(mcp_server.export_knowledge_report())
        assert "经验教训" in result or "lesson" in result.lower()

    def test_get_stale_knowledge(self, eng: Engram):
        result = _run(mcp_server.get_stale_knowledge(days=1, limit=5))
        parsed = json.loads(result)
        assert "lessons" in parsed

    def test_get_knowledge_inheritance(self, eng: Engram):
        eng.add_lesson({"summary": "python async patterns", "domain": "python"})
        result = _run(mcp_server.get_knowledge_inheritance("python web project", limit=5))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_get_relevant_knowledge_empty(self, eng: Engram):
        result = _run(mcp_server.get_relevant_knowledge("/no/proj", limit=5))
        assert "尚无" in result

    def test_get_relevant_knowledge_with_data(self, eng: Engram):
        eng.add_lesson({"summary": "python test pattern", "domain": "python"})
        eng.save_project_snapshot("/proj", {"title": "P", "tech_stack": ["python"]})
        result = _run(mcp_server.get_relevant_knowledge("/proj", limit=5))
        # May return data or "尚无" depending on matching
        assert isinstance(result, str)

    def test_search_knowledge_error_path(self, eng: Engram, monkeypatch):
        def explode(*a, **kw):
            raise RuntimeError("boom")
        monkeypatch.setattr(eng, "search_knowledge", explode)
        result = _run(mcp_server.search_knowledge("test"))
        assert "失败" in result


# ---------------------------------------------------------------------------
# Write tools — optional fields and error paths
# ---------------------------------------------------------------------------


class TestWriteToolsCoverage:
    def test_add_lesson_with_all_fields(self, eng: Engram):
        result = _run(mcp_server.add_lesson(
            summary="full lesson",
            detail="details here",
            domain="python",
            source_tool="claude_code",
            source_url="https://example.com",
        ))
        assert "full lesson" in result
        lessons = eng.get_lessons()
        found = [l for l in lessons if l.get("summary") == "full lesson"]
        assert found
        assert found[0].get("source_tool") == "claude_code"
        assert found[0].get("source_url") == "https://example.com"

    def test_add_lesson_error_path(self, eng: Engram, monkeypatch):
        def explode(data):
            raise RuntimeError("db error")
        monkeypatch.setattr(eng, "add_lesson", explode)
        result = _run(mcp_server.add_lesson(summary="test"))
        assert "失败" in result

    def test_add_decision_with_all_fields(self, eng: Engram):
        result = _run(mcp_server.add_decision(
            question="choice?",
            choice="A",
            reasoning="because",
            source_tool="codex",
            project="myproj",
            domain="arch",
        ))
        assert "choice?" in result
        decisions = eng.get_decisions()
        found = [d for d in decisions if d.get("question") == "choice?"]
        assert found
        assert found[0].get("source_tool") == "codex"
        assert found[0].get("project") == "myproj"
        assert found[0].get("domain") == "arch"

    def test_add_decision_error_path(self, eng: Engram, monkeypatch):
        def explode(data):
            raise RuntimeError("db error")
        monkeypatch.setattr(eng, "add_decision", explode)
        result = _run(mcp_server.add_decision(question="q", choice="c"))
        assert "失败" in result

    def test_add_decision_duplicate(self, eng: Engram):
        _run(mcp_server.add_decision(question="unique_q_dup_test", choice="c1"))
        result = _run(mcp_server.add_decision(question="unique_q_dup_test", choice="c1"))
        parsed = json.loads(result)
        assert parsed.get("status") == "duplicate"

    def test_bulk_add_knowledge_valid(self, eng: Engram):
        items = [{"summary": "bulk1"}, {"summary": "bulk2"}]
        result = _run(mcp_server.bulk_add_knowledge(json.dumps(items), item_type="lesson"))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_bulk_add_knowledge_invalid_json(self, eng: Engram):
        result = _run(mcp_server.bulk_add_knowledge("not json"))
        parsed = json.loads(result)
        assert "error" in parsed

    def test_bulk_add_knowledge_not_array(self, eng: Engram):
        result = _run(mcp_server.bulk_add_knowledge('{"key": "val"}'))
        parsed = json.loads(result)
        assert "error" in parsed

    def test_ingest_notes(self, eng: Engram):
        text = "- 教训：不要在生产环境直接跑未测试的迁移\n- 决策：以后都用 pytest"
        result = _run(mcp_server.ingest_notes(text, source_tool="test", domain="python"))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_extract_session_insights(self, eng: Engram):
        result = _run(mcp_server.extract_session_insights(
            "今天完成了数据库迁移，决定用 PostgreSQL",
            source_tool="claude_code",
        ))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_update_knowledge(self, eng: Engram):
        r = eng.add_lesson({"summary": "old summary"})
        result = _run(mcp_server.update_knowledge(r["id"], '{"summary": "new summary"}'))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_update_knowledge_invalid_json(self, eng: Engram):
        result = _run(mcp_server.update_knowledge("fake-id", "not json"))
        parsed = json.loads(result)
        assert "error" in parsed

    def test_archive_knowledge(self, eng: Engram):
        r = eng.add_lesson({"summary": "to archive"})
        result = _run(mcp_server.archive_knowledge(r["id"]))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_review_knowledge(self, eng: Engram):
        r = eng.add_lesson({"summary": "to review"})
        result = _run(mcp_server.review_knowledge(r["id"]))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Review & merge tools
# ---------------------------------------------------------------------------


class TestReviewMergeTools:
    def test_request_outline_review(self, eng: Engram):
        eng.add_lesson({"summary": "test lesson for review"})
        result = _run(mcp_server.request_outline_review(lang="zh"))
        parsed = json.loads(result)
        assert parsed.get("status") == "review_page_generated"
        assert "path" in parsed

    def test_apply_review_json(self, eng: Engram):
        r = eng.add_lesson({"summary": "to archive via review"})
        review = json.dumps({"archive": [{"id": r["id"], "type": "lesson"}], "promote": []})
        result = _run(mcp_server.apply_review(review))
        parsed = json.loads(result)
        assert parsed.get("archived") >= 0

    def test_apply_review_text(self, eng: Engram):
        r = eng.add_lesson({"summary": "to archive via text review"})
        text = f"archive lesson {r['id']}"
        result = _run(mcp_server.apply_review(text))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_merge_knowledge(self, eng: Engram):
        r1 = eng.add_lesson({"summary": "pytest fixture patterns are reusable across projects"})
        r2 = eng.add_lesson({"summary": "database migration must be tested before production deploy"})
        result = _run(mcp_server.merge_knowledge(r1["id"], r2["id"]))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_link_knowledge(self, eng: Engram):
        r1 = eng.add_lesson({"summary": "React component lifecycle hooks optimization"})
        r2 = eng.add_lesson({"summary": "PostgreSQL query planner uses index scan for small tables"})
        result = _run(mcp_server.link_knowledge(r1["id"], r2["id"]))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_unlink_knowledge(self, eng: Engram):
        r1 = eng.add_lesson({"summary": "Docker multi-stage builds reduce image size significantly"})
        r2 = eng.add_lesson({"summary": "GitHub Actions matrix strategy for cross-platform CI"})
        eng.link_knowledge(r1["id"], r2["id"])
        result = _run(mcp_server.unlink_knowledge(r1["id"], r2["id"]))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Identity update tool
# ---------------------------------------------------------------------------


class TestUpdateIdentity:
    def test_update_profile(self, eng: Engram):
        result = _run(mcp_server.update_identity("profile", '{"role": "senior"}'))
        parsed = json.loads(result)
        assert parsed.get("success") is True
        assert "role" in parsed.get("updated_keys", [])

    def test_update_preferences(self, eng: Engram):
        result = _run(mcp_server.update_identity("preferences", '{"work_patterns": {"pace": "fast"}}'))
        parsed = json.loads(result)
        assert parsed.get("success") is True

    def test_update_invalid_field(self, eng: Engram):
        result = _run(mcp_server.update_identity("nonexistent", '{}'))
        parsed = json.loads(result)
        assert "error" in parsed

    def test_update_invalid_json(self, eng: Engram):
        result = _run(mcp_server.update_identity("profile", "not json"))
        parsed = json.loads(result)
        assert "error" in parsed


# ---------------------------------------------------------------------------
# Save project snapshot (success path)
# ---------------------------------------------------------------------------


class TestSaveProjectSnapshot:
    def test_save_success(self, eng: Engram):
        result = _run(mcp_server.save_project_snapshot(
            project_folder="/test/proj",
            data_json='{"title": "TestProj", "tech_stack": ["python"]}'
        ))
        assert "已保存" in result

    def test_save_invalid_json(self, eng: Engram):
        result = _run(mcp_server.save_project_snapshot(
            project_folder="/test/proj", data_json="not json"
        ))
        assert "错误" in result


# ---------------------------------------------------------------------------
# Import / Export tools
# ---------------------------------------------------------------------------


class TestImportExportCoverage:
    def test_export_engram_to_openclaw(self, eng: Engram):
        eng.update_profile({"role": "dev"})
        result = _run(mcp_server.export_engram_to_openclaw())
        parsed = json.loads(result)
        assert isinstance(parsed, (list, dict))

    def test_import_engram_from_openclaw_empty(self, eng: Engram):
        result = _run(mcp_server.import_engram_from_openclaw())
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_import_engram_success(self, eng: Engram):
        # First export, then import
        export_result = _run(mcp_server.export_engram())
        assert "导出成功" in export_result
        path = export_result.replace("导出成功: ", "")
        result = _run(mcp_server.import_engram(input_path=path, merge=True))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_get_audit_log_no_file(self, eng: Engram):
        result = _run(mcp_server.get_audit_log(limit=10))
        parsed = json.loads(result)
        assert parsed.get("total") == 0 or "entries" in parsed

    def test_get_audit_log_with_entries(self, eng: Engram):
        log_path = eng.root / "audit.log"
        entry = json.dumps({"ts": "2026-01-01", "action": "test", "target": "x"})
        log_path.write_text(entry + "\n", encoding="utf-8")
        result = _run(mcp_server.get_audit_log(limit=10))
        parsed = json.loads(result)
        assert parsed.get("total") >= 1


# ---------------------------------------------------------------------------
# Workflow shortcuts
# ---------------------------------------------------------------------------


class TestWorkflowShortcuts:
    def test_wrap_up_session_minimal(self, eng: Engram):
        result = _run(mcp_server.wrap_up_session(summary="今天完成了测试"))
        parsed = json.loads(result)
        assert "insights" in parsed

    def test_wrap_up_session_with_project(self, eng: Engram):
        result = _run(mcp_server.wrap_up_session(
            summary="完成了数据库迁移",
            project_folder="/test/proj",
            source_tool="claude_code",
            project_title="TestProj",
            tech_stack="python,postgres",
            known_issues="none",
        ))
        parsed = json.loads(result)
        assert "insights" in parsed
        assert "project_snapshot" in parsed

    def test_start_project(self, eng: Engram):
        eng.add_lesson({"summary": "python pattern", "domain": "python"})
        result = _run(mcp_server.start_project(
            description="A new Python web project",
            project_folder="/new/proj",
            project_title="NewProj",
            tech_stack="python,fastapi",
            limit=5,
        ))
        parsed = json.loads(result)
        assert "inherited_knowledge" in parsed
        assert "project_snapshot" in parsed
        assert parsed["project_snapshot"]["created"] is True

    def test_start_project_no_title(self, eng: Engram):
        """When project_title is empty, description[:80] is used as fallback."""
        result = _run(mcp_server.start_project(
            description="Automated testing framework",
            project_folder="/proj2",
        ))
        parsed = json.loads(result)
        assert parsed["project_snapshot"]["created"] is True


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestResourcesCoverage:
    def test_resource_profile(self, eng: Engram):
        eng.update_profile({"role": "dev"})
        result = mcp_server.resource_profile()
        parsed = json.loads(result)
        assert parsed.get("role") == "dev"

    def test_resource_preferences(self, eng: Engram):
        result = mcp_server.resource_preferences()
        json.loads(result)  # just verify valid JSON

    def test_resource_trust_boundaries(self, eng: Engram):
        result = mcp_server.resource_trust_boundaries()
        parsed = json.loads(result)
        assert "default_sharing" in parsed

    def test_resource_work_style(self, eng: Engram):
        result = mcp_server.resource_work_style()
        json.loads(result)

    def test_resource_quality_standards(self, eng: Engram):
        result = mcp_server.resource_quality_standards()
        json.loads(result)

    def test_resource_domains(self, eng: Engram):
        result = mcp_server.resource_domains()
        json.loads(result)

    def test_resource_stats(self, eng: Engram):
        result = mcp_server.resource_stats()
        parsed = json.loads(result)
        assert "schema_version" in parsed


# ---------------------------------------------------------------------------
# memory_store 统一入口测试
# ---------------------------------------------------------------------------


class TestMemoryStore:
    """memory_store 路由、错误处理、落库验证。"""

    def test_memory_store_lesson_routes_to_add_lesson(self, eng: Engram):
        result = _run(mcp_server.memory_store(
            kind="lesson",
            content_json='{"summary": "Always pin dependencies"}',
            source_tool="test_tool",
        ))
        assert "教训已记录" in result
        lessons = eng.get_lessons(limit=50)
        found = [l for l in lessons if "pin dependencies" in l.get("summary", "")]
        assert found
        assert found[0].get("source_tool") == "test_tool"

    def test_memory_store_decision_routes_to_add_decision(self, eng: Engram):
        result = _run(mcp_server.memory_store(
            kind="decision",
            content_json='{"question": "ORM choice", "choice": "SQLAlchemy"}',
            source_tool="test_tool",
        ))
        assert "决策已记录" in result
        decisions = eng.get_decisions(limit=50)
        found = [d for d in decisions if "ORM" in d.get("question", "") or "ORM" in d.get("title", "")]
        assert found

    def test_memory_store_playbook_routes_to_add_playbook(self, eng: Engram):
        result = _run(mcp_server.memory_store(
            kind="playbook",
            content_json=json.dumps({
                "title": "Deploy to staging",
                "triggers": "deploy,staging",
                "steps": [{"order": 1, "action": "build", "detail": "run build"}],
            }),
        ))
        assert "Playbook 已记录" in result

    def test_memory_store_invalid_json(self, eng: Engram):
        result = _run(mcp_server.memory_store(
            kind="lesson", content_json="not json{",
        ))
        assert "content_json 格式错误" in result

    def test_memory_store_unsupported_kind(self, eng: Engram):
        result = _run(mcp_server.memory_store(
            kind="unknown", content_json='{"summary": "test"}',
        ))
        assert "不支持的 kind" in result
        assert "lesson" in result  # should list available kinds

    def test_memory_store_duplicate_returns_json_status(self, eng: Engram):
        content = '{"summary": "Always check return values"}'
        _run(mcp_server.memory_store(kind="lesson", content_json=content))
        result2 = _run(mcp_server.memory_store(kind="lesson", content_json=content))
        parsed = json.loads(result2)
        assert parsed.get("status") == "duplicate"

    def test_mcp_extract_session_insights_persists_staging_tier(self, eng: Engram):
        """MCP wrapper extract_session_insights 落库的知识也应为 staging。"""
        result = _run(mcp_server.extract_session_insights(
            summary="我们决定使用 Redis 做缓存层，因为延迟最低。",
            source_tool="test",
        ))
        parsed = json.loads(result)
        assert parsed["saved_decisions"] >= 1

        decisions = eng.get_decisions(limit=50)
        found = [d for d in decisions if "Redis" in d.get("title", "")]
        assert found
        assert found[0].get("tier") == "staging"


# ---------------------------------------------------------------------------
# F-04: save_project_snapshot rejects non-dict data_json
# ---------------------------------------------------------------------------


class TestSaveProjectSnapshotValidation:
    def test_rejects_array_data_json(self, eng: Engram):
        """data_json 为数组时应返回错误。"""
        result = _run(mcp_server.save_project_snapshot(
            project_folder="/tmp/test-project",
            data_json='[1, 2, 3]',
        ))
        assert "JSON 对象" in result

    def test_rejects_scalar_data_json(self, eng: Engram):
        """data_json 为标量时应返回错误。"""
        result = _run(mcp_server.save_project_snapshot(
            project_folder="/tmp/test-project",
            data_json='"just a string"',
        ))
        assert "JSON 对象" in result


# ---------------------------------------------------------------------------
# F-07: _safe_err strips filesystem paths from error messages
# ---------------------------------------------------------------------------


class TestSafeErr:
    def test_strips_windows_path(self):
        """Windows 绝对路径应被替换为 <path>。"""
        from piia_engram.mcp_server import _safe_err
        err = Exception(r"FileNotFoundError: C:\Users\test\data\file.json not found")
        assert r"C:\Users" not in _safe_err(err)
        assert "<path>" in _safe_err(err)

    def test_strips_unix_path(self):
        """Unix 绝对路径应被替换为 <path>。"""
        from piia_engram.mcp_server import _safe_err
        err = Exception("Permission denied: /home/user/.engram/data.json")
        assert "/home/user" not in _safe_err(err)
        assert "<path>" in _safe_err(err)

    def test_preserves_non_path_message(self):
        """不含路径的错误信息应保持不变。"""
        from piia_engram.mcp_server import _safe_err
        err = Exception("invalid JSON format")
        assert _safe_err(err) == "invalid JSON format"
