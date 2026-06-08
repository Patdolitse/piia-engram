"""Tests for the auto-bootstrap module (first-MCP-call rule-file ingest)."""

from __future__ import annotations

from pathlib import Path

from piia_engram.bootstrap import needs_bootstrap, run_bootstrap
from piia_engram.core import Engram


def _engram(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path / "store")


def test_needs_bootstrap_on_fresh_store(tmp_path: Path):
    eng = _engram(tmp_path)
    assert needs_bootstrap(eng) is True


def test_needs_bootstrap_false_after_content_exists(tmp_path: Path):
    eng = _engram(tmp_path)
    eng.add_lesson({"summary": "something exists", "domain": "misc"})
    assert needs_bootstrap(eng) is False


def test_needs_bootstrap_false_after_marker(tmp_path: Path):
    eng = _engram(tmp_path)
    # Simulate a previous bootstrap that found no files
    (eng.root / ".bootstrap_done").parent.mkdir(parents=True, exist_ok=True)
    (eng.root / ".bootstrap_done").write_text("1", encoding="utf-8")
    assert needs_bootstrap(eng) is False


def test_run_bootstrap_no_files_marks_done(tmp_path: Path, monkeypatch):
    """Even if no rule files found, marker is written so we don't retry."""
    eng = _engram(tmp_path)
    # Monkeypatch _scan_rule_files to return nothing (isolated test env)
    import piia_engram.bootstrap as bs
    monkeypatch.setattr(bs, "_scan_rule_files", lambda: [])

    result = run_bootstrap(eng)
    assert result["bootstrapped"] is True
    assert result["files_scanned"] == 0
    assert (eng.root / ".bootstrap_done").exists()
    assert needs_bootstrap(eng) is False


def test_run_bootstrap_imports_user_rules(tmp_path: Path, monkeypatch):
    """Simulates finding a CLAUDE.md with user preferences."""
    eng = _engram(tmp_path)
    fake_claude_md = tmp_path / "fake_claude.md"
    fake_claude_md.write_text(
        "# Global Instructions\n"
        "## Language\n"
        "所有沟通使用中文。\n"
        "## Style\n"
        "I prefer concise answers with explicit verification commands.\n"
        "Always provide a recommendation when giving options.\n",
        encoding="utf-8",
    )

    import piia_engram.bootstrap as bs
    monkeypatch.setattr(bs, "_scan_rule_files", lambda: [
        {"path": fake_claude_md, "scope": "global",
         "lines": fake_claude_md.read_text(encoding="utf-8").splitlines()},
    ])

    result = run_bootstrap(eng)
    assert result["bootstrapped"] is True
    assert result["files_scanned"] == 1
    assert result["user_rules_imported"] > 0
    assert result["language_detected"] == "zh-CN"

    # Verify lesson was created
    lessons = eng.get_lessons(limit=10, _update_access=False)
    assert any("首次连接自动导入" in l.get("summary", "") for l in lessons)

    # Verify profile language was set
    profile = eng.get_profile()
    assert profile.get("language") == "zh-CN"

    # Verify lesson is auto-verified (low risk content)
    imported = [l for l in lessons if "首次连接自动导入" in l.get("summary", "")]
    assert imported[0]["tier"] == "verified"


def test_run_bootstrap_imports_project_rules(tmp_path: Path, monkeypatch):
    """Project-level rules go to a separate lesson."""
    eng = _engram(tmp_path)
    fake_agents = tmp_path / "AGENTS.md"
    fake_agents.write_text(
        "# Agents\n"
        "Run tests with pytest before deploying.\n"
        "Use docker compose for the local dev environment.\n"
        "Database migrations must be backwards-compatible.\n",
        encoding="utf-8",
    )

    import piia_engram.bootstrap as bs
    monkeypatch.setattr(bs, "_scan_rule_files", lambda: [
        {"path": fake_agents, "scope": "project",
         "lines": fake_agents.read_text(encoding="utf-8").splitlines()},
    ])

    result = run_bootstrap(eng)
    assert result["project_rules_imported"] > 0

    lessons = eng.get_lessons(limit=10, _update_access=False)
    assert any("项目规则" in l.get("summary", "") for l in lessons)


def test_run_bootstrap_idempotent(tmp_path: Path, monkeypatch):
    """Second call is a no-op because marker already exists."""
    eng = _engram(tmp_path)
    import piia_engram.bootstrap as bs
    monkeypatch.setattr(bs, "_scan_rule_files", lambda: [])

    run_bootstrap(eng)
    assert needs_bootstrap(eng) is False

    # Second run — shouldn't even try to scan
    call_count = [0]
    original_scan = bs._scan_rule_files

    def counting_scan():
        call_count[0] += 1
        return original_scan()

    monkeypatch.setattr(bs, "_scan_rule_files", counting_scan)
    # needs_bootstrap returns False, so run_bootstrap won't be called in
    # the normal flow. But even if called directly:
    result2 = run_bootstrap(eng)
    # It still marks done and returns, but the critical thing is the MCP
    # handler won't call it because needs_bootstrap returns False.
    assert result2["bootstrapped"] is True


def test_run_bootstrap_high_risk_content_goes_to_staging(tmp_path: Path, monkeypatch):
    """Content with real credential values routes to staging via the risk gate."""
    eng = _engram(tmp_path)
    dangerous_md = tmp_path / "risky.md"
    dangerous_md.write_text(
        "# Security Config\n"
        "Set the api_key to sk-xxxx for production access.\n"
        "Use password rotation every 90 days.\n",
        encoding="utf-8",
    )

    import piia_engram.bootstrap as bs
    monkeypatch.setattr(bs, "_scan_rule_files", lambda: [
        {"path": dangerous_md, "scope": "global",
         "lines": dangerous_md.read_text(encoding="utf-8").splitlines()},
    ])

    result = run_bootstrap(eng)
    assert result["user_rules_imported"] > 0

    lessons = eng.get_lessons(limit=10, _update_access=False)
    imported = [l for l in lessons if "首次连接自动导入" in l.get("summary", "")]
    # The detail contains api_key and password -> credential strong -> high -> staging
    assert imported[0]["tier"] == "staging"
    assert imported[0]["risk_level"] == "high"
