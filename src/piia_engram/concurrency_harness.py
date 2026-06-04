"""Multi-writer local concurrency stress harness — honest, bounded, offline.

WHY: Engram is a *local* store that several tools (Claude Code, Codex, Cursor)
may write to at overlapping times. Two distinct safety questions matter:

1. **Integrity / no corruption** — can a concurrent writer ever leave a knowledge
   file half-written or non-JSON? The storage layer answers this with
   ``tempfile`` + ``os.fsync`` + ``os.replace`` (atomic rename) under a
   per-directory ``portalocker`` lock, so the on-disk file is always either the
   old or a new *complete* document. This harness verifies that contract holds
   under contention.

2. **No lost writes** — if two writers race, does every write survive? This is
   only guaranteed for the read-modify-write path that holds the lock ACROSS the
   read (``storage._update_json``, used by the governance stores). The knowledge
   add-path (``add_lesson`` / ``add_decision``) reads OUTSIDE the lock and then
   atomically writes, so two in-process racers can read the same list and one
   can clobber the other — a *lost update*, never a *corruption*.

This module reports both honestly. It NEVER claims lost-update safety for the
knowledge path; it measures the surviving-write count and surfaces it. For the
governance path it asserts (via the caller's tests) the supported no-lost-update
contract. Everything runs in a caller-provided temp ``root`` — it never touches a
real store — and the returned report is metadata-only (counts, booleans, error
categories), safe to embed in a committable evidence file.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .core import Engram
from .governance_store import RelationStore


def _classify_error(exc: BaseException) -> str:
    msg = str(exc).lower()
    if "lock" in msg or "timeout" in msg:
        return "lock_timeout"
    if isinstance(exc, (OSError, IOError)):
        return "io"
    return "unknown"


def _read_raw_list(path: Path) -> tuple[bool, int]:
    """Return ``(json_valid, entry_count)`` for a knowledge JSON file.

    ``json_valid`` is True iff the file parses as a JSON list (the integrity
    contract); a torn / partial write would fail to parse and is the corruption
    signal we explicitly check for.
    """
    if not path.exists():
        return True, 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False, 0
    if not isinstance(data, list):
        return False, 0
    return True, len(data)


def run_knowledge_multiwriter_stress(
    root: str | Path,
    *,
    writers: int = 8,
    per_writer: int = 6,
) -> dict[str, Any]:
    """Stress the knowledge add-path with ``writers`` concurrent threads.

    Each thread uses its OWN ``Engram`` instance pointed at the same ``root`` and
    adds ``per_writer`` lessons with globally-unique summaries (so the dedup
    layer can never mask a write). Returns a metadata-only report::

        {"path": "knowledge",
         "intended_writes": int,
         "json_valid": bool,        # integrity contract — must be True
         "persisted": int,          # surviving entries (<= intended)
         "lost_updates": int,       # intended - persisted (honest; may be > 0)
         "errors": {category: count},
         "integrity_ok": bool,      # json_valid AND every entry well-formed
         "no_lost_updates": bool}   # persisted == intended (NOT guaranteed here)

    Bounded and deterministic in shape (no sleeps); the lost-update COUNT is
    timing-dependent by nature, which is exactly why the harness reports it
    instead of asserting it is zero.
    """
    root = Path(root)
    intended = writers * per_writer
    errors: dict[str, int] = {}
    err_lock = threading.Lock()
    barrier = threading.Barrier(writers)

    def _worker(wid: int) -> None:
        eng = Engram(root=root)
        # Release all threads together to maximize real contention.
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        for i in range(per_writer):
            try:
                eng.add_lesson({
                    "summary": f"writer {wid} unique lesson {i} token-{wid}-{i}",
                    "domain": f"d{wid}",
                    "tier": "verified",
                    "status": "active",
                })
            except Exception as exc:  # noqa: BLE001 — categorize, never crash harness
                cat = _classify_error(exc)
                with err_lock:
                    errors[cat] = errors.get(cat, 0) + 1

    threads = [threading.Thread(target=_worker, args=(w,)) for w in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    path = root / "knowledge" / "lessons.json"
    json_valid, persisted = _read_raw_list(path)

    # Integrity: every surviving entry must be a well-formed dict with a summary.
    integrity_ok = json_valid
    if json_valid and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            integrity_ok = all(
                isinstance(e, dict) and isinstance(e.get("summary", ""), str)
                for e in data
            )
        except (json.JSONDecodeError, OSError):
            integrity_ok = False

    lost = max(0, intended - persisted)
    return {
        "path": "knowledge",
        "intended_writes": intended,
        "json_valid": json_valid,
        "persisted": persisted,
        "lost_updates": lost,
        "errors": errors,
        "integrity_ok": integrity_ok,
        "no_lost_updates": persisted == intended,
    }


def run_governance_multiwriter_stress(
    root: str | Path,
    *,
    writers: int = 8,
    per_writer: int = 6,
) -> dict[str, Any]:
    """Stress the lock-across-read governance path (``_update_json``).

    Each thread adds ``per_writer`` globally-unique relation edges concurrently.
    Because ``_update_json`` holds the per-directory write lock across read →
    mutate → atomic replace, every accepted edge must survive: this is the path
    that DOES guarantee no lost updates. Report shape mirrors the knowledge run.

    Lock-timeout outcomes are counted as ``errors`` and subtracted from the
    intended total when judging the no-lost-update contract — a fail-closed lock
    timeout is correct behavior, not a lost write.
    """
    root = Path(root)
    (root / "knowledge").mkdir(parents=True, exist_ok=True)
    intended = writers * per_writer
    errors: dict[str, int] = {}
    accepted = {"n": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(writers)

    def _worker(wid: int) -> None:
        rs = RelationStore(root)
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        for i in range(per_writer):
            try:
                added = rs.add_relation(f"n{wid}-{i}", "led_to", f"m{wid}-{i}")
                if added:
                    with lock:
                        accepted["n"] += 1
            except Exception as exc:  # noqa: BLE001
                cat = _classify_error(exc)
                with lock:
                    errors[cat] = errors.get(cat, 0) + 1

    threads = [threading.Thread(target=_worker, args=(w,)) for w in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    path = root / "knowledge" / "relations.json"
    json_valid, persisted = _read_raw_list(path)
    error_total = sum(errors.values())
    # Edges that were neither accepted nor errored-out should not exist; the
    # contract is: every accepted edge persisted.
    return {
        "path": "governance",
        "intended_writes": intended,
        "accepted_writes": accepted["n"],
        "json_valid": json_valid,
        "persisted": persisted,
        "errors": errors,
        "integrity_ok": json_valid,
        # accepted edges all survived (no lost updates on the locked path)
        "no_lost_updates": persisted == accepted["n"],
        "lock_timeouts": errors.get("lock_timeout", 0),
        "error_total": error_total,
    }


def run_full_report(
    root: str | Path,
    *,
    writers: int = 8,
    per_writer: int = 6,
) -> dict[str, Any]:
    """Run both stress paths under separate subdirectories and roll up an honest,
    metadata-only verdict. ``knowledge_lost_update_possible`` is stated as a known
    architectural property, NOT a bug to hide."""
    root = Path(root)
    k = run_knowledge_multiwriter_stress(
        root / "kstore", writers=writers, per_writer=per_writer
    )
    g = run_governance_multiwriter_stress(
        root / "gstore", writers=writers, per_writer=per_writer
    )
    return {
        "writers": writers,
        "per_writer": per_writer,
        "knowledge": k,
        "governance": g,
        "invariants": {
            # Always-true contracts the suite asserts:
            "no_corruption": k["integrity_ok"] and g["integrity_ok"],
            "governance_no_lost_updates": g["no_lost_updates"],
            # Honest characterization (NOT asserted to be zero):
            "knowledge_lost_update_possible": True,
            "knowledge_lost_updates_observed": k["lost_updates"],
        },
    }
