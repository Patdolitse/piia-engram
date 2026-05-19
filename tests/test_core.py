"""Engram 核心功能基础测试。"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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
