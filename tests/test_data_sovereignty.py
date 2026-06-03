"""Data-sovereignty path-origin guards (Node N9).

These assert the local-first boundary that the rest of Engram relies on:

- the active root is derived from ``ENGRAM_DIR`` (else ``~/.engram``, legacy
  ``~/.piia``);
- backups and the safety ledger always live UNDER that root — even when the
  file being protected is external — so protective copies never escape the
  sovereignty boundary;
- external client configs are never written without explicit authorization, and
  a refused write leaves no file and no ledger entry behind.

They are additive regression guards; they assert existing behavior.
"""

from __future__ import annotations

import pytest

from piia_engram import file_safety as FS
from piia_engram import storage


def test_engram_root_prefers_env_then_legacy(monkeypatch, tmp_path):
    custom = tmp_path / "custom_root"
    monkeypatch.setenv("ENGRAM_DIR", str(custom))
    assert storage._engram_root() == custom.resolve()

    # Without ENGRAM_DIR: legacy ~/.piia is used only if ~/.engram is absent.
    monkeypatch.delenv("ENGRAM_DIR", raising=False)
    home = tmp_path / "home"
    (home / ".piia").mkdir(parents=True)
    monkeypatch.setattr(storage.Path, "home", classmethod(lambda cls: home))
    assert storage._engram_root() == home / ".piia"

    # Once ~/.engram exists it wins over the legacy dir.
    (home / ".engram").mkdir()
    assert storage._engram_root() == home / ".engram"


def test_ledger_lives_under_root(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    target = root / "knowledge" / "lessons.json"
    FS.write_engram_text(root, target, "data", tool="test")
    ledger = FS._ledger_path(root)
    assert ledger.is_file()
    # Path-origin: the ledger is inside the active root, nowhere else.
    assert ledger.resolve().relative_to(root.resolve())


def test_backup_of_external_file_stays_under_root(tmp_path):
    """A protective backup of an EXTERNAL file is stored under the Engram root."""
    root = tmp_path / "engram"
    root.mkdir()
    external = tmp_path / "elsewhere" / "client_config.json"
    external.parent.mkdir(parents=True)
    external.write_text("OLD", encoding="utf-8")

    backup = FS.write_external_config_text(
        root, external, "NEW", tool="test", authorized=True
    )
    assert backup is not None and backup.is_file()
    # Sovereignty: the backup copy never escapes the root.
    assert backup.resolve().relative_to(root.resolve())
    # And the ledger redacts the external path (no raw external path leaks).
    rec = FS.read_ledger_entries(root)[-1]
    assert rec["scope"] == "external"
    assert rec["path"].startswith("<external:")
    assert "elsewhere" not in rec["path"]


def test_refused_external_write_leaves_no_file_and_no_ledger(tmp_path):
    root = tmp_path / "engram"
    root.mkdir()
    external = tmp_path / "elsewhere" / "client_config.json"
    with pytest.raises(PermissionError):
        FS.write_external_config_text(
            root, external, "{}", tool="test", authorized=False
        )
    assert not external.exists()
    # No silent side effect: a refused write records nothing.
    assert FS.read_ledger_entries(root) == []
