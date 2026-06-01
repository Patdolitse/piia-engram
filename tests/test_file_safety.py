"""File safety boundary helpers."""

from pathlib import Path
import json

import pytest

from piia_engram.file_safety import (
    classify_path,
    read_ledger_entries,
    redact_path,
    write_engram_text,
    write_external_config_text,
)


def test_classify_path_inside_engram_root(tmp_path: Path):
    root = tmp_path / "root"
    path = root / "identity" / "profile.json"

    assert classify_path(root, path) == "engram_root"


def test_classify_path_outside_engram_root(tmp_path: Path):
    root = tmp_path / "root"
    path = tmp_path / "home" / ".claude" / ".mcp.json"

    assert classify_path(root, path) == "external"


def test_redact_path_labels_engram_root_and_external(tmp_path: Path):
    root = tmp_path / "root"
    inside = root / "knowledge" / "lessons.json"
    external = tmp_path / "home" / ".codex" / "config.toml"

    assert redact_path(root, inside) == "<engram-root>/knowledge/lessons.json"
    assert redact_path(root, external).startswith("<external:")
    assert str(external) not in redact_path(root, external)


def test_external_write_refuses_without_authorization(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "home" / ".claude" / ".mcp.json"
    external.parent.mkdir(parents=True)
    original = '{"mcpServers": {}}\n'
    external.write_text(original, encoding="utf-8")

    with pytest.raises(PermissionError, match="external file write requires"):
        write_external_config_text(
            root,
            external,
            "{}\n",
            tool="setup",
            authorized=False,
        )

    assert external.read_text(encoding="utf-8") == original
    assert not (root / "file_safety_ledger.jsonl").exists()


def test_authorized_external_write_backs_up_and_ledgers(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "home" / ".zed" / "settings.json"
    external.parent.mkdir(parents=True)
    original = '{"theme": "Ayu"}\n'
    external.write_text(original, encoding="utf-8")

    backup = write_external_config_text(
        root,
        external,
        '{"theme": "Ayu", "context_servers": {}}\n',
        tool="setup",
        authorized=True,
    )

    assert backup is not None
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original
    assert external.read_text(encoding="utf-8") != original

    entries = read_ledger_entries(root)
    assert len(entries) == 1
    assert entries[0]["scope"] == "external"
    assert entries[0]["operation"] == "write"
    assert entries[0]["tool"] == "setup"
    assert entries[0]["backup_path"].startswith("<engram-root>/backups/file_safety/")
    assert str(external) not in json.dumps(entries[0], ensure_ascii=False)


def test_engram_write_backs_up_existing_file_and_skips_unchanged(tmp_path: Path):
    root = tmp_path / "root"
    path = root / "identity" / "profile.json"
    path.parent.mkdir(parents=True)
    original = '{"role": "old"}\n'
    path.write_text(original, encoding="utf-8")

    backup = write_engram_text(root, path, '{"role": "new"}\n', tool="storage")

    assert backup is not None
    assert backup.read_text(encoding="utf-8") == original
    assert path.read_text(encoding="utf-8") == '{"role": "new"}\n'
    entries_after_write = read_ledger_entries(root)
    assert len(entries_after_write) == 1

    second = write_engram_text(root, path, '{"role": "new"}\n', tool="storage")

    assert second is None
    assert read_ledger_entries(root) == entries_after_write


def test_engram_write_refuses_path_outside_root(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "outside.txt"

    with pytest.raises(PermissionError, match="Engram writes must stay inside"):
        write_engram_text(root, external, "x", tool="storage")

    assert not external.exists()
