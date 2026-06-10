"""setup_wizard 辅助函数单元测试。"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from piia_engram.setup_wizard import (
    LEGACY_SERVER_NAMES,
    _build_config_integrity_report,
    _print_config_integrity_report,
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


def _removed_private_builtin_name() -> str:
    return "-".join(("self", "repair", "loop"))


def _removed_private_builtin_title() -> str:
    return "Self" + "-Repair " + "Loop"


class TestSessionsCLI:
    def test_sessions_empty_state_is_successful(self, tmp_path, monkeypatch, capsys):
        """engram sessions should succeed and guide when no sessions exist."""
        from piia_engram.setup_wizard import run_sessions

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

        assert run_sessions([]) == 0

        out = capsys.readouterr().out
        assert "No saved agent sessions" in out

    def test_sessions_lists_metadata_without_content(self, tmp_path, monkeypatch, capsys):
        """engram sessions should list recent session metadata only."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import run_sessions

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        eng = Engram()
        eng.save_agent_context(tool="codex", content="SECRET BODY", session_id="codex-s1")

        assert run_sessions([]) == 0

        out = capsys.readouterr().out
        assert "codex" in out
        assert "codex-s1" in out
        assert "SECRET BODY" not in out

    def test_status_reports_metadata_without_knowledge_bodies(self, tmp_path, monkeypatch, capsys):
        """engram status should summarize health without printing memory content."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import run_status

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        secret = "ZZSTATUS_SECRET_BODY"
        eng = Engram()
        eng.add_lesson(secret, detail="do not print this", domain="security")
        eng.add_decision(
            "status privacy",
            choice=secret,
            reasoning="do not print this either",
        )
        eng.add_playbook(
            {
                "title": secret,
                "triggers": ["status"],
                "steps": [{"action": "hide", "detail": secret}],
            }
        )
        eng.save_agent_context(tool="codex", content=secret, session_id="status-session")

        assert run_status(["--no-probe"]) == 0

        out = capsys.readouterr().out
        assert "Engram status" in out
        assert "Knowledge:" in out
        assert "Agent sessions:" in out
        assert str(tmp_path) in out
        assert secret not in out
        assert "do not print" not in out

    def test_status_does_not_surface_private_builtin_methodology(
        self, tmp_path, monkeypatch, capsys
    ):
        """Status should stay product-focused and not advertise private workflows."""
        from piia_engram.status_report import build_status
        from piia_engram.setup_wizard import run_status

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_TEST", "1")

        status = build_status(probe=False)
        assert run_status(["--no-probe"]) == 0

        out = capsys.readouterr().out
        assert status["builtins"] == {}
        assert _removed_private_builtin_title() not in out
        assert _removed_private_builtin_name() not in out

    def test_status_html_writes_redacted_local_report(self, tmp_path, monkeypatch, capsys):
        """--html should write a local status page with metadata only."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import run_status

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        secret = "ZZSTATUS_HTML_SECRET"
        eng = Engram()
        eng.add_lesson(secret, detail=secret, domain="security")
        output = tmp_path / "status-report.html"

        assert run_status(["--html", "--no-probe", "--output", str(output)]) == 0

        out = capsys.readouterr().out
        html = output.read_text(encoding="utf-8")
        assert str(output) in out
        assert "Engram Status" in html
        assert "Knowledge" in html
        assert secret not in html

    def test_status_redacts_caller_controlled_session_ids(self, tmp_path, monkeypatch, capsys):
        """Status metadata should not echo caller-controlled session IDs."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import run_status

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        secret = "SECRET_CUSTOMER_CASE"
        eng = Engram()
        eng.save_agent_context(tool="codex", content="metadata test", session_id=secret)
        output = tmp_path / "status.html"

        assert run_status(["--no-probe"]) == 0
        text = capsys.readouterr().out
        assert secret not in text

        assert run_status(["--html", "--no-probe", "--output", str(output)]) == 0
        html = output.read_text(encoding="utf-8")
        assert secret not in html

    def test_status_without_html_rejects_output_path(self, tmp_path, monkeypatch, capsys):
        """--output without --html should not silently do nothing."""
        from piia_engram.setup_wizard import run_status

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        output = tmp_path / "ignored.html"

        assert run_status(["--output", str(output), "--no-probe"]) == 2

        out = capsys.readouterr().out
        assert "--output only applies with --html" in out
        assert not output.exists()

    def test_status_does_not_write_default_html_report(self, tmp_path, monkeypatch):
        """Plain text status should not create the default HTML report."""
        from piia_engram.setup_wizard import run_status

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

        assert run_status(["--no-probe"]) == 0

        assert not (tmp_path / "reports" / "status.html").exists()

    def test_status_reports_mcp_client_summary_without_config_paths(
        self, tmp_path, monkeypatch, capsys
    ):
        """Client config summaries should show status, not local config paths."""
        import piia_engram.setup_wizard as sw
        from piia_engram.setup_wizard import run_status

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))
        private_dir = tmp_path / "private-client-path"
        private_dir.mkdir()
        config_path = private_dir / "mcp.json"
        config_path.write_text(
            json.dumps({
                "mcpServers": {
                    "engram": {"command": "piia-engram-mcp", "args": []},
                },
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(
            sw,
            "_tool_configs",
            lambda: {
                "secret_client": {
                    "name": "Secret Client",
                    "config_paths": [config_path],
                    "verified": True,
                },
                "missing_client": {
                    "name": "Missing Client",
                    "config_paths": [tmp_path / "missing.json"],
                    "verified": False,
                },
            },
        )

        assert run_status(["--no-probe"]) == 0

        out = capsys.readouterr().out
        assert "MCP clients:" in out
        assert "Secret Client: configured" in out
        assert "Missing Client: not configured" in out
        assert "recommended-console-script" in out
        assert str(private_dir) not in out
        assert str(config_path) not in out

    def test_status_html_includes_next_action_commands(self, tmp_path, monkeypatch):
        """HTML status page should include common follow-up commands."""
        from piia_engram.status_report import render_status_html

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        status = {
            "version": "test",
            "storage": {"path": str(tmp_path), "file_count": 0, "bytes": 0, "skipped": 0},
            "knowledge": {"total": 0, "verified": 0, "staging": 0, "archived": 0},
            "sessions": {"count": 0, "latest": None},
            "clients": {
                "configured": 1,
                "total": 1,
                "tools": [{"name": "Codex", "status": "configured", "style": "recommended-console-script"}],
            },
            "encoding": {
                "stdout": "utf-8",
                "stderr": "utf-8",
                "pythonioencoding": "utf-8",
                "ok": True,
            },
            "telemetry": {"local_enabled": False, "remote_enabled": False, "phase": "1"},
            "mcp_entry": {"ok": None, "command": "piia-engram-mcp", "message": "probe skipped"},
            "warnings": [],
        }

        html = render_status_html(status)

        assert "MCP Clients" in html
        assert "engram doctor" in html
        assert "engram review" in html
        assert "engram sessions" in html
        assert _removed_private_builtin_name() not in html

    def test_status_html_redacts_local_paths_and_client_config(self, tmp_path, monkeypatch, capsys):
        """HTML status should be shareable without local root or MCP config paths."""
        import piia_engram.setup_wizard as sw
        from piia_engram.setup_wizard import run_status

        private_root = tmp_path / "private-engram-root"
        private_dir = tmp_path / "private-client-path"
        private_dir.mkdir()
        config_path = private_dir / "mcp.json"
        config_path.write_text(
            json.dumps({
                "mcpServers": {
                    "engram": {
                        "command": "piia-engram-mcp",
                        "args": ["--private-arg"],
                        "env": {"SECRET_TOKEN": "do-not-render"},
                    },
                },
            }),
            encoding="utf-8",
        )
        output = tmp_path / "status.html"

        monkeypatch.setenv("ENGRAM_DIR", str(private_root))
        monkeypatch.setattr(
            sw,
            "_tool_configs",
            lambda: {
                "secret_client": {
                    "name": "Secret Client",
                    "config_paths": [config_path],
                    "verified": True,
                },
            },
        )

        assert run_status(["--html", "--no-probe", "--output", str(output)]) == 0

        out = capsys.readouterr().out
        html = output.read_text(encoding="utf-8")
        assert str(output) in out
        assert "MCP Clients" in html
        assert "Secret Client" in html
        assert "Next Commands" in html
        assert str(private_root) not in html
        assert str(private_dir) not in html
        assert str(config_path) not in html
        assert "--private-arg" not in html
        assert "do-not-render" not in html

    def test_status_probe_branch_records_mcp_help_result(self, tmp_path, monkeypatch):
        """build_status(probe=True) should run the bounded MCP help probe."""
        from piia_engram.status_report import build_status

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setattr(
            "piia_engram.status_report.shutil.which",
            lambda command: "piia-engram-mcp",
        )

        calls = []

        class Result:
            returncode = 0
            stdout = "Engram MCP Server"
            stderr = ""

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return Result()

        monkeypatch.setattr("piia_engram.status_report.subprocess.run", fake_run)

        status = build_status(probe=True)

        assert status["mcp_entry"]["ok"] is True
        assert status["mcp_entry"]["message"] == "help probe passed"
        assert calls[0][0] == ["piia-engram-mcp", "--help"]
        assert calls[0][1]["timeout"] == 5

    def test_status_probe_falls_back_to_sibling_mcp_script(self, tmp_path, monkeypatch):
        """Absolute-path CLI launches should find piia-engram-mcp next to engram.exe."""
        from piia_engram import status_report

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setattr(
            "piia_engram.status_report.shutil.which",
            lambda command: None,
        )
        scripts = tmp_path / "Scripts"
        scripts.mkdir()
        engram_script = scripts / "engram.exe"
        mcp_script = scripts / "piia-engram-mcp.exe"
        engram_script.write_text("", encoding="utf-8")
        mcp_script.write_text("", encoding="utf-8")
        monkeypatch.setattr(status_report.sys, "argv", [str(engram_script)])

        calls = []

        class Result:
            returncode = 0
            stdout = "Engram MCP Server"
            stderr = ""

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return Result()

        monkeypatch.setattr("piia_engram.status_report.subprocess.run", fake_run)

        status = status_report.build_status(probe=True)

        assert status["mcp_entry"]["ok"] is True
        assert calls[0][0] == [str(mcp_script.resolve()), "--help"]

    def test_status_help_shows_usage(self, capsys):
        """engram status --help should document text and HTML modes."""
        from piia_engram.setup_wizard import run_status

        assert run_status(["--help"]) == 0

        out = capsys.readouterr().out
        assert "engram status [--no-probe]" in out
        assert "engram status --html" in out

    def test_status_marks_warning_rows(self):
        """Rows with actionable warnings should not still claim [ok]."""
        from piia_engram.status_report import render_status_text

        text = render_status_text({
            "version": "test",
            "storage": {
                "path": "E:\\Temp\\engram",
                "file_count": 1,
                "bytes": 10,
                "skipped": 1,
            },
            "knowledge": {
                "total": 2,
                "verified": 1,
                "staging": 1,
                "archived": 0,
            },
            "sessions": {"count": 0, "latest": None},
            "encoding": {
                "stdout": "cp936",
                "stderr": "cp936",
                "pythonioencoding": "(not set)",
                "ok": False,
            },
            "telemetry": {
                "local_enabled": False,
                "remote_enabled": False,
                "phase": "1 (local log only)",
            },
            "mcp_entry": {
                "ok": None,
                "command": "piia-engram-mcp",
                "message": "probe skipped",
            },
            "warnings": [
                "1 staging item(s) need review",
                "1 storage file(s) could not be scanned",
                "terminal is not reporting UTF-8 stdout/stderr",
            ],
        })

        assert "  [!!] Storage:" in text
        assert "  [!!] Knowledge:" in text
        assert "  [!!] Terminal encoding:" in text
        assert "  [ok] Storage:" not in text
        assert "  [ok] Knowledge:" not in text

    def test_main_status_dispatches(self, tmp_path, monkeypatch):
        """main() with 'status' should dispatch to run_status."""
        import piia_engram.setup_wizard as sw

        seen = {}

        def fake_run_status(argv):
            seen["argv"] = argv
            return 0

        monkeypatch.setattr(sw, "run_status", fake_run_status)
        monkeypatch.setattr("sys.argv", ["engram", "status", "--html"])

        with pytest.raises(SystemExit) as exc_info:
            sw.main()

        assert exc_info.value.code == 0
        assert seen["argv"] == ["--html"]

    def test_sessions_filters_by_tool_and_limit(self, tmp_path, monkeypatch, capsys):
        """--tool and --limit should narrow the listed sessions."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import run_sessions

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        eng = Engram()
        eng.save_agent_context(tool="codex", content="one", session_id="codex-s1")
        eng.save_agent_context(tool="codex", content="two", session_id="codex-s2")
        eng.save_agent_context(tool="claude_code", content="other", session_id="claude-s1")

        assert run_sessions(["--tool", "codex", "--limit", "1"]) == 0

        out = capsys.readouterr().out
        assert "codex" in out
        assert out.count("codex-s") == 1
        assert "claude-s1" not in out

    def test_sessions_show_prints_matching_session_content(self, tmp_path, monkeypatch, capsys):
        """engram sessions show <id> should print that session's content."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import run_sessions

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        Engram().save_agent_context(
            tool="codex",
            content="Detailed checkpoint body",
            session_id="codex-show",
        )

        assert run_sessions(["show", "codex-show"]) == 0

        out = capsys.readouterr().out
        assert "codex-show" in out
        assert "Detailed checkpoint body" in out

    def test_sessions_show_finds_old_session_beyond_list_page(
        self, tmp_path, monkeypatch, capsys
    ):
        """show should not be limited to the first 200 recent sessions."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import run_sessions

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        eng = Engram()
        first = eng.save_agent_context(
            tool="codex",
            content="old checkpoint body",
            session_id="codex-old",
        )
        old_path = Path(first["file"])
        for i in range(201):
            eng.save_agent_context(
                tool="codex",
                content=f"new checkpoint {i}",
                session_id=f"codex-new-{i:03d}",
            )
        old_time = time.time() - 3600
        os.utime(old_path, (old_time, old_time))

        assert run_sessions(["show", "codex-old"]) == 0

        out = capsys.readouterr().out
        assert "codex-old" in out
        assert "old checkpoint body" in out

    def test_sessions_show_missing_returns_nonzero(self, tmp_path, monkeypatch, capsys):
        """show should return nonzero when the requested session is absent."""
        from piia_engram.setup_wizard import run_sessions

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

        assert run_sessions(["show", "missing-session"]) == 1

        out = capsys.readouterr().out
        assert "not found" in out.lower()


class TestDoctorContinuityChecks:
    def test_continuity_check_empty_state_is_informational(
        self, tmp_path, monkeypatch, capsys
    ):
        """No saved sessions should not count as a doctor problem."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import _run_continuity_checks

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        eng = Engram()

        assert _run_continuity_checks(eng) == 0

        out = capsys.readouterr().out
        assert "Continuity" in out
        assert "No saved agent sessions" in out

    def test_continuity_check_reports_recent_session(
        self, tmp_path, monkeypatch, capsys
    ):
        """Saved sessions should appear as a healthy continuity signal."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import _run_continuity_checks

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        eng = Engram()
        eng.save_agent_context(tool="codex", content="checkpoint", session_id="codex-cp")

        assert _run_continuity_checks(eng) == 0

        out = capsys.readouterr().out
        assert "Continuity" in out
        assert "codex-cp" in out
        assert "codex" in out

    def test_functional_checks_runs_continuity_check(
        self, tmp_path, monkeypatch, capsys
    ):
        """doctor functional checks should include the continuity section."""
        import piia_engram.setup_wizard as sw

        called = {}

        def fake_continuity(eng):
            called["root"] = eng.root
            print("  -- Continuity --")
            return 0

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setattr(sw, "_run_continuity_checks", fake_continuity)

        sw._run_functional_checks(fix=False)

        assert called["root"] == tmp_path


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


def test_detect_tools_preserves_config_format_metadata(tmp_path: Path, monkeypatch):
    """Detected tools must carry format/server_key into run_setup writers."""
    from piia_engram.setup_wizard import _detect_tools

    monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
    (tmp_path / ".codex").mkdir()

    detected = _detect_tools()
    codex = next(item for item in detected if item["id"] == "codex")

    assert codex["format"] == "toml"
    assert codex["server_key"] == "mcp_servers"


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


def test_write_mcp_config_backs_up_existing_json_before_write(tmp_path: Path):
    """Existing client JSON config must be backed up before mutation."""
    config_path = tmp_path / "mcp.json"
    original = json.dumps(
        {"mcpServers": {"other-tool": {"command": "node", "args": ["server.js"]}}},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    config_path.write_text(original, encoding="utf-8")

    _write_mcp_config(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")

    backups = list(tmp_path.glob("mcp.json.engram-backup.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "other-tool" in config["mcpServers"]
    assert "engram" in config["mcpServers"]


def test_write_mcp_config_refuses_to_overwrite_unparseable_existing_json(tmp_path: Path):
    """A commented/malformed existing config must stay byte-for-byte intact."""
    config_path = tmp_path / "settings.json"
    original = '{\n  // user comment kept by the client\n  "theme": "Ayu Dark",\n}\n'
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        _write_mcp_config(
            config_path,
            "/usr/bin/python3",
            "/path/to/mcp_server.py",
            server_key="context_servers",
        )

    assert config_path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("settings.json.engram-backup.*")) == []


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


def test_write_mcp_config_preserves_existing_engram_dir_when_not_overridden(tmp_path: Path):
    """Repairing an existing config must not drop a user's custom ENGRAM_DIR."""
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "engram": {
                "command": "/old/python",
                "args": ["old.py"],
                "env": {"ENGRAM_DIR": "D:/EngramData"},
            }
        }
    }), encoding="utf-8")

    _write_mcp_config(
        config_path,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        data_dir=None,
    )

    env = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["engram"]["env"]
    assert env["ENGRAM_DIR"] == "D:/EngramData"


def test_external_config_write_requires_explicit_authorization_with_file_safety_root(
    tmp_path: Path,
):
    """External client config writes must fail closed without explicit apply."""
    engram_root = tmp_path / "engram-root"
    external_config = tmp_path / "external" / "mcp.json"
    external_config.parent.mkdir()
    original = json.dumps(
        {"mcpServers": {"existing": {"command": "node", "args": ["server.js"]}}},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    external_config.write_text(original, encoding="utf-8")

    with pytest.raises(PermissionError, match="explicit authorization"):
        _write_mcp_config(
            external_config,
            "/usr/bin/python3",
            "/path/to/mcp_server.py",
            file_safety_root=engram_root,
            authorized_external_write=False,
        )

    assert external_config.read_text(encoding="utf-8") == original
    assert not (engram_root / "file_safety_ledger.jsonl").exists()
    assert not (engram_root / "backups").exists()


def test_external_config_write_with_file_safety_root_fails_closed_by_default(
    tmp_path: Path,
):
    """A caller must explicitly opt in before mutating external config files."""
    engram_root = tmp_path / "engram-root"
    external_config = tmp_path / "external" / "mcp.json"
    external_config.parent.mkdir()
    original = json.dumps(
        {"mcpServers": {"existing": {"command": "node", "args": ["server.js"]}}},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    external_config.write_text(original, encoding="utf-8")

    with pytest.raises(PermissionError, match="explicit authorization"):
        _write_mcp_config(
            external_config,
            "/usr/bin/python3",
            "/path/to/mcp_server.py",
            file_safety_root=engram_root,
        )

    assert external_config.read_text(encoding="utf-8") == original
    assert not (engram_root / "file_safety_ledger.jsonl").exists()
    assert not (engram_root / "backups").exists()


def test_external_config_write_uses_engram_root_backup_ledger_and_preserves_old_dir(
    tmp_path: Path,
):
    """Explicit repair should preserve old ENGRAM_DIR and keep backup metadata under Engram root."""
    from piia_engram.file_safety import read_ledger_entries

    engram_root = tmp_path / "engram-root"
    external_config = tmp_path / "external" / "mcp.json"
    external_config.parent.mkdir()
    original = json.dumps(
        {
            "mcpServers": {
                "engram": {
                    "command": "/old/python",
                    "args": ["old.py"],
                    "env": {"ENGRAM_DIR": "D:/OldEngramData"},
                }
            }
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    external_config.write_text(original, encoding="utf-8")

    _write_mcp_config(
        external_config,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        data_dir=None,
        file_safety_root=engram_root,
        authorized_external_write=True,
    )

    updated = json.loads(external_config.read_text(encoding="utf-8"))
    assert updated["mcpServers"]["engram"]["env"]["ENGRAM_DIR"] == "D:/OldEngramData"
    assert list(external_config.parent.glob("mcp.json.engram-backup.*")) == []
    backups = list((engram_root / "backups" / "file_safety" / "external").glob("mcp.json.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original

    entries = read_ledger_entries(engram_root)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["scope"] == "external"
    assert entry["tool"] == "setup"
    assert entry["result"] == "success"
    assert entry["path"].startswith("<external:")
    assert str(external_config.parent) not in entry["backup_path"]
    assert entry["backup_path"].startswith("<engram-root>/backups/file_safety/")


def test_write_mcp_config_respects_custom_server_key(tmp_path: Path):
    """Clients such as Copilot use top-level 'servers' instead of 'mcpServers'."""
    config_path = tmp_path / "mcp.json"

    _write_mcp_config(
        config_path,
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
        server_key="servers",
    )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "mcpServers" not in config
    assert config["servers"]["engram"]["command"] == "/usr/bin/python3"


def test_write_mcp_config_toml_creates_missing_codex_config(tmp_path: Path):
    """Codex setup should create config.toml when only ~/.codex exists."""
    from piia_engram.setup_wizard import _write_mcp_config_toml

    config_path = tmp_path / ".codex" / "config.toml"

    _write_mcp_config_toml(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")

    text = config_path.read_text(encoding="utf-8")
    assert '[mcp_servers.engram]' in text
    assert 'PYTHONIOENCODING = "utf-8"' in text
    assert 'ENGRAM_TOOLS = "all"' in text


def test_write_mcp_config_toml_backs_up_and_preserves_existing_codex_config(tmp_path: Path):
    """Codex config.toml must stay TOML and get a pre-write backup."""
    from piia_engram.setup_wizard import _write_mcp_config_toml

    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    original = '[settings]\napproval_policy = "never"\n'
    config_path.write_text(original, encoding="utf-8")

    _write_mcp_config_toml(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")

    backups = list(config_path.parent.glob("config.toml.engram-backup.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    text = config_path.read_text(encoding="utf-8")
    assert text.startswith("[settings]")
    assert '[mcp_servers.engram]' in text
    assert not text.lstrip().startswith("{")


def test_write_mcp_config_toml_preserves_existing_engram_dir_when_not_overridden(tmp_path: Path):
    """Codex repair must keep an existing custom ENGRAM_DIR for old users."""
    from piia_engram.setup_wizard import _write_mcp_config_toml
    try:
        import tomllib as toml_parser
    except ModuleNotFoundError:
        pytest.skip("tomllib unavailable")

    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        '\n'.join([
            '[mcp_servers.engram]',
            'command = "/old/python"',
            'args = ["old.py"]',
            '',
            '[mcp_servers.engram.env]',
            'ENGRAM_DIR = "D:/EngramData"',
            '',
        ]),
        encoding="utf-8",
    )

    _write_mcp_config_toml(config_path, "/usr/bin/python3", "/path/to/mcp_server.py")

    config = toml_parser.loads(config_path.read_text(encoding="utf-8"))
    assert config["mcp_servers"]["engram"]["env"]["ENGRAM_DIR"] == "D:/EngramData"


def test_write_tool_mcp_config_preserves_zed_settings_and_context_servers(tmp_path: Path):
    """Zed settings.json should keep unrelated settings and existing servers."""
    from piia_engram.setup_wizard import _write_tool_mcp_config

    config_path = tmp_path / ".config" / "zed" / "settings.json"
    config_path.parent.mkdir(parents=True)
    original_config = {
        "theme": "Ayu Dark",
        "agent": {"tool_permissions": {"default": "confirm"}},
        "context_servers": {
            "existing": {"command": "node", "args": ["server.js"], "env": {}}
        },
    }
    original = json.dumps(original_config, ensure_ascii=False, indent=2) + "\n"
    config_path.write_text(original, encoding="utf-8")

    _write_tool_mcp_config(
        {
            "name": "Zed",
            "config_path": config_path,
            "server_key": "context_servers",
        },
        "/usr/bin/python3",
        "/path/to/mcp_server.py",
    )

    backups = list(config_path.parent.glob("settings.json.engram-backup.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["theme"] == "Ayu Dark"
    assert config["agent"]["tool_permissions"]["default"] == "confirm"
    assert "existing" in config["context_servers"]
    assert config["context_servers"]["engram"]["args"] == ["-m", "piia_engram.mcp_server"]


def test_write_mcp_config_toml_escapes_windows_paths(tmp_path: Path, monkeypatch):
    """Windows backslashes must produce valid TOML for Codex config.toml."""
    from piia_engram.setup_wizard import _write_mcp_config_toml
    try:
        import tomllib as toml_parser
    except ImportError:  # pragma: no cover - Python 3.10 fallback
        toml_parser = pytest.importorskip("tomli")

    config_path = tmp_path / ".codex" / "config.toml"
    python_path = r"C:\Users\testuser\AppData\Local\Programs\Python\Python312\python.exe"
    mcp_server_path = r"E:\Temp\engram-worktrees\v342-install-gui\src\piia_engram\mcp_server.py"
    data_dir = r"C:\Users\testuser\.engram"

    monkeypatch.setattr("piia_engram.setup_wizard.importlib.util.find_spec", lambda _name: None)

    _write_mcp_config_toml(config_path, python_path, mcp_server_path, data_dir)

    parsed = toml_parser.loads(config_path.read_text(encoding="utf-8"))
    entry = parsed["mcp_servers"]["engram"]
    assert entry["command"] == python_path
    assert entry["env"]["PYTHONPATH"] == r"E:\Temp\engram-worktrees\v342-install-gui\src"
    assert entry["env"]["ENGRAM_DIR"] == data_dir


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

    # ── A2 去碎片化的强校验（不只是"文本出现在某处"）──────────────
    setup_lessons = engram.get_lessons(
        source_tool="engram_setup", limit=None, _update_access=False
    )
    assert setup_lessons, "规则应被导入并标记 source_tool=engram_setup"
    # 1) 归到分组 domain，而不是旧的逐行 "setup" 碎片
    for les in setup_lessons:
        assert les.get("domain") in {"user_preference", "project_rules"}, (
            f"导入 lesson 落到了意外的 domain: {les.get('domain')!r}"
        )
        # 2) 原始规则行不应被直接当作 summary（那正是旧的逐行碎片行为）
        assert les.get("summary") not in (
            "remember to run tests before claiming completion",
            "decided to keep project memory local first",
        ), "规则行不应成为 lesson summary —— 说明仍在逐行碎片化"
    # 3) provenance：原文按来源文件分节保留在 detail 里
    detail_blob = "\n".join(les.get("detail", "") for les in setup_lessons)
    assert f"## {tmp_path.name}/CLAUDE.md" in detail_blob, "detail 应保留来源文件分节标题"
    assert "remember to run tests" in detail_blob
    assert "decided to keep project memory" in detail_blob


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


def test_config_integrity_report_is_metadata_only_and_read_only(tmp_path: Path, monkeypatch):
    """Config integrity should hash files without returning local rule bodies."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("ENGRAM_DIR", raising=False)

    codex_config = home / ".codex" / "config.toml"
    codex_config.parent.mkdir(parents=True)
    codex_config.write_text(
        "\n".join([
            "[mcp_servers.engram]",
            'command = "python"',
            'args = ["-m", "piia_engram.mcp_server"]',
            "",
        ]),
        encoding="utf-8",
    )
    codex_agents = home / ".codex" / "AGENTS.md"
    codex_agents.write_text(
        f"{_INSTRUCTION_MARKER}\n"
        "Call get_resume_brief. Do not print ZZSNIPPET_SECRET_BODY.\n"
        f"{_INSTRUCTION_MARKER_END}\n",
        encoding="utf-8",
    )
    project_agents = project / "AGENTS.md"
    project_agents.write_text(
        "Always run tests first.\n"
        "Project rule containing ZZPROJECT_SECRET_BODY must stay private.\n",
        encoding="utf-8",
    )
    shared_instructions = home / ".engram" / "shared_instructions.md"
    shared_instructions.parent.mkdir(parents=True)
    shared_instructions.write_text(
        "Shared instruction with ZZSHARED_SECRET_BODY.\n",
        encoding="utf-8",
    )
    claude_settings = home / ".claude" / "settings.json"
    claude_settings.parent.mkdir(parents=True)
    claude_settings.write_text(
        json.dumps({
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python -m piia_engram.hooks.auto_save_on_stop ZZHOOK_SECRET_BODY",
                            }
                        ]
                    }
                ]
            }
        }),
        encoding="utf-8",
    )

    before = {
        path: path.read_bytes()
        for path in (
            codex_config,
            codex_agents,
            project_agents,
            shared_instructions,
            claude_settings,
        )
    }

    report = _build_config_integrity_report(cwd=project)

    after = {
        path: path.read_bytes()
        for path in (
            codex_config,
            codex_agents,
            project_agents,
            shared_instructions,
            claude_settings,
        )
    }
    assert after == before
    assert report["live_store_modified"] is False

    serialized = json.dumps(report, ensure_ascii=False)
    assert "ZZSNIPPET_SECRET_BODY" not in serialized
    assert "ZZPROJECT_SECRET_BODY" not in serialized
    assert "ZZSHARED_SECRET_BODY" not in serialized
    assert "ZZHOOK_SECRET_BODY" not in serialized

    codex_config_row = next(
        row for row in report["mcp_configs"]
        if row["tool_id"] == "codex" and row["exists"]
    )
    assert codex_config_row["configured"] is True
    assert len(codex_config_row["sha256_12"]) == 12

    codex_snippet = next(
        row for row in report["instruction_files"]
        if row["tool_id"] == "codex"
    )
    assert codex_snippet["exists"] is True
    assert codex_snippet["has_marker"] is True
    assert codex_snippet["has_resume_brief"] is True
    assert len(codex_snippet["sha256_12"]) == 12

    project_rule = next(
        row for row in report["project_rules"]
        if row["path"] == str(project_agents)
    )
    assert "lines" not in project_rule
    assert project_rule["line_count"] == 2
    assert len(project_rule["sha256_12"]) == 12

    shared_row = next(
        row for row in report["shared_instruction_files"]
        if row["exists"]
    )
    assert shared_row["path"] == str(shared_instructions)
    assert len(shared_row["sha256_12"]) == 12

    stop_hook = next(
        row for row in report["claude_hooks"]
        if row["event"] == "Stop"
    )
    assert stop_hook["settings_exists"] is True
    assert stop_hook["registered"] is True
    assert "command" not in stop_hook


def test_config_integrity_report_prints_counts_without_paths(capsys):
    """Doctor-facing integrity output should be compact and non-sensitive."""
    report = {
        "summary": {
            "mcp_config_paths": 2,
            "mcp_configs_found": 1,
            "mcp_configs_configured": 1,
            "instruction_files": 2,
            "instruction_files_found": 1,
            "instruction_files_fresh": 1,
            "project_rule_files": 1,
            "shared_instruction_files_found": 1,
            "claude_hooks_registered": 2,
            "claude_hooks_total": 4,
        },
        "mcp_configs": [],
        "instruction_files": [],
        "project_rules": [],
        "shared_instruction_files": [],
        "claude_hooks": [],
        "live_store_modified": False,
    }

    _print_config_integrity_report(report)

    out = capsys.readouterr().out
    assert "Config Integrity" in out
    assert "MCP configs: 1/2 files found, 1 configured" in out
    assert "Instruction files: 1/2 found, 1 fresh" in out
    assert "Project rule files: 1 found" in out
    assert "Shared instructions: 1 found" in out
    assert "Claude hooks: 2/4 registered" in out


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


def test_doctor_reports_encoding_mojibake(tmp_path: Path, monkeypatch, capsys):
    """doctor should surface repairable mojibake in the active Engram root."""
    from piia_engram.setup_wizard import _run_functional_checks

    damaged = "发布流程测试".encode("utf-8").decode("gbk")
    kdir = tmp_path / "knowledge"
    kdir.mkdir(parents=True)
    (kdir / "lessons.json").write_text(
        json.dumps([{"id": "l1", "summary": damaged}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")

    result = _run_functional_checks(fix=False)

    assert result >= 1
    out = capsys.readouterr().out
    assert "Encoding health" in out
    assert "repairable mojibake" in out


def test_doctor_reports_search_mode_keyword_hint(tmp_path: Path, monkeypatch, capsys):
    """Default keyword mode: doctor surfaces the hybrid upgrade hint."""
    from piia_engram.setup_wizard import _run_functional_checks

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.delenv("ENGRAM_SEARCH", raising=False)

    _run_functional_checks(fix=False)

    out = capsys.readouterr().out
    assert "Search mode: keyword" in out
    assert "ENGRAM_SEARCH=hybrid" in out


def test_doctor_reports_search_mode_hybrid(tmp_path: Path, monkeypatch, capsys):
    """Hybrid mode: doctor reports it (ok when vector deps import, [!] otherwise)."""
    from piia_engram.setup_wizard import _run_functional_checks

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")

    _run_functional_checks(fix=False)

    out = capsys.readouterr().out
    assert "Search mode: hybrid" in out


def test_doctor_non_fix_does_not_backfill_legacy_knowledge(tmp_path: Path, monkeypatch):
    """doctor without --fix should not rewrite old knowledge files."""
    from piia_engram.setup_wizard import _run_functional_checks

    kdir = tmp_path / "knowledge"
    kdir.mkdir(parents=True)
    lessons_path = kdir / "lessons.json"
    decisions_path = kdir / "decisions.json"
    lessons_path.write_text(
        json.dumps([{"id": "l1", "summary": "legacy lesson"}], ensure_ascii=False),
        encoding="utf-8",
    )
    decisions_path.write_text(
        json.dumps([{"id": "d1", "question": "legacy?", "choice": "yes"}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")

    before = {
        lessons_path: lessons_path.read_bytes(),
        decisions_path: decisions_path.read_bytes(),
    }

    _run_functional_checks(fix=False)

    after = {
        lessons_path: lessons_path.read_bytes(),
        decisions_path: decisions_path.read_bytes(),
    }
    assert after == before


def test_terminal_encoding_check_reports_utf8_ok(capsys):
    """Terminal encoding diagnostics should be informational when UTF-8 is active."""
    from piia_engram.setup_wizard import _run_terminal_encoding_check

    result = _run_terminal_encoding_check(
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        preferred_encoding="UTF-8",
        filesystem_encoding="utf-8",
        pythonioencoding="utf-8",
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "Terminal encoding" in out
    assert "[ok] stdout/stderr: utf-8 / utf-8" in out
    assert "[ok] PYTHONIOENCODING=utf-8" in out


def test_terminal_encoding_check_accepts_unset_pythonioencoding_when_stdio_utf8(capsys):
    """Unset PYTHONIOENCODING is fine when the current terminal streams are UTF-8."""
    from piia_engram.setup_wizard import _run_terminal_encoding_check

    result = _run_terminal_encoding_check(
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        preferred_encoding="UTF-8",
        filesystem_encoding="utf-8",
        pythonioencoding="",
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "[ok] PYTHONIOENCODING not set (stdout/stderr already UTF-8)" in out
    assert "Set PYTHONIOENCODING=utf-8" not in out


def test_terminal_encoding_check_treats_cp65001_as_utf8(capsys):
    """Windows code page 65001 is UTF-8 and should not be flagged as legacy."""
    from piia_engram.setup_wizard import _run_terminal_encoding_check

    result = _run_terminal_encoding_check(
        stdout_encoding="cp65001",
        stderr_encoding="cp65001",
        preferred_encoding="cp65001",
        filesystem_encoding="utf-8",
        pythonioencoding="cp65001",
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "[ok] stdout/stderr: cp65001 / cp65001" in out
    assert "[ok] PYTHONIOENCODING=cp65001" in out
    assert "[ok] Runtime encodings: preferred=cp65001, filesystem=utf-8" in out


def test_terminal_encoding_check_warns_non_utf8_without_failing(capsys):
    """A legacy console code page is a display warning, not a data corruption failure."""
    from piia_engram.setup_wizard import _run_terminal_encoding_check

    result = _run_terminal_encoding_check(
        stdout_encoding="cp936",
        stderr_encoding="cp936",
        preferred_encoding="cp936",
        filesystem_encoding="utf-8",
        pythonioencoding="",
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "[--] stdout/stderr: cp936 / cp936" in out
    assert "Terminal may display UTF-8 text as mojibake" in out
    assert "This does not mean Engram files are corrupted" in out
    assert "[--] PYTHONIOENCODING not set" in out
    assert "[--] Runtime encodings: preferred=cp936, filesystem=utf-8" in out


def test_terminal_encoding_check_flags_non_utf8_pythonioencoding(capsys):
    """PYTHONIOENCODING only helps display safety when it is UTF-8 compatible."""
    from piia_engram.setup_wizard import _run_terminal_encoding_check

    result = _run_terminal_encoding_check(
        stdout_encoding="cp936",
        stderr_encoding="cp936",
        preferred_encoding="cp936",
        filesystem_encoding="utf-8",
        pythonioencoding="cp936",
    )

    assert result == 0
    out = capsys.readouterr().out
    assert "[--] PYTHONIOENCODING=cp936" in out
    assert "Set PYTHONIOENCODING=utf-8" in out


def test_doctor_fix_repairs_encoding_mojibake(tmp_path: Path, monkeypatch, capsys):
    """doctor --fix should repair high-confidence mojibake and create backup."""
    from piia_engram.setup_wizard import _run_functional_checks

    damaged = "发布流程测试".encode("utf-8").decode("gbk")
    kdir = tmp_path / "knowledge"
    kdir.mkdir(parents=True)
    lessons_path = kdir / "lessons.json"
    lessons_path.write_text(
        json.dumps([{"id": "l1", "summary": damaged}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")

    result = _run_functional_checks(fix=True)

    assert result == 0
    fixed = json.loads(lessons_path.read_text(encoding="utf-8"))
    assert fixed[0]["summary"] == "发布流程测试"
    assert list((tmp_path / "backups").glob("encoding_repair_*"))
    out = capsys.readouterr().out
    assert "[fixed] Encoding health" in out


def test_doctor_reports_unrepairable_encoding_suspect(tmp_path: Path, monkeypatch, capsys):
    """doctor should not silently pass mojibake that cannot be safely repaired."""
    from piia_engram.setup_wizard import _run_functional_checks

    kdir = tmp_path / "knowledge"
    kdir.mkdir(parents=True)
    (kdir / "lessons.json").write_text(
        json.dumps([{"id": "l1", "summary": "\u5bee\u20ac\u9359\ufffd"}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")

    result = _run_functional_checks(fix=False)

    assert result >= 1
    out = capsys.readouterr().out
    assert "suspect mojibake" in out


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

    def test_detects_legacy_server_names_without_external_mutation(self, tmp_path, monkeypatch):
        """auto_migrate must not mutate external client configs from MCP startup."""
        from piia_engram.setup_wizard import auto_migrate

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

        # Create a fake tool config with a legacy name
        config_dir = tmp_path / "home" / ".claude"
        config_dir.mkdir(parents=True)
        config_path = config_dir / ".mcp.json"
        original = json.dumps({
            "mcpServers": {
                "piia-pkc": {"command": "python", "args": ["old.py"]},
                "engram": {"command": "python", "args": ["mcp_server.py"]},
            }
        }, ensure_ascii=False, indent=2) + "\n"
        config_path.write_text(original, encoding="utf-8")

        # Patch _tool_configs to point to our test config
        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"test": {"name": "Test", "config_paths": [config_path]}},
        )

        auto_migrate()

        assert config_path.read_text(encoding="utf-8") == original

        # Migration log should exist inside ENGRAM_DIR and explain the manual action.
        log_file = tmp_path / "migration.log"
        assert log_file.is_file()
        log_text = log_file.read_text(encoding="utf-8")
        assert "piia-pkc" in log_text
        assert "external config left unchanged" in log_text
        assert "setup --apply-external-config" in log_text

    def test_auto_migrate_preserves_legacy_only_upgrade_config(self, tmp_path, monkeypatch):
        """Older users with only a legacy server entry must not be broken on startup."""
        from piia_engram.setup_wizard import auto_migrate

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))

        config_path = tmp_path / "home" / ".claude" / ".mcp.json"
        config_path.parent.mkdir(parents=True)
        original = json.dumps({
            "mcpServers": {
                "piia-pkc": {"command": "python", "args": ["-m", "piia_engram.mcp_server"]}
            }
        }, ensure_ascii=False, indent=2) + "\n"
        config_path.write_text(original, encoding="utf-8")

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {"test": {"name": "Test", "config_paths": [config_path]}},
        )

        auto_migrate()

        assert config_path.read_text(encoding="utf-8") == original
        log_text = (tmp_path / "engram-root" / "migration.log").read_text(encoding="utf-8")
        assert "piia-pkc" in log_text
        assert "external config left unchanged" in log_text

    def test_auto_migrate_logs_toml_legacy_without_external_mutation(self, tmp_path, monkeypatch):
        """Codex TOML configs should get guidance without silent mutation."""
        from piia_engram.setup_wizard import auto_migrate

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))

        config_path = tmp_path / "home" / ".codex" / "config.toml"
        config_path.parent.mkdir(parents=True)
        original = '\n'.join([
            '[mcp_servers.piia-pkc]',
            'command = "python"',
            'args = ["-m", "piia_engram.mcp_server"]',
            '',
        ])
        config_path.write_text(original, encoding="utf-8")

        monkeypatch.setattr(
            "piia_engram.setup_wizard._tool_configs",
            lambda: {
                "codex": {
                    "name": "Codex",
                    "config_paths": [config_path],
                    "format": "toml",
                    "server_key": "mcp_servers",
                }
            },
        )

        auto_migrate()

        assert config_path.read_text(encoding="utf-8") == original
        log_text = (tmp_path / "engram-root" / "migration.log").read_text(encoding="utf-8")
        assert "piia-pkc" in log_text
        assert "external config left unchanged" in log_text

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
            "2",   # enhanced search: not now
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
        assert "Piia Engram" in out
        assert "Step 1/3" in out
        assert "TestTool" in out

    def test_wizard_configures_codex_toml_without_json_rewrite(self, tmp_path, monkeypatch, capsys):
        """Explicit external config mode must dispatch Codex to the TOML writer."""
        from piia_engram.setup_wizard import run_setup
        from piia_engram.file_safety import read_ledger_entries

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
        monkeypatch.setattr("piia_engram.setup_wizard._scan_rule_files", lambda cwd=None: [])
        monkeypatch.setattr("piia_engram.setup_wizard._inject_instruction_snippet", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_hook", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_precompact_hook", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_sessionstart_hook", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_postcompact_hook", lambda *_args, **_kwargs: None)

        codex_config = tmp_path / ".codex" / "config.toml"
        codex_config.parent.mkdir()
        codex_config.write_text(
            '[settings]\napproval_policy = "never"\n',
            encoding="utf-8",
        )

        answers = iter([
            "2",   # language: English
            "",    # data dir: default from ENGRAM_DIR
            "2",   # enhanced search: not now
            "",    # seed: role
            "",    # seed: tech_stack
            "",    # seed: language
            "",    # seed: no lessons
            "",    # privacy: reconcile
            "",    # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr(
            "piia_engram.setup_wizard._detect_tools",
            lambda: [{
                "id": "codex",
                "name": "Codex",
                "config_path": codex_config,
                "format": "toml",
                "server_key": "mcp_servers",
            }],
        )
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: "/path/to/mcp_server.py")

        run_setup(apply_external_config=True)

        text = codex_config.read_text(encoding="utf-8")
        assert text.lstrip().startswith("[settings]")
        assert '[mcp_servers.engram]' in text
        assert 'args = ["-m", "piia_engram.mcp_server"]' in text
        assert '"mcpServers"' not in text
        assert not text.lstrip().startswith("{")
        assert list(codex_config.parent.glob("config.toml.engram-backup.*")) == []
        entries = read_ledger_entries(tmp_path / "engram-root")
        assert any(entry["scope"] == "external" for entry in entries)
        assert (tmp_path / "engram-root" / "backups" / "file_safety" / "external").is_dir()

    def test_wizard_custom_data_dir_is_used_for_report_and_client_env(
        self, tmp_path, monkeypatch, capsys
    ):
        """Setup should let users choose a custom Engram root during install."""
        from piia_engram.setup_wizard import run_setup

        default_root = tmp_path / "default-engram"
        custom_root = tmp_path / "custom-drive" / "EngramData"
        monkeypatch.setenv("ENGRAM_DIR", str(default_root))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
        monkeypatch.setattr("piia_engram.setup_wizard._scan_rule_files", lambda cwd=None: [])
        monkeypatch.setattr("piia_engram.setup_wizard._inject_instruction_snippet", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_hook", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_precompact_hook", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_sessionstart_hook", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_postcompact_hook", lambda *_args, **_kwargs: None)

        claude_config = tmp_path / ".claude" / ".mcp.json"
        claude_config.parent.mkdir()
        claude_config.write_text(
            json.dumps({"mcpServers": {}}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        answers = iter([
            "2",                 # language: English
            "c",                 # data dir: custom path
            str(custom_root),    # custom Engram root
            "2",                 # enhanced search: not now
            "",                  # seed: role
            "",                  # seed: tech_stack
            "",                  # seed: language
            "",                  # seed: no lessons
            "",                  # privacy: reconcile
            "",                  # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr(
            "piia_engram.setup_wizard._detect_tools",
            lambda: [{
                "id": "claude_code",
                "name": "Claude Code",
                "config_path": claude_config,
            }],
        )
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: "/path/to/mcp_server.py")

        run_setup(apply_external_config=True)

        config = json.loads(claude_config.read_text(encoding="utf-8"))
        assert config["mcpServers"]["engram"]["env"]["ENGRAM_DIR"] == str(custom_root)
        assert (custom_root / "setup_report.jsonl").is_file()
        assert (custom_root / "quick_context.md").is_file()
        assert not (default_root / "quick_context.md").exists()
        assert not (default_root / "setup_report.jsonl").exists()
        report = json.loads(
            (custom_root / "setup_report.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        )
        assert report["external_config_mode"] == "apply"

    def test_wizard_decline_consent_does_not_mutate_external_client_configs(
        self, tmp_path, monkeypatch, capsys
    ):
        """When the user declines the write-consent prompt, setup is read-only."""
        from piia_engram.setup_wizard import run_setup

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
        monkeypatch.setattr("piia_engram.setup_wizard._scan_rule_files", lambda cwd=None: [])

        codex_config = tmp_path / ".codex" / "config.toml"
        codex_config.parent.mkdir()
        codex_original = '[settings]\napproval_policy = "never"\n'
        codex_config.write_text(codex_original, encoding="utf-8")

        claude_config = tmp_path / ".claude" / ".mcp.json"
        claude_config.parent.mkdir()
        claude_original = json.dumps({
            "mcpServers": {
                "existing": {"command": "node", "args": ["server.js"]}
            }
        }, ensure_ascii=False, indent=2) + "\n"
        claude_config.write_text(claude_original, encoding="utf-8")

        calls: list[tuple] = []
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_instruction_snippet",
            lambda *args, **kwargs: calls.append(("snippet", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_hook",
            lambda *args, **kwargs: calls.append(("stop", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_precompact_hook",
            lambda *args, **kwargs: calls.append(("pre", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_sessionstart_hook",
            lambda *args, **kwargs: calls.append(("start", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_postcompact_hook",
            lambda *args, **kwargs: calls.append(("post", args, kwargs)),
        )

        answers = iter([
            "2",   # language: English
            "",    # data dir: default from ENGRAM_DIR
            "2",   # enhanced search: not now
            "2",   # external config consent: No (read-only)
            "",    # seed: role
            "",    # seed: tech_stack
            "",    # seed: language
            "",    # seed: no lessons
            "",    # privacy: reconcile
            "",    # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr(
            "piia_engram.setup_wizard._detect_tools",
            lambda: [
                {
                    "id": "claude_code",
                    "name": "Claude Code",
                    "config_path": claude_config,
                },
                {
                    "id": "codex",
                    "name": "Codex",
                    "config_path": codex_config,
                    "format": "toml",
                    "server_key": "mcp_servers",
                },
            ],
        )
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: "/path/to/mcp_server.py")

        run_setup()

        out = capsys.readouterr().out
        assert "no external config files were changed" in out.lower()
        assert "apply-external-config" in out
        assert "External AI tool configs are unchanged" in out
        assert "Restart your AI tool to get started" not in out
        assert codex_config.read_text(encoding="utf-8") == codex_original
        assert claude_config.read_text(encoding="utf-8") == claude_original
        assert list(codex_config.parent.glob("*.engram-backup.*")) == []
        assert list(claude_config.parent.glob("*.engram-backup.*")) == []
        assert calls == []

        report_lines = (tmp_path / "engram-root" / "setup_report.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        report = json.loads(report_lines[-1])
        assert report["tools_configured"] == []
        assert report["tools_failed"] == []
        assert report["external_config_mode"] == "read_only"

    def test_wizard_default_writes_after_consent(
        self, tmp_path, monkeypatch, capsys
    ):
        """Default setup (no flag) writes external configs once the user
        confirms the consent prompt — the one-keystroke activation path."""
        from piia_engram.setup_wizard import run_setup

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
        monkeypatch.setattr("piia_engram.setup_wizard._scan_rule_files", lambda cwd=None: [])

        claude_config = tmp_path / ".claude" / ".mcp.json"
        claude_config.parent.mkdir()
        claude_original = json.dumps({
            "mcpServers": {
                "existing": {"command": "node", "args": ["server.js"]}
            }
        }, ensure_ascii=False, indent=2) + "\n"
        claude_config.write_text(claude_original, encoding="utf-8")

        calls: list[tuple] = []
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_instruction_snippet",
            lambda *args, **kwargs: calls.append(("snippet", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_hook",
            lambda *args, **kwargs: calls.append(("stop", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_precompact_hook",
            lambda *args, **kwargs: calls.append(("pre", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_sessionstart_hook",
            lambda *args, **kwargs: calls.append(("start", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_postcompact_hook",
            lambda *args, **kwargs: calls.append(("post", args, kwargs)),
        )

        answers = iter([
            "2",   # language: English
            "",    # data dir: default from ENGRAM_DIR
            "2",   # enhanced search: not now
            "1",   # external config consent: Yes, auto-configure
            "",    # seed: role
            "",    # seed: tech_stack
            "",    # seed: language
            "",    # seed: no lessons
            "",    # privacy: reconcile
            "",    # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr(
            "piia_engram.setup_wizard._detect_tools",
            lambda: [
                {
                    "id": "claude_code",
                    "name": "Claude Code",
                    "config_path": claude_config,
                },
            ],
        )
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: "/path/to/mcp_server.py")

        run_setup()

        out = capsys.readouterr().out
        # The consent prompt and the file list must be shown.
        assert "Detected AI tools" in out
        assert str(claude_config) in out
        # Config was actually written (engram server now present, existing kept).
        config = json.loads(claude_config.read_text(encoding="utf-8"))
        assert "engram" in config["mcpServers"]
        assert "existing" in config["mcpServers"]
        # Instruction snippet + hooks were injected for claude_code.
        assert any(c[0] == "snippet" for c in calls)
        assert any(c[0] == "stop" for c in calls)
        # Report records an applied external config.
        report_lines = (tmp_path / "engram-root" / "setup_report.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        report = json.loads(report_lines[-1])
        assert report["external_config_mode"] == "apply"
        assert "Claude Code" in report["tools_configured"]

    def test_wizard_failed_config_write_does_not_inject_or_report_configured(
        self, tmp_path, monkeypatch, capsys
    ):
        """If a client config is unsafe to mutate, setup must fail closed."""
        from piia_engram.setup_wizard import run_setup

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
        monkeypatch.setattr("piia_engram.setup_wizard._scan_rule_files", lambda cwd=None: [])

        config_path = tmp_path / ".claude" / ".mcp.json"
        config_path.parent.mkdir()
        original = '{\n  // user-managed jsonc\n  "mcpServers": {}\n}\n'
        config_path.write_text(original, encoding="utf-8")

        calls: list[tuple] = []
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_instruction_snippet",
            lambda *args, **kwargs: calls.append(("snippet", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_hook",
            lambda *args, **kwargs: calls.append(("stop", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_precompact_hook",
            lambda *args, **kwargs: calls.append(("pre", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_sessionstart_hook",
            lambda *args, **kwargs: calls.append(("start", args, kwargs)),
        )
        monkeypatch.setattr(
            "piia_engram.setup_wizard._inject_claude_code_postcompact_hook",
            lambda *args, **kwargs: calls.append(("post", args, kwargs)),
        )

        answers = iter([
            "2",   # language: English
            "",    # data dir: default from ENGRAM_DIR
            "2",   # enhanced search: not now
            "",    # seed: role
            "",    # seed: tech_stack
            "",    # seed: language
            "",    # seed: no lessons
            "",    # privacy: reconcile
            "",    # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr(
            "piia_engram.setup_wizard._detect_tools",
            lambda: [{
                "id": "claude_code",
                "name": "Claude Code",
                "config_path": config_path,
            }],
        )
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: "/path/to/mcp_server.py")

        run_setup(apply_external_config=True)

        out = capsys.readouterr().out
        assert "Claude Code" in out
        assert "failed" in out
        assert "refusing to overwrite" in out
        assert config_path.read_text(encoding="utf-8") == original
        assert calls == []

        report_lines = (tmp_path / "engram-root" / "setup_report.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        report = json.loads(report_lines[-1])
        assert report["tools_configured"] == []
        assert any("Claude Code" in item for item in report["tools_failed"])
        assert report["external_config_mode"] == "apply"

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
            "2",   # enhanced search: not now
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
            "2",         # enhanced search: not now
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


# ── Enhanced search (hybrid) setup offer ───────────────────────────


class TestHybridSearchOffer:
    def test_wizard_hybrid_optin_writes_env_and_builds_index(
        self, tmp_path, monkeypatch, capsys
    ):
        """Opting in must persist ENGRAM_SEARCH=hybrid into JSON + TOML client
        configs and build the search index at the end of setup."""
        from piia_engram.setup_wizard import run_setup

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))
        monkeypatch.setenv("ENGRAM_SEARCH", "keyword")  # registers env restore
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
        monkeypatch.setattr("piia_engram.setup_wizard._scan_rule_files", lambda cwd=None: [])
        monkeypatch.setattr("piia_engram.setup_wizard._inject_instruction_snippet", lambda *_a, **_k: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_hook", lambda *_a, **_k: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_precompact_hook", lambda *_a, **_k: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_sessionstart_hook", lambda *_a, **_k: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_postcompact_hook", lambda *_a, **_k: None)
        # Deterministic regardless of whether fastembed is installed locally.
        monkeypatch.setattr("piia_engram.setup_wizard._vector_deps_available", lambda: True)

        claude_config = tmp_path / ".claude" / ".mcp.json"
        claude_config.parent.mkdir()
        claude_config.write_text('{"mcpServers": {}}\n', encoding="utf-8")
        codex_config = tmp_path / ".codex" / "config.toml"
        codex_config.parent.mkdir()
        codex_config.write_text('[settings]\napproval_policy = "never"\n', encoding="utf-8")

        answers = iter([
            "2",   # language: English
            "",    # data dir: default from ENGRAM_DIR
            "1",   # enhanced search: enable
            "",    # seed: role
            "",    # seed: tech_stack
            "",    # seed: language
            "",    # seed: no lessons
            "",    # privacy: reconcile
            "",    # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr(
            "piia_engram.setup_wizard._detect_tools",
            lambda: [
                {"id": "claude_code", "name": "Claude Code", "config_path": claude_config},
                {"id": "codex", "name": "Codex", "config_path": codex_config,
                 "format": "toml", "server_key": "mcp_servers"},
            ],
        )
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: "/path/to/mcp_server.py")

        run_setup(apply_external_config=True)

        config = json.loads(claude_config.read_text(encoding="utf-8"))
        assert config["mcpServers"]["engram"]["env"]["ENGRAM_SEARCH"] == "hybrid"
        assert 'ENGRAM_SEARCH = "hybrid"' in codex_config.read_text(encoding="utf-8")
        out = capsys.readouterr().out
        assert "Enhanced search enabled" in out
        assert "Search index built" in out

    def test_wizard_hybrid_skip_leaves_configs_clean(
        self, tmp_path, monkeypatch, capsys
    ):
        """The default (not now) must not write ENGRAM_SEARCH anywhere."""
        from piia_engram.setup_wizard import run_setup

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-root"))
        monkeypatch.delenv("ENGRAM_SEARCH", raising=False)
        monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
        monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
        monkeypatch.setattr("piia_engram.setup_wizard.Path.home", lambda: tmp_path)
        monkeypatch.setattr("piia_engram.setup_wizard._probe_environment", lambda cwd=None: {})
        monkeypatch.setattr("piia_engram.setup_wizard._scan_rule_files", lambda cwd=None: [])
        monkeypatch.setattr("piia_engram.setup_wizard._inject_instruction_snippet", lambda *_a, **_k: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_hook", lambda *_a, **_k: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_precompact_hook", lambda *_a, **_k: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_sessionstart_hook", lambda *_a, **_k: None)
        monkeypatch.setattr("piia_engram.setup_wizard._inject_claude_code_postcompact_hook", lambda *_a, **_k: None)

        claude_config = tmp_path / ".claude" / ".mcp.json"
        claude_config.parent.mkdir()
        claude_config.write_text('{"mcpServers": {}}\n', encoding="utf-8")

        answers = iter([
            "2",   # language: English
            "",    # data dir: default from ENGRAM_DIR
            "",    # enhanced search: default (not now)
            "",    # seed: role
            "",    # seed: tech_stack
            "",    # seed: language
            "",    # seed: no lessons
            "",    # privacy: reconcile
            "",    # privacy: telemetry
        ])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))
        monkeypatch.setattr(
            "piia_engram.setup_wizard._detect_tools",
            lambda: [{"id": "claude_code", "name": "Claude Code", "config_path": claude_config}],
        )
        monkeypatch.setattr("piia_engram.setup_wizard._find_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr("piia_engram.setup_wizard._find_mcp_server", lambda: "/path/to/mcp_server.py")

        run_setup(apply_external_config=True)

        config = json.loads(claude_config.read_text(encoding="utf-8"))
        assert "ENGRAM_SEARCH" not in config["mcpServers"]["engram"]["env"]
        assert os.environ.get("ENGRAM_SEARCH") is None

    def test_rerun_setup_preserves_existing_engram_search(self, tmp_path):
        """Re-running setup must not silently disable previously enabled
        hybrid search (the env block is rebuilt from scratch on rewrite)."""
        from piia_engram.setup_wizard import _write_mcp_config, _write_mcp_config_toml

        config_path = tmp_path / ".mcp.json"
        _write_mcp_config(
            config_path, "/usr/bin/python3", "/path/to/mcp_server.py",
            data_dir=str(tmp_path), file_safety_root=tmp_path,
            authorized_external_write=True,
            extra_env={"ENGRAM_SEARCH": "hybrid"},
        )
        # Rewrite without extra_env (a later plain re-run of setup).
        _write_mcp_config(
            config_path, "/usr/bin/python3", "/path/to/mcp_server.py",
            data_dir=str(tmp_path), file_safety_root=tmp_path,
            authorized_external_write=True,
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["mcpServers"]["engram"]["env"]["ENGRAM_SEARCH"] == "hybrid"

        toml_path = tmp_path / "config.toml"
        _write_mcp_config_toml(
            toml_path, "/usr/bin/python3", "/path/to/mcp_server.py",
            data_dir=str(tmp_path), file_safety_root=tmp_path,
            authorized_external_write=True,
            extra_env={"ENGRAM_SEARCH": "hybrid"},
        )
        _write_mcp_config_toml(
            toml_path, "/usr/bin/python3", "/path/to/mcp_server.py",
            data_dir=str(tmp_path), file_safety_root=tmp_path,
            authorized_external_write=True,
        )
        assert 'ENGRAM_SEARCH = "hybrid"' in toml_path.read_text(encoding="utf-8")

    def test_offer_installs_vector_deps_on_consent(self, monkeypatch, capsys):
        """Enable + install consent must invoke pip against the wizard's
        chosen Python; pip success is reported as installed."""
        from piia_engram.setup_wizard import _run_hybrid_search_offer

        monkeypatch.setattr("piia_engram.i18n._runtime_lang", "en")
        monkeypatch.setenv("ENGRAM_SEARCH", "keyword")  # registers env restore
        monkeypatch.setattr("piia_engram.setup_wizard._vector_deps_available", lambda: False)
        calls: list[list[str]] = []

        def fake_call(cmd, *args, **kwargs):
            calls.append(list(cmd))
            return 0

        monkeypatch.setattr("piia_engram.setup_wizard.subprocess.call", fake_call)
        answers = iter(["1", "1"])  # enable, install now
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))

        assert _run_hybrid_search_offer("/opt/py/python") is True
        assert os.environ["ENGRAM_SEARCH"] == "hybrid"
        assert calls == [["/opt/py/python", "-m", "pip", "install", "piia-engram[vector]"]]
        out = capsys.readouterr().out
        assert "Vector dependencies installed" in out

    def test_offer_skip_install_still_enables_hybrid(self, monkeypatch, capsys):
        """Declining the dep install must still enable hybrid (keyword+FTS)."""
        from piia_engram.setup_wizard import _run_hybrid_search_offer

        monkeypatch.setattr("piia_engram.i18n._runtime_lang", "en")
        monkeypatch.setenv("ENGRAM_SEARCH", "keyword")
        monkeypatch.setattr("piia_engram.setup_wizard._vector_deps_available", lambda: False)
        monkeypatch.setattr(
            "piia_engram.setup_wizard.subprocess.call",
            lambda *_a, **_k: pytest.fail("pip must not run when install is declined"),
        )
        answers = iter(["1", "2"])  # enable, skip install
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, ""))

        assert _run_hybrid_search_offer("/opt/py/python") is True
        assert os.environ["ENGRAM_SEARCH"] == "hybrid"
        out = capsys.readouterr().out
        assert "Enhanced search enabled" in out


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

    def test_main_sessions_dispatches(self, tmp_path, monkeypatch, capsys):
        """main() with 'sessions' should dispatch to run_sessions."""
        import piia_engram.setup_wizard as sw

        seen = {}

        def fake_run_sessions(argv):
            seen["argv"] = argv
            return 0

        monkeypatch.setattr(sw, "run_sessions", fake_run_sessions)
        monkeypatch.setattr("sys.argv", ["engram", "sessions", "--limit", "3"])

        with pytest.raises(SystemExit) as exc_info:
            sw.main()

        assert exc_info.value.code == 0
        assert seen["argv"] == ["--limit", "3"]

    def test_main_continuity_dispatches(self, monkeypatch):
        """main() with 'continuity' should dispatch to run_continuity."""
        import piia_engram.setup_wizard as sw

        seen = {}

        def fake_run_continuity(argv):
            seen["argv"] = argv
            return 0

        monkeypatch.setattr(sw, "run_continuity", fake_run_continuity)
        monkeypatch.setattr("sys.argv", ["engram", "continuity", "--json"])

        with pytest.raises(SystemExit) as exc_info:
            sw.main()

        assert exc_info.value.code == 0
        assert seen["argv"] == ["--json"]

    def test_continuity_cli_prints_metadata_only(
        self, tmp_path, monkeypatch, capsys
    ):
        """engram continuity should not print saved session bodies."""
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import run_continuity

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        secret = "ZZ_CONTINUITY_CLI_SECRET"
        eng = Engram()
        eng.save_agent_context(tool="claude_code", content=secret)
        eng.save_agent_context(tool="codex", content=secret)

        assert run_continuity(["--project", str(tmp_path)]) == 0

        out = capsys.readouterr().out
        assert "Engram continuity proof" in out
        assert "2 saved session" in out
        assert "claude_code" in out
        assert "codex" in out
        assert secret not in out
        assert str(tmp_path) not in out

    def test_main_repair_encoding_dry_run_dispatches(self, tmp_path, monkeypatch, capsys):
        """repair-encoding should dry-run by default and report findings."""
        from piia_engram.setup_wizard import main

        damaged = "发布流程测试".encode("utf-8").decode("gbk")
        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        (kdir / "lessons.json").write_text(
            json.dumps([{"id": "l1", "summary": damaged}], ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr("sys.argv", ["engram", "repair-encoding"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "knowledge\\lessons.json" in out or "knowledge/lessons.json" in out

    def test_main_repair_encoding_clean_result_points_to_display_encoding(
        self, tmp_path, monkeypatch, capsys
    ):
        """A clean data scan should distinguish storage health from terminal display."""
        from piia_engram.setup_wizard import main

        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        (kdir / "lessons.json").write_text(
            json.dumps([{"id": "l1", "summary": "发布流程测试"}], ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr("sys.argv", ["engram", "repair-encoding"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "no mojibake detected" in out
        assert "This confirms stored Engram data is clean" in out
        assert "Get-Content -Encoding utf8" in out

    def test_main_repair_encoding_no_backup_warns_before_apply(
        self, tmp_path, monkeypatch, capsys
    ):
        from piia_engram.setup_wizard import main

        damaged = "发布流程测试".encode("utf-8").decode("gbk")
        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        (kdir / "lessons.json").write_text(
            json.dumps([{"id": "l1", "summary": damaged}], ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr(
            "sys.argv",
            ["engram", "repair-encoding", "--apply", "--no-backup"],
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "--no-backup disables automatic backup" in out
        assert "repaired 1 field" in out

    def test_main_recover_json_dry_run_redacts_content(self, tmp_path, monkeypatch, capsys):
        from piia_engram.setup_wizard import main

        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        (kdir / "lessons.json").write_bytes(b"\xef\xbb\xbf[]\r\n")
        (kdir / "lessons.corrupt.20260531_010203.json").write_text(
            json.dumps([
                {
                    "id": "l1",
                    "summary": "CLI_SECRET_SUMMARY",
                    "detail": "CLI_SECRET_DETAIL",
                    "tier": "verified",
                    "created_at": "2026-05-31T01:02:03",
                }
            ]),
            encoding="utf-8",
        )
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr("sys.argv", ["engram", "recover-json", "lessons"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "lessons.corrupt.20260531_010203.json" in out
        assert "entries=1" in out
        assert "CLI_SECRET" not in out

    def test_main_import_preview_is_json_metadata_only_and_read_only(
        self, tmp_path, monkeypatch, capsys
    ):
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import main

        source = Engram(root=tmp_path / "source")
        source.add_lesson({
            "summary": "CLI import topic",
            "detail": "CLI_IMPORT_INCOMING_SECRET",
        })
        backup = source.export_all(str(tmp_path / "backup.json"))

        target_root = tmp_path / "target"
        target = Engram(root=target_root)
        target.add_lesson({
            "summary": "CLI import topic",
            "detail": "CLI_IMPORT_LOCAL_SECRET",
        })
        data_dirs = {"identity", "knowledge", "playbooks", "projects", "environment"}
        before = {
            str(path.relative_to(target_root)): path.read_bytes()
            for path in sorted(target_root.rglob("*"))
            if path.is_file() and path.relative_to(target_root).parts[0] in data_dirs
        }

        monkeypatch.setenv("ENGRAM_DIR", str(target_root))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr("sys.argv", ["engram", "import", backup, "--json"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        serialized = json.dumps(payload, ensure_ascii=False)
        after = {
            str(path.relative_to(target_root)): path.read_bytes()
            for path in sorted(target_root.rglob("*"))
            if path.is_file() and path.relative_to(target_root).parts[0] in data_dirs
        }
        assert payload["status"] == "preview"
        assert payload["dry_run"] is True
        assert payload["summary"]["lessons"]["conflicts"] == 1
        assert "CLI_IMPORT_INCOMING_SECRET" not in serialized
        assert "CLI_IMPORT_LOCAL_SECRET" not in serialized
        assert after == before

    def test_main_import_apply_requires_yes_then_mutates(
        self, tmp_path, monkeypatch, capsys
    ):
        from piia_engram.core import Engram
        from piia_engram.setup_wizard import main

        source = Engram(root=tmp_path / "source")
        source.add_lesson({"summary": "CLI apply imported lesson"})
        backup = source.export_all(str(tmp_path / "backup.json"))
        target_root = tmp_path / "target"
        Engram(root=target_root)

        monkeypatch.setenv("ENGRAM_DIR", str(target_root))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr(
            "sys.argv",
            ["engram", "import", backup, "--apply", "--json"],
        )

        with pytest.raises(SystemExit) as denied:
            main()

        assert denied.value.code == 1
        denied_payload = json.loads(capsys.readouterr().out)
        assert denied_payload["requires_confirmation"] is True
        assert denied_payload["status"] == "preview"
        assert denied_payload["summary"]["lessons"]["would_add"] == 1
        assert Engram(root=target_root).get_lessons(limit=None) == []

        monkeypatch.setattr(
            "sys.argv",
            ["engram", "import", backup, "--apply", "--yes", "--json"],
        )

        with pytest.raises(SystemExit) as applied:
            main()

        assert applied.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "success"
        lessons = Engram(root=target_root).get_lessons(limit=None)
        assert [lesson["summary"] for lesson in lessons] == ["CLI apply imported lesson"]

    def test_main_import_apply_materialize_version_chain_flag(
        self, tmp_path, monkeypatch, capsys
    ):
        from piia_engram.core import Engram
        from piia_engram.governance_store import RelationStore
        from piia_engram.setup_wizard import main

        source = Engram(root=tmp_path / "source")
        source.add_lesson({
            "summary": "CLI materialize import topic",
            "detail": "CLI_MATERIALIZE_INCOMING_SECRET_DETAIL",
        })
        backup = source.export_all(str(tmp_path / "backup.json"))
        target_root = tmp_path / "target"
        target = Engram(root=target_root)
        local = target.add_lesson({
            "summary": "CLI materialize import topic",
            "detail": "CLI_MATERIALIZE_LOCAL_SECRET_DETAIL",
        })

        monkeypatch.setenv("ENGRAM_DIR", str(target_root))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr(
            "sys.argv",
            [
                "engram",
                "import",
                backup,
                "--apply",
                "--yes",
                "--materialize-version-chain",
                "--json",
            ],
        )

        with pytest.raises(SystemExit) as applied:
            main()

        assert applied.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        serialized = json.dumps(payload, ensure_ascii=False)
        vc = payload["version_chain_materialization"]
        assert vc["materialized"] == 1
        assert vc["items"][0]["existing_id"] == local["id"]
        assert "CLI_MATERIALIZE_INCOMING_SECRET" not in serialized
        assert "CLI_MATERIALIZE_LOCAL_SECRET" not in serialized
        assert {"src": vc["items"][0]["new_id"], "rel": "supersedes", "dst": local["id"]} in RelationStore(
            target_root
        ).all_edges()

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


class TestDoctorEvidenceTrackedCommunity:
    """doctor 应区分已验证和社区级工具。"""

    def test_evidence_tracked_label_shown(self, tmp_path, monkeypatch, capsys):
        """Evidence-tracked tools should appear under evidence-tracked section."""
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
        assert "Evidence-tracked setup paths" in out
        assert "Claude Code" in out

    def test_community_label_shown(self, tmp_path, monkeypatch, capsys):
        """Community tools should appear under expected/community section."""
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
        assert "Expected/community setup paths" in out
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
        assert "Evidence-tracked setup paths" in out
        assert "Expected/community setup paths" in out
        assert "Claude Code" in out
        assert "Windsurf" in out


class TestDoctorFix:
    def test_doctor_fix_repairs_invalid_path(self, tmp_path, monkeypatch, capsys):
        """doctor --fix should repair external config with Engram-root backup."""
        from piia_engram.setup_wizard import run_doctor, _find_python, _find_mcp_server
        from piia_engram.file_safety import read_ledger_entries

        python_path = _find_python()
        mcp_path = _find_mcp_server()
        if not python_path or not mcp_path:
            pytest.skip("Cannot find Python or mcp_server.py")

        engram_root = tmp_path / "engram-root"
        monkeypatch.setenv("ENGRAM_DIR", str(engram_root))

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
        assert result >= 0
        assert list(config_dir.glob("*.engram-backup.*")) == []
        backups = list((engram_root / "backups" / "file_safety" / "external").glob(".mcp.json.*.bak"))
        assert len(backups) == 1
        entries = read_ledger_entries(engram_root)
        assert any(entry["scope"] == "external" for entry in entries)

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
    def test_classifies_supported_mcp_entry_shapes(self):
        from piia_engram.setup_wizard import _classify_engram_entry

        cases = [
            (
                {"command": "uvx", "args": ["--from", "piia-engram", "piia-engram-mcp"]},
                "recommended-uvx",
                "ok",
                ["uvx", "--from", "piia-engram", "piia-engram-mcp", "--help"],
            ),
            (
                {"command": "piia-engram-mcp", "args": []},
                "recommended-console-script",
                "ok",
                ["piia-engram-mcp", "--help"],
            ),
            (
                {"command": "python", "args": ["-m", "piia_engram.mcp_server"]},
                "compatible-python-module",
                "ok",
                ["python", "-m", "piia_engram.mcp_server", "--help"],
            ),
            (
                {"command": "python", "args": ["/tmp/mcp_server.py"]},
                "legacy-script-path",
                "warn",
                None,
            ),
            (
                {"command": "node", "args": ["server.js"]},
                "unknown",
                "warn",
                None,
            ),
        ]

        for entry, style, severity, probe in cases:
            result = _classify_engram_entry(entry)
            assert result["style"] == style
            assert result["severity"] == severity
            assert result["probe_argv"] == probe

    def test_classifies_invalid_mcp_entry_shapes(self):
        from piia_engram.setup_wizard import _classify_engram_entry

        missing = _classify_engram_entry({"args": []})
        bad_args = _classify_engram_entry({"command": "python", "args": "-m x"})

        assert missing["style"] == "invalid"
        assert missing["severity"] == "error"
        assert bad_args["style"] == "invalid"
        assert bad_args["severity"] == "error"

    def test_probe_mcp_entry_uses_bounded_help_command(self, monkeypatch):
        from piia_engram.setup_wizard import _probe_mcp_entry

        calls = []

        class Result:
            returncode = 0
            stdout = "Engram MCP Server"
            stderr = ""

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        issue = _probe_mcp_entry(
            {"command": "piia-engram-mcp", "args": []},
            timeout=5,
        )

        assert issue is None
        assert calls[0][0] == ["piia-engram-mcp", "--help"]
        assert calls[0][1]["timeout"] == 5
        assert calls[0][1]["capture_output"] is True

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

    def test_user_lines_grouped_into_one_lesson(self, tmp_path):
        """A2: 多条 user 规则应汇成 *一条* user_preference lesson，而非逐行碎片。"""
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        rule_files = [{
            "path": tmp_path / "CLAUDE.md",
            "scope": "global",
            "lines": [
                "I prefer concise answers in all conversations",
                "Always communicate using my preferred style",
                "My role is a non-technical founder learning to build",
            ],
        }]
        result = _import_with_split(rule_files, engram)

        assert result["user_lessons"] == 1
        assert result["user_count"] == 3  # 行计数契约保留
        lessons = engram.get_lessons(domain="user_preference", _update_access=False)
        assert len(lessons) == 1

    def test_project_lines_grouped_into_one_lesson(self, tmp_path):
        """A2: 多条 project 规则应汇成 *一条* project_rules lesson。"""
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        rule_files = [{
            "path": tmp_path / ".cursorrules",
            "scope": "project",
            "lines": [
                "Run the test suite before every commit to this repo",
                "Keep the build green; do not merge failing pipelines",
            ],
        }]
        result = _import_with_split(rule_files, engram)

        assert result["project_lessons"] == 1
        lessons = engram.get_lessons(domain="project_rules", _update_access=False)
        assert len(lessons) == 1

    def test_provenance_kept_in_detail(self, tmp_path):
        """A2: detail 应按来源文件分节（## 标签）保留出处，且用相对标签不存绝对路径。"""
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        rule_files = [{
            "path": tmp_path / "CLAUDE.md",
            "scope": "global",
            "lines": [
                "I prefer concise answers in all conversations",
                "Always communicate using my preferred style",
            ],
        }]
        _import_with_split(rule_files, engram)

        lessons = engram.get_lessons(domain="user_preference", _update_access=False)
        detail = lessons[0].get("detail", "")
        # 分节标题在（父目录名/文件名）
        assert f"## {tmp_path.name}/CLAUDE.md" in detail
        # 规则正文进入 detail
        assert "I prefer concise answers" in detail
        # 不应泄漏绝对路径
        assert str(tmp_path) + "/CLAUDE.md" not in detail

    def test_imported_lessons_tagged_with_setup_source(self, tmp_path):
        """A3: 导入的 lesson 应带 source_tool=engram_setup，doctor 才能统计/引导复核。"""
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        rule_files = [{
            "path": tmp_path / "CLAUDE.md",
            "scope": "global",
            "lines": [
                "I prefer concise answers in all conversations",
                "My role is a non-technical founder learning to build",
            ],
        }]
        _import_with_split(rule_files, engram)

        tagged = engram.get_lessons(
            source_tool="engram_setup", limit=None, _update_access=False
        )
        assert len(tagged) >= 1

    def test_multiple_files_merge_into_one_lesson_with_sections(self, tmp_path):
        """A2: 同类(user)多个来源文件应合并为 *一条* lesson，detail 各自分节。

        同时覆盖「同名文件不同目录」的边界：父目录名用于区分，两个分节都要在。
        """
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        dir_a = tmp_path / "projA"
        dir_b = tmp_path / "projB"
        dir_a.mkdir()
        dir_b.mkdir()
        rule_files = [
            {"path": dir_a / "CLAUDE.md", "scope": "global",
             "lines": ["I prefer concise answers in all conversations",
                       "Explain trade-offs before deciding anything"]},
            {"path": dir_b / "CLAUDE.md", "scope": "global",
             "lines": ["I am a non-technical founder learning to build",
                       "Use plain language and avoid heavy jargon"]},
        ]
        result = _import_with_split(rule_files, engram)

        assert result["user_lessons"] == 1          # 合并成一条
        assert result["user_count"] == 4            # 行计数契约：4 行
        lessons = engram.get_lessons(domain="user_preference", _update_access=False)
        assert len(lessons) == 1
        detail = lessons[0].get("detail", "")
        # 同名文件不同目录 → 用父目录名区分，两个分节都要在
        assert "## projA/CLAUDE.md" in detail
        assert "## projB/CLAUDE.md" in detail
        assert "Explain trade-offs before deciding" in detail
        assert "Use plain language and avoid heavy jargon" in detail

    def test_reimport_refreshes_lesson_not_dropped_by_dedup(self, tmp_path):
        """#1 真 bug 防回归：第二次 setup 应 upsert 刷新同一条，而非被去重丢弃。

        分组 lesson 用固定模板 summary；add_lesson 的 summary 相似度去重会把第二
        次导入判为重复 → 规则更新无法落地。upsert 修复后：仍是一条，detail 反映新内容。
        """
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        v1 = [{
            "path": tmp_path / "CLAUDE.md", "scope": "global",
            "lines": ["I prefer very concise answers always",
                      "My role is a non-technical founder"],
        }]
        _import_with_split(v1, engram)
        first = engram.get_lessons(domain="user_preference", _update_access=False)
        assert len(first) == 1
        assert "always cite the source files" not in first[0].get("detail", "")

        # 用户更新了规则文件后重新跑 setup
        v2 = [{
            "path": tmp_path / "CLAUDE.md", "scope": "global",
            "lines": ["I prefer very concise answers always",
                      "My role is a non-technical founder",
                      "New rule: always cite the source files"],
        }]
        result2 = _import_with_split(v2, engram)

        assert result2["user_lessons"] == 1
        lessons = engram.get_lessons(domain="user_preference", _update_access=False)
        # 关键：仍是一条 —— 既没被去重丢弃，也没新增重复条
        assert len(lessons) == 1
        # 关键：detail 反映了第二次的新增规则（证明是刷新而非丢弃）
        assert "New rule: always cite the source files" in lessons[0].get("detail", "")

    def test_reimport_archives_legacy_line_by_line_fragments(self, tmp_path):
        """M1 迁移防回归：早期逐行导入留下的多条 engram_setup 碎片，

        重新跑 setup 后应只剩一条 active（canonical 被刷新），其余碎片被归档
        （status != active → get_lessons 不再返回），避免新旧并存污染。
        """
        from piia_engram.core import Engram
        engram = Engram(root=tmp_path)

        # 模拟旧版逐行导入：同 domain 下多条 engram_setup lesson（不同 summary 才不会被去重）
        engram.add_lesson("Old fragment one about concise answers",
                          domain="user_preference", detail="frag1",
                          source_tool="engram_setup")
        engram.add_lesson("Old fragment two about plain language",
                          domain="user_preference", detail="frag2",
                          source_tool="engram_setup")
        engram.add_lesson("Old fragment three about founder role",
                          domain="user_preference", detail="frag3",
                          source_tool="engram_setup")
        before = engram.get_lessons(domain="user_preference",
                                    source_tool="engram_setup",
                                    limit=None, _update_access=False)
        assert len(before) == 3  # 旧碎片确实并存

        # 重新跑 setup（升级后的合并导入）
        rule_files = [{
            "path": tmp_path / "CLAUDE.md", "scope": "global",
            "lines": ["I prefer very concise answers always",
                      "Use plain language and avoid jargon"],
        }]
        result = _import_with_split(rule_files, engram)

        assert result["user_lessons"] == 1
        active = engram.get_lessons(domain="user_preference",
                                    source_tool="engram_setup",
                                    limit=None, _update_access=False)
        # 关键：碎片被归整为一条 active，其余旧碎片已归档不再返回
        assert len(active) == 1
        # 关键：canonical 那条被刷新成最新合并内容（不再是旧 frag1 文本）
        assert "Use plain language and avoid jargon" in active[0].get("detail", "")

    def test_detail_truncation_happens_at_line_boundary(self, tmp_path):
        """M2：detail 超过上限时在行边界截断，不把某条规则切成半行。"""
        from piia_engram.setup_wizard import _build_grouped_detail, _MAX_DETAIL_CHARS

        # 造一批等长规则行，总长远超上限，强制触发截断
        rule = "x" * 80
        n = (_MAX_DETAIL_CHARS // 81) + 50  # 每行约 "- " + 80 + "\n"
        sections = {"dir/CLAUDE.md": [rule for _ in range(n)]}
        detail = _build_grouped_detail(sections)

        assert detail.endswith("…(truncated)")
        body = detail[: -len("\n\n…(truncated)")]
        # 关键：截断后正文每一行要么是分节标题，要么是完整的 "- <80个x>"，
        # 不能出现被切半的残缺行
        for line in body.splitlines():
            if line.startswith("## "):
                continue
            assert line == f"- {rule}", f"出现被截断的半行规则: {line!r}"


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

    def test_reads_beyond_200_lines(self, tmp_path, monkeypatch):
        """A1: 不再卡在旧的 200 行硬上限，应读到全文（覆盖典型 CLAUDE.md 全长）。"""
        # 清掉可能由外部 env 注入的上限覆盖，保证测试断言绑定的是代码常量
        monkeypatch.delenv("ENGRAM_MAX_RULE_LINES", raising=False)
        from piia_engram.setup_wizard import _MAX_RULE_LINES

        _OLD_CAP = 200
        # 取一个明显超过旧 200 行上限、又在新上限以内的行数；
        # 绑定常量，若将来 _MAX_RULE_LINES 被调到 ≤200 这条会立刻报警。
        assert _MAX_RULE_LINES > _OLD_CAP, "新上限不应回退到旧的 200 行硬上限"
        n = min(400, _MAX_RULE_LINES)
        lines = [f"rule line number {i}" for i in range(n)]
        path = tmp_path / "rules.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _read_rule_file(path, "global")
        assert result is not None
        # 旧实现 splitlines()[:200] 会丢掉第 200 行之后的内容
        assert f"rule line number {_OLD_CAP + 50}" in result["lines"]
        assert f"rule line number {n - 1}" in result["lines"]
        assert len(result["lines"]) == n

    def test_caps_at_max_rule_lines(self, tmp_path, monkeypatch):
        """A1: 超大文件仍设安全上限 _MAX_RULE_LINES，避免整本灌入。"""
        # 清掉可能由外部 env 注入的上限覆盖，保证断言绑定代码常量而非运行环境
        monkeypatch.delenv("ENGRAM_MAX_RULE_LINES", raising=False)
        from piia_engram.setup_wizard import _MAX_RULE_LINES

        total = _MAX_RULE_LINES + 500
        lines = [f"rule line number {i}" for i in range(total)]
        path = tmp_path / "rules.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _read_rule_file(path, "global")
        assert result is not None
        assert len(result["lines"]) == _MAX_RULE_LINES


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

    def test_probe_success_forces_utf8_decoding(self, monkeypatch):
        from piia_engram.setup_wizard import _probe_mcp_entry

        calls = []

        class Result:
            returncode = 0
            stdout = "Engram MCP Server \u2014 ok"
            stderr = ""

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return Result()

        monkeypatch.setattr("piia_engram.setup_wizard.subprocess.run", fake_run)

        result = _probe_mcp_entry({"command": "piia-engram-mcp", "args": []})

        assert result is None
        assert calls[0][1]["encoding"] == "utf-8"
        assert calls[0][1]["errors"] == "replace"

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
        from unittest.mock import patch
        with patch("piia_engram.setup_wizard.run_setup") as mock_setup:
            main()
            mock_setup.assert_called_once_with(
                advanced=True,
                apply_external_config=False,
            )

    def test_main_setup_apply_external_config(self, monkeypatch):
        """main() should route explicit external config authorization."""
        monkeypatch.setattr("sys.argv", ["engram", "setup", "--apply-external-config"])
        from unittest.mock import patch
        with patch("piia_engram.setup_wizard.run_setup") as mock_setup:
            main()
            mock_setup.assert_called_once_with(
                advanced=False,
                apply_external_config=True,
            )


def test_run_playbook_unknown_builtin_reports_error_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
):
    from piia_engram.core import Engram
    from piia_engram.setup_wizard import run_playbook

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))

    code = run_playbook(["install", _removed_private_builtin_name()])
    out = capsys.readouterr().out

    assert code == 1
    assert "Unknown builtin playbook" in out
    assert Engram(root=tmp_path).get_playbooks(_update_access=False) == []


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

    def test_inject_with_file_safety_root_fails_closed_by_default(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Supplying an Engram backup root is not enough to authorize external writes."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _INSTRUCTION_SNIPPETS,
        )
        engram_root = tmp_path / "engram-root"
        target = tmp_path / "home" / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        original = "# My rules\n\nKeep these.\n"
        target.write_text(original, encoding="utf-8")
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: target,
        )

        result = _inject_instruction_snippet(
            "claude_code",
            lang="zh",
            file_safety_root=engram_root,
        )

        assert result is None
        assert target.read_text(encoding="utf-8") == original
        assert not (engram_root / "file_safety_ledger.jsonl").exists()
        assert not (engram_root / "backups").exists()

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

    def test_remove_claude_code_snippet_with_file_safety_backup_and_ledger(
        self,
        tmp_path,
        monkeypatch,
    ):
        """External instruction snippet removal should be backed up under ENGRAM_DIR."""
        from piia_engram.file_safety import read_ledger_entries
        from piia_engram.setup_wizard import (
            _remove_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_MARKER_END,
            _INSTRUCTION_SNIPPETS,
        )
        engram_root = tmp_path / "engram-root"
        target = tmp_path / "home" / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "# My rules\n\n"
            f"{_INSTRUCTION_MARKER}\n"
            "Piia Engram test snippet\n"
            f"{_INSTRUCTION_MARKER_END}\n\n"
            "Keep these.\n",
            encoding="utf-8",
        )
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: target,
        )
        injected = target.read_text(encoding="utf-8")
        assert _INSTRUCTION_MARKER in injected

        removed = _remove_instruction_snippet(
            "claude_code",
            file_safety_root=engram_root,
            authorized_external_write=True,
        )

        assert removed is True
        assert _INSTRUCTION_MARKER not in target.read_text(encoding="utf-8")
        assert list(target.parent.glob("CLAUDE.md.engram-backup.*")) == []
        backups = list((engram_root / "backups" / "file_safety" / "external").glob("CLAUDE.md.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == injected
        entries = read_ledger_entries(engram_root)
        assert len(entries) == 1
        assert entries[0]["operation"] == "write"
        assert entries[0]["scope"] == "external"
        assert str(target.parent) not in json.dumps(entries[0], ensure_ascii=False)

    def test_remove_claude_code_snippet_refuses_unapproved_external_write(
        self,
        tmp_path,
        monkeypatch,
        caplog,
    ):
        """Unapproved snippet removal must leave external files untouched."""
        from piia_engram.setup_wizard import (
            _remove_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_MARKER_END,
            _INSTRUCTION_SNIPPETS,
        )
        engram_root = tmp_path / "engram-root"
        target = tmp_path / "home" / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "# My rules\n\n"
            f"{_INSTRUCTION_MARKER}\n"
            "Piia Engram test snippet\n"
            f"{_INSTRUCTION_MARKER_END}\n\n"
            "Keep these.\n",
            encoding="utf-8",
        )
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: target,
        )
        before = target.read_text(encoding="utf-8")
        caplog.set_level(logging.WARNING, logger="piia_engram.setup_wizard")

        removed = _remove_instruction_snippet(
            "claude_code",
            file_safety_root=engram_root,
            authorized_external_write=False,
        )

        assert removed is False
        assert "instruction removal failed for claude_code" in caplog.text
        assert target.read_text(encoding="utf-8") == before
        assert not (engram_root / "file_safety_ledger.jsonl").exists()
        assert not (engram_root / "backups").exists()

    def test_remove_cursor_snippet_with_file_safety_backup_and_delete_ledger(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Cursor .mdc removal should back up before deleting the external file."""
        from piia_engram.file_safety import read_ledger_entries
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _remove_instruction_snippet,
            _INSTRUCTION_SNIPPETS,
        )
        engram_root = tmp_path / "engram-root"
        mdc_path = tmp_path / "home" / ".cursor" / "rules" / "engram.mdc"
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["cursor"],
            "path_fn",
            lambda _home: mdc_path,
        )
        _inject_instruction_snippet("cursor")
        injected = mdc_path.read_text(encoding="utf-8")

        removed = _remove_instruction_snippet(
            "cursor",
            file_safety_root=engram_root,
            authorized_external_write=True,
        )

        assert removed is True
        assert not mdc_path.exists()
        assert list(mdc_path.parent.glob("engram.mdc.engram-backup.*")) == []
        backups = list((engram_root / "backups" / "file_safety" / "external").glob("engram.mdc.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == injected
        entries = read_ledger_entries(engram_root)
        assert len(entries) == 1
        assert entries[0]["operation"] == "delete"
        assert entries[0]["scope"] == "external"
        assert str(mdc_path.parent) not in json.dumps(entries[0], ensure_ascii=False)

    def test_remove_cursor_snippet_refuses_unapproved_external_delete(
        self,
        tmp_path,
        monkeypatch,
        caplog,
    ):
        """Unapproved Cursor .mdc removal should preserve the external file."""
        from piia_engram.setup_wizard import (
            _inject_instruction_snippet,
            _remove_instruction_snippet,
            _INSTRUCTION_SNIPPETS,
        )
        engram_root = tmp_path / "engram-root"
        mdc_path = tmp_path / "home" / ".cursor" / "rules" / "engram.mdc"
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["cursor"],
            "path_fn",
            lambda _home: mdc_path,
        )
        _inject_instruction_snippet("cursor")
        before = mdc_path.read_text(encoding="utf-8")
        caplog.set_level(logging.WARNING, logger="piia_engram.setup_wizard")

        removed = _remove_instruction_snippet(
            "cursor",
            file_safety_root=engram_root,
            authorized_external_write=False,
        )

        assert removed is False
        assert "instruction removal failed for cursor" in caplog.text
        assert mdc_path.read_text(encoding="utf-8") == before
        assert not (engram_root / "file_safety_ledger.jsonl").exists()
        assert not (engram_root / "backups").exists()

    def test_remove_with_file_safety_root_fails_closed_by_default(
        self,
        tmp_path,
        monkeypatch,
        caplog,
    ):
        """Snippet removal must also require explicit authorization by default."""
        from piia_engram.setup_wizard import (
            _remove_instruction_snippet,
            _INSTRUCTION_MARKER,
            _INSTRUCTION_MARKER_END,
            _INSTRUCTION_SNIPPETS,
        )
        engram_root = tmp_path / "engram-root"
        target = tmp_path / "home" / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "# My rules\n\n"
            f"{_INSTRUCTION_MARKER}\n"
            "Piia Engram test snippet\n"
            f"{_INSTRUCTION_MARKER_END}\n\n"
            "Keep these.\n",
            encoding="utf-8",
        )
        before = target.read_text(encoding="utf-8")
        monkeypatch.setitem(
            _INSTRUCTION_SNIPPETS["claude_code"],
            "path_fn",
            lambda _home: target,
        )
        caplog.set_level(logging.WARNING, logger="piia_engram.setup_wizard")

        removed = _remove_instruction_snippet(
            "claude_code",
            file_safety_root=engram_root,
        )

        assert removed is False
        assert "instruction removal failed for claude_code" in caplog.text
        assert target.read_text(encoding="utf-8") == before
        assert not (engram_root / "file_safety_ledger.jsonl").exists()
        assert not (engram_root / "backups").exists()

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
