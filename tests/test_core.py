"""Engram 核心功能基础测试。"""

import json
import tempfile
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
    assert results["lessons"][0]["access_count"] == 1

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
