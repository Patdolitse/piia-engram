"""The crypto allowance bounds key derivation and sealing only.

Policy and marker reads, the plaintext publication check and the publish path
answer to the caller's own deadline: a hook producer's lifecycle budget, or the
longer direct-caller default. Every store, key and transcript is synthetic.
"""
from __future__ import annotations

import io
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from test_hook_queue_protection import (SALT, SECRET, enqueue, fault_keys, files,
                                        module, scan, snapshot, wire)

from piia_engram.hooks import _producer, spool

STALL = 0.6  # longer than the crypto allowance, like antivirus scanning a new file


@pytest.fixture
def clock(monkeypatch):
    """Manual monotonic clock, so a stall is exact budget time on any runner."""
    now = [1000.0]

    def advance(seconds):
        now[0] += seconds

    fake = SimpleNamespace(monotonic=lambda: now[0], time=time.time,
                           time_ns=time.time_ns, sleep=advance, advance=advance)
    for target in (module(), spool, _producer):
        monkeypatch.setattr(target, "time", fake)
    return fake


@pytest.fixture
def required_root(tmp_path, monkeypatch, fault_keys):
    """Protection-required root under the production 0.5 s crypto allowance."""
    root = tmp_path / "store"
    root.mkdir()
    (root / ".corpus_salt").write_bytes(SALT)
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    return root


def stall_policy_reads(monkeypatch, delay, *, every=False):
    """Make a policy/marker read pay ``delay`` (the first one, or every one)."""
    original = module()._required_marker
    reads = []

    def slow(root):
        reads.append(root)
        if every or len(reads) == 1:
            delay()
        return original(root)

    monkeypatch.setattr(module(), "_required_marker", slow)
    return reads


def test_slow_policy_reads_keep_the_direct_caller_deadline(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    reads = stall_policy_reads(monkeypatch, lambda: time.sleep(STALL), every=True)
    start = time.monotonic()
    event_id = enqueue(tmp_path)
    # Both the sealing probe and the plaintext publication check stalled in full.
    assert time.monotonic() - start >= 2 * STALL - 0.05 and len(reads) == 2
    path = files(tmp_path)[0]
    assert path.name.endswith(event_id + ".jsonl")
    assert wire(path)["schema"] == 1
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "hooks" / "spool-protection.json").exists()


def test_slow_policy_reads_on_a_required_root_still_seal(required_root, monkeypatch):
    reads = stall_policy_reads(monkeypatch, lambda: time.sleep(STALL), every=True)
    start = time.monotonic()
    event_id = enqueue(required_root)
    # Sealing probe, enablement check and the locked recheck each stalled in full.
    assert time.monotonic() - start >= 3 * STALL - 0.05 and len(reads) == 3
    path = files(required_root)[0]
    assert path.name.endswith(event_id + ".jsonl")
    assert wire(path)["schema"] == 2
    assert wire(required_root / "hooks" / "spool-protection.json") == module().POLICY
    assert not (required_root / "logs").exists()
    scan(required_root)


@pytest.mark.parametrize("stall", [0.6, 0.98], ids=["inside-lifecycle-deadline",
                                                   "beyond-lifecycle-deadline"])
@pytest.mark.parametrize("protected", [False, True], ids=["plain", "protection-required"])
def test_producer_keeps_its_lifecycle_deadline_through_a_policy_stall(
        tmp_path, monkeypatch, clock, fault_keys, stall, protected):
    monkeypatch.delenv("ENGRAM_SECRET", raising=False)
    assert _producer.INPUT_BUDGET_SECONDS == 1.0
    root = spool.store_root()
    root.mkdir(parents=True)
    if protected:
        (root / ".corpus_salt").write_bytes(SALT)
        monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    stall_policy_reads(monkeypatch, lambda: clock.advance(stall))
    transcript = str(tmp_path / "missing.jsonl")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"cwd": str(tmp_path), "transcript_path": transcript})))
    assert _producer.claude_event("claude_stop") == 0
    queued, hooks_log = files(root), root / "logs" / "hooks.log"
    if stall < _producer.INPUT_BUDGET_SECONDS - module().DIAGNOSTIC_RESERVE_SECONDS:
        assert len(queued) == 1 and not hooks_log.exists()
        event = wire(queued[0])
        if protected:
            assert event["schema"] == 2
            assert wire(root / "hooks" / "spool-protection.json") == module().POLICY
        else:
            assert event["schema"] == 1 and event["payload"]["transcript_path"] == transcript
    else:
        assert not queued and not (root / "hooks" / "spool-protection.json").exists()
        assert "crypto-timeout" in hooks_log.read_text(encoding="utf-8")
    scan(root)


def test_sealing_stall_beyond_the_crypto_allowance_refuses(required_root, monkeypatch, fault_keys):
    from piia_engram import crypto
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = crypto.EncryptionEngine.corpus_encrypt

    def slow(*args):
        started.set()
        release.wait(30)
        try:
            return original(*args)
        finally:
            finished.set()

    monkeypatch.setattr(crypto.EncryptionEngine, "corpus_encrypt", slow)
    assert module().CRYPTO_BUDGET_SECONDS == 0.5
    start = time.monotonic()
    try:
        assert enqueue(required_root) == ""
        assert started.is_set() and not finished.is_set()
        assert 0.4 <= time.monotonic() - start < 2.0
        before = snapshot(required_root)
    finally:
        release.set()
    for thread in threading.enumerate():
        if thread.name == "engram-hook-seal":
            thread.join(timeout=30)
    assert finished.is_set()
    # The abandoned worker finishes in memory only: no late event, no policy write.
    assert snapshot(required_root) == before and not files(required_root)
    assert not (required_root / "hooks" / "spool-protection.json").exists()
    assert "crypto-timeout" in (required_root / "logs" / "hooks.log").read_text(encoding="utf-8")
    scan(required_root)


@pytest.mark.parametrize("sealing", [0.49, 0.51], ids=["within-allowance", "beyond-allowance"])
def test_crypto_allowance_starts_with_sealing_not_the_policy_read(
        tmp_path, monkeypatch, clock, fault_keys, sealing):
    from piia_engram import crypto
    root = spool.store_root()
    root.mkdir(parents=True)
    (root / ".corpus_salt").write_bytes(SALT)
    monkeypatch.setenv("ENGRAM_SECRET", SECRET)
    stall_policy_reads(monkeypatch, lambda: clock.advance(0.3))
    original = crypto.EncryptionEngine.corpus_encrypt

    def slow(*args):
        clock.advance(sealing)
        return original(*args)

    monkeypatch.setattr(crypto.EncryptionEngine, "corpus_encrypt", slow)
    event_id = enqueue(root)
    if sealing < module().CRYPTO_BUDGET_SECONDS:
        # Policy read and sealing together exceed the allowance; neither does alone.
        assert files(root)[0].name.endswith(event_id + ".jsonl")
        assert wire(files(root)[0])["schema"] == 2
        assert not (root / "logs").exists()
    else:
        assert event_id == "" and not files(root)
        assert not (root / "hooks" / "spool-protection.json").exists()
        assert "crypto-timeout" in (root / "logs" / "hooks.log").read_text(encoding="utf-8")
    scan(root)
