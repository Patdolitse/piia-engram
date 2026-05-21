"""Audit logger unit tests."""
import json
from pathlib import Path

from engram_core.audit import AuditLogger


def test_audit_log_writes_entry(tmp_path):
    """Enabled logger should write a valid JSON entry."""
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=log_path, enabled=True)
    logger.log("read", "identity/profile")
    content = log_path.read_text(encoding="utf-8").strip()
    entry = json.loads(content)
    assert entry["action"] == "read"
    assert entry["resource"] == "identity/profile"
    assert "timestamp" in entry


def test_audit_log_disabled(tmp_path):
    """Disabled logger should not create a log file."""
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=log_path, enabled=False)
    logger.log("read", "identity/profile")
    assert not log_path.exists()


def test_audit_log_multiple_entries(tmp_path):
    """Multiple writes should produce multiple lines."""
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=log_path, enabled=True)
    logger.log("read", "knowledge/lessons")
    logger.log("write", "knowledge/lessons", detail="新增教训")
    logger.log("export", "all")
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_audit_log_detail_truncated(tmp_path):
    """Long detail should be truncated to 200 chars."""
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(log_path=log_path, enabled=True)
    logger.log("write", "test", detail="x" * 500)
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert len(entry["detail"]) == 200
