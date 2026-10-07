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

import ast
import os
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


# -- no rename bypasses it (checked on the syntax tree) -------------------------

_RENAME_ATTRS = frozenset({"replace", "rename", "renames", "move"})
_RENAME_MODULES = frozenset({"os", "shutil", "pathlib"})
# Renames that are not an atomic overwrite of a file other processes read.
_ALLOWED_RENAMES = {
    ("core.py", "os.replace(staging, final)"),  # backup directory moved to a fresh name
    ("file_safety.py", 'os.replace(path, path.with_name(path.name + ".1"))'),  # best-effort log rotation
}


def _rename_calls(source: str) -> list[tuple[int, str]]:
    """(line, call source) of every call that renames or moves a file.

    Flags os/shutil rename functions however they are imported (``os.replace``,
    ``import os as _os``, ``from os import replace``, ``shutil.move``) and a
    one-argument ``.replace(x)`` / ``.rename(x)`` on any object (``Path.replace``).
    ``str.replace`` takes two arguments and ``datetime.replace`` keywords, so
    neither is flagged; nor is any method called on a string literal.
    """
    tree = ast.parse(source)
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _RENAME_MODULES:
                    modules.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in _RENAME_MODULES:
            for alias in node.names:
                if alias.name in _RENAME_ATTRS:
                    functions.add(alias.asname or alias.name)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            flagged = func.id in functions
        elif isinstance(func, ast.Attribute) and func.attr in _RENAME_ATTRS:
            owner = func.value
            if isinstance(owner, ast.Constant):
                flagged = False
            elif isinstance(owner, ast.Name) and owner.id in modules:
                flagged = True
            else:
                flagged = (func.attr in {"replace", "rename"} and len(node.args) == 1
                           and not node.keywords)
        else:
            flagged = False
        if flagged:
            found.append((node.lineno, ast.get_source_segment(source, node) or ""))
    return found


@pytest.mark.parametrize("code", [
    "import os\nos.replace(a, b)",
    "import os as _os\n_os.replace(a, b)",
    "import os\nos.rename(a, b)",
    "import os\nos.renames(a, b)",
    "from os import replace\nreplace(a, b)",
    "from os import rename as mv\nmv(a, b)",
    "import shutil\nshutil.move(a, b)",
    "from shutil import move\nmove(a, b)",
    "from pathlib import Path\nPath(a).replace(b)",
    "tmp.replace(target)",
    "tmp.rename(target)",
    "self.path.with_suffix('.tmp').replace(self.path)",
])
def test_rename_guard_flags(code):
    assert len(_rename_calls(code)) == 1


@pytest.mark.parametrize("code", [
    "'a-b'.replace('-', '_')",
    "name.replace('-', '_')",
    "text.replace(old, new)",
    "text.replace(old, new, 1)",
    "dt.replace(tzinfo=None)",
    "dataclasses.replace(obj, field=1)",
    "'x'.replace(y)",
    "from os import path\npath.join(a, b)",
])
def test_rename_guard_ignores(code):
    assert _rename_calls(code) == []


def test_no_bare_rename_in_atomic_writers():
    """Atomic writes go through replace_with_retry, never a bare rename or move."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "atomic_replace.py":
            continue  # the implementation itself
        for lineno, call in _rename_calls(path.read_text(encoding="utf-8")):
            if (path.name, call) in _ALLOWED_RENAMES:
                continue
            offenders.append(f"{path.relative_to(SRC).as_posix()}:{lineno}: {call}")
    assert offenders == []


# -- a failed write leaves no temp file and logs no message text ----------------


def test_blocked_replace_logs_the_file_name_and_winerror_only(tmp_path, monkeypatch, on_windows, caplog):
    monkeypatch.setattr(atomic_replace, "_RETRY_TIMEOUT", 0.05)
    src, dst = tmp_path / "new.tmp", tmp_path / "secret-dir" / "target.json"
    dst.parent.mkdir()
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")

    def _blocked(*_args, **_kwargs):
        exc = PermissionError(13, "Access is denied: private detail", str(dst))
        exc.winerror = 32
        raise exc

    monkeypatch.setattr(os, "replace", _blocked)
    with caplog.at_level("WARNING", logger="piia_engram.atomic_replace"):
        with pytest.raises(PermissionError):
            atomic_replace.replace_with_retry(src, dst)

    messages = [r.getMessage() for r in caplog.records if r.name == "piia_engram.atomic_replace"]
    assert len(messages) == 1
    assert "target.json" in messages[0] and "32" in messages[0]
    assert "private detail" not in messages[0] and "secret-dir" not in messages[0]
    assert "Access is denied" not in messages[0]


def test_tombstone_remove_cleans_up_when_the_replace_fails(tmp_path, monkeypatch, on_windows):
    from piia_engram import tombstones

    monkeypatch.setattr(atomic_replace, "_RETRY_TIMEOUT", 0.05)
    path = tombstones._path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"id": "les_1"}\n{"id": "les_2"}\n', encoding="utf-8")
    monkeypatch.setattr(os, "replace", _FlakyReplace(path, failures=-1))

    with pytest.raises(PermissionError):
        tombstones.remove(tmp_path, "les_1")

    assert path.read_text(encoding="utf-8") == '{"id": "les_1"}\n{"id": "les_2"}\n'
    assert _no_temp_files(path.parent)
