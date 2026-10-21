"""Queue body protection contract; every store, key and marker is synthetic."""
import errno
import hashlib
import importlib
import io
import json
import os
import sys
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import portalocker
import pytest

from piia_engram.hooks import _cursor_payload, _processor, _producer, spool

SECRET = "synthetic-queue-key"
SALT = b"0123456789abcdef"
MARKER = "SYNTHETIC_QUEUE_BODY_8df61"
SUMMARY = "Remember to validate " + MARKER + " path containment before reading transcripts because it prevents accidental disclosure."


def module():
    return importlib.import_module("piia_engram.hooks._spool_protection")


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def files(root):
    return list(spool.spool_dir(root).glob("*.jsonl"))


def wire(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_wire(path, value):
    path.write_bytes(spool._json_bytes(value))


def working(summary=SUMMARY):
    return {"schema": 1, "event_id": uuid.uuid4().hex, "kind": "cursor_writeback",
            "client": "cursor", "created_at": "2026-10-21T00:00:00+00:00",
            "payload": {"summary": summary}}


def enqueue(root, summary=SUMMARY, **payload):
    return spool.enqueue("cursor_writeback", "cursor", dict(summary=summary, **payload), root=root)


def status(path):
    return wire(path.with_name(path.name + ".status.json"))["code"]


def scan(root, *extra):
    """Include queue partials/evidence and diagnostic logs, exclude disclosed outputs."""
    candidates = list((root / "hooks").rglob("*")) + list((root / "logs").rglob("*"))
    candidates += [root / "audit.log", root / "audit.log.1", root.with_name(root.name + ".hooks.log")]
    for p in candidates:
        if p.is_file():
            if p.name.endswith(".lock"):
                assert p.stat().st_size == 0  # Windows denies reads while drain holds this empty lock
                continue
            assert MARKER.encode("utf-8") not in p.read_bytes(), str(p.relative_to(root.parent))
    assert all(MARKER not in value for value in extra)


@pytest.fixture
def protected(tmp_path, monkeypatch):
    # These are correctness tests. KDF speed is covered separately by controlled
    # deadline tests, and must not prevent an injected failure from being reached.
    monkeypatch.setattr(module(), "CRYPTO_BUDGET_SECONDS", 30)
    monkeypatch.setattr(_producer, "INPUT_BUDGET_SECONDS", 35)
    original = spool.enqueue
    def patient_enqueue(*args, **kwargs):
        kwargs.setdefault("deadline", spool.time.monotonic() + 35)
        return original(*args, **kwargs)
    monkeypatch.setattr(spool, "enqueue", patient_enqueue)
    root = tmp_path / "store"
    root.mkdir()
    (root / ".corpus_salt").write_bytes(SALT)
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    return root


@pytest.fixture
def fault_keys(monkeypatch):
    # Fault-injection tests exercise the closed reason codes and real AES-GCM
    # authentication, not PBKDF2 cost. Integration/crypto tests retain the real KDF.
    from piia_engram import crypto
    monkeypatch.setattr(crypto, "_derive_key", lambda secret, salt, iterations:
        hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 1))


@pytest.fixture
def budget_clock(monkeypatch):
    now = [1000.0]
    def advance(seconds):
        now[0] += seconds
    clock = SimpleNamespace(monotonic=lambda: now[0], advance=advance,
                           time=time.time, time_ns=time.time_ns, sleep=advance)
    for target in (module(), spool, _producer):
        monkeypatch.setattr(target, "time", clock)
    monkeypatch.setattr(module(), "CRYPTO_BUDGET_SECONDS", 300)
    return clock


def expire_worker_wait(monkeypatch, clock, started, *, wait_number=1):
    """Expire a chosen wait only after its worker reached the injected stall."""
    waits, workers = [], []
    class Completion:
        def __init__(self):
            self.event = threading.Event()
        def set(self):
            self.event.set()
        def wait(self, timeout):
            waits.append(timeout)
            if len(waits) == wait_number:
                assert started.wait(30), "worker never reached the injected stall"
                clock.advance(timeout)
                return False
            assert self.event.wait(30), "worker never completed"
            return True
    def worker(*args, **kwargs):
        thread = threading.Thread(*args, **kwargs)
        workers.append(thread)
        return thread
    monkeypatch.setattr(module(), "threading", SimpleNamespace(Event=Completion, Thread=worker))
    return waits, workers


def finish_workers(workers):
    for worker in workers:
        worker.join(timeout=30)
        assert not worker.is_alive(), "synthetic crypto worker did not finish"


def test_no_encryption_signals_preserves_plaintext_capture(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    assert enqueue(tmp_path)
    assert wire(files(tmp_path)[0])["schema"] == 1
    assert not (tmp_path / "hooks" / "spool-protection.json").exists()


@pytest.mark.parametrize("fault,code", [
    ("secret", "crypto-secret-missing"), ("blank-secret", "crypto-secret-missing"),
    ("salt", "crypto-salt-missing"), ("short-salt", "crypto-salt-invalid"),
    ("long-salt", "crypto-salt-invalid"), ("directory-salt", "crypto-salt-invalid"),
    ("unreadable-salt", "crypto-salt-invalid"), ("dependency", "crypto-unavailable"),
    ("disabled", "crypto-unavailable"), ("short-key", "crypto-unavailable"),
    ("passthrough", "crypto-unavailable"), ("policy", "protection-unavailable"),
])
def test_required_refusal_never_writes_body_or_marks_debounce(protected, monkeypatch, fault_keys, fault, code, capsys):
    from piia_engram import crypto
    salt = protected / ".corpus_salt"
    if fault in {"secret", "blank-secret"}:
        monkeypatch.setenv("ENGRAM_SECRET", " " if fault == "blank-secret" else "")
    elif fault == "salt":
        salt.unlink()
    elif fault in {"short-salt", "long-salt"}:
        salt.write_bytes(b"x" * (15 if fault == "short-salt" else 17))
    elif fault == "directory-salt":
        salt.unlink()
        salt.mkdir()
    elif fault == "unreadable-salt":
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == salt:
                raise PermissionError(MARKER)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", denied)
    elif fault == "dependency":
        monkeypatch.setattr(crypto, "HAS_CRYPTO", False)
    elif fault == "disabled":
        monkeypatch.setattr(crypto.EncryptionEngine, "__init__", lambda self, secret: setattr(self, "enabled", False))
    elif fault == "short-key":
        monkeypatch.setattr(crypto.EncryptionEngine, "derive_corpus_key", lambda *a: b"bad")
    elif fault == "passthrough":
        monkeypatch.setattr(crypto.EncryptionEngine, "corpus_encrypt", lambda self, value, key: value)
    elif fault == "policy":
        (protected / "hooks").mkdir()
        (protected / "hooks" / "spool-protection.json").write_text(MARKER, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"summary": SUMMARY, "session_id": MARKER})))
    _producer.cursor_event("cursor_save", debounce_minutes=10)
    assert not files(protected)
    assert not (protected / "logs" / "cursor_save_state.json").exists()
    assert code in (protected / "logs" / "hooks.log").read_text(encoding="utf-8")
    if fault == "policy":  # existing corrupt evidence is retained, never copied
        (protected / "hooks" / "spool-protection.json").unlink()
    output = capsys.readouterr()
    scan(protected, output.out, output.err)


def test_all_body_fields_sealed_and_header_is_closed(protected):
    payload = {key: MARKER + key for key in ("summary", "transcript_path", "project_folder", "session_id", "event", "hook_cwd")}
    payload["roots"] = [MARKER + "root"]
    assert spool.enqueue("cursor_save", "cursor", payload, root=protected)
    event = wire(files(protected)[0])
    assert set(event) == {"schema", "event_id", "kind", "client", "created_at", "protection", "salt_id", "body"}
    assert event["schema"] == 2 and event["protection"] == "enc:v2c"
    assert event["body"].startswith("enc:v2c:")
    assert event["salt_id"] == hashlib.sha256(SALT).hexdigest()
    assert files(protected)[0].read_bytes().index(b'"body"') < 2048
    decoded = module().QueueCodec(protected).decode(event)
    assert decoded["payload"] == payload
    assert wire(protected / "hooks" / "spool-protection.json") == {"schema": 1, "mode": "required"}
    scan(protected)


def test_literal_cipher_prefix_input_is_still_encrypted(protected):
    assert enqueue(protected, "enc:v2c:" + MARKER)
    assert module().QueueCodec(protected).decode(wire(files(protected)[0]))["payload"]["summary"] == "enc:v2c:" + MARKER
    scan(protected)


@pytest.mark.parametrize("fault,code", [
    ("wrong-key", "crypto-decrypt-failed"), ("rotation", "crypto-salt-mismatch"),
    ("missing-secret", "crypto-secret-missing"), ("missing-salt", "crypto-salt-missing"),
    ("dependency", "crypto-unavailable"), ("disabled", "crypto-unavailable"),
    ("base64", "crypto-decrypt-failed"), ("tag", "crypto-decrypt-failed"),
    ("header", "crypto-decrypt-failed"), ("body-swap", "crypto-decrypt-failed"),
    ("domain", "crypto-decrypt-failed"), ("unknown-field", "crypto-decrypt-failed"),
])
def test_unreadable_preserves_bytes_before_fake_receipt_and_effects(protected, monkeypatch, fault_keys, fault, code):
    from piia_engram import crypto
    assert enqueue(protected)
    path = files(protected)[0]
    event = wire(path)
    if fault == "wrong-key":
        monkeypatch.setenv("ENGRAM_SECRET", "synthetic-wrong-key")
    elif fault == "rotation":
        (protected / ".corpus_salt").write_bytes(b"fedcba9876543210")
    elif fault == "missing-secret":
        monkeypatch.delenv("ENGRAM_SECRET")
    elif fault == "missing-salt":
        (protected / ".corpus_salt").unlink()
    elif fault == "dependency":
        monkeypatch.setattr(crypto, "HAS_CRYPTO", False)
    elif fault == "disabled":
        monkeypatch.setattr(crypto.EncryptionEngine, "__init__", lambda self, secret: setattr(self, "enabled", False))
    elif fault == "base64":
        event["body"] = "enc:v2c:%%%"
    elif fault == "tag":
        event["body"] = event["body"][:-12] + "A" * 12
    elif fault == "header":
        event["event_id"] = uuid.uuid4().hex
    elif fault == "body-swap":
        assert enqueue(protected, SUMMARY + " other")
        other = next(p for p in files(protected) if p != path)
        event["body"] = wire(other)["body"]
        other.unlink()
    elif fault == "unknown-field":
        event["prepared"] = {"summary": "forged"}
    elif fault == "domain":
        engine = crypto.EncryptionEngine(SECRET)
        key = engine.derive_corpus_key(SALT)
        body = json.loads(engine.corpus_decrypt(event["body"], key, strict=True))
        body["domain"] = "other-domain"
        event["body"] = engine.corpus_encrypt(json.dumps(body), key)
    write_wire(path, event)
    before = path.read_bytes()
    receipts = path.parent / "receipts"
    receipts.mkdir()
    fake = receipts / (event["event_id"] + ".json")
    write_wire(fake, {"event_id": event["event_id"], "reason": "processed", "processed_at": event["created_at"]})
    monkeypatch.setattr(_processor, "prepare", lambda *a: pytest.fail("prepare before authentication"))
    monkeypatch.setattr(_processor, "process", lambda *a: pytest.fail("process before authentication"))
    result = spool.drain(protected)
    assert result["failed"] == 1 and result["duplicates"] == result["processed"] == 0
    assert path.read_bytes() == before
    assert status(path) == code
    assert "transcript_missing_attempts" not in wire(path)
    assert fake.exists() and len(list(receipts.glob("*.json"))) == 1
    assert code in json.dumps(spool.backlog(protected)["recent_results"])
    scan(protected)


def test_unreadable_event_does_not_block_healthy_progress_and_restores(protected, monkeypatch):
    assert enqueue(protected)
    bad = files(protected)[0]
    broken = wire(bad)
    original_body = broken["body"]
    broken["body"] = "enc:v2c:broken"
    write_wire(bad, broken)
    before = bad.read_bytes()
    assert enqueue(protected, SUMMARY + " second")
    calls = []
    monkeypatch.setattr(_processor, "process", lambda event, *a: calls.append(event["event_id"]))
    result = spool.drain(protected)
    assert result["failed"] == result["processed"] == len(calls) == 1
    assert bad.read_bytes() == before
    assert spool.backlog(protected)["status_records"] == 1
    broken["body"] = original_body
    write_wire(bad, broken)
    assert spool.drain(protected)["processed"] == 1
    # Failure evidence is retained after eventual success.
    assert bad.with_name(bad.name + ".status.json").exists()
    scan(protected)


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("fault", ["secret", "salt", "dependency", "policy"])
def test_diagnostics_zero_write_without_crypto(protected, monkeypatch, capsys, populated, fault):
    from piia_engram import crypto, setup_wizard
    from piia_engram.doctor import run_doctor_json
    root = protected if populated else protected.parent / "absent"
    if populated:
        assert enqueue(root)
    if fault == "secret":
        monkeypatch.delenv("ENGRAM_SECRET")
    elif fault == "salt":
        (protected / ".corpus_salt").write_bytes(b"bad")
    elif fault == "dependency":
        monkeypatch.setattr(crypto, "HAS_CRYPTO", False)
    else:
        (protected / "hooks").mkdir(exist_ok=True)
        (protected / "hooks" / "spool-protection.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setattr(crypto.EncryptionEngine, "derive_corpus_key", lambda *a: pytest.fail("diagnostic KDF"))
    before = snapshot(root)
    assert spool.drain(root, dry_run=True)["read_only"]
    spool.backlog(root)
    run_doctor_json()
    report = json.loads(capsys.readouterr().out)
    assert report["hook_spool"]["pending"] == int(populated)
    assert report["hook_spool"]["protected_pending"] == int(populated)
    assert "clients" in report  # unrelated connection diagnostics survive store failure
    assert MARKER not in json.dumps(report)
    assert snapshot(root) == before
    saved = protected.parent / "diagnostic-report.json"
    saved.write_text(json.dumps(report), encoding="utf-8")
    assert MARKER not in saved.read_text(encoding="utf-8")
    if not populated:
        assert not root.exists()


def test_policy_is_sticky_and_core_never_rekeys_queue_only_store(protected, monkeypatch):
    from piia_engram.core import Engram
    assert enqueue(protected)
    for fault in ("missing", "corrupt"):
        salt = protected / ".corpus_salt"
        if salt.exists():
            salt.unlink()
        if fault == "corrupt":
            salt.write_bytes(b"bad")
        before = snapshot(protected)
        with pytest.raises(RuntimeError):
            Engram(root=protected)
        assert snapshot(protected) == before
    (protected / ".corpus_salt").unlink()
    monkeypatch.delenv("ENGRAM_SECRET")
    assert enqueue(protected) == ""
    assert "crypto-secret-missing" in (protected / "logs" / "hooks.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("surface", ["pending", "partial", "quarantine", "marker"])
def test_core_salt_guard_with_marker_or_only_queue_ciphertext(protected, surface):
    from piia_engram.core import Engram
    assert enqueue(protected)
    path = files(protected)[0]
    if surface != "marker":
        (protected / "hooks" / "spool-protection.json").unlink()
    if surface == "partial":
        path.rename(path.with_suffix(".partial"))
    elif surface == "quarantine":
        spool._quarantine(path, "pending-cap")
    elif surface == "marker":
        path.unlink()
    (protected / ".corpus_salt").unlink()
    before = snapshot(protected)
    with pytest.raises(RuntimeError):
        Engram(root=protected)
    assert snapshot(protected) == before


def test_writable_enablement_creates_marker_but_read_only_does_not(tmp_path, monkeypatch):
    from piia_engram.core import Engram
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    root = tmp_path / "new"
    Engram(root=root, read_only=True)
    assert not root.exists()
    Engram(root=root)
    assert wire(root / "hooks" / "spool-protection.json") == {"schema": 1, "mode": "required"}


def test_crypto_timeout_cannot_publish_late_or_write_policy(protected, monkeypatch, budget_clock):
    from piia_engram import crypto
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = crypto.EncryptionEngine.derive_corpus_key
    def slow(*args):
        started.set()
        release.wait(30)
        try:
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(crypto.EncryptionEngine, "derive_corpus_key", slow)
    monkeypatch.setattr(module(), "CRYPTO_BUDGET_SECONDS", 0.04)
    waits, workers = expire_worker_wait(monkeypatch, budget_clock, started)
    try:
        assert enqueue(protected) == ""
        assert waits == [pytest.approx(0.04)]
        before = snapshot(protected)
    finally:
        release.set()
        finish_workers(workers)
    assert finished.is_set()
    assert snapshot(protected) == before
    assert not files(protected)
    assert not (protected / "hooks" / "spool-protection.json").exists()
    assert "crypto-timeout" in (protected / "logs" / "hooks.log").read_text(encoding="utf-8")
    scan(protected)


def test_input_and_crypto_share_deadline(protected, monkeypatch, budget_clock):
    from piia_engram import crypto
    monkeypatch.setattr(_producer, "INPUT_BUDGET_SECONDS", 16)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = crypto.EncryptionEngine.derive_corpus_key
    def slow_crypto(*args):
        entered.set()
        release.wait(30)
        try:
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(crypto.EncryptionEngine, "derive_corpus_key", slow_crypto)
    class Delayed(io.StringIO):
        def fileno(self):
            raise OSError("synthetic")
        def read(self, *a):
            budget_clock.advance(4)
            return super().read(*a)
    monkeypatch.setattr(sys, "stdin", Delayed(json.dumps({"summary": SUMMARY})))
    waits, workers = expire_worker_wait(monkeypatch, budget_clock, entered)
    try:
        assert _producer.cursor_event("cursor_writeback") == 0
        assert waits == [pytest.approx(12 - module().DIAGNOSTIC_RESERVE_SECONDS)]
        assert not files(protected)
        before = snapshot(protected)
    finally:
        release.set()
        finish_workers(workers)
    assert finished.is_set()
    assert snapshot(protected) == before
    assert "crypto-timeout" in (protected / "logs/hooks.log").read_text(encoding="utf-8")
    scan(protected)


def _boundary_timestamps():
    # Valid UTC spellings of different lengths, limited to forms the running
    # Python can parse, so the wire size can be adjusted byte by byte.
    stamps = []
    for fraction in ("", ".123", ".123456"):
        for offset in ("+00:00", "+00:00:00", "+00:00:00.000000"):
            stamp = "2026-10-21T00:00:00" + fraction + offset
            try:
                datetime.fromisoformat(stamp)
            except ValueError:
                continue
            stamps.append(stamp)
    return stamps


def test_exact_final_jsonl_limit_and_one_byte_over(protected, monkeypatch):
    codec = module().QueueCodec(protected)
    event = working("x")
    # Ciphertext lengths advance by four bytes. The mutable bounded retry
    # counter contributes single-byte adjustments to reach exactly 128 KiB.
    event["transcript_missing_attempts"] = 0
    low, high = 0, 100_000
    while low < high:
        middle = (low + high + 1) // 2
        event["payload"]["summary"] = "x" * middle
        try:
            codec.encode(event)
            low = middle
        except module().ProtectionError as exc:
            assert exc.code == "event-oversize"
            high = middle - 1
    event["payload"]["summary"] = "x" * low
    data = codec.encode(event)
    # Adjust the valid ISO timestamp fractional precision for an exact wire
    # boundary rather than lowering the production limit for this check.
    for stamp in _boundary_timestamps():
        event["created_at"] = stamp
        for length in range(max(0, low - 20), low + 10):
            event["payload"]["summary"] = "x" * length
            try:
                candidate = codec.encode(event)
            except module().ProtectionError:
                continue
            if len(candidate) == 128 * 1024:
                data = candidate
                break
        else:
            continue
        break
    assert len(data) == spool.MAX_EVENT_BYTES == 128 * 1024
    assert data.endswith(b"\n")
    path = spool.spool_dir(protected) / "boundary.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert spool._read_event(path)["event_id"] == event["event_id"]
    path.write_bytes(data + b" ")
    with pytest.raises(spool.PoisonEvent):
        spool._read_event(path)
    monkeypatch.setattr(spool, "MAX_EVENT_BYTES", 128 * 1024 + 16)
    over = None
    for stamp in _boundary_timestamps():
        event["created_at"] = stamp
        for length in range(max(0, low - 25), low + 10):
            event["payload"]["summary"] = "x" * length
            try:
                candidate = codec.encode(event)
            except module().ProtectionError:
                continue
            if len(candidate) == 128 * 1024 + 1:
                over = candidate
                break
        if over is not None:
            break
    assert over is not None and len(over) == 131073
    monkeypatch.setattr(spool, "MAX_EVENT_BYTES", 128 * 1024)
    with pytest.raises(module().ProtectionError, match="event-oversize"):
        codec.encode(event)


def test_multibyte_preparation_expansion_preserves_original_on_oversize(protected, monkeypatch):
    assert enqueue(protected, "🧠" * 20_000)
    path = files(protected)[0]
    before = path.read_bytes()
    monkeypatch.setattr(_processor, "prepare", lambda *a: {"summary": "🧠" * 25_000})
    monkeypatch.setattr(_processor, "process", lambda *a: pytest.fail("oversize processing"))
    assert spool.drain(protected)["failed"] == 1
    assert path.read_bytes() == before and status(path) == "event-oversize"
    scan(protected)


def test_encrypted_count_and_actual_byte_caps_with_busy_maintenance(protected, monkeypatch):
    directory = spool.spool_dir(protected)
    directory.mkdir(parents=True)
    monkeypatch.setattr(spool, "MAX_PENDING_EVENTS", 2)
    with spool._lock(directory):
        with ThreadPoolExecutor(max_workers=3) as pool:
            assert all(pool.map(lambda _: enqueue(protected), range(3)))
        assert len(files(protected)) == 3
        assert spool.drain(protected)["busy"]
    first = min(files(protected), key=lambda p: p.name)
    spool._record_result(first, None, "crypto-decrypt-failed")
    old_bytes = first.read_bytes()
    with spool._lock(directory):
        assert spool._trim(directory) == 1
    assert len(files(protected)) == 2
    quarantined = directory / "quarantine" / first.name
    assert quarantined.read_bytes() == old_bytes
    assert status(quarantined) == "crypto-decrypt-failed"
    size = files(protected)[0].stat().st_size
    monkeypatch.setattr(spool, "MAX_PENDING_EVENTS", 1000)
    monkeypatch.setattr(spool, "MAX_PENDING_BYTES", size)
    with spool._lock(directory):
        spool._trim(directory)
    assert spool.backlog(protected)["pending_bytes"] <= size
    assert spool.backlog(protected)["pending_bytes"] == sum(p.stat().st_size for p in files(protected))
    scan(protected)


@pytest.mark.parametrize("point", ["prepare", "before-replace", "after-replace", "short-write", "disk-full", "receipt", "store-lock"])
def test_failure_points_only_leave_ciphertext_or_metadata(protected, monkeypatch, fault_keys, point, capsys):
    assert enqueue(protected, transcript_path=MARKER, session_id=MARKER, roots=[MARKER])
    path = files(protected)[0]
    original = path.read_bytes()
    publish, replace = spool._publish, spool.replace_with_retry
    calls = []
    def process(event, *args):
        calls.append(event["prepared"]["summary"])
        if point == "store-lock":
            raise portalocker.LockException(MARKER)
    monkeypatch.setattr(_processor, "process", process)
    def failure(target, data):
        if (point == "receipt" and target.parent.name == "receipts") or (
                point in {"short-write", "disk-full"} and target.suffix == ".jsonl"):
            # Interrupted binary publication leaves precisely the encrypted prefix.
            target.with_name(target.name + ".synthetic.partial").write_bytes(data[:len(data) // 2])
            raise OSError(errno.ENOSPC, MARKER)
        return publish(target, data)
    def interrupted_replace(source, target):
        if target.suffix == ".jsonl":
            if point == "before-replace":
                raise OSError(MARKER)
            replace(source, target)
            if point == "after-replace":
                raise OSError(MARKER)
        else:
            replace(source, target)
    monkeypatch.setattr(spool, "_publish", failure)
    monkeypatch.setattr(spool, "replace_with_retry", interrupted_replace)
    if point == "prepare":
        monkeypatch.setattr(_processor, "prepare", lambda *a: (_ for _ in ()).throw(RuntimeError(MARKER)))
    assert spool.drain(protected)["failed"] == 1
    assert path.exists()
    if point in {"prepare", "before-replace", "short-write", "disk-full"}:
        assert path.read_bytes() == original
    assert not list((path.parent / "receipts").glob("*.json"))
    assert len(calls) == int(point in {"receipt", "store-lock"})
    expected = ("storage-unavailable" if point in {"short-write", "disk-full", "store-lock", "receipt"}
                else "processing-failed")
    assert status(path) == expected
    output = capsys.readouterr()
    report = json.dumps(spool.drain(protected, dry_run=True))
    saved = protected.parent / "failure-diagnostic.json"
    saved.write_text(report, encoding="utf-8")
    scan(protected, output.out, output.err, saved.read_text(encoding="utf-8"))


def test_mixed_legacy_seals_only_on_authorized_drain_and_key_failure_retains(protected, monkeypatch):
    codec = module().QueueCodec(protected)
    directory = spool.spool_dir(protected)
    directory.mkdir(parents=True)
    legacy = working()
    path = directory / "legacy.jsonl"
    write_wire(path, legacy)
    assert enqueue(protected, SUMMARY + " protected")
    before = snapshot(protected)
    spool.drain(protected, dry_run=True)
    assert snapshot(protected) == before
    monkeypatch.delenv("ENGRAM_SECRET")
    assert spool.drain(protected)["failed"] == 2
    assert path.read_bytes() == before[str(path.relative_to(protected))][0]
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    seen = []
    def process(event, *a):
        source = next(p for p in files(protected) if wire(p)["event_id"] == event["event_id"])
        assert wire(source)["schema"] == 2
        assert codec.decode(wire(source))["prepared"] == event["prepared"]
        seen.append(event["event_id"])
    monkeypatch.setattr(_processor, "process", process)
    assert spool.drain(protected)["processed"] == 2
    assert legacy["event_id"] in seen
    assert not files(protected)
    scan(protected)


def test_real_cursor_extraction_audit_rotation_and_downstream_inventory(protected, monkeypatch, capsys):
    from piia_engram.core import Engram
    from piia_engram import audit
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    original_log = audit.AuditLogger.log
    def checked_log(self, *args, **kwargs):
        original_log(self, *args, **kwargs)
        scan(protected)  # inspect both generations immediately, before another rotation
    monkeypatch.setattr(audit.AuditLogger, "log", checked_log)
    eng = Engram(root=protected)
    # Force rotation through the real logger, not a mocked extraction path.
    monkeypatch.setattr(audit, "AUDIT_MAX_BYTES", 1)
    assert enqueue(protected)
    assert spool.enqueue("cursor_save", "cursor", {"summary": SUMMARY, "session_id": MARKER}, root=protected)
    assert spool.drain(protected, engram=eng)["processed"] == 2
    rows = eng.get_lessons(limit=None, _update_access=False)
    assert len(rows) == 1 and rows[0]["tier"] == "staging"
    assert MARKER in rows[0]["summary"]
    assert MARKER not in (protected / "knowledge" / "lessons.json").read_text(encoding="utf-8").split('"extraction"', 1)[0]
    # Archives/digests and non-digest extraction evidence remain plaintext.
    downstream = [p for p in (protected / "contexts").rglob("*") if p.is_file()]
    assert any(MARKER.encode() in p.read_bytes() for p in downstream)
    assert MARKER in (protected / "knowledge" / "lessons.json").read_text(encoding="utf-8")
    assert (protected / "audit.log.1").exists()
    eng._audit.log("probe", "metadata")
    output = capsys.readouterr()
    scan(protected, output.out, output.err)


@pytest.mark.parametrize("kind", ["lesson", "decision"])
def test_event_writer_forces_metadata_audit_even_if_caller_opts_out(tmp_path, monkeypatch, kind):
    from piia_engram.core import Engram
    eng = Engram(root=tmp_path)
    received = []
    monkeypatch.setattr(eng, "add_" + kind, lambda payload, **kw: received.append(kw))
    _processor._EventWriter(eng, working())._add(kind, {"summary": MARKER, "title": MARKER}, _audit_metadata_only=False)
    assert received == [{"_audit_metadata_only": True}]


def test_debug_and_debounce_never_copy_arbitrary_fields(protected, monkeypatch):
    monkeypatch.setenv("ENGRAM_HOOK_DEBUG", "1")
    monkeypatch.setattr(sys, "argv", ["hook", MARKER])
    monkeypatch.setattr(os, "getcwd", lambda: MARKER)
    _cursor_payload.debug_log(MARKER, {MARKER: MARKER, "summary": MARKER})
    directory = protected / "logs"
    directory.mkdir(exist_ok=True)
    (directory / "cursor_save_state.json").write_text(json.dumps({MARKER: datetime.now().isoformat()}), encoding="utf-8")
    _cursor_payload.mark_saved(MARKER + "new")
    assert _cursor_payload.recently_saved(MARKER + "new", 10)
    state = wire(directory / "cursor_save_state.json")
    assert all(len(key) == 64 and all(c in "0123456789abcdef" for c in key) for key in state)
    scan(protected)


def test_salt_read_is_bounded_and_symlink_refused(protected, monkeypatch):
    original = Path.open
    sizes = []
    class Reader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.handle.close()
        def read(self, size=-1):
            sizes.append(size)
            assert size == 17
            return self.handle.read(size)
    def spy(path, *a, **kw):
        handle = original(path, *a, **kw)
        return Reader(handle) if path.name == ".corpus_salt" else handle
    monkeypatch.setattr(Path, "open", spy)
    assert enqueue(protected)
    assert sizes == [17]
    monkeypatch.setattr(Path, "open", original)
    target = protected / "original-salt"
    (protected / ".corpus_salt").rename(target)
    try:
        (protected / ".corpus_salt").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert enqueue(protected) == ""
    assert "crypto-salt-invalid" in (protected / "logs" / "hooks.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("approval", ["default", "strict"])
def test_encrypted_partial_commit_replay_keeps_owner_edit_and_staging(protected, monkeypatch, approval):
    from piia_engram.core import Engram
    monkeypatch.setenv("ENGRAM_APPROVAL", approval)
    assert enqueue(protected)
    original_add = Engram.add_lesson
    def interrupted(self, *args, **kwargs):
        original_add(self, *args, **kwargs)
        raise RuntimeError(MARKER)
    monkeypatch.setattr(Engram, "add_lesson", interrupted)
    assert spool.drain(protected)["failed"] == 1
    eng = Engram(root=protected)
    rows = eng.get_lessons(limit=None, _update_access=False)
    assert len(rows) == 1 and rows[0]["tier"] == "staging"
    edited = "Owner edited this pending proposal; preserve this text on replay."
    eng.update_lesson(rows[0]["id"], {"summary": edited})
    monkeypatch.setattr(Engram, "add_lesson", original_add)
    assert spool.drain(protected)["processed"] == 1
    rows = eng.get_lessons(limit=None, _update_access=False)
    assert len(rows) == 1 and rows[0]["summary"] == edited
    scan(protected)


@pytest.mark.parametrize("kind", ["cursor_save", "claude_compact"])
def test_protected_frozen_input_survives_transcript_deletion_and_receipt_failure(protected, monkeypatch, kind):
    transcript = protected.parent / "transcript.jsonl"
    transcript.write_text(json.dumps({"content": MARKER * 12}) + "\n", encoding="utf-8")
    payload = {"transcript_path": str(transcript)} if kind == "claude_compact" else {
        "summary": SUMMARY, "session_id": "synthetic-session", "project_folder": str(protected.parent / "project")}
    assert spool.enqueue(kind, "claude_code" if kind == "claude_compact" else "cursor", payload, root=protected)
    publish = spool._publish
    def fail_receipt(path, data):
        if path.parent.name == "receipts":
            raise OSError(errno.ENOSPC, MARKER)
        return publish(path, data)
    monkeypatch.setattr(spool, "_publish", fail_receipt)
    assert spool.drain(protected)["failed"] == 1
    prepared = module().QueueCodec(protected).decode(wire(files(protected)[0]))["prepared"]
    transcript.unlink()
    monkeypatch.setattr(_processor, "prepare", lambda *a: pytest.fail("frozen input reread"))
    monkeypatch.setattr(spool, "_publish", publish)
    assert spool.drain(protected)["processed"] == 1
    area = protected / ("daily" if kind == "claude_compact" else "contexts")
    archives = list(area.rglob("*.md"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8").count("<!-- hook-event:") == 1
    if kind == "cursor_save":
        assert "project_revision" in prepared and "project_revision_captured_at" in prepared
    scan(protected)


def test_encrypted_missing_transcript_retries_and_quarantine_stay_protected(protected):
    assert spool.enqueue("claude_stop", "claude_code", {"transcript_path": str(protected.parent / MARKER)}, root=protected)
    for attempt in range(1, 4):
        assert spool.drain(protected)["failed"] == 1
        pending = files(protected)
        path = pending[0] if pending else next((spool.spool_dir(protected) / "quarantine").glob("*.jsonl"))
        assert wire(path)["schema"] == 2
        assert wire(path)["transcript_missing_attempts"] == attempt
        scan(protected)
    assert not files(protected)
    assert status(path) == "transcript-missing"


def test_malformed_protected_bytes_and_closed_diagnostics(protected):
    assert enqueue(protected)
    path = files(protected)[0]
    data = wire(path)
    data["body"] = "enc:v2c:truncated"
    write_wire(path, data)
    before = path.read_bytes()
    assert spool.drain(protected)["failed"] == 1
    assert path.read_bytes() == before
    spool._quarantine(path, "invalid-event")
    scan(protected)
    report = spool.backlog(protected)
    assert report["quarantined"] == report["status_records"] == 1
    assert "crypto-decrypt-failed" in json.dumps(report)


def test_required_policy_publication_failure_never_publishes_event(protected, monkeypatch):
    publish = spool._publish
    def deny_policy(path, data):
        if path.name == "spool-protection.json":
            raise PermissionError(MARKER)
        return publish(path, data)
    monkeypatch.setattr(spool, "_publish", deny_policy)
    assert enqueue(protected) == ""
    assert not files(protected)
    assert "protection-unavailable" in (protected / "logs" / "hooks.log").read_text(encoding="utf-8")
    scan(protected)


def test_text_doctor_keeps_queue_without_opening_encrypted_store(protected, monkeypatch, capsys):
    from piia_engram import crypto, setup_wizard, doctor
    assert enqueue(protected)
    monkeypatch.setattr(crypto.EncryptionEngine, "derive_corpus_key", lambda *a: pytest.fail("text doctor KDF"))
    before = snapshot(protected)
    assert doctor._run_functional_checks(fix=False) == 0
    output = capsys.readouterr().out
    assert "Hook spool:" in output and "1 protected" in output
    assert MARKER not in output
    assert snapshot(protected) == before


def test_core_guard_finds_ciphertext_beyond_header_in_damaged_queue(protected):
    from piia_engram.core import Engram
    directory = spool.spool_dir(protected) / "quarantine"
    directory.mkdir(parents=True)
    (directory / "damaged.jsonl").write_bytes(b" " * 5000 + b"enc:v2c:synthetic-damaged")
    (protected / ".corpus_salt").unlink()
    with module().policy_gate(protected):
        pass  # gate metadata is now part of writable initialization
    before = snapshot(protected)
    with pytest.raises(RuntimeError):
        Engram(root=protected)
    assert snapshot(protected) == before


@pytest.mark.parametrize("zero", [False, True])
def test_real_binary_publication_retries_short_writes_without_clear_partials(protected, monkeypatch, zero):
    original = Path.open
    class ShortWriter:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def write(self, data):
            if zero:
                return 0
            return self.handle.write(data[:71])
    def short(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        if ".jsonl." in path.name and path.suffix == ".partial" and mode == "xb":
            return ShortWriter(handle)
        return handle
    monkeypatch.setattr(Path, "open", short)
    event_id = enqueue(protected)
    assert bool(event_id) is not zero
    if not zero:
        assert spool._read_event(files(protected)[0])["payload"]["summary"] == SUMMARY
    scan(protected)


def test_every_prepared_field_and_provenance_is_inside_sealed_body(protected, monkeypatch):
    values = {key: MARKER + "_" + key for key in ("context", "summary", "session_id")}
    values.update(skip=False, digest=True, project_revision=17,
                  project_revision_captured_at="2026-10-21T01:02:03+00:00")
    assert spool.enqueue("cursor_save", "cursor", {"summary": SUMMARY}, root=protected)
    monkeypatch.setattr(_processor, "prepare", lambda *a: dict(values))
    def stopped(event, *a):
        assert event["prepared"] == values
        raise RuntimeError(MARKER)
    monkeypatch.setattr(_processor, "process", stopped)
    assert spool.drain(protected)["failed"] == 1
    raw = wire(files(protected)[0])
    assert not set(values) & set(raw)
    assert module().QueueCodec(protected).decode(raw)["prepared"] == values
    scan(protected)


def test_fallback_log_contains_only_closed_crypto_reason(protected, monkeypatch):
    (protected / "logs").write_text("synthetic-obstruction", encoding="utf-8")
    monkeypatch.delenv("ENGRAM_SECRET")
    assert enqueue(protected) == ""
    fallback = protected.with_name(protected.name + ".hooks.log")
    assert "crypto-secret-missing" in fallback.read_text(encoding="utf-8")
    scan(protected)


def test_unknown_kind_cannot_put_arbitrary_text_in_clear_header(protected):
    assert spool.enqueue(MARKER, None, {"summary": SUMMARY}, root=protected) == ""
    assert not files(protected)
    scan(protected)


def test_encrypted_mcp_import_and_read_never_drain(protected):
    assert enqueue(protected)
    before = {p.name: p.read_bytes() for p in files(protected)}
    env = dict(os.environ, ENGRAM_EPHEMERAL="1")
    code = "import piia_engram.mcp_server as m; from piia_engram.core import Engram; Engram(read_only=True).get_profile()"
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    assert {p.name: p.read_bytes() for p in files(protected)} == before
    assert not (protected / "knowledge" / "lessons.json").exists()
    scan(protected, result.stdout, result.stderr)


def test_policy_disk_full_keeps_shared_failure_batch_stop(protected, monkeypatch):
    assert enqueue(protected)
    assert enqueue(protected, SUMMARY + " second")
    (protected / "hooks" / "spool-protection.json").unlink()
    publish = spool._publish
    def denied(path, data):
        if path.name == "spool-protection.json":
            raise OSError(errno.ENOSPC, MARKER)
        return publish(path, data)
    monkeypatch.setattr(spool, "_publish", denied)
    monkeypatch.setattr(_processor, "process", lambda *a: pytest.fail("policy failure effect"))
    before = {p.name: p.read_bytes() for p in files(protected)}
    assert spool.drain(protected)["failed"] == 1
    assert {p.name: p.read_bytes() for p in files(protected)} == before
    scan(protected)


def test_near_limit_retry_records_oversize_without_replacing_source(protected, monkeypatch):
    codec = module().QueueCodec(protected)
    event = working()
    low, high = 0, 100_000
    while low < high:
        middle = (low + high + 1) // 2
        event["payload"]["summary"] = "x" * middle
        try:
            codec.encode(event)
            low = middle
        except module().ProtectionError:
            high = middle - 1
    event["payload"]["summary"] = "x" * low
    directory = spool.spool_dir(protected)
    directory.mkdir(parents=True)
    path = directory / "near-limit.jsonl"
    path.write_bytes(codec.encode(event))
    before = path.read_bytes()
    monkeypatch.setattr(_processor, "prepare", lambda *a: (_ for _ in ()).throw(FileNotFoundError(MARKER)))
    assert spool.drain(protected)["failed"] == 1
    assert path.read_bytes() == before
    assert status(path) == "event-oversize"
    scan(protected)


def enable_in_other_process(root):
    """The enabler has a secret; the paused writer need not have one."""
    env = dict(os.environ, ENGRAM_DIR=str(root), ENGRAM_SECRET=SECRET)
    result = subprocess.run([sys.executable, "-c",
        "from piia_engram.core import Engram; Engram()"], env=env,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert module().policy_path(root).is_file()
    assert (root / ".corpus_salt").is_file()


@pytest.mark.parametrize("keys_arrive", [False, True])
def test_enqueue_enablement_interleaving_never_publishes_plaintext(tmp_path, monkeypatch, keys_arrive):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    # Isolate the publication race from subprocess startup/KDF latency.
    monkeypatch.setattr(module(), "CRYPTO_BUDGET_SECONDS", 20)
    original = spool.seal_with_budget
    publications = []
    replace = spool.replace_with_retry
    def inspect(source, target):
        if target.suffix == ".jsonl":
            publications.append(source.read_bytes())
        return replace(source, target)
    def seal_then_enable(*args, **kwargs):
        data, protected = original(*args, **kwargs)
        if not protected:
            assert MARKER.encode("utf-8") in data
            enable_in_other_process(tmp_path)
            if keys_arrive:
                monkeypatch.setenv("ENGRAM_SECRET", SECRET)
        return data, protected
    monkeypatch.setattr(spool, "seal_with_budget", seal_then_enable)
    monkeypatch.setattr(spool, "replace_with_retry", inspect)
    result = spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY},
                           root=tmp_path, deadline=time.monotonic() + 25)
    assert bool(result) is keys_arrive
    if keys_arrive:
        assert wire(files(tmp_path)[0])["schema"] == 2
        assert publications and all(MARKER.encode("utf-8") not in data for data in publications)
    else:
        assert not files(tmp_path) and not publications
        assert "crypto-secret-missing" in (tmp_path / "logs/hooks.log").read_text(encoding="utf-8")
    scan(tmp_path)


@pytest.mark.parametrize("keys_arrive", [False, True])
def test_replacement_enablement_interleaving_retains_or_reseals(tmp_path, monkeypatch, keys_arrive):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    path = spool.spool_dir(tmp_path) / "legacy.jsonl"
    path.parent.mkdir(parents=True)
    event = working()
    write_wire(path, event)
    before = path.read_bytes()
    original = module().QueueCodec.encode
    publications = []
    replace = spool.replace_with_retry
    def inspect(source, target):
        if target == path:
            publications.append(source.read_bytes())
        return replace(source, target)
    def encode_then_enable(codec, value):
        data = original(codec, value)
        if json.loads(data)["schema"] == 1:
            enable_in_other_process(tmp_path)
            if keys_arrive:
                monkeypatch.setenv("ENGRAM_SECRET", SECRET)
        return data
    monkeypatch.setattr(module().QueueCodec, "encode", encode_then_enable)
    monkeypatch.setattr(spool, "replace_with_retry", inspect)
    monkeypatch.setattr(_processor, "process", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic stop")))
    result = spool.drain(tmp_path)
    assert result["failed"] == 1 and result["processed"] == 0
    assert not list(path.parent.glob("receipts/*.json"))
    if keys_arrive:
        assert wire(path)["schema"] == 2 and publications
        scan(tmp_path)
    else:
        assert path.read_bytes() == before and not publications
        assert status(path) == "crypto-secret-missing"
    assert all(MARKER.encode("utf-8") not in data for data in publications)


@pytest.mark.parametrize("replacement", [False, True])
def test_busy_policy_gate_refuses_plaintext_after_bounded_wait(tmp_path, monkeypatch, replacement, budget_clock):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    event = working()
    path = spool.spool_dir(tmp_path) / "legacy.jsonl"
    if replacement:
        path.parent.mkdir()
        write_wire(path, event)
        before = path.read_bytes()
    with portalocker.Lock(hooks / ".spool-policy.lock", "a", timeout=0):
        start = budget_clock.monotonic()
        if replacement:
            with pytest.raises(module().ProtectionError):
                spool._publish_event(path, event, module().QueueCodec(tmp_path))
            assert path.read_bytes() == before
        else:
            assert not enqueue(tmp_path)
            assert not files(tmp_path)
        assert budget_clock.monotonic() - start == pytest.approx(spool.PLAINTEXT_GATE_WAIT)


@pytest.mark.parametrize("encrypted", [False, True])
def test_slow_policy_read_is_inside_remaining_budget(tmp_path, monkeypatch, encrypted, budget_clock):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    if encrypted:
        (tmp_path / ".corpus_salt").write_bytes(SALT)
        module().ensure_required(tmp_path)
        monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = Path.lstat
    marker = module().policy_path(tmp_path)
    def delayed(path, *args, **kwargs):
        if path == marker:
            started.set()
            release.wait(30)
            try:
                return original(path, *args, **kwargs)
            finally:
                finished.set()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", delayed)
    waits, workers = expire_worker_wait(monkeypatch, budget_clock, started)
    start = budget_clock.monotonic()
    try:
        assert not spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY},
                                 root=tmp_path, deadline=start + 0.1)
        assert waits == [pytest.approx(0.1 - module().DIAGNOSTIC_RESERVE_SECONDS)]
        before = snapshot(tmp_path)
    finally:
        release.set()
        finish_workers(workers)
    assert finished.is_set()
    assert snapshot(tmp_path) == before
    assert not files(tmp_path)
    assert "crypto-timeout" in (tmp_path / "logs/hooks.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("phase", ["expired", "policy", "encoding", "completed"])
def test_expired_plaintext_never_starts_publication(tmp_path, monkeypatch, phase, budget_clock):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    seal = spool.seal_with_budget
    if phase in {"policy", "encoding"}:
        target = "required" if phase == "policy" else "json_bytes"
        function = getattr(module(), target)
        def slow(*args, **kwargs):
            budget_clock.advance(101)
            return function(*args, **kwargs)
        monkeypatch.setattr(module(), target, slow)
    elif phase == "completed":
        def delayed_result(*args, **kwargs):
            result = seal(*args, **kwargs)
            budget_clock.advance(101)
            return result
        monkeypatch.setattr(spool, "seal_with_budget", delayed_result)
    published = []
    publish = spool._publish
    def inspect(path, data):
        if path.suffix == ".jsonl":
            published.append(data)
        return publish(path, data)
    monkeypatch.setattr(spool, "_publish", inspect)
    deadline = budget_clock.monotonic() + (-1 if phase == "expired" else 100)
    assert not spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY},
                             root=tmp_path, deadline=deadline)
    assert not published and not files(tmp_path)
    assert "crypto-timeout" in (tmp_path / "logs/hooks.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("salt_state", ["missing", "invalid", "valid"])
def test_enablement_commits_marker_before_salt_under_gate(tmp_path, monkeypatch, salt_state):
    from piia_engram.core import Engram
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    salt_path = tmp_path / ".corpus_salt"
    if salt_state != "missing":
        salt_path.write_bytes(SALT if salt_state == "valid" else b"bad")
    original = Engram._atomic_write_bytes
    writes = []
    def inspect(path, data):
        if path == salt_path:
            assert wire(module().policy_path(tmp_path)) == module().POLICY
            with pytest.raises(module().PolicyGateBusy):
                with module().policy_gate(tmp_path):
                    pytest.fail("salt publication escaped the policy gate")
            writes.append(path)
        return original(path, data)
    monkeypatch.setattr(Engram, "_atomic_write_bytes", staticmethod(inspect))
    Engram(root=tmp_path)
    assert bool(writes) is (salt_state != "valid")
    assert wire(module().policy_path(tmp_path)) == module().POLICY
    # No lock leaked by nested failed acquisitions or successful enablement.
    with module().policy_gate(tmp_path):
        pass


@pytest.mark.parametrize("action", ["enable", "marker"])
def test_enablement_gate_wait_is_bounded_and_creates_no_intent(tmp_path, monkeypatch, action, budget_clock):
    from piia_engram.core import Engram
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    monkeypatch.setattr(module(), "POLICY_GATE_WAIT_SECONDS", 0.05)
    with module().policy_gate(tmp_path):
        start = budget_clock.monotonic()
        with pytest.raises(module().PolicyGateBusy):
            if action == "enable":
                Engram(root=tmp_path)
            else:
                module().ensure_required(tmp_path)
        assert budget_clock.monotonic() - start == pytest.approx(0.05)
        assert not module().policy_path(tmp_path).exists()
        assert not (tmp_path / ".corpus_salt").exists()
    with module().policy_gate(tmp_path):
        pass


def test_plaintext_publication_holds_gate_against_real_enabler(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    ready = tmp_path / "enabler-ready"
    original = spool._publish
    children = []
    def publish(path, data):
        if path.suffix == ".jsonl":
            code = ("from pathlib import Path; from piia_engram.core import Engram; "
                    "import os; Path(os.environ['SYNTHETIC_READY']).write_text('ready', encoding='utf-8'); Engram()")
            child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=dict(os.environ, ENGRAM_DIR=str(tmp_path),
                ENGRAM_SECRET=SECRET, SYNTHETIC_READY=str(ready)))
            children.append(child)
            end = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < end:
                time.sleep(0.01)
            assert ready.exists()
            time.sleep(0.05)
            assert child.poll() is None
            assert not module().policy_path(tmp_path).exists()
            assert not (tmp_path / ".corpus_salt").exists()
        return original(path, data)
    monkeypatch.setattr(spool, "_publish", publish)
    try:
        event_id = enqueue(tmp_path)
    finally:
        for child in children:
            _, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert event_id and wire(files(tmp_path)[0])["schema"] == 1
    assert module().policy_path(tmp_path).is_file()
    monkeypatch.setattr(spool, "_publish", original)
    # Published before enablement completed: disclosed legacy, sealed on normal drain.
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    monkeypatch.setattr(_processor, "process", lambda *a: (_ for _ in ()).throw(RuntimeError("synthetic stop")))
    assert spool.drain(tmp_path)["failed"] == 1
    assert wire(files(tmp_path)[0])["schema"] == 2
    scan(tmp_path)


def test_slow_marker_read_probe_and_no_late_publication(tmp_path, monkeypatch, budget_clock):
    (tmp_path / ".corpus_salt").write_bytes(SALT)
    module().ensure_required(tmp_path)
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    original = Path.open
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    class SlowReader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
            finished.set()
        def read(self, size=-1):
            started.set()
            release.wait(30)
            return self.handle.read(size)
    def delayed(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        return (SlowReader(handle) if path == module().policy_path(tmp_path)
                and threading.current_thread().name == "engram-hook-seal" else handle)
    monkeypatch.setattr(Path, "open", delayed)
    waits, workers = expire_worker_wait(monkeypatch, budget_clock, started)
    try:
        assert not spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY},
                                 root=tmp_path, deadline=budget_clock.monotonic() + 0.1)
        assert waits == [pytest.approx(0.1 - module().DIAGNOSTIC_RESERVE_SECONDS)]
        before = snapshot(tmp_path)
    finally:
        release.set()
        finish_workers(workers)
    assert finished.is_set()
    assert snapshot(tmp_path) == before
    assert not files(tmp_path)


def test_final_policy_recheck_is_bounded_and_releases_gate(tmp_path, monkeypatch, budget_clock):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    original = module().required
    calls = []
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def slow_recheck(root):
        calls.append(root)
        if len(calls) == 2:
            started.set()
            release.wait(30)
            finished.set()
        return original(root)
    monkeypatch.setattr(module(), "required", slow_recheck)
    waits, workers = expire_worker_wait(monkeypatch, budget_clock, started, wait_number=2)
    try:
        assert not spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY},
                                 root=tmp_path, deadline=budget_clock.monotonic() + 0.1)
        assert waits == [pytest.approx(0.075), pytest.approx(0.075)]
        with module().policy_gate(tmp_path):
            pass
    finally:
        release.set()
        finish_workers(workers)
    assert finished.is_set()
    assert not files(tmp_path)
