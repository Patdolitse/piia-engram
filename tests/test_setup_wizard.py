"""setup_wizard 辅助函数单元测试。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from engram_core.setup_wizard import (
    _find_mcp_server,
    _find_python,
    _read_mcp_config,
    _run_seed_knowledge_onboarding,
    _write_mcp_config,
)


def test_find_python():
    """_find_python 应能找到当前运行的 Python。"""
    result = _find_python()
    assert result is not None
    assert Path(result).is_file()


def test_find_mcp_server():
    """_find_mcp_server 应能找到已安装的 mcp_server.py。"""
    result = _find_mcp_server()
    assert result is not None
    assert Path(result).is_file()
    assert result.endswith("mcp_server.py")


def test_write_mcp_config_creates_file(tmp_path: Path):
    """_write_mcp_config 应在新路径创建配置文件。"""
    config_path = tmp_path / "test_mcp.json"
    _write_mcp_config(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")
    assert config_path.is_file()
    config = json.loads(config_path.read_text())
    assert "mcpServers" in config
    assert "engram" in config["mcpServers"]
    assert config["mcpServers"]["engram"]["command"] == "/usr/bin/python3"
    assert config["mcpServers"]["engram"]["args"] == ["/path/to/mcp_server.py"]


def test_write_mcp_config_merges(tmp_path: Path):
    """_write_mcp_config 应保留文件中已有的其他工具配置。"""
    config_path = tmp_path / "mcp.json"
    existing = {"mcpServers": {"other-tool": {"command": "node", "args": ["server.js"]}}}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    _write_mcp_config(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")

    config = json.loads(config_path.read_text())
    assert "other-tool" in config["mcpServers"]   # 原有配置保留
    assert "engram" in config["mcpServers"]        # engram 已添加


def test_write_mcp_config_with_data_dir(tmp_path: Path):
    """设置自定义 ENGRAM_DIR 时应写入 env 字段。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(
        config_path,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        data_dir="/custom/engram",
    )
    config = json.loads(config_path.read_text())
    assert config["mcpServers"]["engram"]["env"]["ENGRAM_DIR"] == "/custom/engram"


def test_write_mcp_config_no_env_without_data_dir(tmp_path: Path):
    """data_dir 为 None 时不应写入 env 字段。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(
        config_path,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        data_dir=None,
    )
    config = json.loads(config_path.read_text())
    assert "env" not in config["mcpServers"]["engram"]


def test_write_mcp_config_overwrites_existing_engram(tmp_path: Path):
    """重复运行 setup 应更新而非累加 engram 配置。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(config_path, "/old/python", "/old/mcp_server.py")
    _write_mcp_config(config_path, "/new/python", "/new/mcp_server.py")
    config = json.loads(config_path.read_text())
    assert config["mcpServers"]["engram"]["command"] == "/new/python"


def test_read_mcp_config_missing_file(tmp_path: Path):
    """不存在的配置文件应返回空 dict。"""
    result = _read_mcp_config(tmp_path / "nonexistent.json")
    assert result == {}


def test_engram_dir_env(tmp_path: Path, monkeypatch):
    """ENGRAM_DIR 环境变量应覆盖默认数据目录。"""
    custom = str(tmp_path / "custom_engram")
    monkeypatch.setenv("ENGRAM_DIR", custom)

    import importlib
    import engram_core.core as core_mod
    importlib.reload(core_mod)

    engram = core_mod.Engram()
    assert custom in str(engram.root)


def test_seed_onboarding_saves_profile_and_lessons(tmp_path: Path, monkeypatch, capsys):
    """种子知识引导应把身份和最多 3 条经验写入 Engram。"""
    answers = iter([
        "全栈开发者",
        "Python + React",
        "中文",
        "AI 总是忘记先跑测试",
        "提交前必须检查 git diff",
        "回答时先给结论",
        "第四条不应被提问",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    summary = _run_seed_knowledge_onboarding(str(tmp_path), cwd=tmp_path)

    from engram_core.core import Engram

    engram = Engram(root=tmp_path)
    profile = engram.get_profile()
    lessons = engram.get_lessons(limit=None, _update_access=False)

    assert profile["role"] == "全栈开发者"
    assert profile["language"] == "中文"
    assert profile["tech_stack"] == "Python + React"
    assert "Python + React" in profile["description"]
    assert [lesson["summary"] for lesson in lessons] == [
        "AI 总是忘记先跑测试",
        "提交前必须检查 git diff",
        "回答时先给结论",
    ]
    assert summary["lessons_added"] == 3
    assert "经验：已录入 3 条" in capsys.readouterr().out


def test_seed_onboarding_imports_claude_rules(tmp_path: Path, monkeypatch):
    """检测到 CLAUDE.md 且用户确认时，应通过 ingest_notes 导入规则。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    (tmp_path / "CLAUDE.md").write_text(
        "remember to run tests before claiming completion\n"
        "decided to keep project memory local first\n",
        encoding="utf-8",
    )
    answers = iter(["", "", "", "", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    summary = _run_seed_knowledge_onboarding(str(tmp_path), cwd=tmp_path)

    from engram_core.core import Engram

    engram = Engram(root=tmp_path)
    lessons = engram.get_lessons(limit=None, _update_access=False)

    assert summary["imported_files"] == [str(tmp_path / "CLAUDE.md")]
    assert any("remember to run tests" in lesson["summary"] for lesson in lessons)
    assert any("decided to keep project memory" in lesson["summary"] for lesson in lessons)


def test_seed_onboarding_allows_skipping_everything(tmp_path: Path, monkeypatch, capsys):
    """所有问题直接回车跳过时，应正常结束且不写入空数据。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    answers = iter(["", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    summary = _run_seed_knowledge_onboarding(str(tmp_path), cwd=tmp_path)

    from engram_core.core import Engram

    engram = Engram(root=tmp_path)

    assert engram.get_profile() == {}
    assert engram.get_lessons(limit=None, _update_access=False) == []
    assert summary["profile"] == {}


# ── Doctor tests ─────────────────────────────────────────────────────


def test_doctor_healthy_config(tmp_path: Path, monkeypatch):
    """doctor 对健康配置应返回 0（无问题）。"""
    from engram_core.setup_wizard import run_doctor, _write_mcp_config, _find_python, _find_mcp_server

    python_path = _find_python()
    mcp_path = _find_mcp_server()
    if not python_path or not mcp_path:
        return  # Skip if can't find paths

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    config_path = config_dir / ".mcp.json"
    _write_mcp_config(config_path, python_path, mcp_path)

    # Patch _tool_configs to point to our test config
    monkeypatch.setattr(
        "engram_core.setup_wizard._tool_configs",
        lambda: {"test": {"name": "Test", "config_paths": [config_path]}},
    )

    result = run_doctor(fix=False)
    assert result == 0


def test_doctor_detects_legacy_server_name(tmp_path: Path, monkeypatch):
    """doctor 应检测到旧版 server 名称。"""
    from engram_core.setup_wizard import run_doctor

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    config_path = config_dir / ".mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "piia-pkc": {"command": "python", "args": ["old_server.py"]},
            "engram": {"command": "python", "args": ["mcp_server.py"]},
        }
    }), encoding="utf-8")

    monkeypatch.setattr(
        "engram_core.setup_wizard._tool_configs",
        lambda: {"test": {"name": "Test", "config_paths": [config_path]}},
    )

    result = run_doctor(fix=False)
    assert result > 0  # Should detect the legacy name


def test_doctor_detects_invalid_python_path(tmp_path: Path, monkeypatch):
    """doctor 应检测到不存在的 Python 路径。"""
    from engram_core.setup_wizard import run_doctor

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    config_path = config_dir / ".mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "engram": {
                "command": "/nonexistent/python999",
                "args": ["/nonexistent/mcp_server.py"],
            }
        }
    }), encoding="utf-8")

    monkeypatch.setattr(
        "engram_core.setup_wizard._tool_configs",
        lambda: {"test": {"name": "Test", "config_paths": [config_path]}},
    )

    result = run_doctor(fix=False)
    assert result > 0  # Should detect invalid paths
