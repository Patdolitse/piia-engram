"""Local offline queue. Producers publish one line by rename, never a store lock."""
from __future__ import annotations

import json
import errno
import os
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from ._log import log_failure
from ..atomic_replace import replace_with_retry

MAX_PENDING_EVENTS = 1000
MAX_PENDING_BYTES = 16 * 1024 * 1024
MAX_EVENT_BYTES = 128 * 1024
MAX_MISSING_TRANSCRIPT_ATTEMPTS = 3
MAX_MISSING_TRANSCRIPT_AGE_SECONDS = 7 * 86400
CLEANUP_CANDIDATE_AGE_SECONDS = 7 * 86400
BACKLOG_HINT_AGE_SECONDS = 86400
RESULT_CODES = frozenset({"processed", "duplicate", "invalid-event", "invalid-prepared-event",
    "pending-cap", "transcript-missing", "transcript-missing-age-limit",
    "transcript-missing-retry-limit", "storage-unavailable", "processing-failed",
    "receipt-failed", "transport-unavailable", "unknown"})
KINDS = {"claude_stop": "claude_code", "claude_compact": "claude_code",
         "cursor_save": "cursor", "cursor_writeback": "cursor"}
_DRAINING = ContextVar("hook_spool_draining", default=False)


def store_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else Path(
        os.environ.get("ENGRAM_DIR", "").strip() or "~/.engram").expanduser()


def spool_dir(root: Path | None = None) -> Path:
    return store_root(root) / "hooks" / "spool"


def _publish(path: Path, data: bytes) -> None:
    """Unique temp file + fsync + replace; incomplete lines are never visible."""
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temporary, path)
    except Exception:
        # Preserve partial evidence, including disk-full writes, for inspection.
        raise


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _files(directory: Path) -> list[Path]:
    def order(path):
        stamp = path.name.partition("-")[0]
        # Preparing/retrying an event replaces its file, but must not reset its order.
        arrival = int(stamp) if len(stamp) == 20 and stamp.isdigit() else path.stat().st_mtime_ns
        return arrival, path.name
    return sorted((p for p in directory.glob("*.jsonl") if not p.is_symlink()),
                  key=order)


def _created_seconds(path: Path) -> float:
    """Read just the envelope header; age survives prepared-file replacement."""
    try:
        with path.open("rb") as handle:
            header = handle.read(4096).decode("utf-8", errors="replace")
        match = re.search(r'"created_at"\s*:\s*"([^"\\]+)"', header)
        if match:
            created = datetime.fromisoformat(match.group(1))
            if created.tzinfo is not None:
                return created.timestamp()
    except (OSError, ValueError, OverflowError):
        pass
    return path.stat().st_mtime


def _quarantine(path: Path, reason: str) -> None:
    directory = path.parent / "quarantine"
    directory.mkdir(exist_ok=True)
    destination = directory / path.name
    if destination.exists():
        destination = directory / (uuid.uuid4().hex + "-" + path.name)
    _publish(destination.with_suffix(".reason.json"), _json_bytes({"reason": reason}))
    replace_with_retry(path, destination)


def _trim(directory: Path) -> int:
    files = _files(directory)
    total = sum(p.stat().st_size for p in files)
    count = 0
    while files and (len(files) > MAX_PENDING_EVENTS or total > MAX_PENDING_BYTES):
        oldest = files.pop(0)
        size = oldest.stat().st_size
        _quarantine(oldest, "pending-cap")
        total -= size
        count += 1
    return count


def _lock(directory: Path):
    import portalocker
    return portalocker.Lock(directory / ".spool.lock", "a", timeout=0,
                           fail_when_locked=True)


def enqueue(kind: str, client: str, payload: dict, *, root: Path | None = None) -> str:
    """Fail-soft producer. Returns the published event ID, or an empty string."""
    try:
        if KINDS.get(kind) != client:
            raise ValueError("unsupported hook kind")
        event_id = uuid.uuid4().hex
        event = {"schema": 1, "event_id": event_id, "kind": kind, "client": client,
                 "created_at": datetime.now(timezone.utc).isoformat(), "payload": payload}
        data = _json_bytes(event)
        if len(data) > MAX_EVENT_BYTES:
            raise ValueError("hook event exceeds size limit")
        directory = spool_dir(root)
        directory.mkdir(parents=True, exist_ok=True)
        _publish(directory / f"{time.time_ns():020d}-{event_id}.jsonl", data)
        try:
            with _lock(directory):
                _trim(directory)
        except Exception as exc:
            # Busy maintenance is normal: no waiting in the client's lifecycle.
            import portalocker
            if not isinstance(exc, portalocker.LockException):
                log_failure("hook_spool", "cap maintenance failed (" + type(exc).__name__ + ")")
        return event_id
    except Exception as exc:
        log_failure("hook_spool", "event publish failed (" + type(exc).__name__ + ")", root=root)
        return ""


def backlog(root: Path | None = None) -> dict:
    """Metadata only, including absent stores. Never mkdir, lock, or instantiate Engram."""
    from ..connection_report import store_identity
    now = time.time()
    candidate = lambda: {"count": 0, "bytes": 0}
    result = {"pending": 0, "pending_bytes": 0, "oldest_age_seconds": None,
              "quarantined": 0, "partial": 0, "cap_events": MAX_PENDING_EVENTS,
              "quarantined_bytes": 0, "partial_bytes": 0, "receipts": 0, "receipt_bytes": 0,
              "cap_bytes": MAX_PENDING_BYTES, "cap_scope": "pending_only", "read_only": True,
              "store": store_identity(store_root(root)), "host_consumption": "unknown",
              "states": {"queued": "Queued locally; durable knowledge not confirmed.",
                         "processed": "Durable processing receipt; not approved; may contain only a session record.",
                         "hook_output": "Output success does not confirm host consumption."},
              "cleanup_candidates": {"quarantine": candidate(), "partial": candidate(),
                                     "receipts": candidate(), "age_seconds": CLEANUP_CANDIDATE_AGE_SECONDS,
                                     "read_only": True, "automatic_deletion": False},
              "receipt_retention": "keep_by_default_for_dedup", "recent_results": [], "drain_hint": ""}
    try:
        directory = spool_dir(root)
        files = _files(directory)
        result["pending"] = len(files)
        result["pending_bytes"] = sum(p.stat().st_size for p in files)
        if files:
            result["oldest_age_seconds"] = max(0, round(now - min(
                _created_seconds(p) for p in files), 3))
        quarantine = directory / "quarantine"
        receipts = directory / "receipts"

        def regular(folder, pattern):
            if folder.is_symlink():
                return []
            return [p for p in folder.glob(pattern) if not p.is_symlink() and p.is_file()]

        quarantined = regular(quarantine, "*.jsonl")
        quarantine_files = regular(quarantine, "*")
        receipt_files = regular(receipts, "*.json")
        partials = [p for folder in (directory, quarantine, receipts) for p in regular(folder, "*.partial")]
        result.update(quarantined=len(quarantined),
                      quarantined_bytes=sum(p.stat().st_size for p in quarantine_files if not p.name.endswith(".partial")),
                      partial=len(partials), partial_bytes=sum(p.stat().st_size for p in partials),
                      receipts=len(receipt_files), receipt_bytes=sum(p.stat().st_size for p in receipt_files))
        for category, paths in (("quarantine", quarantine_files), ("partial", partials)):
            old = [p for p in paths if now - p.stat().st_mtime >= CLEANUP_CANDIDATE_AGE_SECONDS
                   and (category != "quarantine" or not p.name.endswith(".partial"))]
            result["cleanup_candidates"][category] = {"count": len(old), "bytes": sum(p.stat().st_size for p in old)}
        # Read only bounded envelope/receipt metadata. Never return identifiers,
        # file names, payloads, transcript locations, or exception messages.
        candidates = [(p.stat().st_mtime, p, "processing_result") for p in files]
        candidates += [(p.stat().st_mtime, p, "reason") for p in regular(quarantine, "*.reason.json")]
        candidates += [(p.stat().st_mtime, p, "receipt") for p in receipt_files]
        for stamp, path, field in sorted(candidates, key=lambda item: item[0], reverse=True)[:20]:
            try:
                with path.open("rb") as handle:
                    header = handle.read(4096).decode("utf-8", errors="replace")
                if field == "processing_result":
                    header = header.split('"payload"', 1)[0]
                    match = re.search(r'"processing_result"\s*:\s*"([^"\\]+)"', header)
                    if not match:
                        continue
                    code = match.group(1)
                elif field == "receipt":
                    metadata = json.loads(header)
                    code = metadata.get("reason", "processed") if isinstance(metadata, dict) else "unknown"
                else:
                    metadata = json.loads(header)
                    code = metadata.get("reason", "unknown") if isinstance(metadata, dict) else "unknown"
                code = code if isinstance(code, str) and code in RESULT_CODES else "unknown"
            except (OSError, ValueError):
                code = "unknown"
            result["recent_results"].append({"code": code, "age_seconds": max(0, round(now - stamp, 3))})
        if result["pending"] and (result["pending"] >= 100 or
                (result["oldest_age_seconds"] or 0) >= BACKLOG_HINT_AGE_SECONDS):
            result["drain_hint"] = "Run locally against this store: engram hooks drain --dry-run --json; then engram hooks drain --json"
    except OSError:
        result["error"] = "metadata-unavailable"
    return result


def _record_result(path: Path, event: dict | None, code: str) -> None:
    """Reuse the pending envelope for a bounded, body-free processing outcome."""
    if event is not None:
        updated = {"processing_result": code if code in RESULT_CODES else "unknown", **event}
        updated["processing_result"] = code if code in RESULT_CODES else "unknown"
        event.clear()
        event.update(updated)
        data = _json_bytes(updated)
        if len(data) <= MAX_EVENT_BYTES:
            _publish(path, data)


class PoisonEvent(ValueError):
    """Malformed envelope; processing errors are retryable instead."""


def _shared_storage_failure(exc: Exception) -> bool:
    """Only known shared failures stop unrelated events, including wrapped locks."""
    import portalocker
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, portalocker.LockException):
            return True
        if isinstance(exc, OSError) and (
            exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}
            or getattr(exc, "winerror", None) in {39, 112}
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _retry_missing_transcript(path: Path, event: dict) -> bool:
    event["transcript_missing_attempts"] = event.get("transcript_missing_attempts", 0) + 1
    _publish(path, _json_bytes(event))
    age = time.time() - datetime.fromisoformat(event["created_at"]).timestamp()
    reason = ""
    if age >= MAX_MISSING_TRANSCRIPT_AGE_SECONDS:
        reason = "transcript-missing-age-limit"
    elif event["transcript_missing_attempts"] >= MAX_MISSING_TRANSCRIPT_ATTEMPTS:
        reason = "transcript-missing-retry-limit"
    if reason:
        _quarantine(path, reason)
    return bool(reason)


def _read_event(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_EVENT_BYTES:
            raise ValueError("oversize")
        event = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(event, dict) or type(event.get("schema")) is not int or event.get("schema") != 1:
            raise ValueError("schema")
        if KINDS.get(event.get("kind")) != event.get("client") or event.get("kind") not in KINDS:
            raise ValueError("kind")
        event_id = event["event_id"]
        if not isinstance(event_id, str) or uuid.UUID(event_id).hex != event_id:
            raise ValueError("event-id")
        created = datetime.fromisoformat(event["created_at"])
        if created.tzinfo is None or not isinstance(event.get("payload"), dict):
            raise ValueError("payload")
        from ._processor import validate_payload
        validate_payload(event)
        return event
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PoisonEvent(type(exc).__name__) from exc


def drain(root: Path | None = None, *, dry_run: bool = False, engram=None) -> dict:
    root = store_root(root)
    report = dict(backlog(root), processed=0, duplicates=0, failed=0, quarantined_now=0,
                  dry_run=dry_run, busy=False)
    if dry_run or not report["pending"] or _DRAINING.get():
        return report
    directory = spool_dir(root)
    token = _DRAINING.set(True)
    try:
        with _lock(directory):
            report["quarantined_now"] += _trim(directory)
            for path in _files(directory):
                event = None
                phase = "read"
                try:
                    event = _read_event(path)
                    receipt = directory / "receipts" / (event["event_id"] + ".json")
                    if receipt.exists():
                        # Validate receipts; corruption must fail closed, never discard work.
                        saved = json.loads(receipt.read_text(encoding="utf-8"))
                        if saved.get("event_id") != event["event_id"]:
                            raise ValueError("invalid receipt")
                        report["duplicates"] += 1
                    else:
                        from ._processor import prepare, process, validate_payload, freeze_checkpoint_provenance
                        phase = "prepare"
                        changed = "prepared" not in event
                        if changed:
                            event["prepared"] = prepare(event, root, engram)
                        try:
                            validate_payload(event)
                        except (ValueError, TypeError, KeyError, AttributeError) as exc:
                            raise PoisonEvent("invalid prepared fields") from exc
                        phase = "provenance"
                        changed = freeze_checkpoint_provenance(event, root, engram) or changed
                        phase = "publish"
                        if changed:
                            # Frozen input replaces the inline source, avoiding a second
                            # copy of a maximum-size Unicode summary in the envelope.
                            event["payload"].pop("summary", None)
                            data = _json_bytes(event)
                            # Prepared data has the same capture limits; allow metadata overhead.
                            if len(data) > MAX_EVENT_BYTES:
                                raise PoisonEvent("prepared event oversize")
                            _publish(path, data)
                        phase = "process"
                        process(event, root, engram)
                        phase = "receipt"
                        receipt.parent.mkdir(exist_ok=True)
                        _publish(receipt, _json_bytes({"event_id": event["event_id"],
                                                      "reason": "processed",
                                                      "processed_at": datetime.now(timezone.utc).isoformat()}))
                        report["processed"] += 1
                    path.unlink()  # only after durable receipt
                except PoisonEvent:
                    _quarantine(path, "invalid-event" if phase == "read" else "invalid-prepared-event")
                    report["quarantined_now"] += 1
                except Exception as exc:
                    report["failed"] += 1
                    from ..transport_errors import transport_failure
                    failure = transport_failure(exc)
                    if failure:
                        report.update(failure)
                    code = ("transport-unavailable" if failure else
                            "storage-unavailable" if _shared_storage_failure(exc) else
                            "transcript-missing" if phase == "prepare" and isinstance(exc, FileNotFoundError) else
                            "receipt-failed" if phase == "receipt" else "processing-failed")
                    try:
                        _record_result(path, event, code)
                    except Exception:
                        # Reporting cannot replace the original failure or discard work.
                        pass
                    log_failure("hook_spool", "drain deferred (" + type(exc).__name__ + ")", root=root)
                    if _shared_storage_failure(exc):
                        break
                    if phase == "prepare" and isinstance(exc, FileNotFoundError) and event is not None:
                        try:
                            if _retry_missing_transcript(path, event):
                                report["quarantined_now"] += 1
                        except Exception as retry_exc:
                            log_failure("hook_spool", "retry tracking failed (" +
                                        type(retry_exc).__name__ + ")", root=root)
                            if _shared_storage_failure(retry_exc):
                                break
                    # Input and event-specific processing failures keep their file,
                    # without a receipt, while healthy events continue this batch.
    except Exception as exc:
        import portalocker
        if isinstance(exc, portalocker.LockException):
            report["busy"] = True
        else:
            report["failed"] += 1
            log_failure("hook_spool", "drain failed (" + type(exc).__name__ + ")", root=root)
    finally:
        _DRAINING.reset(token)
    report.update(backlog(root))
    report["quarantine_total"] = report["quarantined"]
    report["quarantined"] = report["quarantined_now"]
    report["read_only"] = False
    return report


def run_cli(args: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="engram hooks")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("drain", help="Process local queued hooks into staging")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--json", action="store_true")
    options = parser.parse_args(args)
    result = drain(dry_run=options.dry_run)
    if options.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print(f"Hook spool: {result['pending']} pending, {result['processed']} processed, "
              f"{result['duplicates']} replays, {result['quarantined']} quarantined, "
              f"{result['failed']} failed" + (" (processor busy)" if result["busy"] else ""))
    return 1 if result["failed"] or result["busy"] else 0
