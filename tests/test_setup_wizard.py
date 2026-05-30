"""setup_wizard 辅助函数单元测试。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram.setup_wizard import (
    LEGACY_SERVER_NAMES,
    _choice,
    _classify_line,
    _configure_utf8_stdio,
    _find_mcp_server,
    _find_python,
    _import_with_split,
    _build_feedback_report,
    _inject_claude_code_hook,
    _inject_instruction_snippet,
    _INSTRUCTION_MARKER,
    _INSTRUCTION_MARKER_END,
    _INSTRUCTION_SNIPPETS,
    _read_mcp_config,
    _read_rule_file,
    _remove_instruction_snippet,
    _run_privacy_preferences,
    _run_privacy_report,
    _run_seed_knowledge_onboarding,
    _run_telemetry_cli,
    _save_setup_report,
    _scan_rule_files,
    _tool_configs,
    _write_mcp_config,
    main,
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


def test_tool_configs_include_trae_and_codebuddy(tmp_path: Path, monkeypatch):
    """GUI AI IDEs with home-level MCP files should be auto-configurable."""
    monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)

    configs = _tool_configs()

    assert configs["trae"]["name"] == "Trae"
    assert configs["trae"]["config_paths"] == [tmp_path / ".trae" / "mcp.json"]
    assert configs["trae"]["verified"] is False

    assert configs["codebuddy"]["name"] == "CodeBuddy"
    assert configs["codebuddy"]["config_paths"] == [tmp_path / ".codebuddy" / "mcp.json"]
    assert configs["codebuddy"]["verified"] is False


def test_write_mcp_config_creates_file(tmp_path: Path):
    """_write_mcp_config 应在新路径创建配置文件，使用 -m 模块调用。"""
    config_path = tmp_path / "test_mcp.json"
    _write_mcp_config(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")
    assert config_path.is_file()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "mcpServers" in config
    assert "engram" in config["mcpServers"]
    engram = config["mcpServers"]["engram"]
    assert engram["command"] == "/usr/bin/python3"
    # Must use -m module invocation, never direct .py path
    assert engram["args"] == ["-m", "piia_engram.mcp_server"]
    # Default env always includes PYTHONIOENCODING and ENGRAM_TOOLS
    assert engram["env"]["PYTHONIOENCODING"] == "utf-8"
    assert engram["env"]["ENGRAM_TOOLS"] == "all"


def test_write_mcp_config_merges(tmp_path: Path):
    """_write_mcp_config 应保留文件中已有的其他工具配置。"""
    config_path = tmp_path / "mcp.json"
    existing = {"mcpServers": {"other-tool": {"command": "node", "args": ["server.js"]}}}
    config_path.write_text(json.dumps(existing), encoding="utf-8")

    _write_mcp_config(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "other-tool" in config["mcpServers"]   # 原有配置保留
    assert "engram" in config["mcpServers"]        # engram 已添加


def test_write_mcp_config_with_data_dir(tmp_path: Path):
    """设置自定义 ENGRAM_DIR 时应额外写入 ENGRAM_DIR 到 env。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(
        config_path,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        data_dir="/custom/engram",
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    env = config["mcpServers"]["engram"]["env"]
    assert env["ENGRAM_DIR"] == "/custom/engram"
    # Default env keys should still be present
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["ENGRAM_TOOLS"] == "all"


def test_write_mcp_config_default_env_without_data_dir(tmp_path: Path):
    """data_dir 为 None 时 env 仍应包含 PYTHONIOENCODING 和 ENGRAM_TOOLS。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(
        config_path,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        data_dir=None,
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    env = config["mcpServers"]["engram"]["env"]
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["ENGRAM_TOOLS"] == "all"
    assert "ENGRAM_DIR" not in env


def test_write_mcp_config_overwrites_existing_engram(tmp_path: Path):
    """重复运行 setup 应更新而非累加 engram 配置。"""
    config_path = tmp_path / "mcp.json"
    _write_mcp_config(config_path, "/old/python", "/old/mcp_server.py")
    _write_mcp_config(config_path, "/new/python", "/new/mcp_server.py")
    config = json.loads(config_path.read_text(encoding="utf-8"))
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
    import piia_engram.core as core_mod
    importlib.reload(core_mod)

    engram = core_mod.Engram()
    assert custom in str(engram.root)


def test_seed_onboarding_saves_profile_and_lessons(tmp_path: Path, monkeypatch, capsys):
    """种子知识引导应把身份和最多 3 条经验写入 Engram。"""
    # Isolate from real home directory (prevent global CLAUDE.md auto-import)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    # Mock environment probing to avoid subprocess calls
    monkeypatch.setattr(
        "piia_engram.setup_wizard._probe_environment",
        lambda cwd=None: {},
    )

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

    from piia_engram.core import Engram

    engram = Engram(root=tmp_path)
    profile = engram.get_profile()
    lessons = engram.get_lessons(limit=None, _update_access=False)

    assert profile["role"] == "全栈开发者"
    assert profile["language"] == "中文"
    assert profile["tech_stack"] == "Python + React"
    assert "Python + React" in profile["description"]
    # User lessons come first, then seed templates
    user_lessons = [l["summary"] for l in lessons if l.get("source_tool") == "engram_setup" and l.get("domain") == "setup"]
    assert user_lessons == [
        "AI 总是忘记先跑测试",
        "提交前必须检查 git diff",
        "回答时先给结论",
    ]
    assert summary["lessons_added"] == 3
    out = capsys.readouterr().out
    assert "经验：已录入 3 条" in out
    # Seed templates should have been injected
    assert summary["seed_count"] > 0


def test_seed_onboarding_imports_claude_rules(tmp_path: Path, monkeypatch):
    """检测到 CLAUDE.md 且用户确认时，应通过 ingest_notes 导入规则。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(
        "piia_engram.setup_wizard._probe_environment",
        lambda cwd=None: {},
    )
    (tmp_path / "CLAUDE.md").write_text(
        "remember to run tests before claiming completion\n"
        "decided to keep project memory local first\n",
        encoding="utf-8",
    )
    answers = iter(["", "", "", "", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    summary = _run_seed_knowledge_onboarding(str(tmp_path), cwd=tmp_path)

    from piia_engram.core import Engram

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
    monkeypatch.setattr(
        "piia_engram.setup_wizard._probe_environment",
        lambda cwd=None: {},
    )
    answers = iter(["", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    summary = _run_seed_knowledge_onboarding(str(tmp_path), cwd=tmp_path)

    from piia_engram.core import Engram

    engram = Engram(root=tmp_path)

    assert engram.get_profile() == {}
    assert engram.get_lessons(limit=None, _update_access=False) == []
    assert summary["profile"] == {}
    assert summary["seed_count"] == 0


# ── Cold-start probing & seed template tests ──────────────────────────


def test_probe_environment_detects_project_files(tmp_path: Path):
    """_probe_environment should detect tech stack from project files."""
    from piia_engram.setup_wizard import _probe_environment

    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    (tmp_path / "package.json").write_text('{"name":"test"}')

    signals = _probe_environment(cwd=tmp_path)
    assert "Python" in signals.get("tech_stack_hint", "")
    assert "JavaScript" in signals.get("tech_stack_hint", "")


def test_probe_environment_empty_dir(tmp_path: Path):
    """_probe_environment should return empty dict for empty directory."""
    from piia_engram.setup_wizard import _probe_environment

    signals = _probe_environment(cwd=tmp_path)
    # No project files, no git — might only have name/email from global git config
    assert isinstance(signals, dict)


def test_apply_seed_templates_python(tmp_path: Path):
    """_apply_seed_templates should inject Python + universal lessons."""
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _apply_seed_templates

    engram = Engram(root=tmp_path)
    count = _apply_seed_templates(engram, "Python")

    lessons = engram.get_lessons(limit=None, _update_access=False)
    # Should have Python-specific + universal templates
    assert count >= 4  # 2 Python + 3 universal (minus dedup)
    assert any("commit" in l["summary"].lower() for l in lessons)


def test_apply_seed_templates_no_duplicates(tmp_path: Path):
    """Running _apply_seed_templates twice should not create duplicates."""
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import _apply_seed_templates

    engram = Engram(root=tmp_path)
    count1 = _apply_seed_templates(engram, "Python")
    count2 = _apply_seed_templates(engram, "Python")

    assert count1 > 0
    assert count2 == 0  # All duplicates


# ── Doctor tests ─────────────────────────────────────────────────────


def test_doctor_healthy_config(tmp_path: Path, monkeypatch):
    """doctor 对健康配置应返回 0（无问题）。"""
    from piia_engram.setup_wizard import run_doctor, _write_mcp_config, _find_python, _find_mcp_server

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
        "piia_engram.setup_wizard._tool_configs",
        lambda: {"test": {"name": "Test", "config_paths": [config_path], "verified": True}},
    )

    result = run_doctor(fix=False)
    assert result == 0


def test_doctor_detects_legacy_server_name(tmp_path: Path, monkeypatch):
    """doctor 应检测到旧版 server 名称。"""
    from piia_engram.setup_wizard import run_doctor

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
        "piia_engram.setup_wizard._tool_configs",
        lambda: {"test": {"name": "Test", "config_paths": [config_path], "verified": True}},
    )

    result = run_doctor(fix=False)
    assert result > 0  # Should detect the legacy name


def test_doctor_detects_invalid_python_path(tmp_path: Path, monkeypatch):
    """doctor 应检测到不存在的 Python 路径。"""
    from piia_engram.setup_wizard import run_doctor

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
        "piia_engram.setup_wizard._tool_configs",
        lambda: {"test": {"name": "Test", "config_paths": [config_path], "verified": True}},
    )

    result = run_doctor(fix=False)
    assert result > 0  # Should detect invalid paths


# ── _classify_line tests ─────────────────────────────────────────────


@pytest.mark.parametrize("line,scope,expected", [
    # User identity (global scope)
    ("所有沟通使用中文", "global", "user"),
    ("All communication in English", "global", "user"),
    ("I am a senior backend developer", "global", "user"),
    ("我是全栈开发者", "global", "user"),
    ("Always prefer concise responses", "global", "user"),
    ("Never add unnecessary comments", "global", "user"),
    ("Work style: async, no meetings", "global", "user"),
    # Project rules (project scope)
    ("Run pytest before every commit", "project", "project"),
    ("This repo uses Tailwind CSS", "project", "project"),
    ("Build with docker-compose up", "project", "project"),
    ("Database schema is in schema.sql", "project", "project"),
    ("Pre-commit hooks must pass", "project", "project"),
    ("API endpoints are under /api/v2", "project", "project"),
    # Skip
    ("", "global", "skip"),
    ("# Section Title", "project", "skip"),
    ("---", "global", "skip"),
    ("```python", "project", "skip"),
    ("short", "global", "skip"),  # < 8 chars
    # Ambiguous (falls to scope default)
    ("This is a normal documentation line about the project", "global", "user"),
    ("This is a normal documentation line about the project", "project", "project"),
])
def test_classify_line(line, scope, expected):
    assert _classify_line(line, scope) == expected


# ── _scan_rule_files tests ───────────────────────────────────────────


def test_scan_rule_files_finds_project_claude_md(tmp_path: Path):
    """Should find CLAUDE.md in the project directory."""
    (tmp_path / "CLAUDE.md").write_text(
        "## Instructions\n\nUse Python 3.12 for all scripts.\nAlways run tests first.\n",
        encoding="utf-8",
    )
    results = _scan_rule_files(cwd=tmp_path)
    project_files = [r for r in results if r["scope"] == "project"]
    assert len(project_files) >= 1
    assert any("CLAUDE.md" in str(r["path"]) for r in project_files)


def test_scan_rule_files_skips_tiny_files(tmp_path: Path):
    """Files with < 2 content lines should be skipped."""
    (tmp_path / "CLAUDE.md").write_text("# Title\n", encoding="utf-8")
    results = _scan_rule_files(cwd=tmp_path)
    project_files = [r for r in results if str(tmp_path) in str(r["path"])]
    assert len(project_files) == 0


# ── Privacy preferences tests ───────────────────────────────────────


class TestPrivacyPreferences:
    def test_both_defaults(self, tmp_path, monkeypatch, capsys):
        """Pressing Enter twice should keep defaults: reconcile=Yes, telemetry=No."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        answers = iter(["", ""])  # both defaults
        monkeypatch.setattr("builtins.input", lambda _: next(answers))

        _run_privacy_preferences(str(tmp_path))

        cfg_path = tmp_path / "telemetry_config.json"
        assert cfg_path.is_file()
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert cfg["reconcile_authorized"] is True
        assert cfg["enabled"] is False

    def test_opt_in_telemetry(self, tmp_path, monkeypatch, capsys):
        """Answering 'y' to telemetry should enable it."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        # reconcile default, telemetry yes, remote default (no)
        answers = iter(["", "y", ""])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))

        _run_privacy_preferences(str(tmp_path))

        cfg = json.loads((tmp_path / "telemetry_config.json").read_text(encoding="utf-8"))
        assert cfg["enabled"] is True
        assert "opted_in_at" in cfg

    def test_opt_out_reconcile(self, tmp_path, monkeypatch, capsys):
        """Answering 'n' to reconcile should disable it."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        answers = iter(["n", ""])  # reconcile no, telemetry default
        monkeypatch.setattr("builtins.input", lambda _: next(answers))

        _run_privacy_preferences(str(tmp_path))

        cfg = json.loads((tmp_path / "telemetry_config.json").read_text(encoding="utf-8"))
        assert cfg["reconcile_authorized"] is False


# ── Telemetry CLI tests ─────────────────────────────────────────────


class TestTelemetryCLI:
    def test_status_shows_off(self, tmp_path, monkeypatch, capsys):
        """engram telemetry status should show OFF by default."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_telemetry_cli(["status"])
        out = capsys.readouterr().out
        assert "OFF" in out

    def test_on_then_status(self, tmp_path, monkeypatch, capsys):
        """engram telemetry on, then status should show ON."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_telemetry_cli(["on"])
        capsys.readouterr()  # clear

        _run_telemetry_cli(["status"])
        out = capsys.readouterr().out
        assert "ON" in out

    def test_off_disables(self, tmp_path, monkeypatch, capsys):
        """engram telemetry off should disable."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_telemetry_cli(["on"])
        _run_telemetry_cli(["off"])
        capsys.readouterr()

        _run_telemetry_cli(["status"])
        out = capsys.readouterr().out
        assert "OFF" in out

    def test_preview_returns_json(self, tmp_path, monkeypatch, capsys):
        """engram telemetry preview should output valid JSON."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_telemetry_cli(["preview"])
        out = capsys.readouterr().out
        # The output contains the JSON payload somewhere in it
        assert "schema" in out
        assert "tool_calls" in out

    def test_unknown_subcommand_shows_usage(self, tmp_path, monkeypatch, capsys):
        """Unknown subcommand should show usage help."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        _run_telemetry_cli(["bogus"])
        out = capsys.readouterr().out
        assert "Usage" in out


# ── Privacy report tests ────────────────────────────────────────────


class TestPrivacyReport:
    def test_report_runs_without_error(self, tmp_path, monkeypatch, capsys):
        """engram privacy should print report without error."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)

        _run_privacy_report()
        out = capsys.readouterr().out
        assert "Privacy Report" in out
        assert "[DIR]" in out
        assert "[STAT]" in out
        assert "[NET]" in out

    def test_report_shows_data_dir(self, tmp_path, monkeypatch, capsys):
        """Report should show the ENGRAM_DIR path."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_privacy_report()
        out = capsys.readouterr().out
        assert str(tmp_path) in out

    def test_report_with_identity_file(self, tmp_path, monkeypatch, capsys):
        """Report should show identity file info when it exists."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        # Create a fake identity file
        (tmp_path / "identity.json").write_text(
            '{"profile": {"role": "dev"}}', encoding="utf-8"
        )

        _run_privacy_report()
        out = capsys.readouterr().out
        assert "identity.json" in out
        assert "profile" in out

    def test_report_with_knowledge_file(self, tmp_path, monkeypatch, capsys):
        """Report should count lessons and decisions."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        (tmp_path / "knowledge.json").write_text(
            json.dumps({"lessons": [{"id": "1"}, {"id": "2"}], "decisions": [{"id": "3"}]}),
            encoding="utf-8",
        )

        _run_privacy_report()
        out = capsys.readouterr().out
        assert "Lessons: 2" in out
        assert "Decisions: 1" in out

    def test_report_shows_encrypted_fields(self, tmp_path, monkeypatch, capsys):
        """Report should detect encrypted fields."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        (tmp_path / "identity.json").write_text(
            '{"profile": {"role": "enc:v2:abc123"}}', encoding="utf-8"
        )

        _run_privacy_report()
        out = capsys.readouterr().out
        assert "ENCRYPTED" in out

    def test_report_no_data_dir(self, tmp_path, monkeypatch, capsys):
        """Report should handle non-existent data dir gracefully."""
        nonexistent = tmp_path / "nonexistent_dir"
        monkeypatch.setenv("ENGRAM_DIR", str(nonexistent))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_privacy_report()
        out = capsys.readouterr().out
        assert "not created yet" in out

    def test_report_with_telemetry_log(self, tmp_path, monkeypatch, capsys):
        """Report should show telemetry log stats when log exists."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        (tmp_path / "telemetry.log").write_text(
            '{"schema":1}\n{"schema":1}\n', encoding="utf-8"
        )

        _run_privacy_report()
        out = capsys.readouterr().out
        assert "2 entries" in out

    def test_report_plain_identity(self, tmp_path, monkeypatch, capsys):
        """Report should detect no encrypted fields."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        (tmp_path / "identity.json").write_text(
            '{"profile": {"role": "dev"}}', encoding="utf-8"
        )

        _run_privacy_report()
        out = capsys.readouterr().out
        assert "PLAIN" in out


# ── _safe_print tests ──────────────────────────────────────────────


class TestSafePrint:
    def test_normal_print(self, capsys):
        """Normal ASCII text prints normally."""
        from piia_engram.setup_wizard import _safe_print
        _safe_print("hello world")
        assert "hello world" in capsys.readouterr().out

    def test_unicode_fallback(self, monkeypatch, capsys):
        """When stdout.encoding can't handle chars, fallback strips them."""
        from piia_engram.setup_wizard import _safe_print
        # Mock print to raise UnicodeEncodeError on first call, succeed on second
        call_count = [0]
        original_print = print

        def mock_print(text, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise UnicodeEncodeError("gbk", "hello \u2728", 6, 7, "invalid char")
            original_print(text, **kwargs)

        monkeypatch.setattr("builtins.print", mock_print)
        monkeypatch.setattr("sys.stdout", type("FakeStdout", (), {"encoding": "ascii", "write": sys.stdout.write, "flush": sys.stdout.flush})())
        _safe_print("hello \u2728 world")
        # Should not raise


# ── auto_migrate tests ──────────────────────────────────────────────


class TestAutoMigrate:
    def test_first_run_creates_sentinel(self, tmp_path, monkeypatch):
        """auto_migrate should create .migrated_version sentinel."""
        from piia_engram.setup_wizard import auto_migrate
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

        auto_migrate()

        sentinel = tmp_path / ".migrated_version"
        assert sentinel.is_file()
        # Sentinel should contain some version string
        ver = sentinel.read_text(encoding="utf-8").strip()
        assert len(ver) > 0

    def test_skip_if_already_migrated(self, tmp_path, monkeypatch):
        """auto_migrate should skip if sentinel matches current version."""
        from piia_engram.setup_wizard import auto_migrate
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

        # First run
        auto_migrate()
        sentinel = tmp_path / ".migrated_version"
        mtime1 = sentinel.stat().st_mtime

        # Second run should be a no-op (sentinel already matches)
        import time
        time.sleep(0.05)
        auto_migrate()
        mtime2 = sentinel.stat().st_mtime
        assert mtime1 == mtime2  # File unchanged

    def test_removes_legacy_server_names(self, tmp_path, monkeypatch):
        """auto_migrate should remove legacy server names from tool configs."""
        from piia_engram.setup_wizard import auto_migrate

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

        # Create a fake tool config with a legacy name
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        config_path = config_dir / ".mcp.json"
        config_path.write_text(json.dumps({
            "mcpServers": {
                "piia-pkc": {"command": "python", "args": ["old.py"]},
                "engram": {"command": "python", "args": ["mcp_server.py"]},
            }
        }), encoding="utf-8")

        # Patch _tool_configs to point to our test config
        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"test": {"name": "Test", "config_paths": [config_path]}},
        )

        auto_migrate()

        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert "piia-pkc" not in config["mcpServers"]
        assert "engram" in config["mcpServers"]

        # Migration log should exist
        log_file = tmp_path / "migration.log"
        assert log_file.is_file()
        assert "piia-pkc" in log_file.read_text(encoding="utf-8")

    def test_migration_failure_doesnt_crash(self, tmp_path, monkeypatch):
        """auto_migrate should log warning on failure, not crash."""
        from piia_engram.setup_wizard import auto_migrate

        # Point to a dir that will cause issues — make __version__ import fail
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: 1 / 0,  # will raise if called
        )
        # But the version import happens first — if it succeeds, _tool_configs runs.
        # Either way, auto_migrate should not crash.
        auto_migrate()  # Should not raise


# ── run_setup tests ─────────────────────────────────────────────────


class TestRunSetup:
    def test_full_wizard_flow(self, tmp_path, monkeypatch, capsys):
        """run_setup should complete the full wizard flow with mocked inputs."""
        from piia_engram.setup_wizard import run_setup, _find_python, _find_mcp_server

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)

        python_path = _find_python()
        mcp_path = _find_mcp_server()

        # Mock inputs: language=1(zh), data_dir=default, configure tools=yes,
        # then seed onboarding (4 empty answers), privacy prefs (2 defaults)
        answers = iter([
            "1",   # language: zh
            "",    # data dir: default
            "y",   # configure tools: yes
            "",    # seed: role
            "",    # seed: tech_stack
            "",    # seed: language
            "",    # seed: no lessons
            "",    # privacy: reconcile default
            "",    # privacy: telemetry default
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))

        # Mock _detect_tools to return one fake tool
        fake_config = tmp_path / "fake_mcp.json"
        monkeypatch.setattr(
            "piia_engram.setup_wizard._detect_tools",
            lambda: [{"id": "test", "name": "TestTool", "config_path": fake_config}],
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._find_python",
            lambda: python_path or "/usr/bin/python3",
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._find_mcp_server",
            lambda: mcp_path or "/path/to/mcp_server.py",
        )

        run_setup()

        out = capsys.readouterr().out
        assert "PIIA Engram" in out
        assert "Step 1/3" in out
        assert "TestTool" in out

    def test_wizard_no_python_exits(self, tmp_path, monkeypatch, capsys):
        """run_setup should exit(1) if no Python found."""
        from piia_engram.setup_wizard import run_setup

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        answers = iter(["1"])  # language only
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: None)

        with pytest.raises(SystemExit) as exc_info:
            run_setup()
        assert exc_info.value.code == 1

    def test_wizard_no_mcp_server_exits(self, tmp_path, monkeypatch, capsys):
        """run_setup should exit(1) if no mcp_server.py found."""
        from piia_engram.setup_wizard import run_setup

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        answers = iter(["1"])  # language only
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: None)

        with pytest.raises(SystemExit) as exc_info:
            run_setup()
        assert exc_info.value.code == 1

    def test_wizard_no_tools_detected(self, tmp_path, monkeypatch, capsys):
        """run_setup should continue gracefully when no AI tools detected."""
        from piia_engram.setup_wizard import run_setup, _find_python, _find_mcp_server

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)

        answers = iter([
            "2",   # language: English
            "",    # data dir: default
            "",    # seed: role
            "",    # seed: tech_stack
            "",    # seed: language
            "",    # seed: no lessons
            "",    # privacy: reconcile
            "",    # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr("piia_engram.setup_wizard._detect_tools", lambda: [])
        monkeypatch.setattr(
            "piia_engram.setup_wizard._find_python",
            lambda: _find_python() or "/usr/bin/python3",
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._find_mcp_server",
            lambda: _find_mcp_server() or "/path/to/mcp_server.py",
        )

        run_setup()

        out = capsys.readouterr().out
        assert "No AI tools detected" in out

    def test_wizard_custom_data_dir(self, tmp_path, monkeypatch, capsys):
        """run_setup should accept custom data directory."""
        from piia_engram.setup_wizard import run_setup

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)

        custom_dir = str(tmp_path / "custom_data")
        answers = iter([
            "1",         # language: zh
            custom_dir,  # custom data dir
            "",          # seed: role
            "",          # seed: tech_stack
            "",          # seed: language
            "",          # seed: no lessons
            "",          # privacy: reconcile
            "",          # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr("piia_engram.setup_wizard._detect_tools", lambda: [])
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: "/path/to/mcp_server.py")

        run_setup()

        out = capsys.readouterr().out
        assert custom_dir in out


# ── main() CLI entry tests ─────────────────────────────────────────


class TestMainCLI:
    def test_main_unknown_command(self, monkeypatch, capsys):
        """Unknown command should print usage and exit(0)."""
        from piia_engram.setup_wizard import main
        monkeypatch.setattr("sys.argv", ["engram", "bogus"])

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_main_doctor_dispatches(self, tmp_path, monkeypatch, capsys):
        """main() with 'doctor' should call run_doctor."""
        from piia_engram.setup_wizard import main
        monkeypatch.setattr("sys.argv", ["engram", "doctor"])
        # Patch _tool_configs to avoid scanning real filesystem
        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {},
        )

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0  # healthy = 0

    def test_main_telemetry_dispatches(self, tmp_path, monkeypatch, capsys):
        """main() with 'telemetry' should call _run_telemetry_cli."""
        from piia_engram.setup_wizard import main
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.setattr("sys.argv", ["engram", "telemetry", "status"])

        main()
        out = capsys.readouterr().out
        assert "OFF" in out

    def test_main_privacy_dispatches(self, tmp_path, monkeypatch, capsys):
        """main() with 'privacy' should call _run_privacy_report."""
        from piia_engram.setup_wizard import main
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.setattr("sys.argv", ["engram", "privacy"])

        main()
        out = capsys.readouterr().out
        assert "Privacy Report" in out


# ── Telemetry CLI edge cases ───────────────────────────────────────


class TestTelemetryCLIExtended:
    def test_show_payload_alias(self, tmp_path, monkeypatch, capsys):
        """--show-payload should work as alias for preview."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_telemetry_cli(["--show-payload"])
        out = capsys.readouterr().out
        assert "schema" in out
        assert "tool_calls" in out

    def test_enable_alias(self, tmp_path, monkeypatch, capsys):
        """'enable' should work same as 'on'."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_telemetry_cli(["enable"])
        capsys.readouterr()

        _run_telemetry_cli(["status"])
        out = capsys.readouterr().out
        assert "ON" in out

    def test_disable_alias(self, tmp_path, monkeypatch, capsys):
        """'disable' should work same as 'off'."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_telemetry_cli(["on"])
        _run_telemetry_cli(["disable"])
        capsys.readouterr()

        _run_telemetry_cli(["status"])
        out = capsys.readouterr().out
        assert "OFF" in out

    def test_empty_args_defaults_to_status(self, tmp_path, monkeypatch, capsys):
        """No subcommand should default to status."""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)

        _run_telemetry_cli([])
        out = capsys.readouterr().out
        assert "OFF" in out or "ON" in out


# ── Doctor --fix tests ─────────────────────────────────────────────


class TestDoctorVerifiedCommunity:
    """doctor 应区分已验证和社区级工具。"""

    def test_verified_label_shown(self, tmp_path, monkeypatch, capsys):
        """Verified tools should appear under 'Verified' section."""
        from piia_engram.setup_wizard import run_doctor, _find_python, _find_mcp_server

        python_path = _find_python()
        mcp_path = _find_mcp_server()
        if not python_path or not mcp_path:
            pytest.skip("Cannot find Python or mcp_server.py")

        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        config_path = config_dir / ".mcp.json"
        _write_mcp_config = __import__("piia_engram.setup_wizard", fromlist=["_write_mcp_config"])._write_mcp_config
        _write_mcp_config(config_path, python_path, mcp_path)

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"claude_code": {"name": "Claude Code", "config_paths": [config_path], "verified": True}},
        )

        run_doctor(fix=False)
        out = capsys.readouterr().out
        assert "Verified" in out
        assert "Claude Code" in out

    def test_community_label_shown(self, tmp_path, monkeypatch, capsys):
        """Community tools should appear under 'Community-supported' section."""
        from piia_engram.setup_wizard import run_doctor

        config_dir = tmp_path / ".windsurf"
        config_dir.mkdir()
        config_path = config_dir / "mcp_config.json"
        config_path.write_text(json.dumps({
            "mcpServers": {
                "engram": {"command": "python", "args": ["-m", "piia_engram.mcp_server"]},
            }
        }), encoding="utf-8")

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"windsurf": {"name": "Windsurf", "config_paths": [config_path], "verified": False}},
        )

        run_doctor(fix=False)
        out = capsys.readouterr().out
        assert "Community-supported" in out
        assert "Windsurf" in out

    def test_mixed_verified_and_community(self, tmp_path, monkeypatch, capsys):
        """Both sections should appear when both types are present."""
        from piia_engram.setup_wizard import run_doctor

        # Verified tool
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        claude_path = claude_dir / ".mcp.json"
        claude_path.write_text(json.dumps({
            "mcpServers": {"engram": {"command": "python", "args": ["-m", "piia_engram.mcp_server"]}},
        }), encoding="utf-8")

        # Community tool (installed but not configured)
        wind_dir = tmp_path / ".windsurf"
        wind_dir.mkdir()
        wind_path = wind_dir / "mcp_config.json"

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {
                "claude_code": {"name": "Claude Code", "config_paths": [claude_path], "verified": True},
                "windsurf": {"name": "Windsurf", "config_paths": [wind_path], "verified": False},
            },
        )

        run_doctor(fix=False)
        out = capsys.readouterr().out
        assert "Verified" in out
        assert "Community-supported" in out
        assert "Claude Code" in out
        assert "Windsurf" in out


class TestDoctorFix:
    def test_doctor_fix_repairs_invalid_path(self, tmp_path, monkeypatch, capsys):
        """doctor --fix should repair config with invalid paths."""
        from piia_engram.setup_wizard import run_doctor, _find_python, _find_mcp_server

        python_path = _find_python()
        mcp_path = _find_mcp_server()
        if not python_path or not mcp_path:
            pytest.skip("Cannot find Python or mcp_server.py")

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
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"test": {"name": "Test", "config_paths": [config_path], "verified": True}},
        )

        result = run_doctor(fix=True)
        out = capsys.readouterr().out
        assert "[fixed]" in out

    def test_doctor_fix_no_python_fails(self, tmp_path, monkeypatch, capsys):
        """doctor --fix without Python should report error."""
        from piia_engram.setup_wizard import run_doctor

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
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"test": {"name": "Test", "config_paths": [config_path], "verified": True}},
        )
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: None)

        result = run_doctor(fix=True)
        out = capsys.readouterr().out
        assert "Cannot auto-fix" in out
        assert result > 0


class TestDoctorLaunchProbeIntegration:
    def test_doctor_reports_probe_failure(self, tmp_path, monkeypatch, capsys):
        from piia_engram.setup_wizard import run_doctor

        config_path = tmp_path / ".claude" / ".mcp.json"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps({
            "mcpServers": {
                "engram": {"command": "piia-engram-mcp", "args": []},
            }
        }), encoding="utf-8")

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {
                "claude_code": {
                    "name": "Claude Code",
                    "config_paths": [config_path],
                    "verified": True,
                }
            },
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._probe_mcp_entry",
            lambda entry: "MCP launch probe exited with code 2: bad option",
        )

        result = run_doctor(fix=False)
        out = capsys.readouterr().out

        assert result > 0
        assert "MCP launch probe exited with code 2" in out

    def test_doctor_skips_probe_for_legacy_script_path(
        self, tmp_path, monkeypatch, capsys
    ):
        from piia_engram.setup_wizard import run_doctor

        config_path = tmp_path / ".claude" / ".mcp.json"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps({
            "mcpServers": {
                "engram": {"command": "python", "args": ["/tmp/mcp_server.py"]},
            }
        }), encoding="utf-8")

        called = False

        def fake_probe(entry):
            nonlocal called
            called = True
            return None

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {
                "claude_code": {
                    "name": "Claude Code",
                    "config_paths": [config_path],
                    "verified": True,
                }
            },
        )
        monkeypatch.setattr("piia_engram.setup_wizard._probe_mcp_entry", fake_probe)

        result = run_doctor(fix=False)
        out = capsys.readouterr().out

        assert result > 0
        assert called is False
        assert "direct mcp_server.py path" in out


# ── 覆盖率补充测试 ──────────────────────────────────────────────────


class TestClassifyLineEdgeCases:
    """_classify_line 边缘情况。"""

    def test_both_user_and_project_global(self):
        """同时含用户和项目关键词时，global scope 应返回 user。"""
        # "language" is user keyword, "test" is project keyword
        result = _classify_line("- use English language for all test cases", "global")
        assert result == "user"

    def test_both_user_and_project_project(self):
        """同时含用户和项目关键词时，project scope 应返回 project。"""
        result = _classify_line("- use English language for all test cases", "project")
        assert result == "project"


class TestImportWithSplit:
    """_import_with_split 分流导入测试。"""

    def test_language_detection_chinese(self, tmp_path):
        """中文语言偏好应写入 profile。"""
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        rule_files = [{
            "path": tmp_path / "rules.md",
            "scope": "global",
            "lines": ["所有沟通使用中文"],
        }]
        result = _import_with_split(rule_files, engram)
        profile = engram.get_profile()
        assert profile.get("language") == "中文"

    def test_language_detection_english(self, tmp_path):
        """English 语言偏好应写入 profile。"""
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        rule_files = [{
            "path": tmp_path / "rules.md",
            "scope": "global",
            "lines": ["Use English language for all communication"],
        }]
        result = _import_with_split(rule_files, engram)
        profile = engram.get_profile()
        assert profile.get("language") == "English"


class TestReadRuleFile:
    """_read_rule_file 边缘情况。"""

    def test_permission_error(self, tmp_path, monkeypatch):
        """PermissionError 应返回 None。"""
        path = tmp_path / "rules.md"
        path.write_text("# Header\ncontent line 1\ncontent line 2\n", encoding="utf-8")

        from unittest.mock import patch
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            assert _read_rule_file(path, "global") is None

    def test_too_few_content_lines(self, tmp_path):
        """内容行少于 2 行时返回 None。"""
        path = tmp_path / "rules.md"
        path.write_text("# Only a header\n", encoding="utf-8")
        assert _read_rule_file(path, "global") is None


class TestReadMcpConfig:
    """_read_mcp_config 异常测试。"""

    def test_corrupt_json(self, tmp_path):
        """损坏的 JSON 应返回空结构。"""
        path = tmp_path / "config.json"
        path.write_text("not json!", encoding="utf-8")
        assert _read_mcp_config(path) == {}


class TestWriteMcpConfig:
    """_write_mcp_config 旧版名称清理。"""

    def test_removes_legacy_servers(self, tmp_path, capsys):
        """应清理旧版 server 名称。"""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "mcpServers": {
                "piia-pkc": {"command": "old"},
                "piia_pkc": {"command": "old"},
                "other": {"command": "keep"},
            }
        }), encoding="utf-8")

        _write_mcp_config(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")

        config = json.loads(config_path.read_text(encoding="utf-8"))
        # Legacy names removed
        assert "piia-pkc" not in config["mcpServers"]
        assert "piia_pkc" not in config["mcpServers"]
        # New entry added
        assert "engram" in config["mcpServers"]
        # Migration message printed
        out = capsys.readouterr().out
        assert "migrated" in out


class TestMcpEntryLaunchProbe:
    def test_uvx_entry_is_recommended_and_probeable(self):
        from piia_engram.setup_wizard import _classify_engram_entry

        entry = {
            "command": "uvx",
            "args": ["--from", "piia-engram", "piia-engram-mcp"],
        }

        result = _classify_engram_entry(entry)

        assert result["severity"] == "ok"
        assert result["style"] == "recommended-uvx"
        assert result["probe_argv"] == [
            "uvx", "--from", "piia-engram", "piia-engram-mcp", "--help",
        ]

    def test_console_script_entry_is_recommended_and_probeable(self):
        from piia_engram.setup_wizard import _classify_engram_entry

        result = _classify_engram_entry({"command": "piia-engram-mcp", "args": []})

        assert result["severity"] == "ok"
        assert result["style"] == "recommended-console-script"
        assert result["probe_argv"] == ["piia-engram-mcp", "--help"]

    def test_python_module_entry_is_compatible_and_probeable(self):
        from piia_engram.setup_wizard import _classify_engram_entry

        result = _classify_engram_entry({
            "command": "/usr/bin/python3",
            "args": ["-m", "piia_engram.mcp_server"],
        })

        assert result["severity"] == "ok"
        assert result["style"] == "compatible-python-module"
        assert result["probe_argv"] == [
            "/usr/bin/python3", "-m", "piia_engram.mcp_server", "--help",
        ]

    def test_probe_success_reports_ok(self, monkeypatch):
        from piia_engram.setup_wizard import _probe_mcp_entry

        calls = []

        class Result:
            returncode = 0
            stdout = "Engram MCP Server"
            stderr = ""

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return Result()

        monkeypatch.setattr("piia_engram.setup_wizard.subprocess.run", fake_run)

        result = _probe_mcp_entry({"command": "piia-engram-mcp", "args": []})

        assert result is None
        assert calls[0][0] == ["piia-engram-mcp", "--help"]
        assert calls[0][1]["timeout"] == 5

    def test_probe_nonzero_reports_issue(self, monkeypatch):
        from piia_engram.setup_wizard import _probe_mcp_entry

        class Result:
            returncode = 2
            stdout = ""
            stderr = "bad option"

        monkeypatch.setattr(
            "piia_engram.setup_wizard.subprocess.run",
            lambda *a, **kw: Result(),
        )

        issue = _probe_mcp_entry({"command": "piia-engram-mcp", "args": []})

        assert issue is not None
        assert "exited with code 2" in issue

    def test_probe_timeout_reports_issue(self, monkeypatch):
        from piia_engram.setup_wizard import _probe_mcp_entry

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

        monkeypatch.setattr("piia_engram.setup_wizard.subprocess.run", fake_run)

        issue = _probe_mcp_entry({"command": "piia-engram-mcp", "args": []})

        assert issue == "MCP launch probe timed out after 5s"


class TestChoiceFunction:
    """_choice 数字菜单选择测试。"""

    def test_custom_input_option(self, monkeypatch):
        """选择"其他"时应提示自行输入。"""
        inputs = iter(["3", "自定义值"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        result = _choice("选择语言", ["中文", "English"])
        assert result == "自定义值"

    def test_text_input_instead_of_number(self, monkeypatch):
        """直接输入文本而非数字也应接受。"""
        monkeypatch.setattr("builtins.input", lambda _: "日本語")
        result = _choice("选择语言", ["中文", "English"])
        assert result == "日本語"

    def test_invalid_number_returns_empty(self, monkeypatch):
        """无效数字应返回空字符串。"""
        monkeypatch.setattr("builtins.input", lambda _: "99")
        result = _choice("选择", ["A", "B"], allow_custom=False)
        assert result == ""

    def test_skip_returns_empty(self, monkeypatch):
        """输入 0 应跳过。"""
        monkeypatch.setattr("builtins.input", lambda _: "0")
        result = _choice("选择", ["A", "B"])
        assert result == ""


class TestConfigureUtf8:
    """_configure_utf8_stdio 测试。"""

    def test_reconfigure_called(self, monkeypatch):
        """应调用 stdout/stderr 的 reconfigure 方法。"""
        calls = []

        class MockStream:
            def reconfigure(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr("sys.stdout", MockStream())
        monkeypatch.setattr("sys.stderr", MockStream())
        _configure_utf8_stdio()
        assert len(calls) == 2
        assert calls[0]["encoding"] == "utf-8"

    def test_reconfigure_error_ignored(self, monkeypatch):
        """reconfigure 异常应被忽略。"""
        class BadStream:
            def reconfigure(self, **kwargs):
                raise TypeError("bad")

        monkeypatch.setattr("sys.stdout", BadStream())
        monkeypatch.setattr("sys.stderr", BadStream())
        _configure_utf8_stdio()  # Should not raise


class TestMainCLIRouting:
    """main() CLI 路由补充测试。"""

    def test_main_stats_default(self, monkeypatch, capsys):
        """main() 处理 'stats' 子命令。"""
        monkeypatch.setattr("sys.argv", ["engram", "stats"])
        from unittest.mock import patch
        with (
            patch("piia_engram.stats._gh", return_value=None),
            patch("piia_engram.stats._pypi_recent", return_value=None),
        ):
            main()
        out = capsys.readouterr().out
        assert "Engram" in out and ("Stats" in out or "数据概览" in out)

    def test_main_stats_log(self, tmp_path, monkeypatch, capsys):
        """main() 处理 'stats --log' 子命令。"""
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setattr("sys.argv", ["engram", "stats", "--log"])
        from unittest.mock import patch
        with (
            patch("piia_engram.stats._gh", return_value=None),
            patch("piia_engram.stats._pypi_recent", return_value=None),
        ):
            main()
        assert (tmp_path / "stats.log").is_file()

    def test_main_setup_advanced(self, monkeypatch):
        """main() 处理 'setup --advanced' 应传递 advanced=True。"""
        monkeypatch.setattr("sys.argv", ["engram", "setup", "--advanced"])
        called_with = {}
        from unittest.mock import patch
        with patch("piia_engram.setup_wizard.run_setup") as mock_setup:
            main()
            mock_setup.assert_called_once_with(advanced=True)


class TestScanRuleFilesGlobs:
    """_scan_rule_files 全局文件扫描。"""

    def test_cursor_rules_dir(self, tmp_path, monkeypatch):
        """应扫描 .cursor/rules/*.mdc 文件。"""
        rules_dir = tmp_path / ".cursor" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "style.mdc").write_text(
            "# Style\nAlways use 4 spaces\nNever use tabs\nKeep lines short\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        found = _scan_rule_files(tmp_path)
        paths = [str(f["path"]) for f in found]
        assert any("style.mdc" in p for p in paths)

    def test_claude_project_claude_md(self, tmp_path, monkeypatch):
        """应扫描 .claude/projects/*/CLAUDE.md。"""
        proj_dir = tmp_path / ".claude" / "projects" / "test-proj"
        proj_dir.mkdir(parents=True)
        (proj_dir / "CLAUDE.md").write_text(
            "# Project Rules\nUse pytest for testing\nAlways run linter\nCommit messages in English\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        found = _scan_rule_files(tmp_path)
        paths = [str(f["path"]) for f in found]
        assert any("CLAUDE.md" in p for p in paths)


class TestSetupIsolation:
    """Tests must never modify the real ~/.engram/ profile."""

    def test_setup_with_engram_dir_does_not_touch_real_profile(
        self, tmp_path, monkeypatch, capsys
    ):
        """run_setup with ENGRAM_DIR should only write to the custom dir."""
        from piia_engram.setup_wizard import run_setup

        real_profile = Path.home() / ".engram" / "identity" / "profile.json"
        before = None
        if real_profile.exists():
            before = real_profile.read_text(encoding="utf-8")

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)

        answers = iter([
            "1",    # language
            "",     # role
            "",     # tech_stack
            "",     # language pref
            "",     # no lessons
            "",     # privacy
            "",     # telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr("piia_engram.setup_wizard._detect_tools", lambda: [])
        monkeypatch.setattr(
            "piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3"
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._find_mcp_server",
            lambda: "/path/to/mcp_server.py",
        )

        run_setup()

        # Real profile must be unchanged
        if before is not None:
            after = real_profile.read_text(encoding="utf-8")
            assert after == before, "run_setup modified the real ~/.engram/identity/profile.json!"


# ── Instruction injection tests ────────────────────────────────────


class TestInstructionInjection:
    def test_inject_claude_code_creates_file(self, tmp_path, monkeypatch):
        """_inject_instruction_snippet should create CLAUDE.md with marker."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_MARKER_END,
            _INSTRUCTION_SNIPPETS,
        )
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: tmp_path / "CLAUDE.md",
        )
        result = _inject_instruction_snippet("claude_code", lang="zh")
        assert result is not None
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert _INSTRUCTION_MARKER in content
        assert _INSTRUCTION_MARKER_END in content
        assert "get_user_context" in content
        assert "add_lesson" in content

    def test_inject_appends_to_existing(self, tmp_path, monkeypatch):
        """Should append to existing CLAUDE.md without overwriting."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_SNIPPETS,
        )
        target = tmp_path / "CLAUDE.md"
        target.write_text("# My existing rules\n\nDo not break things.\n", encoding="utf-8")

        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: target,
        )
        _inject_instruction_snippet("claude_code", lang="en")
        content = target.read_text(encoding="utf-8")
        assert "My existing rules" in content
        assert "Do not break things" in content
        assert _INSTRUCTION_MARKER in content

    def test_inject_updates_existing_snippet(self, tmp_path, monkeypatch):
        """Calling inject twice should replace, not duplicate."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_SNIPPETS,
        )
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Existing\n", encoding="utf-8")

        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: target,
        )
        _inject_instruction_snippet("claude_code", lang="zh")
        _inject_instruction_snippet("claude_code", lang="en")
        content = target.read_text(encoding="utf-8")
        # Should have exactly one marker pair
        assert content.count(_INSTRUCTION_MARKER) == 1

    def test_inject_cursor_creates_mdc(self, tmp_path, monkeypatch):
        """Cursor injection should create a .mdc file."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_SNIPPETS,
        )
        mdc_path = tmp_path / "rules" / "engram.mdc"
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["cursor"],
            "path_fn",
            lambda _home: mdc_path,
        )
        result = _inject_instruction_snippet("cursor", lang="zh")
        assert result is not None
        content = mdc_path.read_text(encoding="utf-8")
        assert "alwaysApply: true" in content
        assert "get_user_context" in content

    def test_inject_codex_creates_agents_md(self, tmp_path, monkeypatch):
        """Codex injection should create AGENTS.md with marker."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_SNIPPETS,
        )
        agents_path = tmp_path / "AGENTS.md"
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["codex"],
            "path_fn",
            lambda _home: agents_path,
        )
        result = _inject_instruction_snippet("codex", lang="en")
        assert result is not None
        content = agents_path.read_text(encoding="utf-8")
        assert _INSTRUCTION_MARKER in content
        assert "wrap_up_session" in content

    def test_inject_unknown_tool_returns_none(self):
        """Unknown tool_id should return None."""
        from piia_engram.setup_wizard import _inject_instruction_snippet
        assert _inject_instruction_snippet("unknown_tool") is None

    def test_remove_claude_code_snippet(self, tmp_path, monkeypatch):
        """_remove_instruction_snippet should cleanly remove injected section."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _remove_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_SNIPPETS,
        )
        target = tmp_path / "CLAUDE.md"
        target.write_text("# My rules\n\nKeep these.\n", encoding="utf-8")

        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: target,
        )
        _inject_instruction_snippet("claude_code", lang="zh")
        assert _INSTRUCTION_MARKER in target.read_text(encoding="utf-8")

        removed = _remove_instruction_snippet("claude_code")
        assert removed is True
        content = target.read_text(encoding="utf-8")
        assert _INSTRUCTION_MARKER not in content
        assert "My rules" in content
        assert "Keep these" in content

    def test_remove_cursor_snippet(self, tmp_path, monkeypatch):
        """Removing cursor snippet should delete the .mdc file."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _remove_instruction_snippet,
            _INSTRUCTION_SNIPPETS,
        )
        mdc_path = tmp_path / "engram.mdc"
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["cursor"],
            "path_fn",
            lambda _home: mdc_path,
        )
        _inject_instruction_snippet("cursor")
        assert mdc_path.is_file()

        removed = _remove_instruction_snippet("cursor")
        assert removed is True
        assert not mdc_path.is_file()

    def test_remove_nonexistent_returns_false(self, tmp_path, monkeypatch):
        """Removing when no snippet exists should return False."""
        from piia_engram.setup_wizard import (
            _remove_instruction_snippet,
            _INSTRUCTION_SNIPPETS,
        )
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: tmp_path / "nonexistent.md",
        )
        assert _remove_instruction_snippet("claude_code") is False

    def test_inject_en_variant(self, tmp_path, monkeypatch):
        """English snippet should contain English text."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_SNIPPETS,
        )
        target = tmp_path / "CLAUDE.md"
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: target,
        )
        _inject_instruction_snippet("claude_code", lang="en")
        content = target.read_text(encoding="utf-8")
        assert "Memory Layer" in content
        assert "conversation" in content.lower()


# ---------------------------------------------------------------------------
# v3.31 P0: cross-tool injection partial → real
# ---------------------------------------------------------------------------


class TestCrossToolInjectionV331:
    """v3.31 P0: every snippet must instruct AI to call get_resume_brief
    at session start so Cursor / Codex / Windsurf get the same auto-resume
    behavior Claude Code gets from the SessionStart hook."""

    def test_all_snippets_contain_resume_brief_directive(self):
        """Every snippet (zh+en) must mention get_resume_brief — this is
        the partial→real fidelity guarantee for v3.31 P0."""
        from piia_engram.setup_wizard import (
            _INSTRUCTION_SNIPPETS,
            _SNIPPET_FRESHNESS_TOKEN,
        )
        assert _SNIPPET_FRESHNESS_TOKEN == "get_resume_brief"
        for tool_id, info in _INSTRUCTION_SNIPPETS.items():
            for lang_key in ("snippet_zh", "snippet_en"):
                assert _SNIPPET_FRESHNESS_TOKEN in info[lang_key], (
                    f"{tool_id}/{lang_key} missing {_SNIPPET_FRESHNESS_TOKEN!r}"
                )

    def test_windsurf_snippet_registered(self):
        """v3.31 P0 added Windsurf to the cross-tool dict."""
        from piia_engram.setup_wizard import _INSTRUCTION_SNIPPETS
        assert "windsurf" in _INSTRUCTION_SNIPPETS
        info = _INSTRUCTION_SNIPPETS["windsurf"]
        assert "snippet_zh" in info
        assert "snippet_en" in info
        assert callable(info["path_fn"])

    def test_inject_windsurf_creates_file(self, tmp_path, monkeypatch):
        """Windsurf inject should write an .md file with marker."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_SNIPPETS,
        )
        target = tmp_path / "memories" / "engram.md"
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["windsurf"],
            "path_fn",
            lambda _home: target,
        )
        result = _inject_instruction_snippet("windsurf", lang="en")
        assert result is not None
        content = target.read_text(encoding="utf-8")
        assert _INSTRUCTION_MARKER in content
        assert "get_resume_brief" in content
        assert "Memory Layer" in content

    def test_inject_windsurf_replaces_existing_block(self, tmp_path, monkeypatch):
        """Re-injecting Windsurf must NOT duplicate the marker block."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_SNIPPETS,
        )
        target = tmp_path / "engram.md"
        target.write_text("# pre-existing windsurf rule\n", encoding="utf-8")
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["windsurf"],
            "path_fn",
            lambda _home: target,
        )
        _inject_instruction_snippet("windsurf", lang="zh")
        _inject_instruction_snippet("windsurf", lang="en")
        content = target.read_text(encoding="utf-8")
        assert content.count(_INSTRUCTION_MARKER) == 1
        assert "pre-existing windsurf rule" in content

    def test_marker_bumped_to_v2(self):
        """Marker version must include v=2 so doctor can detect v=1
        files that lack the get_resume_brief directive."""
        from piia_engram.setup_wizard import _INSTRUCTION_MARKER
        assert "v=2" in _INSTRUCTION_MARKER

    def test_claude_code_snippet_has_resume_brief(self):
        """Claude Code snippet should ALSO mention get_resume_brief even
        though SessionStart hook injects it automatically — this keeps
        the directive visible in CLAUDE.md so AI knows to call it on
        Cursor/Codex/Windsurf when toggling tools."""
        from piia_engram.setup_wizard import _INSTRUCTION_SNIPPETS
        cc = _INSTRUCTION_SNIPPETS["claude_code"]
        assert "get_resume_brief" in cc["snippet_zh"]
        assert "get_resume_brief" in cc["snippet_en"]


# ---------------------------------------------------------------------------
# _save_setup_report
# ---------------------------------------------------------------------------


class TestSaveSetupReport:
    """_save_setup_report 生成 JSONL 激活漏斗报告。"""

    def test_creates_valid_jsonl(self, tmp_path):
        """Should create a valid JSONL file with all required fields."""
        tools = [{"name": "Claude Code", "id": "claude_code"}]
        _save_setup_report(str(tmp_path), tools, ["Claude Code"], [])

        report_path = tmp_path / "setup_report.jsonl"
        assert report_path.is_file()
        line = report_path.read_text(encoding="utf-8").strip()
        report = json.loads(line)
        assert "timestamp" in report
        assert "version" in report
        assert "os" in report
        assert "python" in report
        assert report["tools_detected"] == ["Claude Code"]
        assert report["tools_configured"] == ["Claude Code"]
        assert report["tools_failed"] == []
        assert report["status"] == "success"

    def test_no_tools_writes_empty_lists(self, tmp_path):
        """When no tools detected, lists should be empty, status success."""
        _save_setup_report(str(tmp_path), [], [], [])

        report_path = tmp_path / "setup_report.jsonl"
        assert report_path.is_file()
        report = json.loads(report_path.read_text(encoding="utf-8").strip())
        assert report["tools_detected"] == []
        assert report["tools_configured"] == []
        assert report["tools_failed"] == []
        assert report["status"] == "success"

    def test_partial_failure_status(self, tmp_path):
        """When some tools fail, status should be 'partial'."""
        tools = [
            {"name": "Claude Code", "id": "claude_code"},
            {"name": "Cursor", "id": "cursor"},
        ]
        _save_setup_report(str(tmp_path), tools, ["Claude Code"], ["Cursor (err)"])

        report = json.loads(
            (tmp_path / "setup_report.jsonl").read_text(encoding="utf-8").strip()
        )
        assert report["status"] == "partial"
        assert report["tools_configured"] == ["Claude Code"]
        assert report["tools_failed"] == ["Cursor (err)"]

    def test_appends_multiple_runs(self, tmp_path):
        """Multiple calls should append lines, not overwrite."""
        _save_setup_report(str(tmp_path), [], [], [])
        _save_setup_report(str(tmp_path), [{"name": "X"}], ["X"], [])

        lines = (tmp_path / "setup_report.jsonl").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        # Both lines should be valid JSON
        for line in lines:
            json.loads(line)

    def test_creates_parent_dirs(self, tmp_path):
        """Should create nested parent directories if needed."""
        nested = str(tmp_path / "a" / "b" / "c")
        _save_setup_report(nested, [], [], [])
        assert (Path(nested) / "setup_report.jsonl").is_file()

    def test_never_raises(self, tmp_path, monkeypatch):
        """Should silently swallow errors, never crashing setup."""
        # Make json.dumps raise to simulate failure
        monkeypatch.setattr("piia_engram.setup_wizard.json.dumps", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        # Should not raise
        _save_setup_report(str(tmp_path), [], [], [])


# ---------------------------------------------------------------------------
# _inject_claude_code_hook
# ---------------------------------------------------------------------------


class TestInjectClaudeCodeHook:
    """Claude Code Stop hook 注册。"""

    def test_creates_settings_with_hook(self, tmp_path, monkeypatch):
        """Should create settings.json with Stop hook when no file exists."""
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        # Create a fake hook script
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        assert (scripts_dir / "auto_save_on_stop.py").is_file()

        result = _inject_claude_code_hook(sys.executable)
        assert result is not None

        settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
        hooks = settings["hooks"]["Stop"][0]["hooks"]
        assert any("auto_save_on_stop" in h.get("command", "") for h in hooks)

    def test_appends_to_existing_hooks(self, tmp_path, monkeypatch):
        """Should append to existing Stop hooks without overwriting."""
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "echo existing", "timeout": 10}]}]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        result = _inject_claude_code_hook(sys.executable)
        assert result is not None

        settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
        hooks = settings["hooks"]["Stop"][0]["hooks"]
        assert len(hooks) == 2
        assert hooks[0]["command"] == "echo existing"
        assert "auto_save_on_stop" in hooks[1]["command"]

    def test_idempotent_skip_if_exists(self, tmp_path, monkeypatch):
        """Should return None if engram hook already registered."""
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "python auto_save_on_stop.py"}]}]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        result = _inject_claude_code_hook(sys.executable)
        assert result is None  # Already registered

    def test_preserves_other_settings(self, tmp_path, monkeypatch):
        """Should preserve non-hook settings in settings.json."""
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {"statusLine": {"type": "text"}, "foo": "bar"}
        (claude_dir / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        _inject_claude_code_hook(sys.executable)

        settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
        assert settings["statusLine"] == {"type": "text"}
        assert settings["foo"] == "bar"


# ---------------------------------------------------------------------------
# v3.30 H1+H2+H3 — hook command construction and SessionStart sync.
# ---------------------------------------------------------------------------


class TestEngramHookCommandConstruction:
    """Hook command builder and event registration (v3.30)."""

    def test_hook_command_uses_python_dash_m_module_form(self):
        """No more script-path quoting: hooks ride ``python -m`` so the
        wheel can ship them inside the package (H1)."""
        from piia_engram.setup_wizard import (
            _build_engram_hook_command, _HOOK_MODULES,
        )
        cmd = _build_engram_hook_command(
            r"C:\Python312\python.exe",
            module=_HOOK_MODULES["auto_save_on_stop"],
        )
        assert "-m" in cmd
        assert "piia_engram.hooks.auto_save_on_stop" in cmd
        # Script-path style must not leak through.
        assert "auto_save_on_stop.py" not in cmd

    def test_hook_command_quotes_python_path_with_spaces(self):
        """H2: the Windows ``Program Files`` path must survive shell
        parsing as a single argument."""
        from piia_engram.setup_wizard import (
            _build_engram_hook_command, _HOOK_MODULES,
        )
        cmd = _build_engram_hook_command(
            r"C:\Program Files\Python312\python.exe",
            module=_HOOK_MODULES["auto_save_on_stop"],
        )
        assert cmd.startswith('"C:\\Program Files\\Python312\\python.exe"')
        # Critically NOT the legacy double-escaped form.
        assert "\\\\Program" not in cmd

    def test_hook_command_carries_env_via_argv(self):
        """H2: env hints must travel as ``--env KEY=VAL`` argv so they
        work identically on Windows cmd, PowerShell, and POSIX shells
        (the legacy inline ``KEY=VAL prog`` prefix doesn't work on
        Windows)."""
        from piia_engram.setup_wizard import (
            _build_engram_hook_command, _HOOK_MODULES,
        )
        cmd = _build_engram_hook_command(
            "/usr/bin/python3",
            module=_HOOK_MODULES["auto_save_on_stop"],
            extra_env={
                "ENGRAM_MIN_TURNS_TO_FLUSH": "5",
                "CLAUDE_INVOKED_BY": "engram_precompact",
            },
        )
        assert "--env" in cmd
        # Values without shell-sensitive chars are unquoted (H2 fix);
        # values with spaces/special chars would be quoted.
        assert "ENGRAM_MIN_TURNS_TO_FLUSH=5" in cmd
        assert "CLAUDE_INVOKED_BY=engram_precompact" in cmd
        # No POSIX-only inline env prefix.
        assert not cmd.startswith("ENGRAM_MIN_TURNS")

    def test_quote_for_shell_skips_clean_paths(self):
        """H2 fix: paths without shell-sensitive chars are unquoted,
        making them work in both cmd.exe and PowerShell."""
        from piia_engram.setup_wizard import _quote_for_shell
        # No spaces → unquoted
        assert _quote_for_shell("/usr/bin/python3") == "/usr/bin/python3"
        assert _quote_for_shell(r"E:\codex\python.exe") == r"E:\codex\python.exe"
        # Spaces → quoted (cmd.exe style)
        assert _quote_for_shell(r"C:\Program Files\python.exe") == r'"C:\Program Files\python.exe"'
        # Empty → empty quotes
        assert _quote_for_shell("") == '""'
        # Special chars → quoted
        assert _quote_for_shell("path&name") == '"path&name"'

    def test_sessionstart_hook_registered_synchronously(self, tmp_path, monkeypatch):
        """H3: SessionStart must be a synchronous hook — otherwise the
        first user turn ships before the resume brief is written and
        mechanism (6) silently degrades."""
        import sys as _sys
        from piia_engram.setup_wizard import (
            _inject_claude_code_sessionstart_hook,
        )
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir()

        result = _inject_claude_code_sessionstart_hook(_sys.executable)
        assert result is not None

        settings = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        hooks = settings["hooks"]["SessionStart"][0]["hooks"]
        engram_hook = [
            h for h in hooks
            if "piia_engram.hooks.auto_inject_resume_brief" in h.get("command", "")
        ]
        assert len(engram_hook) == 1
        # Either no async key or async=False; never async=True for
        # SessionStart (that's the H3 bug).
        assert engram_hook[0].get("async") is not True, (
            "SessionStart hook is marked async — additionalContext "
            "may not land before the first user turn"
        )

    def test_stop_hook_registered_async(self, tmp_path, monkeypatch):
        """Counterpart to test_sessionstart: Stop is fire-and-forget."""
        import sys as _sys
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir()
        result = _inject_claude_code_hook(_sys.executable)
        assert result is not None
        settings = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        hook = settings["hooks"]["Stop"][0]["hooks"][0]
        assert hook.get("async") is True

    def test_postcompact_hook_registered_async(self, tmp_path, monkeypatch):
        """R4: PostCompact is fire-and-forget (async=True)."""
        import sys as _sys
        from piia_engram.setup_wizard import _inject_claude_code_postcompact_hook
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir()

        result = _inject_claude_code_postcompact_hook(_sys.executable)
        assert result is not None

        settings = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        hooks = settings["hooks"]["PostCompact"][0]["hooks"]
        engram_hook = [
            h for h in hooks
            if "auto_absorb_compact" in h.get("command", "")
        ]
        assert len(engram_hook) == 1
        assert engram_hook[0].get("async") is True
        assert "CLAUDE_INVOKED_BY=engram_postcompact" in engram_hook[0]["command"]

    def test_postcompact_hook_idempotent(self, tmp_path, monkeypatch):
        """R4: PostCompact hook is idempotent — second call returns None."""
        import sys as _sys
        from piia_engram.setup_wizard import _inject_claude_code_postcompact_hook
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir()

        first = _inject_claude_code_postcompact_hook(_sys.executable)
        assert first is not None
        second = _inject_claude_code_postcompact_hook(_sys.executable)
        assert second is None

    def test_force_rewrite_upgrades_stale_script_path_hook(self, tmp_path, monkeypatch):
        """v3.30.1 fix: doctor --fix must upgrade old script-path style PreCompact
        hook to current ``python -m`` form, instead of silently skipping it."""
        import sys as _sys
        from piia_engram.setup_wizard import _inject_claude_code_precompact_hook

        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir()

        # Pre-populate settings.json with a v3.29-style stale hook that
        # references a .py script path (not python -m module form). Its
        # command contains the env marker ``CLAUDE_INVOKED_BY=engram_precompact``
        # so legacy idempotent skip considers it "present", but doctor's
        # strict-match check fails (no ``piia_engram.hooks.auto_save_on_stop``
        # substring) — exactly the v3.30 dogfooding bug we're fixing.
        stale_cmd = (
            "ENGRAM_MIN_TURNS_TO_FLUSH=5 CLAUDE_INVOKED_BY=engram_precompact "
            "/usr/bin/python /opt/engram/scripts/auto_save_on_stop.py"
        )
        existing = {
            "hooks": {
                "PreCompact": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": stale_cmd,
                                "timeout": 30,
                                "async": True,
                            }
                        ]
                    }
                ]
            }
        }
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps(existing), encoding="utf-8"
        )

        # Without force_rewrite — backward-compatible: skip (returns None)
        result_skip = _inject_claude_code_precompact_hook(_sys.executable)
        assert result_skip is None, \
            "default behaviour must remain idempotent skip"

        # Confirm settings.json was NOT modified (stale command still there)
        settings_after_skip = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        assert settings_after_skip["hooks"]["PreCompact"][0]["hooks"][0]["command"] \
            == stale_cmd

        # With force_rewrite=True — should upgrade in place
        result_fix = _inject_claude_code_precompact_hook(
            _sys.executable, force_rewrite=True,
        )
        assert result_fix is not None, "force_rewrite must overwrite stale hook"

        settings_after_fix = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        new_cmd = settings_after_fix["hooks"]["PreCompact"][0]["hooks"][0]["command"]
        assert "piia_engram.hooks.auto_save_on_stop" in new_cmd, \
            "rewritten hook must use python -m module form"
        assert "scripts/auto_save_on_stop.py" not in new_cmd, \
            "stale .py script path must be gone"
        # Env marker must survive in the rewritten command
        assert "CLAUDE_INVOKED_BY=engram_precompact" in new_cmd

    def test_force_rewrite_noop_when_already_current(self, tmp_path, monkeypatch):
        """force_rewrite shouldn't re-touch settings.json when the hook is
        already at the current spec — that would dirty the file for nothing
        and confuse anyone looking at mtime."""
        import sys as _sys
        from piia_engram.setup_wizard import _inject_claude_code_postcompact_hook

        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir()

        # First call: fresh install
        first = _inject_claude_code_postcompact_hook(_sys.executable)
        assert first is not None

        settings_path = tmp_path / ".claude" / "settings.json"
        mtime_before = settings_path.stat().st_mtime_ns

        # Second call with force_rewrite=True but nothing actually
        # changed — should return None ("no rewrite needed") and not
        # touch the file.
        second = _inject_claude_code_postcompact_hook(
            _sys.executable, force_rewrite=True,
        )
        assert second is None, \
            "force_rewrite on an up-to-date hook should be a no-op"

        # File mtime should be unchanged
        assert settings_path.stat().st_mtime_ns == mtime_before, \
            "settings.json must not be touched when hook is already current"

    def test_force_rewrite_preserves_other_event_hooks(self, tmp_path, monkeypatch):
        """Rewriting Engram's own hook in PreCompact must not affect
        unrelated user-added hooks (e.g. Stop, SessionStart, or even a
        different hook in the same event)."""
        import sys as _sys
        from piia_engram.setup_wizard import _inject_claude_code_precompact_hook

        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        (tmp_path / ".claude").mkdir()

        # User has a custom Stop hook + a stale Engram PreCompact + a user
        # PreCompact hook unrelated to Engram. After force_rewrite, only
        # the Engram PreCompact should be replaced.
        existing = {
            "hooks": {
                "Stop": [{"hooks": [
                    {"type": "command", "command": "echo my-stop-hook"}
                ]}],
                "PreCompact": [{"hooks": [
                    {"type": "command", "command": "echo unrelated-precompact"},
                    {
                        "type": "command",
                        "command": (
                            "CLAUDE_INVOKED_BY=engram_precompact "
                            "/old/python /old/auto_save_on_stop.py"
                        ),
                        "timeout": 30,
                    },
                ]}],
            }
        }
        (tmp_path / ".claude" / "settings.json").write_text(
            json.dumps(existing), encoding="utf-8"
        )

        result = _inject_claude_code_precompact_hook(
            _sys.executable, force_rewrite=True,
        )
        assert result is not None

        after = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
        )

        # Stop hook untouched
        assert after["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo my-stop-hook"

        # PreCompact group: unrelated hook still there, Engram hook upgraded
        pre_hooks = after["hooks"]["PreCompact"][0]["hooks"]
        assert len(pre_hooks) == 2
        unrelated = [h for h in pre_hooks if "unrelated-precompact" in h["command"]]
        engram = [h for h in pre_hooks if "CLAUDE_INVOKED_BY=engram_precompact" in h["command"]]
        assert len(unrelated) == 1, "user's unrelated hook must survive"
        assert len(engram) == 1, "Engram hook must remain (just upgraded)"
        assert "piia_engram.hooks.auto_save_on_stop" in engram[0]["command"], \
            "Engram hook must now use -m form"


# ---------------------------------------------------------------------------
# _build_feedback_report
# ---------------------------------------------------------------------------


class TestFeedbackReport:
    """内测反馈报告生成。"""

    def test_empty_data_dir(self, tmp_path):
        """Should return valid report even with empty data dir."""
        report = _build_feedback_report(str(tmp_path))
        assert report["report_type"] == "engram_beta_feedback"
        assert report["report_version"] == 1
        k = report["knowledge"]
        assert k["total"] == 0
        assert k["staging"] == 0
        assert k["verified"] == 0
        assert k["promotion_rate"] is None

    def test_counts_staging_and_verified(self, tmp_path):
        """Should correctly count staging vs verified items."""
        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        lessons = [
            {"id": "1", "tier": "staging", "created_at": "2026-05-20T00:00:00Z"},
            {"id": "2", "tier": "verified", "created_at": "2026-05-18T00:00:00Z"},
            {"id": "3", "tier": "verified", "created_at": "2026-05-15T00:00:00Z"},
        ]
        (kdir / "lessons.json").write_text(json.dumps(lessons), encoding="utf-8")
        decisions = [
            {"id": "4", "tier": "staging", "created_at": "2026-05-21T00:00:00Z"},
        ]
        (kdir / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")

        report = _build_feedback_report(str(tmp_path))
        k = report["knowledge"]
        assert k["total"] == 4
        assert k["staging"] == 2
        assert k["verified"] == 2
        assert k["promotion_rate"] == 0.5
        assert k["lessons"]["staging"] == 1
        assert k["lessons"]["verified"] == 2
        assert k["decisions"]["staging"] == 1

    def test_domain_distribution(self, tmp_path):
        """Should count domain occurrences."""
        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        lessons = [
            {"id": "1", "tier": "verified", "domain": "python,testing"},
            {"id": "2", "tier": "verified", "domain": "python"},
        ]
        (kdir / "lessons.json").write_text(json.dumps(lessons), encoding="utf-8")
        (kdir / "decisions.json").write_text("[]", encoding="utf-8")

        report = _build_feedback_report(str(tmp_path))
        assert report["top_domains"]["python"] == 2
        assert report["top_domains"]["testing"] == 1

    def test_no_content_leaked(self, tmp_path):
        """Report must never contain knowledge content."""
        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        lessons = [
            {"id": "1", "tier": "verified", "summary": "SECRET CONTENT HERE",
             "detail": "PRIVATE DETAIL", "domain": "test",
             "created_at": "2026-05-20T00:00:00Z"},
        ]
        (kdir / "lessons.json").write_text(json.dumps(lessons), encoding="utf-8")
        (kdir / "decisions.json").write_text("[]", encoding="utf-8")

        report = _build_feedback_report(str(tmp_path))
        report_str = json.dumps(report)
        assert "SECRET CONTENT" not in report_str
        assert "PRIVATE DETAIL" not in report_str

    def test_session_count(self, tmp_path):
        """Should count context session files."""
        ctx_dir = tmp_path / "contexts"
        ctx_dir.mkdir(parents=True)
        for i in range(3):
            (ctx_dir / f"session_{i}.json").write_text("{}", encoding="utf-8")

        report = _build_feedback_report(str(tmp_path))
        assert report["session_count"] == 3
