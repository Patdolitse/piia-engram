"""Engram 核心功能基础测试。"""

import json
import math
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from engram_core.core import (
    CONFLICT_C_CEILING,
    CONFLICT_Q_THRESHOLD,
    Engram,
    SEARCH_RELEVANCE_THRESHOLD,
    STALE_KNOWLEDGE_DAYS,
    export_to_openclaw,
    extract_knowledge,
    import_from_openclaw,
    ingest_extraction,
    migrate_from_oca_memory,
)


def make_engram(tmp_path: Path) -> Engram:
    """在临时目录创建一个干净的 Engram 实例。"""
    return Engram(root=tmp_path)


def test_init_creates_structure(tmp_path: Path):
    """初始化应创建完整目录结构。"""
    engram = make_engram(tmp_path)
    assert (tmp_path / "identity").is_dir()
    assert (tmp_path / "knowledge").is_dir()
    assert (tmp_path / "projects").is_dir()
    assert (tmp_path / "exports").is_dir()


def test_schema_version(tmp_path: Path):
    """初始化后 schema 应为 2.0。"""
    engram = make_engram(tmp_path)
    ver_path = tmp_path / "schema_version.json"
    assert ver_path.is_file()
    data = json.loads(ver_path.read_text(encoding="utf-8"))
    assert data.get("schema_version") == "2.0"


def test_profile_crud(tmp_path: Path):
    """画像的读写应正常工作。"""
    engram = make_engram(tmp_path)
    # 初始应为空或默认
    profile = engram.get_profile()
    assert isinstance(profile, dict)

    # 更新
    engram.update_profile({"role": "测试用户", "language": "中文"})
    profile = engram.get_profile()
    assert profile["role"] == "测试用户"
    assert profile["language"] == "中文"


def test_add_and_get_lesson(tmp_path: Path):
    """添加教训后应能查询到。"""
    engram = make_engram(tmp_path)
    engram.add_lesson({
        "summary": "测试教训",
        "domain": "testing",
        "source_tool": "test",
    })
    lessons = engram.get_lessons()
    assert len(lessons) >= 1
    assert lessons[0]["summary"] == "测试教训"
    assert lessons[0]["source_tool"] == "test"


def test_add_and_get_decision(tmp_path: Path):
    """添加决策后应能查询到。"""
    engram = make_engram(tmp_path)
    engram.add_decision({
        "question": "用什么测试框架",
        "choice": "pytest",
        "reasoning": "简单好用",
        "source_tool": "test",
    })
    decisions = engram.get_decisions()
    assert len(decisions) >= 1
    assert decisions[0]["question"] == "用什么测试框架"
    assert decisions[0]["choice"] == "pytest"


def test_preferences_crud(tmp_path: Path):
    """偏好的读写应正常工作。"""
    engram = make_engram(tmp_path)
    engram.update_preferences({"tool_preferences": {"编码": "Claude Code"}})
    prefs = engram.get_preferences()
    assert prefs.get("tool_preferences", {}).get("编码") == "Claude Code"


def test_trust_boundaries_crud(tmp_path: Path):
    """信任边界的读写应正常工作。"""
    engram = make_engram(tmp_path)
    tb = engram.get_trust_boundaries()
    assert isinstance(tb, dict)
    assert tb["restricted_fields"] == []

    engram.update_trust_boundaries({"default_sharing": "limited"})
    tb = engram.get_trust_boundaries()
    assert tb["default_sharing"] == "limited"


def test_atomic_write_integrity(tmp_path: Path):
    """原子写辅助方法应完整写入 JSON 且不留下临时文件。"""
    engram = make_engram(tmp_path)
    target = tmp_path / "identity" / "atomic_write_test.json"
    data = {"items": [{"summary": "完整写入"}]}

    engram._atomic_write(target, data)

    assert json.loads(target.read_text(encoding="utf-8")) == data
    assert list(target.parent.glob("*.tmp")) == []


def test_restricted_fields_filtering(tmp_path: Path):
    """safe profile 应过滤 restricted_fields，直接 profile 仍保留完整数据。"""
    engram = make_engram(tmp_path)
    engram.update_trust_boundaries({"restricted_fields": ["email"]})
    engram.update_profile({"name": "Test", "email": "secret@test.com"})

    safe_profile = engram.get_safe_profile()

    assert "email" not in safe_profile
    assert safe_profile["name"] == "Test"
    assert engram.get_profile()["email"] == "secret@test.com"


def test_get_user_context_respects_restricted_fields(tmp_path: Path):
    """冷启动上下文应使用 safe profile，避免输出被限制的画像字段。"""
    engram = make_engram(tmp_path)
    engram.update_trust_boundaries({"restricted_fields": ["role"]})
    engram.update_profile({
        "role": "Secret Role",
        "language": "中文",
        "description": "可公开简介",
    })

    context = engram.generate_context()

    assert "Secret Role" not in context
    assert "可公开简介" in context


def test_project_snapshot_crud(tmp_path: Path):
    """项目快照的读写应正常工作。"""
    engram = make_engram(tmp_path)
    project_folder = str(tmp_path / "demo-project")
    engram.save_project_snapshot(project_folder, {
        "title": "Demo",
        "tech_stack": ["python"],
    })

    snapshot = engram.get_project_snapshot(project_folder)
    assert snapshot["title"] == "Demo"
    assert snapshot["tech_stack"] == ["python"]
    assert snapshot["project_folder"] == project_folder


def test_stats(tmp_path: Path):
    """统计应返回正确数字。"""
    engram = make_engram(tmp_path)
    engram.add_lesson({"summary": "教训1", "domain": "a"})
    engram.add_lesson({"summary": "教训2", "domain": "b"})
    engram.add_decision({"question": "Q", "choice": "A"})

    stats = engram.get_stats()
    assert stats["total_lessons"] == 2
    assert stats["total_decisions"] == 1
    assert stats["domain_count"] >= 2


def test_identity_card(tmp_path: Path):
    """身份卡导出应返回非空字符串。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "工程师", "language": "中文"})
    engram.add_lesson({"summary": "测试", "domain": "test"})
    card = engram.export_identity_card()
    assert isinstance(card, str)
    assert len(card) > 0
    assert "工程师" in card


def test_export_import_round_trip(tmp_path: Path):
    """导出再导入应保留数据。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "RT测试"})
    engram.add_lesson({"summary": "RT教训", "domain": "rt"})
    engram.add_decision({"question": "RT问题", "choice": "RT选择"})

    # 导出
    export_path = tmp_path / "exports" / "test_backup.json"
    engram.export_all(str(export_path))
    assert export_path.is_file()

    # 导入到新实例
    new_root = tmp_path / "new_engram"
    new_engram = Engram(root=new_root)
    new_engram.import_all(str(export_path), merge=True)

    assert new_engram.get_profile().get("role") == "RT测试"
    assert len(new_engram.get_lessons()) >= 1
    assert len(new_engram.get_decisions()) >= 1


def test_import_all_file_not_found(tmp_path: Path):
    """不存在的备份文件应返回 error。"""
    engram = make_engram(tmp_path)
    result = engram.import_all(str(tmp_path / "nonexistent.json"))
    assert "error" in result


def test_import_all_invalid_backup(tmp_path: Path):
    """非法备份文件应返回 error。"""
    engram = make_engram(tmp_path)
    bad_file = tmp_path / "bad_backup.json"
    bad_file.write_text('{"foo": "bar"}', encoding="utf-8")
    result = engram.import_all(str(bad_file))
    assert "error" in result


def test_export_all_custom_path(tmp_path: Path):
    """自定义导出路径应正确创建文件。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("导出路径测试")
    custom_path = tmp_path / "custom" / "backup.json"
    result_path = engram.export_all(str(custom_path))
    assert custom_path.is_file()
    data = json.loads(custom_path.read_text(encoding="utf-8"))
    assert data["schema_version"]
    assert len(data["knowledge"]["lessons"]) >= 1


def test_search_knowledge(tmp_path: Path):
    """应能按关键词搜索经验教训和关键决策。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("Python 虚拟环境避免依赖冲突", "python", source_tool="claude_code")
    engram.add_lesson("Git rebase 前先备份分支", "git", source_tool="codex")
    engram.add_decision("选择 Apache 2.0 许可证", "Apache 2.0", "专利保护", source_tool="claude_code")

    results = engram.search_knowledge("python")
    assert len(results["lessons"]) == 1
    assert results["lessons"][0]["summary"] == "Python 虚拟环境避免依赖冲突"
    assert results["lessons"][0]["access_count"] == 0
    assert results["lessons"][0]["_score"] > 0

    results = engram.search_knowledge("Apache", scope="decisions")
    assert len(results["decisions"]) == 1
    assert len(results["lessons"]) == 0


def test_update_and_archive_lesson(tmp_path: Path):
    """经验教训应能更新并标记为过时。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("旧的经验", "test")
    lessons = engram.get_lessons()
    lesson_id = lessons[0]["id"]

    updated = engram.update_lesson(lesson_id, {"summary": "更新后的经验"})
    assert updated["summary"] == "更新后的经验"
    assert "last_updated" in updated

    archived = engram.archive_lesson(lesson_id)
    assert archived["status"] == "outdated"
    assert engram.get_lessons() == []


def test_duplicate_detection(tmp_path: Path):
    """相似经验不应重复写入。"""
    engram = make_engram(tmp_path)
    first = engram.add_lesson("Python 虚拟环境避免全局依赖冲突", "python")
    duplicate = engram.add_lesson("Python 虚拟环境避免依赖冲突问题", "python")

    assert first.get("status") == "active"
    assert duplicate.get("status") == "duplicate"
    assert len(engram.get_lessons()) == 1


def test_source_filter(tmp_path: Path):
    """经验教训和决策应支持来源工具过滤。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("经验A", "test", source_tool="claude_code")
    engram.add_lesson("经验B", "test", source_tool="codex")
    engram.add_decision("决策A", "A", source_tool="claude_code", project="demo")
    engram.add_decision("决策B", "B", source_tool="codex", project="demo")

    claude_lessons = engram.get_lessons(source_tool="claude_code")
    assert len(claude_lessons) == 1
    assert claude_lessons[0]["summary"] == "经验A"

    codex_decisions = engram.get_decisions(source_tool="codex", project="demo")
    assert len(codex_decisions) == 1
    assert codex_decisions[0]["question"] == "决策B"


def test_health_report(tmp_path: Path):
    """健康报告应返回知识资产统计和分布。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("经验1", "python", source_tool="claude_code")
    engram.add_lesson("经验2", "git", source_tool="codex")
    engram.add_decision("决策1", "A", "因为A好", source_tool="claude_code")

    report = engram.get_health_report()
    assert report["summary"]["active_lessons"] == 2
    assert report["summary"]["active_decisions"] == 1
    assert "python" in report["domain_distribution"]
    assert report["source_distribution"]["claude_code"] == 2
    assert len(report["warnings"]) == 0


def test_update_decision(tmp_path: Path):
    """关键决策应能更新并标记为过时。"""
    engram = make_engram(tmp_path)
    engram.add_decision("旧决策", "A", "理由A")
    decisions = engram.get_decisions()
    decision_id = decisions[0]["id"]

    updated = engram.update_decision(decision_id, {"choice": "B", "reasoning": "理由B更好"})
    assert updated["choice"] == "B"

    archived = engram.archive_decision(decision_id)
    assert archived["status"] == "outdated"
    assert engram.get_decisions() == []


def test_last_reviewed_updated_on_read(tmp_path: Path):
    """读取经验教训时应刷新 last_reviewed 和 access_count。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("需要定期复查的经验", "knowledge")
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    old_review = (datetime.now() - timedelta(days=40)).isoformat()
    lessons[0]["last_reviewed"] = old_review
    lessons[0]["access_count"] = 0
    engram._atomic_write(lessons_path, lessons)

    lessons = engram.get_lessons()

    assert lessons[0]["last_reviewed"] != old_review
    reviewed_at = datetime.fromisoformat(lessons[0]["last_reviewed"])
    assert reviewed_at > datetime.now() - timedelta(minutes=1)
    assert lessons[0]["access_count"] == 1


def test_get_stale_knowledge(tmp_path: Path):
    """超过指定天数未访问的 active 知识应出现在 stale 结果中。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("很久没复查的经验", "python")
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    stale_review = (datetime.now() - timedelta(days=40)).isoformat()
    lessons[0]["last_reviewed"] = stale_review
    engram._atomic_write(lessons_path, lessons)

    stale = engram.get_stale_knowledge(days=30)

    assert len(stale["lessons"]) == 1
    assert stale["lessons"][0]["title"] == "很久没复查的经验"
    assert stale["lessons"][0]["last_reviewed"] == stale_review
    assert stale["decisions"] == []


def test_review_knowledge_updates_review_metadata(tmp_path: Path):
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("round9 review lifecycle lesson", "lifecycle")
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    old_review = (datetime.now() - timedelta(days=45)).isoformat()
    lessons[0]["last_reviewed"] = old_review
    lessons[0]["access_count"] = 1
    engram._atomic_write(lessons_path, lessons)

    reviewed = engram.review_knowledge(lesson["id"])

    assert reviewed["id"] == lesson["id"]
    assert reviewed["last_reviewed"] != old_review
    assert datetime.fromisoformat(reviewed["last_reviewed"]) > datetime.now() - timedelta(minutes=1)
    assert reviewed["access_count"] == 2


def test_review_knowledge_not_found(tmp_path: Path):
    engram = make_engram(tmp_path)

    result = engram.review_knowledge("missing-id")

    assert "error" in result


def test_get_stale_knowledge_limit(tmp_path: Path):
    engram = make_engram(tmp_path)
    first = engram.add_lesson("alpha orchard expiry marker", "lifecycle")
    second = engram.add_lesson("zebra quartz overdue signal", "lifecycle")
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    stale_review = (datetime.now() - timedelta(days=60)).isoformat()
    for lesson in lessons:
        lesson["last_reviewed"] = stale_review
    engram._atomic_write(lessons_path, lessons)

    stale = engram.get_stale_knowledge(days=30, limit=1)
    none = engram.get_stale_knowledge(days=30, limit=0)

    assert len(stale["lessons"]) == 1
    assert stale["lessons"][0]["id"] in {first["id"], second["id"]}
    assert none["lessons"] == []
    assert none["decisions"] == []


def test_health_report_lifecycle_recommendations(tmp_path: Path):
    engram = make_engram(tmp_path)
    review = engram.add_lesson("round9 high access stale review item", "lifecycle")
    archive = engram.add_lesson("round9 zero access archive item", "lifecycle")
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    for lesson in lessons:
        if lesson["id"] == review["id"]:
            lesson["last_reviewed"] = (datetime.now() - timedelta(days=60)).isoformat()
            lesson["access_count"] = 5
        if lesson["id"] == archive["id"]:
            lesson["last_reviewed"] = (datetime.now() - timedelta(days=90)).isoformat()
            lesson["access_count"] = 0
    engram._atomic_write(lessons_path, lessons)

    health = engram.get_health_report()

    assert any(item["id"] == review["id"] for item in health["items_needing_review"])
    assert any(item["id"] == archive["id"] for item in health["items_to_archive"])


def test_generate_context_warns_when_many_stale_items(tmp_path: Path):
    engram = make_engram(tmp_path)
    stale_review = (datetime.now() - timedelta(days=60)).isoformat()
    summaries = [
        "alpha orchard expiry marker",
        "bravo canyon review signal",
        "charlie cedar overdue note",
        "delta harbor stale pointer",
        "echo lantern refresh reminder",
        "foxtrot quartz lifecycle item",
    ]
    for summary in summaries:
        engram.add_lesson(summary, "lifecycle")
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    for lesson in lessons:
        lesson["last_reviewed"] = stale_review
    engram._atomic_write(lessons_path, lessons)

    context = engram.generate_context()

    assert "stale_knowledge_warning" in context
    assert "6" in context


def test_generate_context_omits_warning_when_few_stale_items(tmp_path: Path):
    engram = make_engram(tmp_path)
    stale_review = (datetime.now() - timedelta(days=60)).isoformat()
    for index in range(3):
        engram.add_lesson(f"round9 small stale context item {index}", "lifecycle")
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    for lesson in lessons:
        lesson["last_reviewed"] = stale_review
    engram._atomic_write(lessons_path, lessons)

    context = engram.generate_context()

    assert "stale_knowledge_warning" not in context


def test_knowledge_digest_structure(tmp_path: Path):
    """知识摘要应包含总量、近期新增、常访问、领域分布和过期数量。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("Python 虚拟环境复用经验", "python")
    engram.add_lesson("架构决策需要记录理由", "architecture")
    engram.add_decision("日志格式怎么选", "JSON Lines", "方便机器读取", project="architecture")
    engram.search_knowledge("Python")

    digest = engram.get_knowledge_digest()

    assert digest["total_lessons"] == 2
    assert digest["total_decisions"] == 1
    assert "recent_additions" in digest
    assert "top_accessed" in digest
    assert "by_domain" in digest
    assert digest["by_domain"]["python"]["lessons"] == 1
    assert digest["by_domain"]["architecture"]["decisions"] == 1
    assert isinstance(digest["stale_count"], int)


def test_export_knowledge_report(tmp_path: Path):
    """知识报告应返回中文 Markdown 内容并写入 exports 目录。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("报告里应该出现的经验", "python", source_tool="codex")
    engram.add_decision("报告格式怎么选", "Markdown", "人和 AI 都容易读")

    report = engram.export_knowledge_report()

    assert "# 个人知识报告" in report
    assert "报告里应该出现的经验" in report
    assert "报告格式怎么选" in report
    generated = list((tmp_path / "exports").glob("knowledge_report_*.md"))
    assert len(generated) == 1
    assert generated[0].read_text(encoding="utf-8") == report


def test_link_knowledge(tmp_path: Path):
    """link_knowledge 应在 lesson 和 decision 间建立双向关联。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("数据库迁移前先备份", "database")
    decision = engram.add_decision("迁移策略怎么选", "先备份再迁移", "降低恢复风险")

    result = engram.link_knowledge(lesson["id"], decision["id"])

    assert result["success"] is True
    lessons = json.loads((tmp_path / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    decisions = json.loads((tmp_path / "knowledge" / "decisions.json").read_text(encoding="utf-8"))
    assert decision["id"] in lessons[0]["related_ids"]
    assert lesson["id"] in decisions[0]["related_ids"]


def test_unlink_knowledge(tmp_path: Path):
    """unlink_knowledge 应移除双向关联。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("缓存失效需要显式策略", "backend")
    decision = engram.add_decision("缓存策略怎么选", "短 TTL", "减少陈旧数据风险")
    engram.link_knowledge(lesson["id"], decision["id"])

    result = engram.unlink_knowledge(lesson["id"], decision["id"])

    assert result["success"] is True
    lessons = json.loads((tmp_path / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    decisions = json.loads((tmp_path / "knowledge" / "decisions.json").read_text(encoding="utf-8"))
    assert decision["id"] not in lessons[0]["related_ids"]
    assert lesson["id"] not in decisions[0]["related_ids"]


def test_link_idempotent(tmp_path: Path):
    """重复 link 同一组 ID 不应产生重复 related_ids。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("API 兼容性要先写测试", "api")
    decision = engram.add_decision("版本策略怎么选", "语义化版本", "用户预期清晰")

    engram.link_knowledge(lesson["id"], decision["id"])
    engram.link_knowledge(lesson["id"], decision["id"])

    lessons = json.loads((tmp_path / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    assert lessons[0]["related_ids"].count(decision["id"]) == 1


def test_get_related_knowledge(tmp_path: Path):
    """get_related_knowledge 应返回 source 和关联条目。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("失败测试先行能防止误修", "testing")
    decision = engram.add_decision("开发流程怎么定", "TDD", "先看到失败再实现")
    engram.link_knowledge(lesson["id"], decision["id"])

    related = engram.get_related_knowledge(lesson["id"])

    assert related["source"]["id"] == lesson["id"]
    assert related["source"]["type"] == "lesson"
    assert related["total"] == 1
    assert related["related"][0]["id"] == decision["id"]
    assert related["related"][0]["type"] == "decision"


def test_get_related_knowledge_not_found(tmp_path: Path):
    """不存在的 ID 应返回错误信息而不是抛异常。"""
    engram = make_engram(tmp_path)

    result = engram.get_related_knowledge("missing-id")

    assert result == {"error": "Item not found: missing-id"}


def test_report_shows_related(tmp_path: Path):
    """知识报告应显示关联知识标题。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("发布前需要完整验证", "release")
    decision = engram.add_decision("发版门禁怎么定", "测试通过后再发", "减少回滚风险")
    engram.link_knowledge(lesson["id"], decision["id"])

    report = engram.export_knowledge_report()

    assert "关联：" in report
    assert "发版门禁怎么定" in report
    assert "发布前需要完整验证" in report


def test_bulk_add_lessons(tmp_path: Path):
    """bulk_add_lessons 应一次保存多条 lessons。"""
    engram = make_engram(tmp_path)

    result = engram.bulk_add_lessons([
        "pytest fixture 支持嵌套复用",
        "git rebase 前先备份分支",
        {"summary": "FastAPI 依赖注入要集中管理", "domain": "python"},
    ], source_tool="test")

    assert result["total"] == 3
    assert result["saved"] == 3
    lessons = engram.get_lessons(limit=None)
    assert len(lessons) == 3
    assert all(lesson.get("source_tool") == "test" for lesson in lessons)


def test_bulk_add_lessons_dedup(tmp_path: Path):
    """bulk_add_lessons 应复用 add_lesson 的去重逻辑。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("已有经验需要避免重复", "test")

    result = engram.bulk_add_lessons([
        "已有经验需要避免重复",
        "全新的经验应该保存",
    ])

    assert result["saved"] == 1
    assert result["duplicates"] == 1
    assert len(engram.get_lessons(limit=None)) == 2


def test_bulk_add_decisions(tmp_path: Path):
    """bulk_add_decisions 应一次保存多条 decisions。"""
    engram = make_engram(tmp_path)

    result = engram.bulk_add_decisions([
        {"title": "use postgres", "choice": "postgres"},
        "use redis",
    ], source_tool="test")

    assert result["total"] == 2
    assert result["saved"] == 2
    decisions = engram.get_decisions(limit=None)
    assert len(decisions) == 2
    assert all(decision.get("source_tool") == "test" for decision in decisions)


def test_ingest_notes_basic(tmp_path: Path):
    """ingest_notes 应从自由文本中解析 lessons 和 decisions。"""
    engram = make_engram(tmp_path)
    result = engram.ingest_notes(
        "发现 pytest fixture 可以嵌套使用\n"
        "决定采用 FastAPI 替代 Flask\n"
        "学到 git rebase 比 merge 更干净",
        source_tool="test",
    )

    assert result["saved_lessons"] >= 2
    assert result["saved_decisions"] >= 1
    assert result["parsed"] == 3


def test_ingest_notes_domain_detection(tmp_path: Path):
    """ingest_notes 应按关键词推断 domain。"""
    engram = make_engram(tmp_path)

    result = engram.ingest_notes(
        "pip install 出现依赖冲突时用 venv 隔离",
        source_tool="test",
    )

    lesson_results = [item for item in result["results"] if item.get("type") == "lesson"]
    assert lesson_results[0]["domain"] == "python"


def test_ingest_notes_skip_short(tmp_path: Path):
    """ingest_notes 应跳过短行并保存有效长行。"""
    engram = make_engram(tmp_path)

    result = engram.ingest_notes(
        "ok\n"
        "yes\n"
        "\n"
        "发现 MCP 工具调用可以并行执行以提升性能",
        source_tool="test",
    )

    assert result["skipped"] == 2
    assert result["saved_lessons"] == 1


def test_search_multiword(tmp_path: Path):
    """search_knowledge 应支持多词拆分匹配。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("python 里遇到 import error 时检查 sys.path", domain="python")

    results = engram.search_knowledge("python error")

    assert len(results["lessons"]) == 1
    assert results["lessons"][0]["summary"] == "python 里遇到 import error 时检查 sys.path"


def test_search_returns_score(tmp_path: Path):
    """search_knowledge 应在结果中返回相关性分数。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("pytest fixture 可以嵌套使用")

    results = engram.search_knowledge("pytest fixture")

    assert "_score" in results["lessons"][0]
    assert results["lessons"][0]["_score"] > 0


def test_search_ranking(tmp_path: Path):
    """search_knowledge 应按相关性分数排序。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("pytest 基础用法")
    engram.add_lesson("pytest fixture 进阶：scope 和 autouse 详解")

    results = engram.search_knowledge("pytest fixture")

    assert results["lessons"][0]["summary"] == "pytest fixture 进阶：scope 和 autouse 详解"
    assert results["lessons"][0]["_score"] > results["lessons"][1]["_score"]


def test_search_no_mutation(tmp_path: Path):
    """search_knowledge 是只读操作，不应更新 access_count。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("some test lesson")
    path = engram._knowledge_dir / "lessons.json"
    original_access = next(
        item for item in engram._read_entries(path, "lesson")
        if item["id"] == lesson["id"]
    )["access_count"]

    engram.search_knowledge("test lesson")

    after_access = next(
        item for item in engram._read_entries(path, "lesson")
        if item["id"] == lesson["id"]
    )["access_count"]
    assert after_access == original_access


def test_find_similar_knowledge(tmp_path: Path):
    """find_similar_knowledge 应找到相似知识并排除低相似度条目。"""
    engram = make_engram(tmp_path)
    lesson_a = engram.add_lesson("pytest fixture usage basics", domain="python")
    lesson_b = engram.add_lesson("fixture setup and teardown in pytest")
    lesson_c = engram.add_lesson("docker compose network config")

    result = engram.find_similar_knowledge(lesson_a["id"])
    similar_ids = {item["id"] for item in result["similar"]}

    assert lesson_b["id"] in similar_ids
    assert lesson_c["id"] not in similar_ids


def test_find_similar_not_found(tmp_path: Path):
    """find_similar_knowledge 对不存在 ID 应返回错误。"""
    engram = make_engram(tmp_path)

    result = engram.find_similar_knowledge("nonexistent_id")

    assert result == {"error": "Item not found: nonexistent_id"}


def test_archive_knowledge_lesson(tmp_path: Path):
    """archive_knowledge 应能归档 lesson。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("需要归档的经验")

    result = engram.archive_knowledge(lesson["id"])

    assert result.get("status") == "outdated"
    path = engram._knowledge_dir / "lessons.json"
    all_lessons = engram._read_entries(path, "lesson")
    archived = next((l for l in all_lessons if l["id"] == lesson["id"]), None)
    assert archived is not None
    assert archived["status"] == "outdated"


def test_archive_knowledge_decision(tmp_path: Path):
    """archive_knowledge 应能归档 decision。"""
    engram = make_engram(tmp_path)
    decision = engram.add_decision({"title": "需要归档的决策", "choice": "已过时"})

    result = engram.archive_knowledge(decision["id"])

    assert result.get("status") in ("archived", "outdated")


def test_archive_knowledge_not_found(tmp_path: Path):
    """archive_knowledge 对不存在 ID 应返回错误。"""
    engram = make_engram(tmp_path)

    result = engram.archive_knowledge("nonexistent_id")

    assert "error" in result


def test_merge_knowledge_basic(tmp_path: Path):
    """合并后 primary 保留，secondary 归档。"""
    engram = make_engram(tmp_path)
    primary_lesson = engram.add_lesson("保留主条目的工程实践", domain="engineering")
    secondary_lesson = engram.add_lesson("归档次要条目的维护经验", domain="maintenance")

    result = engram.merge_knowledge(primary_lesson["id"], secondary_lesson["id"])

    assert result["success"] is True
    assert result["secondary_archived"] is True

    all_lessons = engram._read_entries(engram._knowledge_dir / "lessons.json", "lesson")
    secondary = next(item for item in all_lessons if item["id"] == secondary_lesson["id"])
    assert secondary["status"] == "outdated"
    assert secondary["merged_into"] == primary_lesson["id"]

    primary = next(item for item in all_lessons if item["id"] == primary_lesson["id"])
    assert primary["status"] == "active"
    assert primary["summary"] == "保留主条目的工程实践"


def test_merge_knowledge_transfers_related_ids(tmp_path: Path):
    """secondary 的 related_ids 应迁移到 primary（去重并排除自身引用）。"""
    engram = make_engram(tmp_path)
    primary_lesson = engram.add_lesson("主条目保留内容", domain="testing")
    secondary_lesson = engram.add_lesson("次要条目用于合并", domain="quality")
    related_lesson = engram.add_lesson("关联条目迁移验证", domain="integration")

    engram.link_knowledge(secondary_lesson["id"], related_lesson["id"])

    result = engram.merge_knowledge(primary_lesson["id"], secondary_lesson["id"])

    assert result["related_ids_transferred"] == 1
    related = engram.get_related_knowledge(primary_lesson["id"])
    related_ids = [item["id"] for item in related["related"]]
    assert related_lesson["id"] in related_ids
    assert secondary_lesson["id"] not in related_ids


def test_merge_knowledge_self_merge_rejected(tmp_path: Path):
    """不能把自己合并到自己。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("自合并测试", domain="test")

    result = engram.merge_knowledge(lesson["id"], lesson["id"])

    assert result == {"error": "Cannot merge an item with itself"}


def test_merge_knowledge_nonexistent_rejected(tmp_path: Path):
    """不存在的 ID 应返回 error。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("存在的条目", domain="test")

    result = engram.merge_knowledge(lesson["id"], "nonexistent_id")

    assert "error" in result


def test_merge_knowledge_archived_primary_rejected(tmp_path: Path):
    """不能以已归档条目为 primary。"""
    engram = make_engram(tmp_path)
    primary_lesson = engram.add_lesson("已归档主条目", domain="test")
    secondary_lesson = engram.add_lesson("待合并次要条目", domain="merge")
    engram.archive_knowledge(primary_lesson["id"])

    result = engram.merge_knowledge(primary_lesson["id"], secondary_lesson["id"])

    assert "error" in result
    assert "not active" in result["error"]


def test_knowledge_inheritance_returns_relevant_items(tmp_path: Path):
    """描述中出现的关键词应能召回相关条目。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("Python 异步编程优先用 asyncio", domain="python")
    engram.add_lesson("前端组件应保持无状态", domain="frontend")
    engram.add_decision(
        "选择 MCP 协议作为工具接口标准",
        choice="MCP",
        reasoning="跨工具兼容",
    )

    result = engram.get_knowledge_inheritance("Python MCP server asyncio")

    assert result["total"] > 0
    titles = [item.get("title") or item.get("summary") or item.get("question") or "" for item in result["items"]]
    assert any("Python" in title or "MCP" in title or "asyncio" in title for title in titles)


def test_knowledge_inheritance_mixed_types(tmp_path: Path):
    """结果应同时包含 lesson 和 decision（如果都相关）。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("MCP server 应支持 stdio 传输", domain="mcp_dev")
    engram.add_decision(
        "MCP 工具数量控制在 40 以内",
        choice="≤40 tools",
        reasoning="超过20个工具 AI 决策质量下降",
    )

    result = engram.get_knowledge_inheritance("MCP server tool design", limit=10)

    types_found = {item["type"] for item in result["items"]}
    assert "lesson" in types_found
    assert "decision" in types_found


def test_knowledge_inheritance_ranked_by_score(tmp_path: Path):
    """items 应按 score 降序排列，rank 从 1 开始。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("文件写入使用原子操作防止竞争", domain="engineering")
    engram.add_lesson("记录每次重构的原因", domain="engineering")

    result = engram.get_knowledge_inheritance("文件写入原子操作", limit=5)

    if result["total"] >= 2:
        scores = [item["score"] for item in result["items"]]
        assert scores == sorted(scores, reverse=True)
    ranks = [item["rank"] for item in result["items"]]
    assert ranks == list(range(1, len(ranks) + 1))


def test_knowledge_inheritance_recommended_domains(tmp_path: Path):
    """recommended_domains 应包含匹配条目的 domain。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("Python typing 提升代码可读性", domain="python")
    engram.add_lesson("Python 异常应有详细 message", domain="python")

    result = engram.get_knowledge_inheritance("Python 类型注解 异常处理")

    assert "python" in result["recommended_domains"]


def test_knowledge_inheritance_empty_description(tmp_path: Path):
    """空描述应返回空结果，不报错。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("任意教训", domain="test")

    result = engram.get_knowledge_inheritance("")

    assert result["total"] == 0
    assert result["items"] == []
    assert result["recommended_domains"] == []


def test_knowledge_inheritance_limit_respected(tmp_path: Path):
    """limit 参数应被遵守。"""
    engram = make_engram(tmp_path)
    summaries = [
        "alpha ingestion notes",
        "bravo context startup",
        "charlie tool routing",
        "delta memory recall",
        "echo project kickoff",
        "foxtrot archive hygiene",
        "golf report export",
        "hotel search tuning",
        "india workflow guard",
        "juliet release notes",
    ]
    for summary in summaries:
        engram.add_lesson({
            "summary": summary,
            "detail": "Python MCP knowledge inheritance",
            "domain": "python",
        })

    result = engram.get_knowledge_inheritance("Python MCP", limit=3)

    assert result["total"] <= 3


def test_update_knowledge_lesson(tmp_path: Path):
    """update_knowledge 应能更新 lesson 字段。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("原始摘要", domain="test")

    result = engram.update_knowledge(lesson["id"], {"summary": "更新后的摘要"})

    assert result.get("summary") == "更新后的摘要"


def test_update_knowledge_decision(tmp_path: Path):
    """update_knowledge 应能更新 decision 字段。"""
    engram = make_engram(tmp_path)
    decision = engram.add_decision("原始问题", choice="选择A")

    result = engram.update_knowledge(decision["id"], {"choice": "选择B"})

    assert result.get("choice") == "选择B"


def test_update_knowledge_not_found(tmp_path: Path):
    """不存在的 ID 应返回 error。"""
    engram = make_engram(tmp_path)

    result = engram.update_knowledge("nonexistent", {"summary": "x"})

    assert "error" in result


def test_bulk_add_knowledge_lessons(tmp_path: Path):
    """bulk_add_knowledge 应能批量添加 lessons。"""
    engram = make_engram(tmp_path)
    items = [
        {"summary": "Python package metadata includes classifiers", "domain": "python"},
        {"summary": "Docker compose networks need explicit names", "domain": "docker"},
    ]

    result = engram.bulk_add_knowledge(items, item_type="lesson")

    assert result["saved"] == 2


def test_bulk_add_knowledge_decisions(tmp_path: Path):
    """bulk_add_knowledge 应能批量添加 decisions。"""
    engram = make_engram(tmp_path)
    items = [
        {"question": "Use pytest for unit tests", "choice": "pytest"},
        {"question": "Use semver for package releases", "choice": "semver"},
    ]

    result = engram.bulk_add_knowledge(items, item_type="decision")

    assert result["saved"] == 2


def test_bulk_add_knowledge_invalid_type(tmp_path: Path):
    """无效 item_type 应返回 error。"""
    engram = make_engram(tmp_path)

    result = engram.bulk_add_knowledge([], item_type="invalid")

    assert "error" in result


def test_knowledge_overview_all(tmp_path: Path):
    """section=all 应返回 digest + health + stale 三个 key。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("概览测试", domain="test")

    result = engram.get_knowledge_overview("all")

    assert "digest" in result
    assert "health" in result
    assert "stale" in result


def test_knowledge_overview_single_section(tmp_path: Path):
    """单独请求 digest section 应只返回 digest。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("单 section 测试", domain="test")

    result = engram.get_knowledge_overview("digest")

    assert "digest" in result
    assert "health" not in result
    assert "stale" not in result


def test_knowledge_overview_invalid_section(tmp_path: Path):
    """无效 section 应返回 error。"""
    engram = make_engram(tmp_path)

    result = engram.get_knowledge_overview("invalid_section")

    assert "error" in result


def test_get_profile_safe_mode(tmp_path: Path):
    """safe=True 应过滤 restricted_fields 中列出的字段。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"name": "测试用户", "email": "test@example.com"})
    engram.update_trust_boundaries({"restricted_fields": ["email"]})

    safe_profile = engram.get_profile(safe=True)

    assert "email" not in safe_profile
    assert safe_profile.get("name") == "测试用户"


def test_get_profile_normal_mode(tmp_path: Path):
    """safe=False（默认）应返回完整 profile。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"name": "测试用户", "email": "test@example.com"})
    engram.update_trust_boundaries({"restricted_fields": ["email"]})

    full_profile = engram.get_profile(safe=False)

    assert "email" in full_profile


# ── 中文搜索质量 ─────────────────────────────────────────────────────────────

def test_search_chinese_exact(tmp_path: Path):
    """中文关键词应能精确匹配中文内容。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("工具数量控制在35个以内", domain="mcp_dev")
    result = engram.search_knowledge("工具", scope="lessons")
    summaries = [lesson.get("summary", "") for lesson in result["lessons"]]
    assert any("工具" in summary for summary in summaries)


def test_search_chinese_partial(tmp_path: Path):
    """查询子词应能匹配包含该词的条目。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("MCP server 应支持 stdio 传输", domain="mcp_dev")
    result = engram.search_knowledge("stdio", scope="lessons")
    summaries = [lesson.get("summary", "") for lesson in result["lessons"]]
    assert any("stdio" in summary.lower() for summary in summaries)


def test_search_cross_language_alias(tmp_path: Path):
    """英文查 'tool' 应能匹配含 '工具' 的中文条目。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("工具数量影响AI决策质量", domain="mcp_dev")
    result = engram.search_knowledge("tool", scope="lessons")
    summaries = [lesson.get("summary", "") for lesson in result["lessons"]]
    assert any("工具" in summary for summary in summaries)


def test_search_case_insensitive(tmp_path: Path):
    """大小写不应影响搜索结果。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("MCP server 设计原则", domain="mcp_dev")
    result_lower = engram.search_knowledge("mcp server", scope="lessons")
    result_upper = engram.search_knowledge("MCP SERVER", scope="lessons")
    assert len(result_lower["lessons"]) == len(result_upper["lessons"])


def test_bigram_similarity_chinese(tmp_path: Path):
    """中文 bigram 相似度应对相近内容返回 > 0。"""
    engram = make_engram(tmp_path)
    sim = engram._bigram_similarity("工具数量", "工具设计数量")
    assert sim > 0.0


def test_tokenize_cjk(tmp_path: Path):
    """_tokenize 应提取 CJK 字符 unigram 和 bigram。"""
    engram = make_engram(tmp_path)
    tokens = engram._tokenize("工具数量")
    assert "工" in tokens
    assert "工具" in tokens
    assert "具数" in tokens


def test_tokenize_alias_expansion(tmp_path: Path):
    """_tokenize 应将 'tool' 展开为包含 '工具' 的 token 集合。"""
    engram = make_engram(tmp_path)
    tokens = engram._tokenize("tool design")
    assert "工具" in tokens
    assert "tool" in tokens


def test_tokenize_alias_js_ts_db(tmp_path: Path):
    """缩写 js/ts/db 应展开为完整形式。"""
    engram = make_engram(tmp_path)
    js_tokens = engram._tokenize("js")
    assert "javascript" in js_tokens
    ts_tokens = engram._tokenize("ts")
    assert "typescript" in ts_tokens
    db_tokens = engram._tokenize("db")
    assert "database" in db_tokens
    assert "数据库" in db_tokens


def test_search_alias_cross_language_new(tmp_path: Path):
    """CJK 别名搜索应命中英文 lesson（数据库→database、部署→deploy）。"""
    engram = make_engram(tmp_path)
    engram.add_lesson({"summary": "Database indexing improves query speed", "domain": "backend"})
    engram.add_lesson({"summary": "Deploy with zero-downtime rolling updates", "domain": "devops"})
    db_results = engram.search_knowledge("数据库")
    db_lessons = db_results.get("lessons", [])
    assert any("Database" in r.get("summary", "") for r in db_lessons)
    deploy_results = engram.search_knowledge("部署")
    deploy_lessons = deploy_results.get("lessons", [])
    assert any("Deploy" in r.get("summary", "") for r in deploy_lessons)


# ── extract_session_insights ─────────────────────────────────────────────────

def test_extract_session_insights_basic(tmp_path: Path):
    """段落摘要应能提取 lessons 和 decisions。"""
    engram = make_engram(tmp_path)
    summary = """
    我们决定采用 portalocker 来保护文件写入。
    发现 Python bigram 对中文支持不好，需要改进。
    最终选择了字符级 n-gram 方案。
    """

    result = engram.extract_session_insights(summary, source_tool="test")

    assert result["saved_lessons"] + result["saved_decisions"] > 0


def test_extract_session_insights_english(tmp_path: Path):
    """英文摘要也应能提取知识。"""
    engram = make_engram(tmp_path)
    summary = (
        "We decided to use GitHub Releases to trigger PyPI publishing. "
        "Remember to check package name availability before publishing."
    )

    result = engram.extract_session_insights(summary, source_tool="test")

    assert result["saved_lessons"] + result["saved_decisions"] > 0


def test_extract_session_insights_empty(tmp_path: Path):
    """空摘要应返回全零结果，不报错。"""
    engram = make_engram(tmp_path)

    result = engram.extract_session_insights("", source_tool="test")

    assert result["saved_lessons"] == 0
    assert result["saved_decisions"] == 0
    assert result["results"] == []


def test_extract_session_insights_deduplication(tmp_path: Path):
    """重复调用相同摘要不应重复存储。"""
    engram = make_engram(tmp_path)
    summary = "注意：PyPI 包名需要提前确认是否被占用。"

    result1 = engram.extract_session_insights(summary, source_tool="test")
    result2 = engram.extract_session_insights(summary, source_tool="test")

    assert result1["saved_lessons"] >= 1
    assert result2["duplicates"] >= 1


def test_extract_session_insights_skips_noise(tmp_path: Path):
    """短句和无意义内容应被跳过。"""
    engram = make_engram(tmp_path)
    summary = "OK\n好的\n嗯\n确认"

    result = engram.extract_session_insights(summary, source_tool="test")

    assert result["skipped"] > 0
    assert result["saved_lessons"] == 0
    assert result["saved_decisions"] == 0


def test_extract_session_insights_broader_patterns(tmp_path: Path):
    """'应该'/'因此' 等结构词应触发提取，即使没有 LESSON_TRIGGERS 词。"""
    engram = make_engram(tmp_path)
    summary = "应该在发布前运行完整测试套件。因此我们改为使用 Release 触发发布。"

    result = engram.extract_session_insights(summary, source_tool="test")

    assert result["saved_lessons"] + result["saved_decisions"] >= 1


# ── remote deployment ──────────────────────────────────────────────────────

def test_parse_args_defaults():
    """默认参数应为 stdio 模式。"""
    from engram_core.mcp_server import _parse_args

    args = _parse_args(["mcp_server.py"])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8767


def test_parse_args_sse_mode():
    """SSE 模式参数应正确解析。"""
    from engram_core.mcp_server import _parse_args

    args = _parse_args([
        "mcp_server.py",
        "--transport",
        "sse",
        "--host",
        "0.0.0.0",
        "--port",
        "9999",
    ])

    assert args.transport == "sse"
    assert args.host == "0.0.0.0"
    assert args.port == 9999


def test_token_auth_middleware_rejects_invalid_token():
    """TokenAuthMiddleware 应拒绝缺失或错误 Bearer token。"""
    import asyncio

    from engram_core.mcp_server import TokenAuthMiddleware

    class FakeRequest:
        headers = {}

    async def call_next(_request):
        raise AssertionError("unauthorized request should not reach app")

    middleware = TokenAuthMiddleware(lambda scope, receive, send: None, token="secret")
    response = asyncio.run(middleware.dispatch(FakeRequest(), call_next))

    assert response.status_code == 401


def test_token_auth_middleware_accepts_valid_token():
    """TokenAuthMiddleware 应放行正确 Bearer token。"""
    import asyncio

    from starlette.responses import JSONResponse

    from engram_core.mcp_server import TokenAuthMiddleware

    class FakeRequest:
        headers = {"authorization": "Bearer secret"}

    async def call_next(_request):
        return JSONResponse({"ok": True})

    middleware = TokenAuthMiddleware(lambda scope, receive, send: None, token="secret")
    response = asyncio.run(middleware.dispatch(FakeRequest(), call_next))

    assert response.status_code == 200


# ══════════════════════════════════════════════════════════════════════
# Security hardening tests (v3.11.2)
# ══════════════════════════════════════════════════════════════════════


def test_update_profile_rejects_unknown_fields(tmp_path: Path):
    """update_profile 应静默丢弃不在白名单中的字段。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "developer", "malicious_key": "evil"})

    profile = engram.get_profile()
    assert profile.get("role") == "developer"
    assert "malicious_key" not in profile


def test_update_preferences_rejects_unknown_fields(tmp_path: Path):
    """update_preferences 应丢弃未知字段。"""
    engram = make_engram(tmp_path)
    engram.update_preferences({"communication": "简洁", "hack": "inject"})

    prefs = engram.get_preferences()
    assert prefs.get("communication") == "简洁"
    assert "hack" not in prefs


def test_update_trust_boundaries_rejects_unknown_fields(tmp_path: Path):
    """update_trust_boundaries 应丢弃未知字段。"""
    engram = make_engram(tmp_path)
    engram.update_trust_boundaries({"restricted_fields": ["email"], "pwned": True})

    tb = engram.get_trust_boundaries()
    assert "email" in tb["restricted_fields"]
    assert "pwned" not in tb


def test_update_quality_standards_rejects_unknown_fields(tmp_path: Path):
    """update_quality_standards 应丢弃未知字段。"""
    engram = make_engram(tmp_path)
    engram.update_quality_standards({"acceptance_threshold": 4, "exploit": "xss"})

    qs = engram.get_quality_standards()
    assert qs.get("acceptance_threshold") == 4
    assert "exploit" not in qs


def test_identity_card_respects_trust_boundaries(tmp_path: Path):
    """export_identity_card 应使用 safe profile，不输出被限制字段。"""
    engram = make_engram(tmp_path)
    engram.update_profile({
        "role": "高级工程师",
        "email": "secret@corp.com",
        "language": "中文",
    })
    engram.update_trust_boundaries({"restricted_fields": ["email"]})

    card = engram.export_identity_card()
    assert "高级工程师" in card
    assert "secret@corp.com" not in card


def test_reconcile_skips_large_files(tmp_path: Path):
    """reconcile_memories 应跳过超过 10KB 的文件。"""
    engram = make_engram(tmp_path / "engram")

    mem_dir = tmp_path / ".claude" / "projects" / "test" / "memory"
    mem_dir.mkdir(parents=True)
    # Normal file
    (mem_dir / "small.md").write_text("Small memory content here", encoding="utf-8")
    # Oversized file (> 10KB)
    (mem_dir / "huge.md").write_text("x" * 15_000, encoding="utf-8")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()

    assert result["skipped_large"] == 1
    assert result["scanned_files"] == 2  # both encountered, one skipped


def test_filter_allowed_static_method(tmp_path: Path):
    """_filter_allowed 应正确分离合法和非法字段。"""
    engram = make_engram(tmp_path)
    allowed = frozenset({"a", "b", "c"})
    filtered, rejected = engram._filter_allowed(
        {"a": 1, "b": 2, "x": 3, "y": 4}, allowed
    )
    assert filtered == {"a": 1, "b": 2}
    assert sorted(rejected) == ["x", "y"]


# ── Work style / domains / projects ────────────────────────────────


def test_get_and_update_work_style(tmp_path: Path):
    """update_work_style 应合并数据，get_work_style 应返回。"""
    engram = make_engram(tmp_path)
    assert engram.get_work_style() == {}
    engram.update_work_style({"decision_style": "data-driven"})
    result = engram.get_work_style()
    assert result["decision_style"] == "data-driven"
    assert "updated_at" in result


def test_get_domains_empty(tmp_path: Path):
    """无 domain 数据时应返回空 dict。"""
    engram = make_engram(tmp_path)
    assert engram.get_domains() == {}


def test_update_domain_creates_and_updates(tmp_path: Path):
    """update_domain 应创建和更新 domain 条目。"""
    engram = make_engram(tmp_path)
    # get_domains() derives counts from active lessons, so we need a lesson
    engram.add_lesson("Python 列表推导式效率高", domain="python")
    engram.update_domain("python", {"skills": ["FastAPI"]})
    domains = engram.get_domains()
    assert "python" in domains
    assert domains["python"]["skills"] == ["FastAPI"]
    assert domains["python"]["project_count"] == 1  # from the lesson


def test_list_projects_empty(tmp_path: Path):
    """无项目快照时应返回空列表。"""
    engram = make_engram(tmp_path)
    assert engram.list_projects() == []


def test_list_projects_after_save(tmp_path: Path):
    """保存项目快照后 list_projects 应列出它。"""
    engram = make_engram(tmp_path)
    engram.save_project_snapshot(str(tmp_path / "my-app"), {
        "title": "My App",
        "tech_stack": ["Python", "FastAPI"],
    })
    projects = engram.list_projects()
    assert len(projects) >= 1
    assert any(p.get("title") == "My App" for p in projects)


def test_get_relevant_lessons_returns_list(tmp_path: Path):
    """get_relevant_lessons 应返回经验列表。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("pytest 支持 parametrize", domain="python")
    engram.add_lesson("React hooks 必须在顶层调用", domain="frontend")
    engram.add_lesson("Docker 镜像要用多阶段构建", domain="devops")
    lessons = engram.get_relevant_lessons(limit=2, _update_access=False)
    assert isinstance(lessons, list)
    assert len(lessons) <= 2


def test_get_relevant_lessons_project_domain_priority(tmp_path: Path):
    """项目技术栈匹配的 domain 应优先返回。"""
    engram = make_engram(tmp_path)
    # Semantically distinct lessons to avoid dedup
    py_lessons = [
        "用 pathlib 替代 os.path 处理文件路径更 Pythonic",
        "asyncio TaskGroup 比 gather 更安全",
        "dataclass frozen 可以模拟不可变对象",
    ]
    fe_lessons = [
        "React 组件拆分遵循单一职责原则",
        "TypeScript 用 interface 定义对象形状",
        "Vite 替代 webpack 加速前端开发构建",
    ]
    for s in py_lessons:
        engram.add_lesson(s, domain="python")
    for s in fe_lessons:
        engram.add_lesson(s, domain="frontend")

    engram.save_project_snapshot(str(tmp_path / "py-proj"), {
        "title": "Python Project",
        "tech_stack": ["Python"],
    })

    lessons = engram.get_relevant_lessons(
        project_folder=str(tmp_path / "py-proj"),
        limit=6,
        _update_access=False,
    )
    python_count = sum(1 for l in lessons if l.get("domain") == "python")
    # With a Python tech stack, python domain lessons should dominate
    assert python_count >= 2


# ── extract_knowledge / ingest_extraction ──────────────────────────


def test_extract_knowledge_returns_none_without_provider(tmp_path: Path):
    """provider=None 时 extract_knowledge 应返回 None。"""
    result = extract_knowledge(
        conversation=[{"role": "user", "content": "hello"}],
        project_folder=str(tmp_path),
        project_files="main.py",
        provider=None,
    )
    assert result is None


def test_extract_knowledge_returns_none_for_empty_conversation():
    """空对话时应返回 None。"""
    result = extract_knowledge(
        conversation=[],
        project_folder="/some/path",
        project_files="",
        provider="dummy",
    )
    assert result is None


def test_ingest_extraction_applies_profile(tmp_path: Path):
    """ingest_extraction 应将 profile_updates 写入 Engram。"""
    engram = make_engram(tmp_path)
    extracted = {
        "profile_updates": {"role": "全栈开发者", "language": "中文"},
    }
    result = ingest_extraction(engram, extracted, str(tmp_path))
    assert result["items_learned"] >= 1
    profile = engram.get_profile()
    assert profile["role"] == "全栈开发者"


def test_ingest_extraction_applies_lessons_and_decisions(tmp_path: Path):
    """ingest_extraction 应添加 lessons 和 decisions。"""
    engram = make_engram(tmp_path)
    extracted = {
        "lessons": [
            {"summary": "pytest 的 parametrize 能减少重复测试代码", "domain": "python"},
        ],
        "decisions": [
            {"question": "测试框架选型", "choice": "pytest", "reasoning": "生态好"},
        ],
    }
    result = ingest_extraction(engram, extracted, str(tmp_path), session_id="test-session")
    assert result["items_learned"] >= 2

    lessons = engram.get_lessons(limit=None, _update_access=False)
    assert any("parametrize" in l.get("summary", "") for l in lessons)

    decisions = engram.get_decisions(limit=None, _update_access=False)
    assert any("测试框架" in d.get("question", d.get("title", "")) for d in decisions)


def test_ingest_extraction_empty_dict(tmp_path: Path):
    """空提取结果不应崩溃。"""
    engram = make_engram(tmp_path)
    result = ingest_extraction(engram, {}, str(tmp_path))
    assert result["items_learned"] == 0


# =====================================================================
# increment_domain_usage
# =====================================================================


def test_increment_domain_usage_creates_entry(tmp_path: Path):
    """首次调用应创建 domain 条目，含 first_seen 和 project_count=1。"""
    engram = make_engram(tmp_path)
    engram.increment_domain_usage("rust")
    domains_path = tmp_path / "knowledge" / "domains.json"
    data = json.loads(domains_path.read_text(encoding="utf-8"))
    assert "rust" in data
    assert data["rust"]["project_count"] == 1
    assert "first_seen" in data["rust"]
    assert "last_used" in data["rust"]


def test_increment_domain_usage_increments(tmp_path: Path):
    """多次调用应递增 project_count。"""
    engram = make_engram(tmp_path)
    engram.increment_domain_usage("go")
    engram.increment_domain_usage("go")
    engram.increment_domain_usage("go")
    domains_path = tmp_path / "knowledge" / "domains.json"
    data = json.loads(domains_path.read_text(encoding="utf-8"))
    assert data["go"]["project_count"] == 3


def test_increment_domain_usage_multiple_domains(tmp_path: Path):
    """不同 domain 应独立计数。"""
    engram = make_engram(tmp_path)
    engram.increment_domain_usage("python")
    engram.increment_domain_usage("javascript")
    engram.increment_domain_usage("python")
    domains_path = tmp_path / "knowledge" / "domains.json"
    data = json.loads(domains_path.read_text(encoding="utf-8"))
    assert data["python"]["project_count"] == 2
    assert data["javascript"]["project_count"] == 1


# =====================================================================
# migrate_from_oca_memory
# =====================================================================


def test_migrate_from_oca_memory_empty_dir(tmp_path: Path):
    """空目录迁移不应崩溃，返回空 migrated 列表。"""
    engram = make_engram(tmp_path)
    oca_dir = tmp_path / "oca_memory"
    oca_dir.mkdir()
    result = migrate_from_oca_memory(str(oca_dir), engram)
    assert result["migrated"] == []


def test_migrate_from_oca_memory_owner_profile(tmp_path: Path):
    """应迁移 owner_profile.json 到 Engram profile。"""
    engram = make_engram(tmp_path)
    oca_dir = tmp_path / "oca_memory"
    oca_dir.mkdir()
    profile = {
        "language": "中文",
        "preferences": {"editor": "vscode"},
        "quality_threshold": 0.85,
    }
    (oca_dir / "owner_profile.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )
    result = migrate_from_oca_memory(str(oca_dir), engram)
    assert "owner_profile" in result["migrated"]
    p = engram.get_profile()
    assert p["language"] == "中文"
    # migrated_from 不在 _ALLOWED_PROFILE_FIELDS 白名单中，会被过滤
    standards = engram.get_quality_standards()
    assert standards.get("acceptance_threshold") == 0.85


def test_migrate_from_oca_memory_project_patterns(tmp_path: Path):
    """应迁移 project_patterns.json 的 file_types 到 domain。"""
    engram = make_engram(tmp_path)
    oca_dir = tmp_path / "oca_memory"
    oca_dir.mkdir()
    patterns = {"common_file_types": {".py": 10, ".js": 5, ".unknown": 2}}
    (oca_dir / "project_patterns.json").write_text(
        json.dumps(patterns), encoding="utf-8"
    )
    result = migrate_from_oca_memory(str(oca_dir), engram)
    assert "project_patterns" in result["migrated"]
    domains_path = tmp_path / "knowledge" / "domains.json"
    data = json.loads(domains_path.read_text(encoding="utf-8"))
    assert "python" in data
    assert "javascript" in data
    # .unknown 不在 domain_map 中，应被忽略
    assert "unknown" not in data


def test_migrate_from_oca_memory_near_misses(tmp_path: Path):
    """应迁移 near_misses.json 为 safety domain 的 lesson。"""
    engram = make_engram(tmp_path)
    oca_dir = tmp_path / "oca_memory"
    oca_dir.mkdir()
    near_misses = [
        {
            "what_happened": "差点删了生产数据库",
            "what_could_have_happened": "数据全丢",
        },
        {
            "what_happened": "误操作 git force push",
            "what_could_have_happened": "团队代码丢失",
        },
    ]
    (oca_dir / "near_misses.json").write_text(
        json.dumps(near_misses), encoding="utf-8"
    )
    result = migrate_from_oca_memory(str(oca_dir), engram)
    assert any("near_misses" in m for m in result["migrated"])
    lessons = engram.get_lessons(limit=None, _update_access=False)
    safety_lessons = [l for l in lessons if l.get("domain") == "safety"]
    assert len(safety_lessons) == 2


# =====================================================================
# export_to_openclaw
# =====================================================================


def test_export_to_openclaw_creates_three_files(tmp_path: Path):
    """应在输出目录创建 SOUL.md、USER.md、MEMORY.md。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "developer", "language": "中文"})
    out_dir = tmp_path / "openclaw_export"
    result = export_to_openclaw(engram, str(out_dir))
    assert result["status"] == "success"
    assert len(result["files"]) == 3
    assert (out_dir / "SOUL.md").is_file()
    assert (out_dir / "USER.md").is_file()
    assert (out_dir / "MEMORY.md").is_file()


def test_export_to_openclaw_soul_contains_profile(tmp_path: Path):
    """SOUL.md 应包含 profile 信息。"""
    engram = make_engram(tmp_path)
    engram.update_profile({
        "role": "senior_engineer",
        "language": "中文",
        "technical_level": "expert",
    })
    out_dir = tmp_path / "export"
    export_to_openclaw(engram, str(out_dir))
    soul = (out_dir / "SOUL.md").read_text(encoding="utf-8")
    assert "senior_engineer" in soul
    assert "expert" in soul
    assert "# SOUL" in soul


def test_export_to_openclaw_memory_contains_lessons(tmp_path: Path):
    """MEMORY.md 应包含 lessons 和 decisions。"""
    engram = make_engram(tmp_path)
    engram.add_lesson({"summary": "永远先写测试", "domain": "testing"})
    engram.add_decision({
        "question": "数据库选型",
        "choice": "PostgreSQL",
        "reasoning": "成熟可靠",
    })
    out_dir = tmp_path / "export"
    export_to_openclaw(engram, str(out_dir))
    memory = (out_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "永远先写测试" in memory
    assert "[testing]" in memory
    assert "PostgreSQL" in memory
    assert "数据库选型" in memory


def test_export_to_openclaw_empty_data(tmp_path: Path):
    """空数据导出不应崩溃。"""
    engram = make_engram(tmp_path)
    out_dir = tmp_path / "export_empty"
    result = export_to_openclaw(engram, str(out_dir))
    assert result["status"] == "success"
    assert (out_dir / "SOUL.md").is_file()


# =====================================================================
# import_from_openclaw
# =====================================================================


def test_import_from_openclaw_user_md(tmp_path: Path):
    """应从 USER.md 导入 profile 字段。"""
    engram = make_engram(tmp_path)
    user_file = tmp_path / "USER.md"
    user_file.write_text(
        "# USER\n\n- Role: architect\n- Language: English\n- Technical Level: senior\n",
        encoding="utf-8",
    )
    result = import_from_openclaw(engram, user_path=str(user_file))
    assert result["status"] == "success"
    p = engram.get_profile()
    assert p["role"] == "architect"
    assert p["language"] == "English"
    assert p["technical_level"] == "senior"


def test_import_from_openclaw_soul_md(tmp_path: Path):
    """应从 SOUL.md 导入 work preferences 和 quality standards。"""
    engram = make_engram(tmp_path)
    soul_file = tmp_path / "SOUL.md"
    soul_file.write_text(
        "# SOUL\n\n"
        "## Work Preferences\n"
        "- Editor: VSCode\n"
        "- Style: pragmatic\n\n"
        "## Quality Standards\n"
        "- 所有代码必须有测试\n"
        "- PR 必须经过 review\n",
        encoding="utf-8",
    )
    result = import_from_openclaw(engram, soul_path=str(soul_file))
    assert result["status"] == "success"
    assert any("preferences" in item for item in result["imported"])
    assert any("quality_standards" in item for item in result["imported"])


def test_import_from_openclaw_memory_md_lessons(tmp_path: Path):
    """应从 MEMORY.md 导入 lessons，跳过重复。"""
    engram = make_engram(tmp_path)
    # 先添加一条已有的 lesson
    engram.add_lesson({"summary": "已有的经验"})
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "# MEMORY\n\n"
        "## Lessons Learned\n"
        "- [python] 用 virtualenv 隔离依赖\n"
        "- 已有的经验\n"
        "- [testing] mock 要谨慎使用\n",
        encoding="utf-8",
    )
    result = import_from_openclaw(engram, memory_path=str(memory_file))
    assert result["status"] == "success"
    # 应只导入 2 条新的（"已有的经验"跳过）
    lessons = engram.get_lessons(limit=None, _update_access=False)
    summaries = [l.get("summary", "") for l in lessons]
    assert "用 virtualenv 隔离依赖" in summaries
    assert "mock 要谨慎使用" in summaries


def test_import_from_openclaw_missing_files(tmp_path: Path):
    """不存在的文件路径不应崩溃。"""
    engram = make_engram(tmp_path)
    result = import_from_openclaw(
        engram,
        soul_path=str(tmp_path / "nonexistent_SOUL.md"),
        user_path=str(tmp_path / "nonexistent_USER.md"),
    )
    assert result["status"] == "no_new_data"
    assert result["imported"] == []


# =====================================================================
# classify_rarity
# =====================================================================


def test_classify_rarity_staging_always_staging(tmp_path: Path):
    """staging 条目不论内容如何，rarity 始终为 staging。"""
    engram = make_engram(tmp_path)
    item = {"tier": "staging", "summary": "核心身份原则", "domain": "identity", "access_count": 99}
    assert engram.classify_rarity(item) == "staging"


def test_classify_rarity_decision_gets_bonus(tmp_path: Path):
    """decision 类型有额外加分，更容易达到 epic/legendary。"""
    engram = make_engram(tmp_path)
    # 简单 lesson 应该是 rare
    lesson = {"tier": "verified", "summary": "一个普通经验", "domain": "general"}
    assert engram.classify_rarity(lesson, "lesson") == "rare"
    # 相同内容但 decision 类型会有加分
    decision = {"tier": "verified", "summary": "一个普通决策", "domain": "python",
                "reasoning": "因为有很好的理由所以选择了这个方案"}
    rarity = engram.classify_rarity(decision, "decision")
    assert rarity in ("epic", "legendary")


def test_classify_rarity_identity_content_scores_high(tmp_path: Path):
    """包含身份关键词的内容应得到较高 rarity。"""
    engram = make_engram(tmp_path)
    item = {
        "tier": "verified",
        "summary": "核心身份定位",
        "detail": "这是一条关于角色定位和核心原则的深入分析" * 5,
        "domain": "identity",
        "access_count": 5,
    }
    rarity = engram.classify_rarity(item)
    assert rarity in ("epic", "legendary")


# =====================================================================
# evaluate_tiers
# =====================================================================


def test_evaluate_tiers_promotes_referenced_staging(tmp_path: Path):
    """access_count >= 3 的 staging 条目应被提升为 verified。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("高频引用的经验", domain="python", tier="staging")
    # 手动设置 access_count >= 3
    path = tmp_path / "knowledge" / "lessons.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data:
        if entry.get("id") == lesson["id"]:
            entry["access_count"] = 5
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result = engram.evaluate_tiers()
    assert result["promoted"] >= 1

    # 验证已变为 verified
    data = json.loads(path.read_text(encoding="utf-8"))
    promoted_entry = next(e for e in data if e.get("id") == lesson["id"])
    assert promoted_entry["tier"] == "verified"
    assert "promoted_at" in promoted_entry


def test_evaluate_tiers_keeps_low_access_staging(tmp_path: Path):
    """access_count < 3 的 staging 条目不应被提升。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("低频引用的经验", domain="python", tier="staging")
    result = engram.evaluate_tiers()
    assert result["promoted"] == 0


# =====================================================================
# get_staging_summary
# =====================================================================


def test_get_staging_summary_empty(tmp_path: Path):
    """无 staging 条目时应返回全零。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("一条普通 lesson")  # 默认 tier=verified
    summary = engram.get_staging_summary()
    assert summary["total_staging"] == 0
    assert summary["staging_lessons"] == 0
    assert summary["staging_decisions"] == 0


def test_get_staging_summary_counts_staging(tmp_path: Path):
    """应正确统计 staging 的 lesson 和 decision 数量。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("数据库索引策略值得深入研究", tier="staging")
    engram.add_lesson("Docker 多阶段构建减少镜像体积", tier="staging")
    engram.add_decision("staging question", choice="A", tier="staging")
    engram.add_lesson("verified lesson")  # 默认 verified，不计入
    summary = engram.get_staging_summary()
    assert summary["staging_lessons"] == 2
    assert summary["staging_decisions"] == 1
    assert summary["total_staging"] == 3


# =====================================================================
# promote_knowledge
# =====================================================================


def test_promote_knowledge_lesson(tmp_path: Path):
    """应将 staging lesson 提升为 verified。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("待审核经验", tier="staging")
    result = engram.promote_knowledge(lesson["id"])
    assert result["status"] == "promoted"
    # 检查实际写入
    lessons = engram.get_lessons(limit=None, _update_access=False)
    promoted = next(l for l in lessons if l.get("id") == lesson["id"])
    assert promoted["tier"] == "verified"
    assert promoted["promotion_reason"] == "user_confirmed"


def test_promote_knowledge_not_found(tmp_path: Path):
    """不存在的 ID 应返回 not_found。"""
    engram = make_engram(tmp_path)
    result = engram.promote_knowledge("nonexistent-id")
    assert result["status"] == "not_found"


# =====================================================================
# apply_review
# =====================================================================


def test_apply_review_dict_format(tmp_path: Path):
    """dict 格式的 review_data 应正确处理 promote 和 archive。"""
    engram = make_engram(tmp_path)
    staging = engram.add_lesson("待审核", tier="staging")
    to_archive = engram.add_lesson("要归档的")
    review = {
        "promote": [{"type": "lesson", "id": staging["id"]}],
        "archive": [{"type": "lesson", "id": to_archive["id"]}],
    }
    result = engram.apply_review(review)
    assert result["promoted"] == 1
    assert result["archived"] == 1
    assert result["errors"] == []


def test_apply_review_string_format(tmp_path: Path):
    """字符串格式的 review 命令应被正确解析。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("待审核字符串", tier="staging")
    review_str = f"promote lesson {lesson['id']}"
    result = engram.apply_review(review_str)
    assert result["promoted"] == 1


def test_apply_review_nonexistent_item(tmp_path: Path):
    """review 中引用不存在的 ID 应记录 error。"""
    engram = make_engram(tmp_path)
    review = {
        "archive": [{"type": "lesson", "id": "fake-id-12345"}],
    }
    result = engram.apply_review(review)
    assert result["archived"] == 0
    assert len(result["errors"]) >= 1


def test_export_import_roundtrip(tmp_path: Path):
    """导出后再导入应保持 profile 数据一致。"""
    engram = make_engram(tmp_path)
    engram.update_profile({
        "role": "data_engineer",
        "language": "中文",
        "technical_level": "mid",
    })
    engram.add_lesson({"summary": "Spark 调优先看 shuffle", "domain": "data"})

    # 导出
    out_dir = tmp_path / "roundtrip"
    export_to_openclaw(engram, str(out_dir))

    # 新实例导入
    engram2 = Engram(root=tmp_path / "engram2")
    import_from_openclaw(
        engram2,
        soul_path=str(out_dir / "SOUL.md"),
        memory_path=str(out_dir / "MEMORY.md"),
        user_path=str(out_dir / "USER.md"),
    )
    p2 = engram2.get_profile()
    assert p2["role"] == "data_engineer"
    assert p2["language"] == "中文"
    lessons2 = engram2.get_lessons(limit=None, _update_access=False)
    assert any("Spark" in l.get("summary", "") for l in lessons2)


# ── _score_item tests ───────────────────────────────────────────────


def test_score_item_empty_terms(tmp_path: Path):
    """空查询词应返回 0 分。"""
    engram = make_engram(tmp_path)
    item = {"summary": "use pytest for testing"}
    assert engram._score_item(item, []) == 0.0


def test_score_item_summary_match_scores_high(tmp_path: Path):
    """summary 字段匹配应获得高权重（weight=3.0）。"""
    engram = make_engram(tmp_path)
    item_match = {"summary": "use python pytest for testing"}
    item_no_match = {"summary": "cook dinner at restaurant"}
    score_match = engram._score_item(item_match, ["python", "pytest"])
    score_no = engram._score_item(item_no_match, ["python", "pytest"])
    assert score_match > score_no
    assert score_match > 0


def test_score_item_detail_match_lower_than_summary(tmp_path: Path):
    """detail 匹配（weight=1.5）应低于 summary 匹配（weight=3.0）。"""
    engram = make_engram(tmp_path)
    item_summary = {"summary": "python testing best practices"}
    item_detail = {"detail": "python testing best practices"}
    s1 = engram._score_item(item_summary, ["python"])
    s2 = engram._score_item(item_detail, ["python"])
    assert s1 > s2


def test_score_item_access_count_bonus(tmp_path: Path):
    """access_count 高的条目应获得额外加分。"""
    engram = make_engram(tmp_path)
    base = {"summary": "python testing"}
    item_low = {**base, "access_count": 0}
    item_high = {**base, "access_count": 50}
    s_low = engram._score_item(item_low, ["python"])
    s_high = engram._score_item(item_high, ["python"])
    assert s_high > s_low
    assert s_high - s_low == pytest.approx(math.log1p(50) * 0.1, abs=0.01)


def test_score_item_multi_term_coverage_bonus(tmp_path: Path):
    """匹配多个查询词应获得 coverage bonus。"""
    engram = make_engram(tmp_path)
    item_both = {"summary": "python pytest integration test"}
    item_one = {"summary": "python only stuff here"}
    s_both = engram._score_item(item_both, ["python", "pytest"])
    s_one = engram._score_item(item_one, ["python", "pytest"])
    assert s_both > s_one


def test_score_item_cjk_query(tmp_path: Path):
    """CJK 查询应能匹配 CJK 内容。"""
    engram = make_engram(tmp_path)
    item = {"summary": "测试框架选择很重要"}
    score = engram._score_item(item, ["测试"])
    assert score > 0


def test_score_item_no_matching_fields(tmp_path: Path):
    """完全无匹配时应返回 0（或接近 0）。"""
    engram = make_engram(tmp_path)
    item = {"summary": "cooking recipes for dinner"}
    score = engram._score_item(item, ["quantum", "physics"])
    assert score == pytest.approx(0.0, abs=0.01)


# ── search_knowledge tests ──────────────────────────────────────────


def test_search_knowledge_ranking(tmp_path: Path):
    """搜索结果应按相关性排序，高匹配在前。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("pytest is the best testing framework for python")
    engram.add_lesson("docker compose simplifies container orchestration")
    engram.add_lesson("python type hints improve code quality")

    results = engram.search_knowledge("python pytest")
    assert len(results["lessons"]) >= 1
    # 包含两个查询词的条目应排第一
    assert "pytest" in results["lessons"][0].get("summary", "").lower()


def test_search_knowledge_cjk(tmp_path: Path):
    """CJK 搜索应能召回中文内容。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("数据库索引优化可以大幅提升查询性能", domain="database")
    engram.add_lesson("python unit test should cover edge cases")

    results = engram.search_knowledge("数据库")
    assert len(results["lessons"]) >= 1
    assert "数据库" in results["lessons"][0].get("summary", "")


def test_search_knowledge_alias_expansion(tmp_path: Path):
    """别名搜索（py → python）应能召回关联内容。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("python virtual environments prevent dependency conflicts")

    results = engram.search_knowledge("py")
    assert len(results["lessons"]) >= 1


def test_search_knowledge_below_threshold(tmp_path: Path):
    """低于 SEARCH_RELEVANCE_THRESHOLD 的结果不应返回。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("cooking pasta requires boiling water")
    results = engram.search_knowledge("quantum computing algorithms")
    assert results["lessons"] == []
    assert results["decisions"] == []


# ── _detect_decision_conflicts tests ────────────────────────────────


def test_detect_decision_conflict_same_topic_different_choice(tmp_path: Path):
    """同一主题不同选择应被检测为冲突。"""
    engram = make_engram(tmp_path)
    decisions = [
        {"question": "which testing framework to use", "choice": "pytest", "domain": "python"},
        {"question": "which testing framework to use", "choice": "unittest", "domain": "python"},
    ]
    conflicts = engram._detect_decision_conflicts(decisions)
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "decision"


def test_detect_decision_conflict_same_choice_no_conflict(tmp_path: Path):
    """同一主题同一选择不应被标记为冲突。"""
    engram = make_engram(tmp_path)
    decisions = [
        {"question": "which testing framework to use", "choice": "pytest is the best choice", "domain": "python"},
        {"question": "which testing framework to use", "choice": "pytest is the best choice for us", "domain": "python"},
    ]
    conflicts = engram._detect_decision_conflicts(decisions)
    assert len(conflicts) == 0


def test_detect_decision_conflict_different_domains_skipped(tmp_path: Path):
    """不同 domain 的决策不应被检测为冲突。"""
    engram = make_engram(tmp_path)
    decisions = [
        {"question": "which framework to use", "choice": "React", "domain": "frontend"},
        {"question": "which framework to use", "choice": "Django", "domain": "backend"},
    ]
    conflicts = engram._detect_decision_conflicts(decisions)
    assert len(conflicts) == 0


def test_detect_decision_conflict_overlapping_domains(tmp_path: Path):
    """domain 有交集时仍应检测冲突。"""
    engram = make_engram(tmp_path)
    decisions = [
        {"question": "which test runner to use", "choice": "pytest", "domain": "python,testing"},
        {"question": "which test runner to use", "choice": "unittest", "domain": "testing,ci"},
    ]
    conflicts = engram._detect_decision_conflicts(decisions)
    assert len(conflicts) == 1


# ── _detect_lesson_conflicts tests ──────────────────────────────────


def test_detect_lesson_conflict_negation_vs_affirmation(tmp_path: Path):
    """一条否定一条肯定同一话题应被检测为冲突。"""
    engram = make_engram(tmp_path)
    lessons = [
        {"summary": "should always use type hints in python code"},
        {"summary": "don't use type hints in python, they slow you down"},
    ]
    conflicts = engram._detect_lesson_conflicts(lessons)
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "lesson"


def test_detect_lesson_conflict_no_sentiment_asymmetry(tmp_path: Path):
    """两条都是肯定语气不应被标记为冲突。"""
    engram = make_engram(tmp_path)
    lessons = [
        {"summary": "always write tests before implementation"},
        {"summary": "should write documentation alongside code"},
    ]
    conflicts = engram._detect_lesson_conflicts(lessons)
    assert len(conflicts) == 0


def test_detect_lesson_conflict_different_domains(tmp_path: Path):
    """不同 domain 的经验不应被检测为冲突。"""
    engram = make_engram(tmp_path)
    lessons = [
        {"summary": "avoid using global state in python", "domain": "python"},
        {"summary": "always use global state in javascript", "domain": "javascript"},
    ]
    conflicts = engram._detect_lesson_conflicts(lessons)
    assert len(conflicts) == 0


def test_detect_lesson_conflict_cjk_markers(tmp_path: Path):
    """中文否定/肯定标记也应被检测。"""
    engram = make_engram(tmp_path)
    lessons = [
        {"summary": "推荐使用 pytest 作为测试框架"},
        {"summary": "不推荐使用 pytest 因为配置复杂"},
    ]
    conflicts = engram._detect_lesson_conflicts(lessons)
    assert len(conflicts) == 1


# ── generate_context tests ──────────────────────────────────────────


def test_generate_context_empty_profile_warning(tmp_path: Path):
    """空 profile 应输出设置提示。"""
    engram = make_engram(tmp_path)
    ctx = engram.generate_context()
    assert "身份画像未设置" in ctx


def test_generate_context_includes_profile(tmp_path: Path):
    """有 profile 时应包含角色和语言。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "全栈开发者", "language": "中文", "technical_level": "senior"})
    ctx = engram.generate_context()
    assert "全栈开发者" in ctx
    assert "中文" in ctx
    assert "senior" in ctx


def test_generate_context_includes_lessons(tmp_path: Path):
    """有 lesson 时应包含在上下文中。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("always run tests before committing code")
    ctx = engram.generate_context()
    assert "always run tests" in ctx


def test_generate_context_includes_decisions(tmp_path: Path):
    """有 decision 时应包含在上下文中。"""
    engram = make_engram(tmp_path)
    engram.add_decision({"question": "testing framework", "choice": "pytest"})
    ctx = engram.generate_context()
    assert "pytest" in ctx


def test_generate_context_token_budget_drops_low_priority(tmp_path: Path):
    """token 预算不足时应按优先级丢弃低优先级 section。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "developer", "language": "en"})
    # 添加足够多的 lesson 和 decision 使总 token 数较高
    for i in range(8):
        engram.add_lesson(f"lesson number {i} about important python testing practices and patterns")
    for i in range(6):
        engram.add_decision({"question": f"decision {i} about architecture", "choice": f"choice {i}"})

    # 很小的预算：只应包含最高优先级的 section（profile）
    ctx_small = engram.generate_context(max_tokens=30)
    ctx_full = engram.generate_context(max_tokens=None)
    assert len(ctx_small) < len(ctx_full)
    # Profile（priority=1）应在小预算中存活
    assert "关于用户" in ctx_small


def test_generate_context_no_budget_includes_all(tmp_path: Path):
    """max_tokens=None 时应包含所有 section。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "dev"})
    engram.add_lesson("test lesson for context")
    engram.add_decision({"question": "test q", "choice": "test c"})
    ctx = engram.generate_context(max_tokens=None)
    assert "关于用户" in ctx
    assert "test lesson" in ctx
    assert "test c" in ctx


def test_generate_context_conflict_section(tmp_path: Path):
    """有冲突决策时应出现冲突提醒 section。"""
    engram = make_engram(tmp_path)
    # 直接写入两条冲突决策（绕过 add_decision 的去重）
    path = tmp_path / "knowledge" / "decisions.json"
    decisions = [
        {
            "id": "d1", "question": "which testing framework should we adopt",
            "choice": "pytest", "domain": "python", "status": "active",
            "tier": "verified", "timestamp": "2026-01-01T00:00:00",
        },
        {
            "id": "d2", "question": "which testing framework should we adopt",
            "choice": "unittest", "domain": "python", "status": "active",
            "tier": "verified", "timestamp": "2026-01-02T00:00:00",
        },
    ]
    path.write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
    ctx = engram.generate_context()
    assert "冲突" in ctx


def test_estimate_tokens_cjk_vs_ascii(tmp_path: Path):
    """CJK 字符每个算 1 token，ASCII 每 4 个算 1 token。"""
    assert Engram._estimate_tokens("你好世界") == 4  # 4 CJK chars
    assert Engram._estimate_tokens("hello world") == 2  # 11 chars / 4 ≈ 2
    assert Engram._estimate_tokens("你好 world") == 2 + 1  # 2 CJK + 6 ASCII/4


# ── ingest_notes tests ──────────────────────────────────────────────


def test_ingest_notes_decision_trigger(tmp_path: Path):
    """包含决策触发词的行应被分类为 decision。"""
    engram = make_engram(tmp_path)
    result = engram.ingest_notes("decided to use pytest for all integration tests")
    assert result["saved_decisions"] == 1
    assert result["saved_lessons"] == 0


def test_ingest_notes_lesson_trigger(tmp_path: Path):
    """包含经验触发词的行应被分类为 lesson。"""
    engram = make_engram(tmp_path)
    result = engram.ingest_notes("discovered that connection pooling reduces latency by 40%")
    assert result["saved_lessons"] == 1
    assert result["saved_decisions"] == 0


def test_ingest_notes_skips_short_lines(tmp_path: Path):
    """短于 5 字符的行应被跳过。"""
    engram = make_engram(tmp_path)
    result = engram.ingest_notes("hi\nok\nyes\n")
    assert result["skipped"] == 3
    assert result["saved_lessons"] == 0


def test_ingest_notes_skips_headers(tmp_path: Path):
    """以 # 开头的行应被跳过。"""
    engram = make_engram(tmp_path)
    result = engram.ingest_notes("# Section Title\nlearned that caching matters a lot")
    assert result["saved_lessons"] == 1
    assert result["skipped"] == 0  # headers aren't counted as skipped


def test_ingest_notes_medium_line_without_trigger_skipped(tmp_path: Path):
    """6-15 字符且无触发词的行应被跳过。"""
    engram = make_engram(tmp_path)
    result = engram.ingest_notes("some text ok")
    assert result["skipped"] == 1


def test_ingest_notes_deduplicates(tmp_path: Path):
    """重复内容应被检测为 duplicate。"""
    engram = make_engram(tmp_path)
    engram.ingest_notes("discovered that connection pooling reduces latency significantly")
    result2 = engram.ingest_notes("discovered that connection pooling reduces latency significantly")
    assert result2["duplicates"] == 1


def test_ingest_notes_domain_inference(tmp_path: Path):
    """含有 domain 关键词的行应自动推断 domain。"""
    engram = make_engram(tmp_path)
    result = engram.ingest_notes("learned that pytest fixtures simplify test setup")
    lessons = engram.get_lessons(limit=None, _update_access=False)
    assert any("python" in l.get("domain", "") for l in lessons)


def test_ingest_notes_cjk_triggers(tmp_path: Path):
    """中文触发词也应被正确识别。"""
    engram = make_engram(tmp_path)
    result = engram.ingest_notes("决定使用 FastAPI 作为后端框架\n发现缓存可以大幅提升性能")
    assert result["saved_decisions"] == 1
    assert result["saved_lessons"] == 1


# ── _infer_domain tests ─────────────────────────────────────────────


def test_infer_domain_python(tmp_path: Path):
    """含 python 关键词应推断为 python domain。"""
    engram = make_engram(tmp_path)
    assert "python" in engram._infer_domain("use pytest for testing python code")


def test_infer_domain_multiple(tmp_path: Path):
    """同时含多个 domain 关键词应返回逗号分隔的多个 domain。"""
    engram = make_engram(tmp_path)
    result = engram._infer_domain("deploy python app with docker compose")
    assert "python" in result
    assert "docker" in result


def test_infer_domain_fallback(tmp_path: Path):
    """无法推断时应使用 fallback。"""
    engram = make_engram(tmp_path)
    assert engram._infer_domain("something generic here", fallback="general") == "general"


def test_infer_domain_no_match_no_fallback(tmp_path: Path):
    """无法推断且无 fallback 时应返回空字符串。"""
    engram = make_engram(tmp_path)
    assert engram._infer_domain("nothing relevant here") == ""


# ── _bigram_similarity tests ────────────────────────────────────────


def test_bigram_similarity_identical(tmp_path: Path):
    """相同文本应返回 1.0。"""
    engram = make_engram(tmp_path)
    assert engram._bigram_similarity("hello world", "hello world") == pytest.approx(1.0)


def test_bigram_similarity_empty(tmp_path: Path):
    """空字符串应返回 0.0。"""
    engram = make_engram(tmp_path)
    assert engram._bigram_similarity("", "hello") == 0.0
    assert engram._bigram_similarity("hello", "") == 0.0
    assert engram._bigram_similarity("", "") == 0.0


def test_bigram_similarity_partially_similar(tmp_path: Path):
    """部分相似的文本应返回 0 < score < 1。"""
    engram = make_engram(tmp_path)
    score = engram._bigram_similarity("python testing", "python cooking")
    assert 0.0 < score < 1.0


def test_bigram_similarity_completely_different(tmp_path: Path):
    """完全不同的文本应返回 0 或接近 0。"""
    engram = make_engram(tmp_path)
    score = engram._bigram_similarity("quantum physics", "cooking recipes")
    assert score < 0.1


# ── evaluate_tiers tests ────────────────────────────────────────────


def test_evaluate_tiers_promotes_accessed_staging(tmp_path: Path):
    """access_count >= 3 的 staging 条目应被提升为 verified。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("test staging promotion")
    # 手动设为 staging 并增加 access_count
    path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(path.read_text(encoding="utf-8"))
    lessons[-1]["tier"] = "staging"
    lessons[-1]["access_count"] = 5
    path.write_text(json.dumps(lessons, ensure_ascii=False), encoding="utf-8")

    result = engram.evaluate_tiers()
    assert result["promoted"] == 1

    lessons_after = json.loads(path.read_text(encoding="utf-8"))
    assert lessons_after[-1]["tier"] == "verified"
    assert "promoted_at" in lessons_after[-1]


def test_evaluate_tiers_no_promote_low_access(tmp_path: Path):
    """access_count < 3 的 staging 条目不应被提升。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("low access staging item")
    path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(path.read_text(encoding="utf-8"))
    lessons[-1]["tier"] = "staging"
    lessons[-1]["access_count"] = 1
    path.write_text(json.dumps(lessons, ensure_ascii=False), encoding="utf-8")

    result = engram.evaluate_tiers()
    assert result["promoted"] == 0


# ── Knowledge eviction tests ───────────────────────────────────────


def test_lesson_eviction_staging_first(tmp_path: Path):
    """超出 MAX_KNOWLEDGE_ENTRIES 时应优先驱逐 staging 条目。"""
    engram = make_engram(tmp_path)
    path = tmp_path / "knowledge" / "lessons.json"

    # 填充到接近上限
    from engram_core.core import MAX_KNOWLEDGE_ENTRIES
    lessons = []
    for i in range(MAX_KNOWLEDGE_ENTRIES - 1):
        lessons.append({
            "id": f"v-{i}", "summary": f"verified lesson {i}",
            "tier": "verified", "status": "active",
            "timestamp": "2026-01-01T00:00:00",
        })
    # 加一个 staging
    lessons.append({
        "id": "s-0", "summary": "staging lesson to evict",
        "tier": "staging", "status": "active",
        "timestamp": "2026-01-01T00:00:00",
    })
    path.write_text(json.dumps(lessons, ensure_ascii=False), encoding="utf-8")

    # 再添加一个新的，触发驱逐
    engram.add_lesson("new lesson that triggers eviction")
    final = json.loads(path.read_text(encoding="utf-8"))
    assert len(final) <= MAX_KNOWLEDGE_ENTRIES
    # staging 应该被驱逐
    assert not any(l.get("id") == "s-0" for l in final)
