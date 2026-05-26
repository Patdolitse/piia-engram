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
