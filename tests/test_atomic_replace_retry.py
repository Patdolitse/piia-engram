"""Atomic writes ride out a short Windows sharing conflict on the target file.

On Windows, ``os.replace`` onto a file fails with WinError 5 (access denied) or
32 (sharing violation) while another handle to the target is open without
FILE_SHARE_DELETE. Python's ``open()`` opens files that way, so a reader in
another process (another AI client reading the store without the write lock,
an antivirus or search indexer scan) briefly blocks every atomic write. The
writers retry those errors for a bounded time and then re-raise, so a real
permission error is not hidden.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pytest

from piia_engram import atomic_replace
from piia_engram.storage import _atomic_write_json, _update_json

SRC = Path(__file__).resolve().parent.parent / "src" / "piia_engram"


def _sharing_error(winerror: int = 5) -> PermissionError:
    exc = PermissionError(13, "Access is denied")
    exc.winerror = winerror
    return exc


class _FlakyReplace:
    """``os.replace`` that fails ``failures`` times for one target, then works."""

    def __init__(self, target: Path, failures: int, winerror: int = 5):
        self.target = os.path.normcase(os.path.abspath(target))
        self.failures = failures
        self.winerror = winerror
        self.calls = 0
        self._real = os.replace

    def __call__(self, src, dst, *args, **kwargs):
        if os.path.normcase(os.path.abspath(dst)) == self.target:
            self.calls += 1
            if self.failures < 0 or self.calls <= self.failures:
                raise _sharing_error(self.winerror)
        return self._real(src, dst, *args, **kwargs)


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(atomic_replace, "_IS_WINDOWS", True)


def _no_temp_files(directory: Path) -> bool:
    return not [p for p in directory.iterdir() if p.name.endswith(".tmp")]


# -- the helper -----------------------------------------------------------------


@pytest.mark.parametrize("winerror", [5, 32])
def test_transient_sharing_error_is_retried(tmp_path, monkeypatch, on_windows, winerror):
    src, dst = tmp_path / "new.tmp", tmp_path / "target.json"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    flaky = _FlakyReplace(dst, failures=3, winerror=winerror)
    monkeypatch.setattr(os, "replace", flaky)

    atomic_replace.replace_with_retry(src, dst)

    assert flaky.calls == 4
    assert dst.read_text(encoding="utf-8") == "new"
    assert not src.exists()


def test_persistent_sharing_error_still_raises(tmp_path, monkeypatch, on_windows):
    monkeypatch.setattr(atomic_replace, "_RETRY_TIMEOUT", 0.2)
    src, dst = tmp_path / "new.tmp", tmp_path / "target.json"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    flaky = _FlakyReplace(dst, failures=-1)
    monkeypatch.setattr(os, "replace", flaky)

    started = time.monotonic()
    with pytest.raises(PermissionError) as info:
        atomic_replace.replace_with_retry(src, dst)
    elapsed = time.monotonic() - started

    assert getattr(info.value, "winerror", None) == 5
    assert flaky.calls > 1
    assert 0.2 <= elapsed < 2.0
    assert dst.read_text(encoding="utf-8") == "old"


def test_other_permission_errors_are_not_retried(tmp_path, monkeypatch, on_windows):
    src, dst = tmp_path / "new.tmp", tmp_path / "target.json"
    src.write_text("new", encoding="utf-8")
    flaky = _FlakyReplace(dst, failures=-1, winerror=1314)  # privilege not held
    monkeypatch.setattr(os, "replace", flaky)

    with pytest.raises(PermissionError):
        atomic_replace.replace_with_retry(src, dst)
    assert flaky.calls == 1


def test_no_retry_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_replace, "_IS_WINDOWS", False)
    src, dst = tmp_path / "new.tmp", tmp_path / "target.json"
    src.write_text("new", encoding="utf-8")
    flaky = _FlakyReplace(dst, failures=1)
    monkeypatch.setattr(os, "replace", flaky)

    with pytest.raises(PermissionError):
        atomic_replace.replace_with_retry(src, dst)
    assert flaky.calls == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows share-mode semantics")
def test_real_reader_handle_on_target_does_not_fail_the_write(tmp_path):
    """The actual failure: a reader holds the target open while a writer replaces it."""
    target = tmp_path / "_index.json"
    target.write_text("[]\n", encoding="utf-8")
    reader = open(target, "r", encoding="utf-8")
    closer = threading.Timer(0.15, reader.close)
    closer.start()
    try:
        _atomic_write_json(target, [{"id": "pb_1"}])
    finally:
        closer.cancel()
        reader.close()
    assert target.read_text(encoding="utf-8") == '[\n  {\n    "id": "pb_1"\n  }\n]\n'


# -- every atomic writer uses it ------------------------------------------------


def test_atomic_write_json_retries(tmp_path, monkeypatch, on_windows):
    target = tmp_path / "_index.json"
    target.write_text("[]\n", encoding="utf-8")
    flaky = _FlakyReplace(target, failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    _atomic_write_json(target, [{"id": "pb_1"}])

    assert flaky.calls == 3
    assert '"pb_1"' in target.read_text(encoding="utf-8")
    assert _no_temp_files(tmp_path)


def test_atomic_write_json_persistent_error_raises_and_cleans_up(tmp_path, monkeypatch, on_windows):
    monkeypatch.setattr(atomic_replace, "_RETRY_TIMEOUT", 0.1)
    target = tmp_path / "_index.json"
    target.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(os, "replace", _FlakyReplace(target, failures=-1))

    with pytest.raises(PermissionError):
        _atomic_write_json(target, [{"id": "pb_1"}])

    assert target.read_text(encoding="utf-8") == "[]\n"
    assert _no_temp_files(tmp_path)


def test_update_json_retries(tmp_path, monkeypatch, on_windows):
    target = tmp_path / "_index.json"
    target.write_text("[]\n", encoding="utf-8")
    flaky = _FlakyReplace(target, failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    result = _update_json(target, lambda cur: cur + [{"id": "pb_1"}], default=[])

    assert flaky.calls == 3
    assert result == [{"id": "pb_1"}]
    assert '"pb_1"' in target.read_text(encoding="utf-8")
    assert _no_temp_files(tmp_path)


def test_update_json_persistent_error_raises_and_cleans_up(tmp_path, monkeypatch, on_windows):
    monkeypatch.setattr(atomic_replace, "_RETRY_TIMEOUT", 0.1)
    target = tmp_path / "_index.json"
    target.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(os, "replace", _FlakyReplace(target, failures=-1))

    with pytest.raises(PermissionError):
        _update_json(target, lambda cur: cur + [{"id": "pb_1"}], default=[])

    assert target.read_text(encoding="utf-8") == "[]\n"
    assert _no_temp_files(tmp_path)


def test_atomic_write_bytes_retries(tmp_path, monkeypatch, on_windows):
    from piia_engram.core import Engram

    target = tmp_path / ".corpus_salt"
    target.write_bytes(b"old")
    flaky = _FlakyReplace(target, failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    Engram._atomic_write_bytes(target, b"new")

    assert flaky.calls == 3
    assert target.read_bytes() == b"new"


def test_quick_context_snapshot_retries(tmp_path, monkeypatch, on_windows):
    from piia_engram.core import Engram

    eng = Engram(root=tmp_path / "store")
    target = tmp_path / "quick_context.md"
    target.write_text("old", encoding="utf-8")
    flaky = _FlakyReplace(target, failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    eng.refresh_quick_context(target=target, level="quick")

    assert flaky.calls == 3
    assert "quick_context snapshot" in target.read_text(encoding="utf-8")


def test_tombstone_remove_retries(tmp_path, monkeypatch, on_windows):
    from piia_engram import tombstones

    path = tombstones._path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"id": "les_1"}\n{"id": "les_2"}\n', encoding="utf-8")
    flaky = _FlakyReplace(path, failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    assert tombstones.remove(tmp_path, "les_1") is True

    assert flaky.calls == 3
    assert path.read_text(encoding="utf-8") == '{"id": "les_2"}\n'


def test_usage_ping_setting_retries(monkeypatch, on_windows):
    from piia_engram import usage_ping

    target = usage_ping.state_dir() / usage_ping._SETTINGS_FILE
    flaky = _FlakyReplace(target, failures=2)
    monkeypatch.setattr(os, "replace", flaky)

    usage_ping.set_enabled(False)

    assert flaky.calls == 3
    assert '"enabled": false' in target.read_text(encoding="utf-8")


_REPLACE_CALL = re.compile(r"\b_?os\.replace\(|\bPath\([^()]*\)\.replace\(")
# Renames that are not an atomic overwrite of a file other processes read.
_ALLOWED_REPLACES = {
    ("core.py", "os.replace(staging, final)"),  # backup directory moved to a fresh name
    ("file_safety.py", "os.replace(path, path.with_name("),  # best-effort log rotation
}


def test_no_bare_os_replace_in_atomic_writers():
    """Atomic writes go through replace_with_retry, not a bare os.replace."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "atomic_replace.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if not _REPLACE_CALL.search(code):
                continue
            if any(path.name == name and snippet in code for name, snippet in _ALLOWED_REPLACES):
                continue
            offenders.append(f"{path.relative_to(SRC).as_posix()}:{lineno}: {line.strip()}")
    assert offenders == []
