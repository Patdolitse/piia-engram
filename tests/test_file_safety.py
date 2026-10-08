"""File safety boundary helpers."""

from pathlib import Path
import json
import os
import stat

import pytest

from piia_engram import file_safety
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


def test_external_delete_refuses_without_authorization(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "home" / ".cursor" / "rules" / "engram.mdc"
    external.parent.mkdir(parents=True)
    original = "alwaysApply: true\n"
    external.write_text(original, encoding="utf-8")

    with pytest.raises(PermissionError, match="external file delete requires"):
        file_safety.delete_external_config_file(
            root,
            external,
            tool="setup",
            authorized=False,
        )

    assert external.read_text(encoding="utf-8") == original
    assert not (root / "file_safety_ledger.jsonl").exists()


def test_authorized_external_delete_backs_up_and_ledgers(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "home" / ".cursor" / "rules" / "engram.mdc"
    external.parent.mkdir(parents=True)
    original = "alwaysApply: true\n"
    external.write_text(original, encoding="utf-8")

    backup = file_safety.delete_external_config_file(
        root,
        external,
        tool="setup",
        authorized=True,
    )

    assert backup is not None
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original
    assert not external.exists()

    entries = read_ledger_entries(root)
    assert len(entries) == 1
    assert entries[0]["scope"] == "external"
    assert entries[0]["operation"] == "delete"
    assert entries[0]["tool"] == "setup"
    assert entries[0]["backup_path"].startswith("<engram-root>/backups/file_safety/")
    assert str(external) not in json.dumps(entries[0], ensure_ascii=False)


def test_external_delete_missing_file_is_noop_without_ledger(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "home" / ".cursor" / "rules" / "missing.mdc"

    backup = file_safety.delete_external_config_file(
        root,
        external,
        tool="setup",
        authorized=True,
    )

    assert backup is None
    assert not external.exists()
    assert not (root / "file_safety_ledger.jsonl").exists()


def test_external_delete_missing_file_still_requires_authorization(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "home" / ".cursor" / "rules" / "missing.mdc"

    with pytest.raises(PermissionError, match="external file delete requires"):
        file_safety.delete_external_config_file(
            root,
            external,
            tool="setup",
            authorized=False,
        )

    assert not external.exists()
    assert not (root / "file_safety_ledger.jsonl").exists()


def test_external_delete_failure_keeps_file_and_does_not_ledger(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "root"
    external = tmp_path / "home" / ".cursor" / "rules" / "engram.mdc"
    external.parent.mkdir(parents=True)
    original = "alwaysApply: true\n"
    external.write_text(original, encoding="utf-8")

    real_unlink = Path.unlink

    def fail_external_unlink(path: Path, *args, **kwargs):
        if path == external:
            raise PermissionError("locked")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_external_unlink)

    with pytest.raises(PermissionError, match="locked"):
        file_safety.delete_external_config_file(
            root,
            external,
            tool="setup",
            authorized=True,
        )

    assert external.read_text(encoding="utf-8") == original
    backups = list((root / "backups" / "file_safety" / "external").glob("engram.mdc.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    assert not (root / "file_safety_ledger.jsonl").exists()


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


def test_backup_existing_file_enforces_retention(tmp_path: Path, monkeypatch):
    """每次写入都备份，但只保留最近 N 份，防止热文件备份膨胀到数 GB。"""
    monkeypatch.setattr(file_safety, "BACKUP_RETENTION", 5)
    path = tmp_path / "lessons.json"
    path.write_text("[]", encoding="utf-8")

    for i in range(12):
        path.write_text(f"[{i}]", encoding="utf-8")
        file_safety.backup_existing_file(
            tmp_path, path, scope="engram_root", tool="storage"
        )

    backup_dir = tmp_path / "backups" / "file_safety" / "engram_root"
    remaining = [p for p in backup_dir.iterdir() if p.is_file()]
    assert len(remaining) == 5


def test_backup_retention_is_per_source_file(tmp_path: Path, monkeypatch):
    """保留计数按源文件独立，不会因别的文件备份多而误删本文件备份。"""
    monkeypatch.setattr(file_safety, "BACKUP_RETENTION", 3)
    a = tmp_path / "lessons.json"
    b = tmp_path / "domains.json"
    a.write_text("[]", encoding="utf-8")
    b.write_text("{}", encoding="utf-8")

    for i in range(6):
        a.write_text(f"[{i}]", encoding="utf-8")
        file_safety.backup_existing_file(tmp_path, a, scope="engram_root", tool="t")
        b.write_text(f'{{"{i}": 1}}', encoding="utf-8")
        file_safety.backup_existing_file(tmp_path, b, scope="engram_root", tool="t")

    backup_dir = tmp_path / "backups" / "file_safety" / "engram_root"
    names = [p.name for p in backup_dir.iterdir() if p.is_file()]
    assert sum(n.startswith("lessons.json.") for n in names) == 3
    assert sum(n.startswith("domains.json.") for n in names) == 3


def test_external_write_is_atomic_when_the_replace_fails(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    external = tmp_path / "home" / "client.json"
    external.parent.mkdir(parents=True)
    original = '{"mcpServers": {"keep": {"command": "x"}}}\n'
    external.write_text(original, encoding="utf-8")

    from piia_engram import atomic_replace

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_replace, "replace_with_retry", boom)
    with pytest.raises(OSError):
        write_external_config_text(root, external, "{}\n", tool="setup", authorized=True)

    assert external.read_text(encoding="utf-8") == original  # not truncated
    assert sorted(p.name for p in external.parent.iterdir()) == ["client.json"]  # no temp file left


def test_external_write_replaces_the_content(tmp_path: Path):
    root = tmp_path / "root"
    external = tmp_path / "home" / "client.json"
    external.parent.mkdir(parents=True)
    external.write_text("{}\n", encoding="utf-8")
    write_external_config_text(root, external, '{"a": 1}\n', tool="setup", authorized=True)
    assert json.loads(external.read_text(encoding="utf-8")) == {"a": 1}


def _can_symlink(tmp_path: Path) -> bool:
    probe_target = tmp_path / "symlink-probe-target"
    probe_target.write_text("x", encoding="utf-8")
    probe_link = tmp_path / "symlink-probe-link"
    try:
        probe_link.symlink_to(probe_target)
    except (OSError, NotImplementedError):
        return False
    finally:
        probe_target.unlink()
    probe_link.unlink()
    return True


def test_atomic_write_through_a_symlink_keeps_the_link(tmp_path: Path):
    if not _can_symlink(tmp_path):
        pytest.skip("symbolic links cannot be created here")
    real_dir = tmp_path / "dotfiles"
    real_dir.mkdir()
    real = real_dir / "client.json"
    real.write_text("{}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    link = home / "client.json"
    link.symlink_to(real)
    original_target = os.readlink(link)

    file_safety.atomic_write_text(link, '{"a": 1}\n')

    assert link.is_symlink()
    assert os.readlink(link) == original_target
    assert link.resolve() == real.resolve()
    assert json.loads(real.read_text(encoding="utf-8")) == {"a": 1}
    assert sorted(p.name for p in home.iterdir()) == ["client.json"]
    assert sorted(p.name for p in real_dir.iterdir()) == ["client.json"]


def test_external_write_through_a_symlink_keeps_the_link(tmp_path: Path):
    if not _can_symlink(tmp_path):
        pytest.skip("symbolic links cannot be created here")
    real = tmp_path / "dotfiles" / "client.json"
    real.parent.mkdir()
    real.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "home" / "client.json"
    link.parent.mkdir()
    link.symlink_to(real)

    write_external_config_text(tmp_path / "root", link, '{"b": 2}\n', tool="setup", authorized=True)

    assert link.is_symlink()
    assert json.loads(real.read_text(encoding="utf-8")) == {"b": 2}


def test_atomic_write_resolves_a_symlink_to_its_target(tmp_path: Path, monkeypatch):
    # Runs everywhere, including where links cannot be created: the write goes
    # to the resolved target and the link path is never replaced.
    real = tmp_path / "dotfiles" / "client.json"
    real.parent.mkdir()
    real.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "home" / "client.json"
    link.parent.mkdir()
    link.write_text("stand-in for a link\n", encoding="utf-8")

    original_is_symlink = Path.is_symlink
    original_realpath = os.path.realpath
    monkeypatch.setattr(
        Path, "is_symlink", lambda self: self == link or original_is_symlink(self)
    )
    monkeypatch.setattr(
        os.path,
        "realpath",
        lambda p, *a, **k: str(real) if Path(p) == link else original_realpath(p, *a, **k),
    )

    file_safety.atomic_write_text(link, '{"a": 1}\n')

    assert json.loads(real.read_text(encoding="utf-8")) == {"a": 1}
    assert link.read_text(encoding="utf-8") == "stand-in for a link\n"
    assert sorted(p.name for p in real.parent.iterdir()) == ["client.json"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_atomic_write_keeps_the_permission_bits(tmp_path: Path):
    target = tmp_path / "client.json"
    target.write_text("{}\n", encoding="utf-8")
    os.chmod(target, 0o644)

    file_safety.atomic_write_text(target, '{"a": 1}\n')

    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_atomic_write_through_a_symlink_keeps_the_target_permission_bits(tmp_path: Path):
    real = tmp_path / "real.json"
    real.write_text("{}\n", encoding="utf-8")
    os.chmod(real, 0o640)
    link = tmp_path / "link.json"
    link.symlink_to(real)

    file_safety.atomic_write_text(link, '{"a": 1}\n')

    assert link.is_symlink()
    assert stat.S_IMODE(os.stat(real).st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_atomic_write_new_file_is_private(tmp_path: Path):
    target = tmp_path / "new.json"

    file_safety.atomic_write_text(target, "{}\n")

    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
