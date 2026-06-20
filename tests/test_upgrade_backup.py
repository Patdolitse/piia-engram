"""Auto-backup before an upgrade migration.

Engram snapshots the user's data store the first time it's opened under a NEWER
version — before any schema/field migration can touch it — so an upgrade can never
silently lose or corrupt the irreplaceable memory. Best-effort: a failure warns but
never blocks. Opt out with ENGRAM_NO_AUTO_BACKUP=1. Skipped under ENGRAM_TEST=1 in the
ambient init (suite isolation), so these tests call the method directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import piia_engram
from piia_engram.core import Engram


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    # The ambient init's auto-backup is gated off under ENGRAM_TEST; we drive the
    # method explicitly so seeding a store never spuriously backs up.
    monkeypatch.setenv("ENGRAM_TEST", "1")


def _seed(root: Path) -> Engram:
    eng = Engram(root=root)
    eng.add_lesson({"summary": "protect me — irreplaceable"})
    eng.add_decision({"question": "q", "choice": "c"})
    return eng


def _set_last_version(root: Path, version: str) -> None:
    (root / ".backup_state.json").write_text(
        json.dumps({"last_backed_up_version": version}), encoding="utf-8"
    )


def test_backup_triggers_on_version_change(tmp_path: Path):
    store = tmp_path / "store"
    eng = _seed(store)
    _set_last_version(store, "0.0.0")  # an older recorded version => current looks like an upgrade

    eng._maybe_backup_on_upgrade()

    backups = list((store / "backups").glob("engram-*"))
    assert len(backups) == 1
    b = backups[0]
    assert piia_engram.__version__ in b.name
    # core memory copied byte-for-byte
    assert (b / "knowledge" / "lessons.json").read_bytes() == (store / "knowledge" / "lessons.json").read_bytes()
    assert (b / "knowledge" / "decisions.json").read_bytes() == (store / "knowledge" / "decisions.json").read_bytes()
    # version recorded so we don't re-backup on the next open
    assert json.loads((store / ".backup_state.json").read_text())["last_backed_up_version"] == piia_engram.__version__


def test_no_backup_when_version_unchanged(tmp_path: Path):
    store = tmp_path / "store"
    eng = _seed(store)
    _set_last_version(store, piia_engram.__version__)

    eng._maybe_backup_on_upgrade()

    assert not (store / "backups").exists()


def test_no_backup_on_fresh_empty_store_but_records_version(tmp_path: Path):
    store = tmp_path / "store"
    eng = Engram(root=store)  # no data added

    eng._maybe_backup_on_upgrade()

    assert not (store / "backups").exists()  # nothing to protect
    assert (store / ".backup_state.json").is_file()  # but record version so we don't backup later for nothing


def test_read_only_open_never_backs_up(tmp_path: Path):
    store = tmp_path / "store"
    _seed(store)
    _set_last_version(store, "0.0.0")
    ro = Engram(root=store, read_only=True)

    ro._maybe_backup_on_upgrade()

    assert not (store / "backups").exists()


def test_opt_out_env_disables_backup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENGRAM_NO_AUTO_BACKUP", "1")
    store = tmp_path / "store"
    eng = _seed(store)
    _set_last_version(store, "0.0.0")

    eng._maybe_backup_on_upgrade()

    assert not (store / "backups").exists()


def test_backup_excludes_logs_and_does_not_recurse(tmp_path: Path):
    store = tmp_path / "store"
    eng = _seed(store)
    (store / "beta_events.jsonl").write_text("telemetry", encoding="utf-8")
    (store / "audit.log").write_text("audit", encoding="utf-8")
    _set_last_version(store, "0.0.0")

    eng._maybe_backup_on_upgrade()

    b = list((store / "backups").glob("engram-*"))[0]
    assert not (b / "beta_events.jsonl").exists()   # transient telemetry skipped
    assert not (b / "audit.log").exists()
    assert not (b / "backups").exists()             # never recursively back up the backups dir


def test_retention_keeps_only_recent(tmp_path: Path):
    store = tmp_path / "store"
    eng = _seed(store)
    bdir = store / "backups"
    bdir.mkdir(parents=True)
    for i in range(7):
        d = bdir / f"engram-0.0.{i}-2026010{i}-000000"
        d.mkdir()
        (d / "marker").write_text("x", encoding="utf-8")

    eng._prune_backups(keep=5)

    remaining = [p for p in bdir.iterdir() if p.is_dir()]
    assert len(remaining) == 5
