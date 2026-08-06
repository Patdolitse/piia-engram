"""Store self-healing: transient exemption, log rotation, backup slimming."""
import json
from pathlib import Path

from piia_engram import file_safety


def _ledger_lines(root: Path) -> list[str]:
    p = root / "file_safety_ledger.jsonl"
    return p.read_text(encoding="utf-8").splitlines() if p.is_file() else []


def test_transient_write_skips_ledger_and_backup(tmp_path):
    root = tmp_path
    target = root / "session_state.json"
    target.write_text('{"pid": 1}', encoding="utf-8")

    result = file_safety.write_engram_text(
        root, target, '{"pid": 2}', tool="session_stamp"
    )

    assert result is None  # no backup path returned
    assert target.read_text(encoding="utf-8") == '{"pid": 2}'
    assert _ledger_lines(root) == []  # no ledger entry
    assert not (root / "backups").exists()  # no .bak created


def test_transient_exemption_covers_known_basenames(tmp_path):
    for name in (".update_check.json", ".backup_state.json", "heartbeat.json"):
        target = tmp_path / name
        target.write_text("old", encoding="utf-8")
        file_safety.write_engram_text(tmp_path, target, "new", tool="t")
    assert _ledger_lines(tmp_path) == []
    assert not (tmp_path / "backups").exists()


def test_durable_write_still_ledgers_and_backs_up(tmp_path):
    target = tmp_path / "knowledge" / "lessons.json"
    target.parent.mkdir()
    target.write_text("[]", encoding="utf-8")

    result = file_safety.write_engram_text(
        tmp_path, target, '[{"id": "x"}]', tool="t"
    )

    assert result is not None  # backup was made
    lines = _ledger_lines(tmp_path)
    assert len(lines) == 1
    assert json.loads(lines[0])["path"] == "<engram-root>/knowledge/lessons.json"
