"""setup_wizard 辅助函数单元测试。"""

import json
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
    _read_mcp_config,
    _read_rule_file,
    _run_privacy_preferences,
    _run_privacy_report,
    _run_seed_knowledge_onboarding,
    _run_telemetry_cli,
    _scan_rule_files,
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
    answers = iter(["", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    summary = _run_seed_knowledge_onboarding(str(tmp_path), cwd=tmp_path)

    from piia_engram.core import Engram

    engram = Engram(root=tmp_path)

    assert engram.get_profile() == {}
    assert engram.get_lessons(limit=None, _update_access=False) == []
    assert summary["profile"] == {}


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
        answers = iter(["", "y"])  # reconcile default, telemetry yes
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
        assert "Engram Stats" in out

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
