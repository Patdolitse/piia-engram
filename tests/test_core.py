"""Engram 核心功能基础测试。"""

import json
import math
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from piia_engram.core import (
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


# ── health score tests ──


def test_health_score_empty_knowledge(tmp_path: Path):
    """空知识库的健康评分应有合理默认值。"""
    engram = make_engram(tmp_path)
    report = engram.get_health_report()

    assert "health_score" in report
    assert "dimensions" in report
    score = report["health_score"]
    dims = report["dimensions"]
    assert isinstance(score, int)
    assert 0 <= score <= 100
    for dim in ("freshness", "quality", "coverage", "cleanliness"):
        assert dim in dims
        assert 0 <= dims[dim] <= 100


def test_health_score_with_data(tmp_path: Path):
    """有数据时健康评分维度应反映实际状态。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("经验1", "python", source_tool="claude_code")
    engram.add_lesson("经验2", "git", source_tool="codex")
    engram.add_lesson("经验3", "testing", source_tool="claude_code")
    engram.add_decision("架构选型", "FastAPI", "性能好", source_tool="claude_code")

    report = engram.get_health_report()

    assert report["health_score"] > 0
    dims = report["dimensions"]
    # All fresh → freshness should be high
    assert dims["freshness"] >= 80
    # All active, no staging → quality should be high
    assert dims["quality"] >= 80
    # 3 domains → coverage should reflect diversity
    assert dims["coverage"] > 30
    # No duplicates → cleanliness should be high
    assert dims["cleanliness"] >= 80


def test_health_score_stale_items_reduce_freshness(tmp_path: Path):
    """过期知识应降低 freshness 维度。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("老经验", "python")
    # Mark as stale
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    lessons[0]["last_reviewed"] = (datetime.now() - timedelta(days=60)).isoformat()
    engram._atomic_write(lessons_path, lessons)

    report = engram.get_health_report()
    dims = report["dimensions"]
    assert dims["freshness"] == 0  # 100% stale


def test_health_score_duplicates_reduce_cleanliness(tmp_path: Path):
    """近似重复条目应降低 cleanliness 维度。"""
    engram = make_engram(tmp_path)
    # add_lesson deduplicates on ingestion, so write directly to bypass
    base = "使用 pytest 进行 Python 单元测试覆盖率检查是最佳实践"
    engram.add_lesson({"summary": base})
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    dup = dict(lessons[0])
    dup["id"] = "dup-manual-id"
    dup["summary"] = base + "之一"
    lessons.append(dup)
    engram._atomic_write(lessons_path, lessons)

    report = engram.get_health_report()
    assert len(report["potential_duplicates"]) >= 1
    dims = report["dimensions"]
    assert dims["cleanliness"] < 100


# ── suggest_merges tests ──


def test_suggest_merges_empty(tmp_path: Path):
    """空知识库应返回 0 条建议。"""
    engram = make_engram(tmp_path)
    result = engram.suggest_merges()
    assert result["total_candidates"] == 0
    assert result["suggestions"] == []


def test_suggest_merges_finds_duplicates(tmp_path: Path):
    """近似重复条目应被建议合并。"""
    engram = make_engram(tmp_path)
    base = "Python 项目必须使用 pytest 框架进行完整的单元测试覆盖"
    r1 = engram.add_lesson({"summary": base})
    # Bypass ingestion dedup by writing directly
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    dup = dict(lessons[0])
    dup["id"] = "dup-suggest-test"
    dup["summary"] = base.replace("单元测试", "测试")
    lessons.append(dup)
    engram._atomic_write(lessons_path, lessons)

    result = engram.suggest_merges(threshold=0.4)
    assert result["total_candidates"] >= 1
    suggestion = result["suggestions"][0]
    assert "primary_id" in suggestion
    assert "secondary_id" in suggestion
    assert "similarity" in suggestion
    assert suggestion["similarity"] >= 0.4
    assert "action" in suggestion
    assert "merge_knowledge" in suggestion["action"]


def test_suggest_merges_respects_threshold(tmp_path: Path):
    """高阈值应过滤掉低相似度对。"""
    engram = make_engram(tmp_path)
    engram.add_lesson({"summary": "Python 项目应该使用虚拟环境管理依赖"})
    engram.add_lesson({"summary": "JavaScript 项目应该使用 package.json 管理依赖"})

    high = engram.suggest_merges(threshold=0.9)
    assert high["total_candidates"] == 0


def test_suggest_merges_recommends_higher_access_as_primary(tmp_path: Path):
    """访问量更高的条目应被推荐为主条目。"""
    engram = make_engram(tmp_path)
    base = "Git commit 消息必须遵循 Conventional Commits 规范格式"
    r1 = engram.add_lesson({"summary": base})
    # Bypass dedup, add similar lesson directly
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    dup = dict(lessons[0])
    dup["id"] = "high-access-lesson"
    dup["summary"] = base.replace("规范格式", "的格式规范")
    dup["access_count"] = 10
    lessons.append(dup)
    engram._atomic_write(lessons_path, lessons)

    result = engram.suggest_merges(threshold=0.4)
    assert result["total_candidates"] >= 1
    suggestion = result["suggestions"][0]
    assert suggestion["primary_id"] == "high-access-lesson"


def test_suggest_merges_limit(tmp_path: Path):
    """limit 参数应限制返回数量。"""
    engram = make_engram(tmp_path)
    base = "数据库连接池配置最佳实践版本非常重要的经验总结"
    engram.add_lesson({"summary": base})
    # Bypass dedup, add variants directly
    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = json.loads(lessons_path.read_text(encoding="utf-8"))
    for i in range(4):
        dup = dict(lessons[0])
        dup["id"] = f"dup-limit-{i}"
        dup["summary"] = f"{base}第{i}版"
        lessons.append(dup)
    engram._atomic_write(lessons_path, lessons)

    result = engram.suggest_merges(threshold=0.3, limit=2)
    assert len(result["suggestions"]) <= 2


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
    from piia_engram.mcp_server import _parse_args

    args = _parse_args(["mcp_server.py"])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8767


def test_parse_args_sse_mode():
    """SSE 模式参数应正确解析。"""
    from piia_engram.mcp_server import _parse_args

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

    from piia_engram.mcp_server import TokenAuthMiddleware

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

    from piia_engram.mcp_server import TokenAuthMiddleware

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


def test_identity_card_all_sections(tmp_path: Path):
    """export_identity_card 应覆盖所有可选 section：work_style, quality, domains, decisions。"""
    engram = make_engram(tmp_path)
    engram.update_profile({
        "role": "全栈工程师",
        "description": "10年经验",
        "language": "中文",
        "technical_level": "senior",
    })
    engram.update_work_style({
        "preferences": {"editor": "vim", "testing": "pytest"},
        "communication": "简洁直接",
    })
    engram.update_quality_standards({"rules": ["必须有测试", "代码评审"]})
    engram.add_lesson({"summary": "不要硬编码", "domain": "python"})
    engram.add_lesson({"summary": "监控先行", "domain": "devops"})
    engram.add_decision({
        "question": "用什么框架",
        "choice": "FastAPI",
    })
    # decision with only question
    engram.add_decision({"question": "数据库选型"})
    # decision with only choice
    engram.add_decision({"choice": "PostgreSQL"})

    card = engram.export_identity_card()

    # profile fields
    assert "全栈工程师" in card
    assert "10年经验" in card
    assert "中文" in card
    assert "senior" in card

    # work_style
    assert "我的工作方式" in card
    assert "vim" in card
    assert "简洁直接" in card

    # quality
    assert "我的质量标准" in card
    assert "必须有测试" in card

    # domains
    assert "我的经验" in card

    # decisions — all 3 patterns (question+choice, question-only, choice-only)
    assert "用什么框架" in card
    assert "FastAPI" in card
    assert "数据库选型" in card
    assert "PostgreSQL" in card

    # lessons
    assert "不要硬编码" in card
    assert "监控先行" in card

    # export file saved
    assert (tmp_path / "exports" / "identity_card.md").is_file()


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
    from piia_engram.core import MAX_KNOWLEDGE_ENTRIES
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


# ── context.py coverage: generate_context sections ─────────────────


def test_generate_context_preferences_section(tmp_path: Path):
    """generate_context should include work preferences when set."""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "dev", "language": "中文"})
    engram.update_preferences({
        "work_patterns": {"decision_style": "data-driven", "review_depth": "thorough"},
        "communication": "简洁直接",
        "tool_preferences": {"editor": "VS Code", "terminal": "iTerm2"},
    })
    ctx = engram.generate_context()
    assert "decision_style" in ctx
    assert "简洁直接" in ctx
    assert "VS Code" in ctx


def test_generate_context_quality_section(tmp_path: Path):
    """generate_context should include quality standards."""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "dev"})
    engram.update_quality_standards({
        "acceptance_threshold": 4,
        "rules": ["所有代码必须有测试", "不允许 TODO 留在 main 分支"],
    })
    ctx = engram.generate_context()
    assert "4" in ctx
    assert "所有代码必须有测试" in ctx


def test_generate_context_project_section(tmp_path: Path):
    """generate_context with project_folder should include project history."""
    engram = make_engram(tmp_path)
    engram.save_project_snapshot("E:/test-project", {
        "title": "Test Project",
        "tech_stack": ["Python", "FastAPI"],
        "session_count": 10,
        "known_issues": ["性能问题", "文档缺失"],
    })
    ctx = engram.generate_context(project_folder="E:/test-project")
    assert "Test Project" in ctx
    assert "10" in ctx
    assert "Python" in ctx
    assert "性能问题" in ctx


def test_generate_context_decisions_partial_fields(tmp_path: Path):
    """Decisions with missing question or choice should still render."""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "dev"})
    # decision with only question
    engram.add_decision({"question": "用什么框架", "choice": ""})
    # decision with only choice
    engram.add_decision({"title": "", "question": "", "choice": "FastAPI"})
    ctx = engram.generate_context()
    assert "用什么框架" in ctx or "FastAPI" in ctx


def test_generate_context_empty_returns_empty_string(tmp_path: Path):
    """generate_context with absolutely no data should return empty."""
    engram = Engram(tmp_path)
    # Patch reconcile to do nothing
    engram.reconcile_memories = lambda: {"imported": 0, "sources": []}
    engram.reconcile_ai_configs = lambda: {"imported": 0, "sources": [], "scanned_files": 0}
    # Remove all data files
    for f in tmp_path.glob("**/*.json"):
        f.unlink()
    # Empty profile, no lessons, no decisions
    ctx = engram.generate_context()
    # Should still have at least profile warning
    assert "身份画像未设置" in ctx


def test_generate_context_reconcile_failure_graceful(tmp_path: Path):
    """generate_context should not crash if reconcile raises."""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "dev"})

    def boom():
        raise RuntimeError("reconcile boom")

    engram.reconcile_memories = boom
    engram.reconcile_ai_configs = boom
    # Should not raise
    ctx = engram.generate_context()
    assert "关于用户" in ctx


# ── context.py coverage: extract_knowledge with mock LLM ──────────


def test_extract_knowledge_with_mock_provider():
    """extract_knowledge should parse LLM JSON response."""
    from piia_engram.context import extract_knowledge

    class MockProvider:
        def chat(self, messages, project_folder):
            return json.dumps({
                "profile_updates": {"language": "中文", "technical_level": "高级"},
                "lessons": [{"summary": "测试很重要", "domain": "testing"}],
                "decisions": [{"question": "用什么数据库", "choice": "PostgreSQL", "reasoning": "稳定"}],
                "domains_used": ["python", "database"],
                "project_info": {"title": "Test", "tech_stack": ["Python"]},
            })

    conversation = [
        {"role": "user", "content": "帮我写个测试"},
        {"role": "assistant", "content": "好的，我来写测试"},
    ]
    result = extract_knowledge(conversation, "E:/test", "main.py", provider=MockProvider())
    assert result is not None
    assert result["profile_updates"]["language"] == "中文"
    assert len(result["lessons"]) == 1
    assert len(result["decisions"]) == 1


def test_extract_knowledge_handles_bad_json():
    """extract_knowledge should return None on invalid LLM response."""
    from piia_engram.context import extract_knowledge

    class BadProvider:
        def chat(self, messages, project_folder):
            return "This is not JSON at all, just text"

    result = extract_knowledge(
        [{"role": "user", "content": "hello"}], "E:/test", "main.py", provider=BadProvider()
    )
    assert result is None


def test_extract_knowledge_handles_exception():
    """extract_knowledge should return None on LLM exception."""
    from piia_engram.context import extract_knowledge

    class CrashProvider:
        def chat(self, messages, project_folder):
            raise ConnectionError("API down")

    result = extract_knowledge(
        [{"role": "user", "content": "hello"}], "E:/test", "main.py", provider=CrashProvider()
    )
    assert result is None


# ── context.py coverage: ingest_extraction branches ────────────────


def test_ingest_extraction_work_style(tmp_path: Path):
    """ingest_extraction should apply work_style_updates."""
    from piia_engram.context import ingest_extraction

    engram = make_engram(tmp_path)
    extracted = {
        "work_style_updates": {
            "preferences": {"review_depth": "deep"},
            "communication": "简洁",
        }
    }
    result = ingest_extraction(engram, extracted, "E:/test")
    assert result["items_learned"] >= 1
    style = engram.get_work_style()
    assert style.get("communication") == "简洁"


def test_ingest_extraction_quality_standards(tmp_path: Path):
    """ingest_extraction should apply quality_updates with rule dedup."""
    from piia_engram.context import ingest_extraction

    engram = make_engram(tmp_path)
    # Add existing rule
    engram.update_quality_standards({"rules": ["existing rule"]})
    extracted = {
        "quality_updates": {
            "acceptance_threshold": 4,
            "rules": ["existing rule", "new rule"],
        }
    }
    result = ingest_extraction(engram, extracted, "E:/test")
    assert result["items_learned"] >= 1
    standards = engram.get_quality_standards()
    assert standards["acceptance_threshold"] == 4
    assert "new rule" in standards["rules"]
    # existing rule not duplicated
    assert standards["rules"].count("existing rule") == 1


def test_ingest_extraction_domains_and_project(tmp_path: Path):
    """ingest_extraction should increment domains and save project snapshot."""
    from piia_engram.context import ingest_extraction
    from piia_engram.core import _read_json

    engram = Engram(root=tmp_path)
    extracted = {
        "domains_used": ["python", "testing"],
        "project_info": {
            "title": "My Project",
            "tech_stack": ["Python", "React"],
        },
    }
    result = ingest_extraction(engram, extracted, "E:/test-project")
    # domains.json should have incremented counts
    domains_raw = _read_json(tmp_path / "knowledge" / "domains.json")
    assert "python" in domains_raw
    assert domains_raw["python"]["project_count"] == 1
    # Project snapshot should be saved
    proj = engram.get_project_snapshot("E:/test-project")
    assert proj["title"] == "My Project"
    assert proj["session_count"] == 1  # first session


def test_ingest_extraction_quality_bad_threshold(tmp_path: Path):
    """ingest_extraction should handle non-numeric acceptance_threshold."""
    from piia_engram.context import ingest_extraction

    engram = make_engram(tmp_path)
    extracted = {
        "quality_updates": {
            "acceptance_threshold": "not a number",
            "rules": ["rule one"],
        }
    }
    result = ingest_extraction(engram, extracted, "E:/test")
    # Should still save rules even if threshold is bad
    standards = engram.get_quality_standards()
    assert "rule one" in standards.get("rules", [])


# ── context.py coverage: ingest_notes duplicate decision ──────────


def test_ingest_notes_duplicate_decision(tmp_path: Path):
    """Ingesting the same decision text twice should report duplicate."""
    engram = make_engram(tmp_path)
    text = "决定使用 PostgreSQL 作为主数据库"
    engram.ingest_notes(text)
    result = engram.ingest_notes(text)
    assert result["duplicates"] >= 1


# ── context.py coverage: extract_session_insights branches ────────


def test_extract_session_insights_decision_duplicate(tmp_path: Path):
    """Duplicate decisions from session insights should be reported."""
    engram = make_engram(tmp_path)
    text = "最终决定使用 Redis 做缓存层"
    engram.extract_session_insights(text)
    result = engram.extract_session_insights(text)
    assert result["duplicates"] >= 1


def test_extract_session_insights_no_content_chars(tmp_path: Path):
    """Sentences without content chars should be skipped."""
    engram = make_engram(tmp_path)
    result = engram.extract_session_insights("---------- .......... $$$$$$$$$$")
    assert result["saved_lessons"] == 0
    assert result["saved_decisions"] == 0


# ── CJK search quality regression tests ───────────────────────────


def test_cjk_search_finds_chinese_lessons(tmp_path: Path):
    """中文查询应能找到中文 lesson。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("测试框架的选择要考虑团队熟悉度", domain="testing")
    engram.add_lesson("部署流程自动化减少人为错误", domain="devops")
    engram.add_lesson("代码审查重点关注逻辑正确性而非格式", domain="python")
    results = engram.search_knowledge("测试框架")
    lessons = results.get("lessons", [])
    assert any("测试框架" in l.get("summary", "") for l in lessons)


def test_cjk_search_no_false_positives(tmp_path: Path):
    """不相关的中文 lesson 不应被中文查询找到。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("Python 列表推导式是强大的特性", domain="python")
    engram.add_lesson("今天天气真好适合出去散步", domain="general")
    results = engram.search_knowledge("Python 列表")
    lessons = results.get("lessons", [])
    noise = [l for l in lessons if "天气" in l.get("summary", "")]
    assert len(noise) == 0


def test_cjk_bigram_exact_match_ranks_higher(tmp_path: Path):
    """精确匹配的中文内容应排名更高。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("pytest 覆盖率报告生成方法", domain="python")
    engram.add_lesson("pytest 速度优化技巧总结", domain="python")
    results = engram.search_knowledge("pytest 覆盖率")
    lessons = results.get("lessons", [])
    assert lessons, "should find at least one result"
    assert "覆盖率" in lessons[0].get("summary", "")


def test_cjk_alias_cross_language(tmp_path: Path):
    """中英别名应互通：查 'tool' 能找到含 '工具' 的 lesson。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("选择合适的工具是提高效率的关键因素", domain="general")
    results = engram.search_knowledge("tool")
    lessons = results.get("lessons", [])
    assert any("工具" in l.get("summary", "") for l in lessons)


def test_cjk_mixed_query(tmp_path: Path):
    """中英混合查询应正常工作。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("Docker 容器化部署简单高效", domain="devops")
    engram.add_lesson("Redis 缓存策略选择指南", domain="database")
    results = engram.search_knowledge("Docker 部署")
    lessons = results.get("lessons", [])
    assert any("Docker" in l.get("summary", "") for l in lessons)


# ── core.py 覆盖率补充测试 ──────────────────────────────────────────


def test_parse_schema_version_invalid(tmp_path: Path):
    """_parse_schema_version 应对无效输入返回 (0, 0)。"""
    from piia_engram.core import Engram
    assert Engram._parse_schema_version("abc") == (0, 0)
    assert Engram._parse_schema_version(None) == (0, 0)


def test_migrate_v1_to_v2(tmp_path: Path):
    """_migrate_v1_to_v2 应将 work_style 迁移为 preferences 并创建 trust_boundaries。"""
    engram = make_engram(tmp_path)

    # Set schema to v1.0
    schema_path = tmp_path / "schema_version.json"
    schema_path.write_text('{"schema_version": "1.0"}', encoding="utf-8")

    # Create old-style work_style.json
    identity_dir = tmp_path / "identity"
    identity_dir.mkdir(exist_ok=True)
    work_style_path = identity_dir / "work_style.json"
    work_style_path.write_text(json.dumps({
        "preferences": {"editor": "vim"},
        "communication": "简洁直接",
    }), encoding="utf-8")

    # Remove trust_boundaries if it exists
    tb_path = identity_dir / "trust_boundaries.json"
    if tb_path.exists():
        tb_path.unlink()

    # Remove preferences.json if it exists
    prefs_path = identity_dir / "preferences.json"
    if prefs_path.exists():
        prefs_path.unlink()

    engram._migrate_v1_to_v2()

    # preferences.json created
    assert prefs_path.is_file()
    prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert prefs["work_patterns"] == {"editor": "vim"}
    assert prefs["communication"] == "简洁直接"
    assert prefs.get("migrated_from") == "work_style.json"

    # trust_boundaries.json created
    assert tb_path.is_file()

    # schema version bumped
    ver = json.loads(schema_path.read_text(encoding="utf-8"))
    assert ver["schema_version"] == "2.0"


def test_migrate_v1_to_v2_skips_if_already_v2(tmp_path: Path):
    """已是 v2.0 时不应重复迁移。"""
    engram = make_engram(tmp_path)
    schema_path = tmp_path / "schema_version.json"
    schema_path.write_text('{"schema_version": "2.0"}', encoding="utf-8")

    engram._migrate_v1_to_v2()

    # Should be a no-op, schema still v2.0
    ver = json.loads(schema_path.read_text(encoding="utf-8"))
    assert ver["schema_version"] == "2.0"


def test_update_profile_all_rejected(tmp_path: Path):
    """全部字段被拒绝时 update_profile 应直接返回。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"evil_field": "bad", "injection": "data"})
    profile = engram.get_profile()
    assert "evil_field" not in profile


def test_update_preferences_all_rejected(tmp_path: Path):
    """全部字段被拒绝时 update_preferences 应直接返回。"""
    engram = make_engram(tmp_path)
    engram.update_preferences({"unknown_pref": "bad"})
    prefs = engram.get_preferences()
    assert "unknown_pref" not in prefs


def test_update_trust_boundaries_all_rejected(tmp_path: Path):
    """全部字段被拒绝时 update_trust_boundaries 应直接返回。"""
    engram = make_engram(tmp_path)
    engram.update_trust_boundaries({"evil": "field"})
    tb = engram.get_trust_boundaries()
    assert "evil" not in tb


def test_update_quality_standards_all_rejected(tmp_path: Path):
    """全部字段被拒绝时 update_quality_standards 应直接返回。"""
    engram = make_engram(tmp_path)
    engram.update_quality_standards({"unknown_rule": "nope"})
    qs = engram.get_quality_standards()
    assert "unknown_rule" not in qs


def test_get_preferences_falls_back_to_work_style(tmp_path: Path):
    """无 preferences.json 时应回退到 work_style.json。"""
    engram = make_engram(tmp_path)
    # Ensure no preferences.json
    prefs_path = tmp_path / "identity" / "preferences.json"
    if prefs_path.exists():
        prefs_path.unlink()

    engram.update_work_style({
        "preferences": {"theme": "dark"},
        "communication": "直接",
    })
    prefs = engram.get_preferences()
    assert prefs["work_patterns"] == {"theme": "dark"}
    assert prefs["communication"] == "直接"
    assert prefs["tool_preferences"] == {}


def test_sanitize_project_with_path(tmp_path: Path):
    """_sanitize_project 应从路径中提取项目名。"""
    from piia_engram.core import Engram
    assert Engram._sanitize_project("/home/user/projects/my-app") == "my-app"
    assert Engram._sanitize_project("C:\\Users\\dev\\project") == "project"
    assert Engram._sanitize_project("") == ""
    assert Engram._sanitize_project("simple-name") == "simple-name"


def test_ensure_fields_non_dict(tmp_path: Path):
    """_ensure_fields 应将非 dict 输入转为 dict。"""
    engram = make_engram(tmp_path)
    result = engram._ensure_fields("not a dict", "lesson")
    assert isinstance(result, dict)
    assert "id" in result
    assert "timestamp" in result


def test_add_lesson_with_source_url(tmp_path: Path):
    """add_lesson 应存储 source_url 字段。"""
    engram = make_engram(tmp_path)
    result = engram.add_lesson(
        "来源测试",
        domain="test",
        source_url="https://example.com/article",
    )
    assert result.get("source_url") == "https://example.com/article"


def test_add_decision_with_alternatives(tmp_path: Path):
    """add_decision 应存储 alternatives 列表。"""
    engram = make_engram(tmp_path)
    result = engram.add_decision(
        "选择框架",
        choice="FastAPI",
        alternatives=["Flask", "Django"],
    )
    assert result.get("alternatives") == ["Flask", "Django"]


def test_lesson_eviction_overflow(tmp_path: Path):
    """超过 MAX_KNOWLEDGE_ENTRIES 时应驱逐 staging 条目优先。"""
    from piia_engram.storage import MAX_KNOWLEDGE_ENTRIES, _read_json

    engram = make_engram(tmp_path)
    path = tmp_path / "knowledge" / "lessons.json"

    # Pre-fill with MAX entries (mix of staging and verified)
    entries = []
    for i in range(MAX_KNOWLEDGE_ENTRIES):
        tier = "staging" if i < 5 else "verified"
        entries.append({
            "id": f"lesson-{i:04d}",
            "summary": f"lesson {i} unique content for eviction test",
            "domain": "test",
            "status": "active",
            "tier": tier,
            "timestamp": f"2026-01-{(i % 28) + 1:02d}T00:00:00",
        })
    from piia_engram.storage import _write_json
    _write_json(path, entries)

    # Add one more → triggers eviction
    result = engram.add_lesson("溢出测试条目", domain="overflow")
    assert "status" not in result or result.get("status") != "duplicate"

    lessons = _read_json(path)
    assert len(lessons) <= MAX_KNOWLEDGE_ENTRIES


def test_decision_eviction_overflow(tmp_path: Path):
    """超过 MAX_KNOWLEDGE_ENTRIES 时应驱逐 staging 决策优先。"""
    from piia_engram.storage import MAX_KNOWLEDGE_ENTRIES, _read_json

    engram = make_engram(tmp_path)
    path = tmp_path / "knowledge" / "decisions.json"

    entries = []
    for i in range(MAX_KNOWLEDGE_ENTRIES):
        tier = "staging" if i < 5 else "verified"
        entries.append({
            "id": f"decision-{i:04d}",
            "question": f"decision {i} unique question for eviction test",
            "choice": f"choice {i}",
            "status": "active",
            "tier": tier,
            "timestamp": f"2026-01-{(i % 28) + 1:02d}T00:00:00",
        })
    from piia_engram.storage import _write_json
    _write_json(path, entries)

    result = engram.add_decision({"question": "溢出决策", "choice": "测试"})
    assert "status" not in result or result.get("status") != "duplicate"

    decisions = _read_json(path)
    assert len(decisions) <= MAX_KNOWLEDGE_ENTRIES


def test_get_lessons_domain_filter(tmp_path: Path):
    """get_lessons 应能按 domain 过滤。"""
    engram = make_engram(tmp_path)
    engram.add_lesson("Python 优化技巧", domain="python")
    engram.add_lesson("Docker 部署实践", domain="docker")

    python_only = engram.get_lessons(domain="python")
    assert all("python" in l.get("domain", "") for l in python_only)
    assert len(python_only) >= 1


def test_get_decisions_domain_filter(tmp_path: Path):
    """get_decisions 应能按 domain 过滤。"""
    engram = make_engram(tmp_path)
    engram.add_decision({"question": "框架选择", "choice": "FastAPI", "domain": "python"})
    engram.add_decision({"question": "数据库选型", "choice": "PostgreSQL", "domain": "database"})

    python_only = engram.get_decisions(domain="python")
    assert len(python_only) >= 1
    assert all("python" in d.get("domain", "") for d in python_only)


def test_update_lesson_not_found(tmp_path: Path):
    """update_lesson 对不存在的 ID 应返回 error。"""
    engram = make_engram(tmp_path)
    result = engram.update_lesson("nonexistent-id", {"summary": "new"})
    assert "error" in result


def test_update_decision_not_found(tmp_path: Path):
    """update_decision 对不存在的 ID 应返回 error。"""
    engram = make_engram(tmp_path)
    result = engram.update_decision("nonexistent-id", {"choice": "new"})
    assert "error" in result


def test_link_knowledge_not_found(tmp_path: Path):
    """link_knowledge 对不存在的 ID 应返回 error。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("存在的条目", domain="test")
    lid = lesson["id"]

    # id_b not found
    result = engram.link_knowledge(lid, "nonexistent")
    assert "error" in result

    # id_a not found
    result = engram.link_knowledge("nonexistent", lid)
    assert "error" in result


def test_unlink_knowledge_not_found(tmp_path: Path):
    """unlink_knowledge 对不存在的 ID 应返回 error。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("存在的条目", domain="test")
    lid = lesson["id"]

    result = engram.unlink_knowledge(lid, "nonexistent")
    assert "error" in result

    result = engram.unlink_knowledge("nonexistent", lid)
    assert "error" in result


def test_merge_knowledge_secondary_not_found(tmp_path: Path):
    """merge_knowledge secondary 不存在时返回 error。"""
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("主条目", domain="test")
    result = engram.merge_knowledge(lesson["id"], "nonexistent")
    assert "error" in result
    assert "Secondary" in result["error"]


def test_merge_knowledge_not_active(tmp_path: Path):
    """merge_knowledge 对非 active 条目应返回 error。"""
    engram = make_engram(tmp_path)
    l1 = engram.add_lesson("主条目", domain="test")
    l2 = engram.add_lesson("副条目", domain="test")
    engram.archive_lesson(l2["id"])

    result = engram.merge_knowledge(l1["id"], l2["id"])
    assert "error" in result
    assert "not active" in result["error"]


def test_merge_knowledge_transfers_related(tmp_path: Path):
    """merge_knowledge 应转移 secondary 的 related_ids 到 primary。"""
    engram = make_engram(tmp_path)
    l1 = engram.add_lesson("主条目", domain="test")
    l2 = engram.add_lesson("副条目", domain="test")
    l3 = engram.add_lesson("关联条目", domain="test")

    # Link l2 ↔ l3
    engram.link_knowledge(l2["id"], l3["id"])

    # Merge l2 into l1 → l3's link should transfer to l1
    result = engram.merge_knowledge(l1["id"], l2["id"])
    assert result.get("success") is True
    assert result.get("related_ids_transferred", 0) >= 1


def test_import_all_overwrite_mode(tmp_path: Path):
    """import_all(merge=False) 应覆盖而不是合并。"""
    engram = make_engram(tmp_path)

    # Set up initial data
    engram.update_profile({"role": "原始角色"})
    engram.update_work_style({"preferences": {"old": True}})
    engram.update_quality_standards({"rules": ["旧规则"]})
    engram.add_lesson("原始教训", domain="test")
    engram.add_decision({"question": "原始决策", "choice": "A"})

    # Export
    export_path = engram.export_all(str(tmp_path / "backup.json"))

    # Modify exported data
    backup = json.loads(Path(export_path).read_text(encoding="utf-8"))
    backup["identity"]["profile"] = {"role": "新角色", "name": "测试"}
    backup["identity"]["work_style"] = {"preferences": {"new": True}}
    backup["identity"]["quality_standards"] = {"rules": ["新规则"], "acceptance_threshold": 0.9}
    backup["knowledge"]["lessons"] = [{"summary": "新教训", "domain": "new"}]
    backup["knowledge"]["decisions"] = [{"question": "新决策", "choice": "B"}]
    backup["knowledge"]["domains"] = {"new_domain": {"project_count": 5}}
    backup["projects"] = {"proj1": {"title": "覆写项目", "project_folder": "/test"}}

    modified_path = tmp_path / "modified_backup.json"
    modified_path.write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8")

    # Import with overwrite
    result = engram.import_all(str(modified_path), merge=False)
    assert result["status"] == "success"
    assert result["mode"] == "overwrite"
    assert "profile" in result["imported"]
    assert "work_style" in result["imported"]
    assert "quality_standards" in result["imported"]


# ===========================================================================
# Tiered cold-start context (level parameter)
# ===========================================================================


def _seed_full_context_data(engram: Engram) -> None:
    """给 engram 填一份足以让所有 section 出现的数据。"""
    engram.update_profile({
        "role": "PM",
        "language": "Chinese",
        "technical_level": "advanced",
        "description": "test user",
    })
    engram.update_preferences({
        "work_patterns": {"pace": "fast"},
        "communication": "concise",
        "tool_preferences": {"editor": "vscode"},
    })
    engram.update_quality_standards({
        "acceptance_threshold": 4,
        "rules": ["always test before push"],
    })
    for i in range(3):
        engram.add_lesson(f"lesson item alpha bravo {i}", "python")
    for i in range(3):
        engram.add_decision({
            "question": f"choose-tool-{i}",
            "choice": f"option-{i}",
            "reasoning": "context",
        })


def test_generate_context_level_quick_minimal(tmp_path: Path):
    """quick 只输出 profile + preferences，不含 quality/lessons/decisions/domains。"""
    engram = make_engram(tmp_path)
    _seed_full_context_data(engram)

    ctx = engram.generate_context(level="quick")

    assert "## 关于用户" in ctx
    assert "## 工作偏好" in ctx
    assert "## 质量标准" not in ctx
    assert "## 相关经验教训" not in ctx
    assert "## 已做的关键决策" not in ctx
    assert "## 经验领域" not in ctx
    assert "auto_sync" not in ctx
    assert "staging_review_reminder" not in ctx


def test_generate_context_level_standard_includes_top_knowledge(tmp_path: Path):
    """standard 含质量/教训/决策/领域，但跳过 sync/stale/staging/conflicts。"""
    engram = make_engram(tmp_path)
    _seed_full_context_data(engram)

    ctx = engram.generate_context(level="standard")

    assert "## 关于用户" in ctx
    assert "## 质量标准" in ctx
    assert "## 相关经验教训" in ctx
    assert "## 已做的关键决策" in ctx
    # 重 IO 的部分不应出现
    assert "auto_sync" not in ctx
    assert "stale_knowledge_warning" not in ctx
    assert "staging_review_reminder" not in ctx


def test_generate_context_level_full_default_backward_compat(tmp_path: Path):
    """不传 level 时行为应与之前一致(level=full)。"""
    engram = make_engram(tmp_path)
    _seed_full_context_data(engram)

    legacy = engram.generate_context()
    explicit = engram.generate_context(level="full")

    # 两者结构相同；reconcile 的 side effects 可能在二次调用导入数变化，
    # 因此只比对静态 section 标题，足以证明默认 = full。
    for header in ["## 关于用户", "## 工作偏好", "## 质量标准",
                   "## 相关经验教训", "## 已做的关键决策"]:
        assert header in legacy
        assert header in explicit


def test_generate_context_unknown_level_falls_back_to_full(tmp_path: Path):
    """未知 level 字符串应回退到 full，避免静默丢内容。"""
    engram = make_engram(tmp_path)
    _seed_full_context_data(engram)

    ctx = engram.generate_context(level="nonsense")

    assert "## 关于用户" in ctx
    assert "## 质量标准" in ctx
    assert "## 相关经验教训" in ctx


def test_generate_context_quick_is_faster_than_full(tmp_path: Path):
    """quick 应明显快于 full（跳过文件系统扫描的副作用）。"""
    import time
    engram = make_engram(tmp_path)
    _seed_full_context_data(engram)

    # warm-up
    engram.generate_context(level="full")
    engram.generate_context(level="quick")

    def _avg(level: str, runs: int = 3) -> float:
        samples = []
        for _ in range(runs):
            t0 = time.perf_counter()
            engram.generate_context(level=level)
            samples.append(time.perf_counter() - t0)
        return sum(samples) / len(samples)

    full_avg = _avg("full")
    quick_avg = _avg("quick")

    # full 必然慢于 quick（reconcile 扫描）。设较宽松阈值避免 CI flake。
    assert quick_avg < full_avg, (
        f"quick={quick_avg*1000:.1f}ms full={full_avg*1000:.1f}ms — "
        "quick mode should skip reconcile and be faster"
    )


# ===========================================================================
# Quick-context snapshot file (refresh_quick_context)
# ===========================================================================


def test_refresh_quick_context_writes_file(tmp_path: Path):
    """refresh_quick_context 应写出 markdown 文件，含身份内容。"""
    engram = make_engram(tmp_path)
    _seed_full_context_data(engram)

    path = engram.refresh_quick_context()

    assert path.exists(), "snapshot file should be created"
    assert path.name == "quick_context.md"
    text = path.read_text(encoding="utf-8")
    # 顶部标记
    assert "Engram quick_context snapshot" in text
    assert "level=standard" in text
    # standard 应含的内容
    assert "## 关于用户" in text
    assert "## 质量标准" in text
    # standard 不应含的内容（reconcile / sync）
    assert "auto_sync" not in text


def test_refresh_quick_context_atomic_overwrite(tmp_path: Path):
    """重复刷新应正确覆盖旧内容，无残留临时文件。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "first role"})
    p1 = engram.refresh_quick_context()
    text1 = p1.read_text(encoding="utf-8")
    assert "first role" in text1

    engram.update_profile({"role": "second role"})
    p2 = engram.refresh_quick_context()
    text2 = p2.read_text(encoding="utf-8")
    assert "second role" in text2
    assert "first role" not in text2  # 被覆盖

    # 无残留 .tmp 文件
    leftovers = [p for p in p1.parent.iterdir() if p.name.startswith(".quick_context.md.")]
    assert leftovers == [], f"leftover tmp files: {leftovers}"


def test_refresh_quick_context_respects_level(tmp_path: Path):
    """level 参数应控制快照详细度。"""
    engram = make_engram(tmp_path)
    _seed_full_context_data(engram)

    quick_path = engram.refresh_quick_context(level="quick")
    quick_text = quick_path.read_text(encoding="utf-8")
    assert "## 关于用户" in quick_text
    assert "## 质量标准" not in quick_text  # quick 不含

    # 切回 standard 应重新填回 quality
    std_path = engram.refresh_quick_context(level="standard")
    std_text = std_path.read_text(encoding="utf-8")
    assert "## 质量标准" in std_text


def test_refresh_quick_context_custom_target(tmp_path: Path):
    """target 参数应允许写到自定义路径。"""
    engram = make_engram(tmp_path)
    engram.update_profile({"role": "PM"})

    target = tmp_path / "subdir" / "my_card.md"
    path = engram.refresh_quick_context(target=target)

    assert path == target
    assert target.exists()
    assert "PM" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Playbook CRUD Tests
# ---------------------------------------------------------------------------

def _sample_playbook() -> dict:
    """创建一个测试用的 Playbook。"""
    return {
        "title": "MCP Registry 发布流程",
        "triggers": ["发布", "registry", "上架", "mcp-publisher"],
        "domain": "mcp,发布",
        "description": "从版本号更新到 MCP Registry 上架的完整流程",
        "preconditions": ["mcp-publisher 已下载", "GitHub OAuth 已授权"],
        "steps": [
            {"order": 1, "action": "版本号同步更新", "detail": "__init__.py + pyproject.toml + server.json"},
            {"order": 2, "action": "提交推送打 tag", "detail": "CI 自动发布到 PyPI"},
            {"order": 3, "action": "执行 mcp-publisher publish", "detail": "等 PyPI 传播后执行"},
        ],
        "pitfalls": ["不能重复发布同一版本号", "PyPI 传播需 ~10s"],
        "outcome": "MCP Registry 显示新版本",
        "source_tool": "test",
    }


def test_init_creates_playbooks_dir(tmp_path: Path):
    """初始化应创建 playbooks 目录。"""
    engram = make_engram(tmp_path)
    assert (tmp_path / "playbooks").is_dir()


def test_add_and_get_playbook(tmp_path: Path):
    """添加 Playbook 后应能获取到。"""
    engram = make_engram(tmp_path)
    result = engram.add_playbook(_sample_playbook())
    assert "id" in result
    assert result["title"] == "MCP Registry 发布流程"
    assert len(result["steps"]) == 3

    playbooks = engram.get_playbooks()
    assert len(playbooks) == 1
    assert playbooks[0]["title"] == "MCP Registry 发布流程"


def test_get_playbook_by_id(tmp_path: Path):
    """通过 ID 获取单条 Playbook。"""
    engram = make_engram(tmp_path)
    added = engram.add_playbook(_sample_playbook())
    pb_id = added["id"]

    pb = engram.get_playbook(pb_id)
    assert pb["title"] == "MCP Registry 发布流程"
    assert pb["triggers"] == ["发布", "registry", "上架", "mcp-publisher"]


def test_playbook_index_consistency(tmp_path: Path):
    """索引应与实际文件保持一致。"""
    engram = make_engram(tmp_path)
    added = engram.add_playbook(_sample_playbook())

    index = engram._read_playbook_index()
    assert len(index) == 1
    assert index[0]["id"] == added["id"]
    assert index[0]["title"] == "MCP Registry 发布流程"
    assert index[0]["triggers"] == ["发布", "registry", "上架", "mcp-publisher"]

    # 文件也应存在
    pb_file = tmp_path / "playbooks" / f"{added['id']}.json"
    assert pb_file.exists()


def test_playbook_duplicate_detection(tmp_path: Path):
    """标题相似的 Playbook 应被去重。"""
    engram = make_engram(tmp_path)
    engram.add_playbook(_sample_playbook())

    dup = engram.add_playbook({
        "title": "MCP Registry 发布流程",
        "triggers": ["发布"],
    })
    assert dup.get("status") == "duplicate"


def test_playbook_update(tmp_path: Path):
    """更新 Playbook 应持久化并更新索引。"""
    engram = make_engram(tmp_path)
    added = engram.add_playbook(_sample_playbook())
    pb_id = added["id"]

    result = engram.update_playbook(pb_id, {
        "description": "更新后的描述",
        "outcome": "更新后的预期结果",
    })
    assert result["description"] == "更新后的描述"
    assert result["version"] == 2

    # 重新读取应保持
    pb = engram.get_playbook(pb_id)
    assert pb["description"] == "更新后的描述"


def test_playbook_archive(tmp_path: Path):
    """归档后 Playbook 不应出现在活跃列表中。"""
    engram = make_engram(tmp_path)
    added = engram.add_playbook(_sample_playbook())
    pb_id = added["id"]

    engram.archive_playbook(pb_id)

    # 活跃列表应为空
    playbooks = engram.get_playbooks()
    assert len(playbooks) == 0

    # 但文件仍在（非删除）
    pb = engram.get_playbook(pb_id)
    assert pb["status"] == "outdated"


def test_search_knowledge_playbooks(tmp_path: Path):
    """通过 trigger 关键词搜索应命中 Playbook。"""
    engram = make_engram(tmp_path)
    engram.add_playbook(_sample_playbook())

    results = engram.search_knowledge("发布 registry")
    assert "playbooks" in results
    assert len(results["playbooks"]) >= 1
    assert results["playbooks"][0]["title"] == "MCP Registry 发布流程"


def test_search_knowledge_playbooks_scope(tmp_path: Path):
    """scope='playbooks' 应只搜索 Playbook，不返回 lessons/decisions。"""
    engram = make_engram(tmp_path)
    engram.add_playbook(_sample_playbook())
    engram.add_lesson({"summary": "发布流程的经验", "domain": "mcp"})

    results = engram.search_knowledge("发布", scope="playbooks")
    assert len(results.get("playbooks", [])) >= 1
    assert len(results.get("lessons", [])) == 0


def test_search_knowledge_lessons_scope_unaffected(tmp_path: Path):
    """scope='lessons' 不应返回 playbook 结果。"""
    engram = make_engram(tmp_path)
    engram.add_playbook(_sample_playbook())

    results = engram.search_knowledge("发布", scope="lessons")
    assert len(results.get("playbooks", [])) == 0


def test_find_item_by_id_playbook(tmp_path: Path):
    """_find_item_by_id 应能找到 Playbook。"""
    engram = make_engram(tmp_path)
    added = engram.add_playbook(_sample_playbook())
    pb_id = added["id"]

    item_type, item = engram._find_item_by_id(pb_id)
    assert item_type == "playbook"
    assert item["title"] == "MCP Registry 发布流程"


def test_update_knowledge_playbook(tmp_path: Path):
    """update_knowledge 应能分派到 Playbook 更新。"""
    engram = make_engram(tmp_path)
    added = engram.add_playbook(_sample_playbook())
    pb_id = added["id"]

    result = engram.update_knowledge(pb_id, {"description": "通用更新测试"})
    assert result["description"] == "通用更新测试"


def test_archive_knowledge_playbook(tmp_path: Path):
    """archive_knowledge 应能分派到 Playbook 归档。"""
    engram = make_engram(tmp_path)
    added = engram.add_playbook(_sample_playbook())
    pb_id = added["id"]

    engram.archive_knowledge(pb_id)
    pb = engram.get_playbook(pb_id)
    assert pb["status"] == "outdated"


def test_playbook_export_import(tmp_path: Path):
    """export → import 循环应保持 Playbook 完整性。"""
    engram = make_engram(tmp_path)
    engram.add_playbook(_sample_playbook())

    export_path = engram.export_all()

    # 在新目录导入
    new_root = tmp_path / "imported"
    new_engram = Engram(root=new_root)
    result = new_engram.import_all(export_path)

    assert any("playbooks" in item for item in result["imported"])

    imported_pbs = new_engram.get_playbooks()
    assert len(imported_pbs) == 1
    assert imported_pbs[0]["title"] == "MCP Registry 发布流程"
    assert len(imported_pbs[0]["steps"]) == 3


def test_playbook_title_required(tmp_path: Path):
    """没有 title 的 Playbook 应返回错误。"""
    engram = make_engram(tmp_path)
    result = engram.add_playbook({"triggers": ["test"]})
    assert result.get("error")


def test_playbook_domain_filter(tmp_path: Path):
    """get_playbooks 的 domain 筛选应正常工作。"""
    engram = make_engram(tmp_path)
    engram.add_playbook({
        "title": "MCP 发布",
        "triggers": ["mcp"],
        "domain": "mcp,发布",
    })
    engram.add_playbook({
        "title": "Docker 部署",
        "triggers": ["docker"],
        "domain": "docker,部署",
    })

    mcp_pbs = engram.get_playbooks(domain="mcp")
    assert len(mcp_pbs) == 1
    assert mcp_pbs[0]["title"] == "MCP 发布"

    docker_pbs = engram.get_playbooks(domain="docker")
    assert len(docker_pbs) == 1
    assert docker_pbs[0]["title"] == "Docker 部署"


# ===========================================================================
# Tools Registry Tests
# ===========================================================================


def test_init_creates_environment_dir(tmp_path: Path):
    """初始化应创建 environment 目录。"""
    engram = make_engram(tmp_path)
    assert (tmp_path / "environment").is_dir()


def test_register_and_list_tools(tmp_path: Path):
    """注册工具后应能列出。"""
    engram = make_engram(tmp_path)
    result = engram.register_tool({
        "name": "Python",
        "path": "/usr/bin/python3",
        "category": "runtime",
        "version": "3.12",
        "purpose": "Python interpreter",
    })
    assert result.get("name") == "Python"
    assert result.get("_action") == "registered"

    tools = engram.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "Python"
    assert tools[0]["path"] == "/usr/bin/python3"
    assert tools[0]["id"]  # should have generated an ID


def test_register_tool_update_existing(tmp_path: Path):
    """同名工具重复注册应更新而非新增。"""
    engram = make_engram(tmp_path)
    engram.register_tool({"name": "gh", "path": "/usr/bin/gh", "version": "2.50"})
    result = engram.register_tool({"name": "gh", "path": "/usr/local/bin/gh", "version": "2.88"})
    assert result.get("_action") == "updated"
    assert result["path"] == "/usr/local/bin/gh"
    assert result["version"] == "2.88"

    tools = engram.list_tools()
    assert len(tools) == 1  # not duplicated


def test_register_tool_name_required(tmp_path: Path):
    """没有 name 的工具应返回错误。"""
    engram = make_engram(tmp_path)
    result = engram.register_tool({"path": "/usr/bin/something"})
    assert "error" in result


def test_find_tool(tmp_path: Path):
    """按关键词搜索工具。"""
    engram = make_engram(tmp_path)
    engram.register_tool({"name": "Python", "category": "runtime", "purpose": "解释器"})
    engram.register_tool({"name": "gh", "category": "cli", "purpose": "GitHub CLI"})
    engram.register_tool({"name": "wrangler", "category": "cli", "purpose": "Cloudflare Workers"})

    # Search by name
    results = engram.find_tool("python")
    assert len(results) == 1
    assert results[0]["name"] == "Python"

    # Search by purpose
    results = engram.find_tool("github")
    assert len(results) == 1
    assert results[0]["name"] == "gh"

    # Search by category
    results = engram.find_tool("cli")
    assert len(results) == 2

    # Empty query returns all active
    results = engram.find_tool("")
    assert len(results) == 3


def test_list_tools_by_category(tmp_path: Path):
    """按分类筛选工具。"""
    engram = make_engram(tmp_path)
    engram.register_tool({"name": "Python", "category": "runtime"})
    engram.register_tool({"name": "gh", "category": "cli"})

    runtimes = engram.list_tools(category="runtime")
    assert len(runtimes) == 1
    assert runtimes[0]["name"] == "Python"

    clis = engram.list_tools(category="cli")
    assert len(clis) == 1


def test_update_tool(tmp_path: Path):
    """按 ID 更新工具字段。"""
    engram = make_engram(tmp_path)
    result = engram.register_tool({"name": "Node.js", "version": "20.0"})
    tool_id = result["id"]

    updated = engram.update_tool(tool_id, {"version": "22.0", "notes": "LTS"})
    assert updated["version"] == "22.0"
    assert updated["notes"] == "LTS"


def test_remove_tool(tmp_path: Path):
    """软删除工具后不应出现在列表中。"""
    engram = make_engram(tmp_path)
    result = engram.register_tool({"name": "old-tool", "category": "cli"})
    tool_id = result["id"]

    engram.remove_tool(tool_id)
    tools = engram.list_tools()
    assert len(tools) == 0  # removed tools don't appear


def test_find_item_by_id_tool(tmp_path: Path):
    """通用 ID 查找应找到工具。"""
    engram = make_engram(tmp_path)
    result = engram.register_tool({"name": "curl", "path": "/usr/bin/curl"})
    tool_id = result["id"]

    item_type, item = engram._find_item_by_id(tool_id)
    assert item_type == "tool"
    assert item["name"] == "curl"


def test_tools_export_import(tmp_path: Path):
    """导出导入应保留工具注册信息。"""
    engram = make_engram(tmp_path)
    engram.register_tool({"name": "Python", "path": "/usr/bin/python3", "version": "3.12"})
    engram.register_tool({"name": "gh", "path": "/usr/bin/gh", "version": "2.88"})

    export_path = engram.export_all()
    exported = json.loads(Path(export_path).read_text(encoding="utf-8"))
    assert len(exported["environment"]["tools"]) == 2

    # Import into fresh Engram
    engram2 = make_engram(tmp_path / "import_target")
    result = engram2.import_all(export_path, merge=True)
    assert "tools(+2)" in result["imported"]

    tools = engram2.list_tools()
    assert len(tools) == 2

    # Re-import should not duplicate
    result2 = engram2.import_all(export_path, merge=True)
    assert "tools(+0)" in result2["imported"]
    assert len(engram2.list_tools()) == 2


# ===========================================================================
# Playbook Auto-Extraction Tests
# ===========================================================================


def test_detect_procedural_workflow(tmp_path: Path):
    """含顺序标记和操作动词的文本应被识别为流程。"""
    engram = make_engram(tmp_path)
    # Positive: clear procedure
    assert engram._detect_procedural_workflow(
        "先更新版本号，然后提交推送到 GitHub，接着发布到 PyPI，最后上架到 Registry"
    )
    # Negative: too short
    assert not engram._detect_procedural_workflow("简单修复")
    # Negative: no sequential markers
    assert not engram._detect_procedural_workflow(
        "今天讨论了数据库选型，分析了 PostgreSQL 和 MySQL 的优缺点"
    )


def test_detect_procedural_workflow_english(tmp_path: Path):
    """English procedural text should also be detected."""
    engram = make_engram(tmp_path)
    assert engram._detect_procedural_workflow(
        "First install the CLI tool, then configure the API token, "
        "next deploy the worker, finally publish to the registry"
    )


def test_extract_steps_from_summary_numbered(tmp_path: Path):
    """数字列表格式应正确提取步骤。"""
    engram = make_engram(tmp_path)
    summary = (
        "1. 更新版本号\n"
        "2. 提交并推送代码\n"
        "3. 等 CI 通过后构建\n"
        "4. 发布到 PyPI"
    )
    steps = engram._extract_steps_from_summary(summary)
    assert len(steps) >= 3
    assert steps[0]["order"] == 1


def test_extract_steps_from_summary_sequential(tmp_path: Path):
    """顺序标记格式应正确提取步骤。"""
    engram = make_engram(tmp_path)
    summary = "先更新版本号，然后提交推送代码，接着构建发布到 PyPI，最后创建 Release"
    steps = engram._extract_steps_from_summary(summary)
    assert len(steps) >= 3


def test_extract_pitfalls(tmp_path: Path):
    """应从文本中提取陷阱/注意事项。"""
    engram = make_engram(tmp_path)
    text = (
        "完成了发布。过程中踩坑了 API Key 的格式不对。"
        "另外注意 JWT 会过期需要重新授权。"
    )
    pitfalls = engram._extract_pitfalls(text)
    assert len(pitfalls) >= 1


def test_extract_playbook_from_session_positive(tmp_path: Path):
    """包含多步骤流程的摘要应自动生成 Playbook 草稿。"""
    engram = make_engram(tmp_path)
    summary = (
        "完成 v3.24.0 发布流程。先同步更新了三处版本号，"
        "然后提交推送到 GitHub，接着等 CI 通过后发布到 PyPI，"
        "最后用 mcp-publisher 上架到 MCP Registry。"
        "过程中踩坑了 cfk_ 前缀的 Key 不被接受。"
    )
    result = engram.extract_playbook_from_session(summary, source_tool="claude_code")
    assert result is not None
    assert result.get("title")
    assert len(result.get("steps", [])) >= 3
    assert result.get("tier") == "staging"
    assert result.get("id")

    # Should be findable via search
    found = engram.get_playbooks()
    assert len(found) >= 1


def test_extract_playbook_from_session_negative(tmp_path: Path):
    """非流程性摘要不应生成 Playbook。"""
    engram = make_engram(tmp_path)
    summary = "今天和用户讨论了产品定位，确认了 ICP 是多工具开发者"
    result = engram.extract_playbook_from_session(summary)
    assert result is None


def test_extract_playbook_dedup(tmp_path: Path):
    """相同流程应合并到已有 staging Playbook，而非重复创建。"""
    engram = make_engram(tmp_path)
    summary = (
        "先更新版本号，然后提交推送到 GitHub，"
        "接着发布到 PyPI，最后上架到 Registry"
    )
    result1 = engram.extract_playbook_from_session(summary)
    assert result1 is not None
    original_id = result1.get("id")

    # Same summary triggers merge (staging tier) instead of discard
    result2 = engram.extract_playbook_from_session(summary)
    assert result2 is not None  # merged, not discarded
    assert result2.get("id") == original_id  # same playbook
    assert result2.get("merged") is True


def test_extract_steps_from_checkpoints(tmp_path: Path):
    """应从 context 检查点内容中提取步骤。"""
    engram = make_engram(tmp_path)
    checkpoint_content = (
        "# Session: claude_code @ 2026-05-23 14:00\n"
        "### 14:00\n"
        "当前任务：发布新版本\n"
        "### 14:15\n"
        "已完成：版本号三处同步更新\n"
        "### 14:30\n"
        "已完成：commit+push 并等 CI 通过\n"
        "### 14:45\n"
        "已完成：PyPI 发布成功\n"
        "### 15:00\n"
        "已完成：MCP Registry 上架完成\n"
    )
    steps = engram._extract_steps_from_checkpoints(checkpoint_content)
    assert len(steps) >= 3
    assert steps[0]["order"] == 1
    assert "版本号" in steps[0]["action"]


# --- P0 improvements: detection, redaction, promotion ---


def test_detect_keywords_only_no_action_verb(tmp_path: Path):
    """纯关键词（无操作动词）不应触发检测 — 防止元讨论误报。"""
    engram = make_engram(tmp_path)
    # This text has many playbook trigger keywords but zero action verbs
    text = (
        "我们讨论了发布流程的步骤设计，分析了操作手册的结构，"
        "评估了 runbook 的可复用性和 workflow 的自动化方案"
    )
    assert not engram._detect_procedural_workflow(text)


def test_detect_keywords_with_action_verb(tmp_path: Path):
    """关键词 + 至少 1 个操作动词应触发。"""
    engram = make_engram(tmp_path)
    text = (
        "这个发布流程的步骤是：先构建包，操作手册记录了部署细节"
    )
    assert engram._detect_procedural_workflow(text)


def test_checkpoint_bypasses_text_detection(tmp_path: Path):
    """有 3+ 检查点时，即使 summary 不像流程也应生成草稿。"""
    engram = make_engram(tmp_path)
    # Save a session with 3+ checkpoints
    engram.save_agent_context(
        tool="test_tool",
        content=(
            "### 10:00\n已完成：初始化项目\n"
            "### 10:15\n已完成：配置数据库\n"
            "### 10:30\n已完成：部署到服务器\n"
            "### 10:45\n已完成：验证上线成功\n"
        ),
        project_folder="/test",
        session_id="sess_checkpoint_test",
    )
    # Summary is intentionally vague — would NOT pass text detection
    bland_summary = "完成了一些配置工作"
    result = engram.extract_playbook_from_session(
        bland_summary,
        source_tool="test_tool",
        session_id="sess_checkpoint_test",
    )
    assert result is not None
    assert result.get("confidence") == "high"
    assert len(result.get("steps", [])) >= 3


def test_extract_playbook_confidence_medium(tmp_path: Path):
    """无检查点的文本检测应返回 medium confidence。"""
    engram = make_engram(tmp_path)
    summary = (
        "先更新版本号，然后提交推送到 GitHub，"
        "接着发布到 PyPI，最后上架到 Registry"
    )
    result = engram.extract_playbook_from_session(summary, source_tool="test")
    assert result is not None
    assert result.get("confidence") == "medium"


def test_redact_sensitive_tokens(tmp_path: Path):
    """敏感信息应被脱敏。"""
    from piia_engram.context import ContextMixin
    assert "{{REDACTED}}" in ContextMixin._redact_sensitive(
        "使用 sk-9d970b7bc6094c409a09f2f9be88d64b 发布"
    )
    assert "{{PATH}}" in ContextMixin._redact_sensitive(
        "路径是 C:\\Users\\john\\projects\\app"
    )
    assert "{{EMAIL}}" in ContextMixin._redact_sensitive(
        "联系 admin@example.com 获取权限"
    )
    assert "{{PATH}}" in ContextMixin._redact_sensitive(
        "文件在 /home/user/.ssh/id_rsa"
    )
    # Non-sensitive text should pass through unchanged
    assert ContextMixin._redact_sensitive("更新版本号") == "更新版本号"


def test_redact_applied_to_playbook_steps(tmp_path: Path):
    """自动提取的 Playbook 步骤和 pitfall 应经过脱敏。"""
    engram = make_engram(tmp_path)
    summary = (
        "先用 token sk-abcdef1234567890abcdef 登录，"
        "然后部署到 C:\\Projects\\myapp 目录，"
        "接着运行构建命令，最后发布到 Registry。"
        "过程中踩坑了 admin@corp.com 的权限不对。"
    )
    result = engram.extract_playbook_from_session(summary, source_tool="test")
    assert result is not None
    # Check that steps are redacted
    all_steps_text = " ".join(s["action"] for s in result.get("steps", []))
    assert "sk-abcdef" not in all_steps_text
    # Check that pitfalls are redacted
    all_pitfalls_text = " ".join(result.get("pitfalls", []))
    assert "admin@corp.com" not in all_pitfalls_text


def test_playbook_no_auto_promote(tmp_path: Path):
    """Playbook 不应通过访问次数自动晋升 — 需要用户确认。"""
    engram = make_engram(tmp_path)
    pb = engram.add_playbook({
        "title": "测试流程",
        "triggers": ["test"],
        "steps": [{"order": 1, "action": "step1"}],
        "tier": "staging",
    })
    pb_id = pb["id"]

    # Simulate 5 accesses (well above the threshold of 3)
    for _ in range(5):
        engram.get_playbook(pb_id)

    # Run tier evaluation
    engram.evaluate_tiers()

    # Should still be staging — NOT promoted
    updated = engram.get_playbook(pb_id, _update_access=False)
    assert updated.get("tier") == "staging"


def test_discussion_flow_not_triggered(tmp_path: Path):
    """讨论流程设计的对话不应生成 Playbook。"""
    engram = make_engram(tmp_path)
    # Simulates a conversation about playbook design — full of keywords
    # but no actual execution
    summary = (
        "讨论了 Playbook 自动提取方案的检测规则。"
        "分析了流程关键词、操作动词、顺序标记的判定逻辑。"
        "评估了 runbook 模式和 workflow 提取策略。"
    )
    result = engram.extract_playbook_from_session(summary)
    assert result is None


def test_playbook_auto_extract_kill_switch(tmp_path: Path):
    """用户关闭 playbook_auto_extract 后不应自动提取。"""
    engram = make_engram(tmp_path)
    # This summary would normally trigger extraction
    summary = (
        "先更新版本号，然后提交推送到 GitHub，"
        "接着发布到 PyPI，最后上架到 Registry"
    )
    # Turn off the switch
    engram.update_preferences({"playbook_auto_extract": False})
    result = engram.extract_playbook_from_session(summary, source_tool="test")
    assert result is None

    # Turn it back on
    engram.update_preferences({"playbook_auto_extract": True})
    result = engram.extract_playbook_from_session(summary, source_tool="test")
    assert result is not None


# ── Provider 兼容层测试 ─────────────────────────────────────────────────


def test_extract_session_insights_saves_lesson_as_staging(tmp_path: Path):
    """自动提取的 lesson 应默认 tier=staging，不直接进 verified。"""
    engram = make_engram(tmp_path)
    summary = "注意：部署前必须检查环境变量是否齐全，否则会启动失败。"
    engram.extract_session_insights(summary, source_tool="test")

    lessons = engram.get_lessons(limit=50)
    found = [l for l in lessons if "环境变量" in l.get("summary", "")]
    assert found, "lesson should be extracted"
    assert found[0].get("tier") == "staging"


def test_extract_session_insights_saves_decision_as_staging(tmp_path: Path):
    """自动提取的 decision 应默认 tier=staging，不直接进 verified。"""
    engram = make_engram(tmp_path)
    summary = "我们决定采用 SQLite 作为本地搜索索引，因为零依赖。"
    engram.extract_session_insights(summary, source_tool="test")

    decisions = engram.get_decisions(limit=50)
    found = [d for d in decisions if "SQLite" in d.get("title", "")]
    assert found, "decision should be extracted"
    assert found[0].get("tier") == "staging"


def test_search_knowledge_filters_by_domain(tmp_path: Path):
    """search_knowledge filters={"domain": ...} 应只返回匹配 domain 的条目。"""
    engram = make_engram(tmp_path)
    engram.add_lesson({"summary": "Python GIL limits threading", "domain": "python"})
    engram.add_lesson({"summary": "Docker layer caching saves build time", "domain": "docker"})

    result = engram.search_knowledge("caching threading", filters={"domain": "python"})
    summaries = [l["summary"] for l in result["lessons"]]
    assert any("GIL" in s for s in summaries)
    assert not any("Docker" in s for s in summaries)


def test_search_knowledge_filters_by_tier(tmp_path: Path):
    """search_knowledge filters={"tier": ...} 应只返回匹配 tier 的条目。"""
    engram = make_engram(tmp_path)
    engram.add_lesson({"summary": "staging draft about async error handling", "tier": "staging"})
    engram.add_lesson({"summary": "confirmed practice for async retry logic", "tier": "verified"})

    staging_only = engram.search_knowledge("async", filters={"tier": "staging"})
    verified_only = engram.search_knowledge("async", filters={"tier": "verified"})

    assert len(staging_only["lessons"]) >= 1
    assert all(l.get("tier") == "staging" for l in staging_only["lessons"])
    assert len(verified_only["lessons"]) >= 1
    assert all(l.get("tier") == "verified" for l in verified_only["lessons"])


def test_search_knowledge_filters_by_date_after(tmp_path: Path):
    """search_knowledge filters={"date_after": ...} 应排除更早的条目。"""
    engram = make_engram(tmp_path)
    old_ts = "2024-01-01T00:00:00"
    new_ts = "2026-05-20T00:00:00"
    engram.add_lesson({
        "summary": "legacy migration guide for database schema upgrades",
        "timestamp": old_ts,
    })
    engram.add_lesson({
        "summary": "container orchestration patterns for database scaling",
        "timestamp": new_ts,
    })

    result = engram.search_knowledge("database", filters={"date_after": "2025-01-01"})
    summaries = [l["summary"] for l in result["lessons"]]
    assert any("container" in s for s in summaries)
    assert not any("legacy" in s for s in summaries)
