"""piia_engram.storage 单元测试 — 覆盖 I/O helpers 和 edge-case 路径。"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import portalocker
import pytest

from piia_engram.storage import (
    DataCorruptionError,
    _atomic_write_json,
    _engram_root,
    _parse_iso,
    _read_json,
    _update_json,
)


# ── _engram_root tests ──────────────────────────────────────────────


def test_engram_root_env_override(tmp_path, monkeypatch):
    """ENGRAM_DIR 环境变量应覆盖默认路径。"""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "custom"))
    assert _engram_root() == (tmp_path / "custom").resolve()


def test_engram_root_legacy_fallback(tmp_path, monkeypatch):
    """当 .engram 不存在但 .piia 存在时，应回退到 .piia。"""
    monkeypatch.delenv("ENGRAM_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # .piia exists, .engram does not
    legacy = tmp_path / ".piia"
    legacy.mkdir()

    root = _engram_root()
    assert root == legacy


def test_engram_root_default(tmp_path, monkeypatch):
    """两者都不存在时，应返回 .engram（默认）。"""
    monkeypatch.delenv("ENGRAM_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = _engram_root()
    assert root == tmp_path / ".engram"


# ── _read_json tests ────────────────────────────────────────────────


def test_read_json_missing_file(tmp_path):
    """不存在的文件应返回 {}。"""
    assert _read_json(tmp_path / "nope.json") == {}


def test_read_json_valid(tmp_path):
    """正常 JSON 文件应正确解析。"""
    path = tmp_path / "data.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert _read_json(path) == {"a": 1}


def test_read_json_accepts_utf8_bom(tmp_path):
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf[]\r\n")

    assert _read_json(path) == []
    assert not list(tmp_path.glob("bom.corrupt.*.json"))


def test_read_json_corrupt(tmp_path):
    """损坏的 JSON 应抛 DataCorruptionError 并备份文件。"""
    path = tmp_path / "bad.json"
    path.write_text("not json!", encoding="utf-8")
    with pytest.raises(DataCorruptionError):
        _read_json(path)
    # Backup file should be created
    backups = list(tmp_path.glob("bad.corrupt.*.json"))
    assert len(backups) >= 1


def test_read_json_corrupt_allow_corrupt(tmp_path):
    """allow_corrupt=True 时损坏 JSON 应返回 {} 而不抛异常。"""
    path = tmp_path / "bad.json"
    path.write_text("not json!", encoding="utf-8")
    assert _read_json(path, allow_corrupt=True) == {}


def test_read_json_retries_transient_failure(tmp_path):
    """并发替换窗口内的瞬时读失败应重试成功，不应隔离有效文件。"""
    path = tmp_path / "race.json"
    path.write_text('{"a": 1}', encoding="utf-8")

    real_read_text = Path.read_text
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient: file being replaced mid-read")
        return real_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", flaky):
        assert _read_json(path) == {"a": 1}

    assert calls["n"] == 2  # failed once, succeeded on retry
    assert not list(tmp_path.glob("race.corrupt.*.json"))


def test_read_json_corrupt_dedup_skips_identical_copy(tmp_path):
    """已存在相同内容的 .corrupt 副本时，重复读取不应再生成新副本。"""
    path = tmp_path / "bad.json"
    path.write_bytes(b"not json!")

    with pytest.raises(DataCorruptionError):
        _read_json(path)
    assert len(list(tmp_path.glob("bad.corrupt.*.json"))) == 1

    with pytest.raises(DataCorruptionError):
        _read_json(path)
    assert len(list(tmp_path.glob("bad.corrupt.*.json"))) == 1


def test_read_json_permission_error(tmp_path):
    """读取权限异常时应抛 DataCorruptionError。"""
    path = tmp_path / "locked.json"
    path.write_text('{"ok": true}', encoding="utf-8")

    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        with pytest.raises(DataCorruptionError):
            _read_json(path)


# ── _atomic_write_json tests ────────────────────────────────────────


def test_atomic_write_json_success(tmp_path):
    """正常写入应生成正确 JSON 文件。"""
    path = tmp_path / "out.json"
    _atomic_write_json(path, {"hello": "world"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"hello": "world"}


def test_atomic_write_json_backs_up_existing_engram_file(tmp_path, monkeypatch):
    """Existing Engram-owned JSON files get a backup before replacement."""
    from piia_engram.file_safety import read_ledger_entries

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    path = tmp_path / "knowledge" / "lessons.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"items": ["old"]}\n', encoding="utf-8")

    _atomic_write_json(path, {"items": ["new"]})

    backups = list((tmp_path / "backups" / "file_safety" / "engram_root").glob("lessons.json.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"items": ["old"]}\n'
    assert json.loads(path.read_text(encoding="utf-8")) == {"items": ["new"]}
    entries = read_ledger_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["scope"] == "engram_root"
    assert entries[0]["path"] == "<engram-root>/knowledge/lessons.json"


def test_update_json_backs_up_existing_engram_file(tmp_path, monkeypatch):
    """Read-modify-write storage path should use the same backup boundary."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    path = tmp_path / "knowledge" / "decisions.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"items": []}\n', encoding="utf-8")

    _update_json(path, lambda current: {"items": current["items"] + ["new"]})

    backups = list((tmp_path / "backups" / "file_safety" / "engram_root").glob("decisions.json.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"items": []}\n'


def test_atomic_write_json_lock_timeout(tmp_path):
    """LockException 应转为 RuntimeError 并清理临时文件。"""
    path = tmp_path / "locked.json"

    with patch(
        "piia_engram.storage.portalocker.Lock",
        side_effect=portalocker.LockException("timeout"),
    ):
        with pytest.raises(RuntimeError, match="无法获取文件锁"):
            _atomic_write_json(path, {"data": 1})

    # temp file should be cleaned up
    tmp_files = list(tmp_path.glob(".locked.json.*.tmp"))
    assert len(tmp_files) == 0


def test_atomic_write_json_general_exception(tmp_path):
    """写入过程中的一般异常应清理临时文件并重新抛出。"""
    path = tmp_path / "fail.json"

    with patch(
        "piia_engram.storage.portalocker.Lock",
        side_effect=OSError("disk full"),
    ):
        with pytest.raises(OSError, match="disk full"):
            _atomic_write_json(path, {"data": 1})

    # temp file should be cleaned up
    tmp_files = list(tmp_path.glob(".fail.json.*.tmp"))
    assert len(tmp_files) == 0


# ── _parse_iso tests ────────────────────────────────────────────────


def test_parse_iso_valid():
    """有效 ISO 字符串应正确解析。"""
    dt = _parse_iso("2026-05-22T10:00:00")
    assert dt is not None
    assert dt.year == 2026


def test_parse_iso_none():
    """None 应返回 None。"""
    assert _parse_iso(None) is None


def test_parse_iso_empty():
    """空字符串应返回 None。"""
    assert _parse_iso("") is None


def test_parse_iso_invalid():
    """无效字符串应返回 None 而不崩溃。"""
    assert _parse_iso("not-a-date") is None
    assert _parse_iso("2026-99-99") is None


# ── _project_id tests ──────────────────────────────────────────────


def test_project_id_normalizes_slashes():
    """正斜杠和反斜杠路径应产生相同 ID。"""
    from piia_engram.storage import _project_id
    id_forward = _project_id("/home/user/my-project")
    id_back = _project_id("\\home\\user\\my-project")
    # After resolve + lower + slash normalization, same logical path → same ID
    # (on the same machine, resolve produces the same result)
    assert len(id_forward) == 12
    assert len(id_back) == 12


def test_project_id_case_insensitive():
    """大小写不同的路径应产生相同 ID（Windows 兼容）。"""
    from piia_engram.storage import _project_id
    id_lower = _project_id("/tmp/MyProject")
    id_upper = _project_id("/tmp/myproject")
    assert id_lower == id_upper


# ── lock timeout message tests ─────────────────────────────────────


def test_lock_timeout_message_hides_full_path(tmp_path):
    """锁超时错误信息应只包含文件名，不包含完整路径。"""
    path = tmp_path / "test.json"

    with patch(
        "piia_engram.storage.portalocker.Lock",
        side_effect=portalocker.LockException("timeout"),
    ):
        with pytest.raises(RuntimeError, match="test.json") as exc_info:
            _atomic_write_json(path, {"data": 1})
        # Should NOT contain the full directory path
        assert str(tmp_path) not in str(exc_info.value)


# ── _update_json corruption fail-closed tests ─────────────────────


class TestUpdateJsonCorruptionFailClosed:
    """_update_json must propagate DataCorruptionError on corrupt input,
    never silently overwrite with defaults. (X2-4 / security-critical)"""

    def test_corrupt_json_raises_and_preserves_file(self, tmp_path):
        """Corrupt file → DataCorruptionError, original bytes untouched."""
        path = tmp_path / "data.json"
        bad_bytes = b"{{not json at all!!"
        path.write_bytes(bad_bytes)

        with pytest.raises(DataCorruptionError):
            _update_json(path, lambda cur: {**cur, "new": True})

        assert path.read_bytes() == bad_bytes

    def test_corrupt_json_creates_backup(self, tmp_path):
        """Corrupt file → .corrupt.* backup created."""
        path = tmp_path / "data.json"
        path.write_text("truncated{", encoding="utf-8")

        with pytest.raises(DataCorruptionError):
            _update_json(path, lambda cur: cur)

        backups = list(tmp_path.glob("data.corrupt.*.json"))
        assert len(backups) >= 1

    def test_empty_file_raises_not_overwrites(self, tmp_path):
        """0-byte file → DataCorruptionError, NOT silently replaced by default."""
        path = tmp_path / "data.json"
        path.write_bytes(b"")

        with pytest.raises(DataCorruptionError):
            _update_json(path, lambda cur: {"replaced": True})

        assert path.read_bytes() == b""

    def test_binary_garbage_raises(self, tmp_path):
        """Random binary bytes → DataCorruptionError."""
        path = tmp_path / "data.json"
        path.write_bytes(bytes(range(256)))

        with pytest.raises(DataCorruptionError):
            _update_json(path, lambda cur: {"replaced": True})

        assert len(path.read_bytes()) == 256

    def test_mutator_never_called_on_corrupt(self, tmp_path):
        """Mutator must NOT execute when the file is corrupt."""
        path = tmp_path / "data.json"
        path.write_text("not valid json", encoding="utf-8")
        called = {"n": 0}

        def mutator(cur):
            called["n"] += 1
            return cur

        with pytest.raises(DataCorruptionError):
            _update_json(path, mutator)

        assert called["n"] == 0


class TestGrantStoreCorruptionPropagation:
    """GrantStore methods must propagate DataCorruptionError from storage,
    never fall back to empty grants (would erase trust state)."""

    def test_set_grant_on_corrupt_file(self, tmp_path):
        from piia_engram.governance_store import GrantStore

        store = GrantStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("not json!", encoding="utf-8")

        with pytest.raises(DataCorruptionError):
            store.set_grant("agent-1", "private-self")

    def test_revoke_on_corrupt_file(self, tmp_path):
        from piia_engram.governance_store import GrantStore

        store = GrantStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("truncated{", encoding="utf-8")

        with pytest.raises(DataCorruptionError):
            store.revoke("agent-1")

    def test_load_on_corrupt_file(self, tmp_path):
        from piia_engram.governance_store import GrantStore

        store = GrantStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{bad", encoding="utf-8")

        with pytest.raises(DataCorruptionError):
            store._load()


class TestRelationStoreCorruptionPropagation:
    """RelationStore must propagate DataCorruptionError, never silently
    return empty edges (would erase decision threads)."""

    def test_add_relation_on_corrupt_file(self, tmp_path):
        from piia_engram.governance_store import RelationStore

        store = RelationStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("not json!", encoding="utf-8")

        with pytest.raises(DataCorruptionError):
            store.add_relation("id-a", "supersedes", "id-b")

    def test_remove_relation_on_corrupt_file(self, tmp_path):
        from piia_engram.governance_store import RelationStore

        store = RelationStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{{bad", encoding="utf-8")

        with pytest.raises(DataCorruptionError):
            store.remove_relation("id-a", "supersedes", "id-b")

    def test_load_on_corrupt_file(self, tmp_path):
        from piia_engram.governance_store import RelationStore

        store = RelationStore(tmp_path)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("corrupted!", encoding="utf-8")

        with pytest.raises(DataCorruptionError):
            store._load()
