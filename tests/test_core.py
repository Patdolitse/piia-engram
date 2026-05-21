"""Engram 核心功能基础测试。"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from engram_core.core import Engram


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
