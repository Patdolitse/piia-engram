"""Characterization tests for the file-safety invariants (Task 12).

These lock in the safety guarantees that setup/upgrade relies on today:
authorization gates, backups before overwrite, no-op on unchanged content, and
metadata-only (redacted) ledger records. They are additive regression guards —
they assert existing behavior, they do not change it.
"""

from __future__ import annotations

import pytest

from piia_engram import file_safety as FS


def test_engram_write_refuses_outside_root(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    with pytest.raises(PermissionError):
        FS.write_engram_text(root, outside, "data", tool="test")
    assert not outside.exists()


def test_external_write_refuses_without_authorization(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    target = tmp_path / "ext" / "config.json"
    with pytest.raises(PermissionError):
        FS.write_external_config_text(root, target, "{}", tool="test", authorized=False)
    assert not target.exists()


def test_external_delete_refuses_without_authorization(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    target = tmp_path / "ext" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text("keep me", encoding="utf-8")
    with pytest.raises(PermissionError):
        FS.delete_external_config_file(root, target, tool="test", authorized=False)
    assert target.exists()  # untouched


def test_authorized_external_write_backs_up_existing(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    target = tmp_path / "ext" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text("OLD", encoding="utf-8")

    backup = FS.write_external_config_text(
        root, target, "NEW", tool="test", authorized=True
    )
    assert target.read_text(encoding="utf-8") == "NEW"
    assert backup is not None and backup.is_file()
    assert backup.read_text(encoding="utf-8") == "OLD"  # original preserved


def test_engram_write_is_noop_when_unchanged(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    target = root / "knowledge" / "lessons.json"
    first = FS.write_engram_text(root, target, "same", tool="test")
    assert first is None or first.is_file()  # first write may have no prior backup
    second = FS.write_engram_text(root, target, "same", tool="test")
    assert second is None  # unchanged content => no write, no backup


def test_ledger_is_metadata_only_and_redacts_external_paths(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    target = tmp_path / "ext" / "secret_location" / "config.json"
    target.parent.mkdir(parents=True)
    target.write_text("OLD", encoding="utf-8")

    FS.write_external_config_text(root, target, "NEW", tool="test", authorized=True)
    entries = FS.read_ledger_entries(root)
    assert entries, "ledger should record the external write"
    rec = entries[-1]
    # The raw external path must NOT appear; only a redacted label + hash.
    assert "secret_location" not in rec["path"]
    assert rec["path"].startswith("<external:")
    assert len(rec["path_sha256_12"]) == 12
    assert rec["scope"] == "external"
    assert rec["operation"] == "write"


def test_classify_and_redact_distinguish_internal_vs_external(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    inside = root / "knowledge" / "lessons.json"
    outside = tmp_path / "elsewhere.json"
    assert FS.classify_path(root, inside) == "engram_root"
    assert FS.classify_path(root, outside) == "external"
    assert FS.redact_path(root, inside).startswith("<engram-root>")
    assert FS.redact_path(root, outside).startswith("<external:")
