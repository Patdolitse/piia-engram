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


def _set_schema(root: Path, version: str) -> None:
    (root / "schema_version.json").write_text(
        json.dumps({"schema_version": version}), encoding="utf-8"
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


def test_backup_triggers_on_identity_only_store(tmp_path: Path):
    # A store that holds only identity facts (no lessons/decisions/playbooks) is
    # still non-empty and irreplaceable — it must be backed up, not skipped.
    store = tmp_path / "store"
    eng = Engram(root=store)
    (store / "identity").mkdir(parents=True, exist_ok=True)
    (store / "identity" / "preferences.json").write_text(
        json.dumps({"work_patterns": {"language": "zh"}}), encoding="utf-8"
    )
    _set_last_version(store, "0.0.0")

    eng._maybe_backup_on_upgrade()

    backups = list((store / "backups").glob("engram-*"))
    assert len(backups) == 1
    assert (backups[0] / "identity" / "preferences.json").is_file()


def test_backup_triggers_on_projects_only_store(tmp_path: Path):
    # Project snapshots are irreplaceable user data too — a projects-only store
    # must be backed up.
    store = tmp_path / "store"
    eng = Engram(root=store)
    eng.save_project_snapshot("/some/project", {"title": "X", "notes": "keep me"})
    _set_last_version(store, "0.0.0")

    eng._maybe_backup_on_upgrade()

    backups = list((store / "backups").glob("engram-*"))
    assert len(backups) == 1
    assert any((backups[0] / "projects").glob("*.json"))


def test_no_backup_on_downgrade(tmp_path: Path):
    # Opening an OLDER Engram after a newer one recorded a higher version is a
    # downgrade, not an upgrade — don't back up (and don't churn retention).
    store = tmp_path / "store"
    eng = _seed(store)
    _set_last_version(store, "99.99.99")  # higher than current => current looks older

    eng._maybe_backup_on_upgrade()

    assert not (store / "backups").exists()


def test_partial_backup_is_cleaned_up_on_failure(tmp_path: Path, monkeypatch):
    # If copytree fails mid-copy (e.g. disk full), no half-written backup dir may
    # survive to masquerade as a valid snapshot. With no schema migration pending
    # the failure is best-effort (no raise).
    import shutil

    store = tmp_path / "store"
    eng = _seed(store)  # _seed's init leaves schema at the current version (no migration pending)
    _set_last_version(store, "0.0.0")

    def boom(src, dst, *args, **kwargs):
        Path(dst).mkdir(parents=True, exist_ok=True)
        (Path(dst) / "half").write_text("partial", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", boom)

    eng._maybe_backup_on_upgrade()  # best-effort: must not raise

    bdir = store / "backups"
    assert list(bdir.glob("engram-*")) == []   # no complete backup
    assert list(bdir.glob(".*")) == []          # no leftover .partial staging


def test_backup_failure_blocks_init_when_migration_pending(tmp_path: Path, monkeypatch):
    # Owner decision: when a real schema migration is pending and the backup
    # fails, halt rather than migrate the store unprotected.
    from piia_engram.core import BackupFailedError

    store = tmp_path / "store"
    eng = _seed(store)
    _set_schema(store, "1.0")  # pending migration to current schema
    _set_last_version(store, "0.0.0")

    def boom(self, version):
        raise OSError("disk full")

    monkeypatch.setattr(Engram, "_backup_store", boom)

    with pytest.raises(BackupFailedError):
        eng._maybe_backup_on_upgrade()

    assert not list((store / "backups").glob("engram-*")) if (store / "backups").exists() else True


def test_backup_failure_does_not_block_when_no_migration_pending(tmp_path: Path, monkeypatch):
    # No pending migration => the open won't mutate the store => a backup failure
    # stays best-effort (warn, never block), and the version is NOT recorded so it
    # retries on the next open.
    store = tmp_path / "store"
    eng = _seed(store)  # schema already at current
    _set_last_version(store, "0.0.0")

    def boom(self, version):
        raise OSError("disk full")

    monkeypatch.setattr(Engram, "_backup_store", boom)

    eng._maybe_backup_on_upgrade()  # must not raise

    meta = json.loads((store / ".backup_state.json").read_text())
    assert meta["last_backed_up_version"] == "0.0.0"  # unchanged => will retry next open


def test_backup_dir_name_is_unique_across_rapid_calls(tmp_path: Path):
    # Two backups in quick succession must not collide on the same directory name
    # (timestamp carries sub-second + pid uniqueness).
    store = tmp_path / "store"
    eng = _seed(store)

    d1 = eng._backup_store("4.8.0")
    d2 = eng._backup_store("4.8.0")

    assert d1 != d2
    assert d1.is_dir() and d2.is_dir()
