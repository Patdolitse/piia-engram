"""Cross-process policy gate and encrypted publication regressions."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from piia_engram.hooks import _spool_protection as protection, spool


def _ready(child):
    result = queue.Queue()
    threading.Thread(target=lambda: result.put(child.stdout.readline()), daemon=True).start()
    assert result.get(timeout=60).rstrip(b"\r\n") == b"ready"


def _release(child):
    output, error = child.communicate(b"release\n", timeout=120)
    assert child.returncode == 0, error.decode("utf-8", errors="replace")
    return output


@contextmanager
def _holder(root, shared):
    code = """
import sys
from pathlib import Path
from piia_engram.hooks._spool_protection import policy_gate
with policy_gate(Path(sys.argv[1]), shared=sys.argv[2] == 'shared'):
    print('ready', flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(root),
                              "shared" if shared else "exclusive"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _ready(child)
        yield child
    finally:
        if child.poll() is None:
            _release(child)


@pytest.mark.parametrize("contents", [b"", b"offset-regression"])
@pytest.mark.parametrize("shared", [False, True])
def test_exclusive_gate_acquires_and_waits_for_other_process(tmp_path, monkeypatch, contents, shared):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    lock = hooks / ".spool-policy.lock"
    lock.write_bytes(contents)  # ab opens at EOF; the lock must still start at zero.
    for _ in range(3):
        with protection.policy_gate(tmp_path, timeout=1):
            with pytest.raises(protection.PolicyGateBusy):
                with protection.policy_gate(tmp_path):
                    pytest.fail("another handle acquired the exclusive gate")
    with _holder(tmp_path, shared) as child:
        with pytest.raises(protection.PolicyGateBusy):
            with protection.policy_gate(tmp_path):
                pytest.fail("exclusive gate ignored a holder")
        if shared:
            with protection.policy_gate(tmp_path, shared=True):
                pass
        else:
            with pytest.raises(protection.PolicyGateBusy):
                with protection.policy_gate(tmp_path, shared=True):
                    pytest.fail("shared gate ignored the exclusive holder")
        now, sleeps = [100.0], []
        def advance(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        monkeypatch.setattr(protection, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=advance))
        with pytest.raises(protection.PolicyGateBusy):
            with protection.policy_gate(tmp_path, timeout=0.1):
                pytest.fail("bounded wait ignored a holder")
        assert sum(sleeps) == pytest.approx(0.1)
        sleeps.clear()
        def release_on_retry(seconds):
            advance(seconds)
            _release(child)
        monkeypatch.setattr(protection.time, "sleep", release_on_retry)
        with protection.policy_gate(tmp_path, timeout=10):
            pass
        assert sleeps  # A failed acquisition was retried after the holder released.
    with protection.policy_gate(tmp_path):
        pass
    assert lock.read_bytes() == contents


def test_slow_open_still_attempts_uncontended_gate(tmp_path, monkeypatch):
    original = Path.open
    now = [100.0]
    def slow_open(path, *args, **kwargs):
        if path.name == ".spool-policy.lock":
            now[0] += 2
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", slow_open)
    monkeypatch.setattr(protection, "time", SimpleNamespace(monotonic=lambda: now[0]))
    with protection.policy_gate(tmp_path, timeout=1):
        pass


@pytest.mark.skipif(os.name != "nt", reason="Windows gate backend")
def test_windows_gate_does_not_change_or_use_global_portalocker_backend(tmp_path, monkeypatch):
    import portalocker
    from portalocker import portalocker as backend
    original = backend.LOCKER
    def forbidden(*args, **kwargs):
        pytest.fail("policy gate used the mixed process-global locker")
    monkeypatch.setattr(portalocker, "lock", forbidden)
    monkeypatch.setattr(portalocker, "unlock", forbidden)
    for shared in (True, False):
        with protection.policy_gate(tmp_path, shared=shared):
            pass
    assert backend.LOCKER is original


def test_existing_encrypted_store_and_publication_never_acquire_gate(tmp_path, monkeypatch):
    from piia_engram.core import Engram
    monkeypatch.setenv("ENGRAM_SECRET", "synthetic-policy-secret")
    Engram(tmp_path)
    salt = (tmp_path / ".corpus_salt").read_bytes()
    marker = protection.policy_path(tmp_path).read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("valid encrypted store attempted the enablement gate")
    monkeypatch.setattr(protection, "policy_gate", forbidden)
    monkeypatch.setattr(protection, "CRYPTO_BUDGET_SECONDS", 30)
    assert Engram(tmp_path)._corpus_key
    protection.ensure_required(tmp_path)
    protection.ensure_required(tmp_path, deadline=time.monotonic() + 30)
    assert spool.enqueue("cursor_writeback", "cursor", {"summary": "synthetic event"},
                         root=tmp_path, deadline=time.monotonic() + 30)
    assert (tmp_path / ".corpus_salt").read_bytes() == salt
    assert protection.policy_path(tmp_path).read_bytes() == marker


def test_enablement_rechecks_files_after_acquiring_gate(tmp_path, monkeypatch):
    from piia_engram.core import Engram
    monkeypatch.setenv("ENGRAM_SECRET", "synthetic-policy-secret")
    original = protection.policy_gate
    salt = b"0123456789abcdef"
    waits = []
    @contextmanager
    def gate(root, *, timeout):
        waits.append(timeout)
        with original(root, timeout=timeout):
            protection._ensure_required_locked(root)
            (root / ".corpus_salt").write_bytes(salt)
            yield
    monkeypatch.setattr(protection, "policy_gate", gate)
    assert Engram(tmp_path)._corpus_key
    assert waits == [10.0]
    assert (tmp_path / ".corpus_salt").read_bytes() == salt


@pytest.mark.parametrize("existing_marker", [False, True])
def test_concurrent_required_publications_all_succeed(tmp_path, monkeypatch, existing_marker):
    monkeypatch.setenv("ENGRAM_SECRET", "synthetic-concurrent-secret")
    (tmp_path / ".corpus_salt").write_bytes(b"0123456789abcdef")
    if existing_marker:
        protection.ensure_required(tmp_path)
    code = """
import json
import sys
import time
from pathlib import Path
from piia_engram.hooks import _spool_protection as protection, spool
root = Path(sys.argv[1])
protection.CRYPTO_BUDGET_SECONDS = 60
if sys.argv[3] == 'existing':
    def forbidden(*args, **kwargs):
        raise AssertionError('existing marker attempted the enablement gate')
    protection.policy_gate = forbidden
print('ready', flush=True)
sys.stdin.readline()
ids = [spool.enqueue('cursor_writeback', 'cursor', {'summary': sys.argv[2] + ':' + str(i)},
                     root=root, deadline=time.monotonic() + 60) for i in range(10)]
print(json.dumps(ids), flush=True)
assert all(ids), 'required publication refused'
"""
    children = []
    try:
        for index in range(6):
            children.append(subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(index),
                "existing" if existing_marker else "new"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for child in children:
            _ready(child)
        for child in children:
            child.stdin.write(b"publish\n")
            child.stdin.flush()
        ids = []
        for child in children:
            output, error = child.communicate(timeout=180)
            assert child.returncode == 0, error.decode("utf-8", errors="replace")
            ids.extend(json.loads(output))
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=30)
    assert len(ids) == len(set(ids)) == 60
    paths = list(spool.spool_dir(tmp_path).glob("*.jsonl"))
    assert len(paths) == 60
    codec = protection.QueueCodec(tmp_path)
    events = [codec.decode(json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    assert {event["event_id"] for event in events} == set(ids)
    assert {event["payload"]["summary"] for event in events} == {
        f"{index}:{event}" for index in range(6) for event in range(10)}
