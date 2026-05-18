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

    engram.update_trust_boundaries({"default_sharing": "limited"})
    tb = engram.get_trust_boundaries()
    assert tb["default_sharing"] == "limited"


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
