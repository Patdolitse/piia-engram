"""Local offline queue. Producers publish one line by rename, never a store lock."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from ._log import log_failure

MAX_PENDING_EVENTS = 1000
MAX_PENDING_BYTES = 16 * 1024 * 1024
MAX_EVENT_BYTES = 128 * 1024
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
        os.replace(temporary, path)
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
    os.replace(path, destination)


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
    result = {"pending": 0, "pending_bytes": 0, "oldest_age_seconds": None,
              "quarantined": 0, "partial": 0, "cap_events": MAX_PENDING_EVENTS,
              "cap_bytes": MAX_PENDING_BYTES, "read_only": True}
    try:
        directory = spool_dir(root)
        files = _files(directory)
        result["pending"] = len(files)
        result["pending_bytes"] = sum(p.stat().st_size for p in files)
        if files:
            result["oldest_age_seconds"] = max(0, round(time.time() - min(
                _created_seconds(p) for p in files), 3))
        result["quarantined"] = len(list((directory / "quarantine").glob("*.jsonl")))
        result["partial"] = len(list(directory.glob("*.partial")))
    except OSError as exc:
        result["error"] = type(exc).__name__
    return result


class PoisonEvent(ValueError):
    """Malformed envelope; processing errors are retryable instead."""


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
                try:
                    event = _read_event(path)
                except PoisonEvent:
                    _quarantine(path, "invalid-event")
                    report["quarantined_now"] += 1
                    continue
                try:
                    receipt = directory / "receipts" / (event["event_id"] + ".json")
                    if receipt.exists():
                        # Validate receipts; corruption must fail closed, never discard work.
                        saved = json.loads(receipt.read_text(encoding="utf-8"))
                        if saved.get("event_id") != event["event_id"]:
                            raise ValueError("invalid receipt")
                        report["duplicates"] += 1
                    else:
                        from ._processor import prepare, process
                        if "prepared" not in event:
                            event["prepared"] = prepare(event, root, engram)
                            # Frozen input replaces the inline source, avoiding a second
                            # copy of a maximum-size Unicode summary in the envelope.
                            event["payload"].pop("summary", None)
                            data = _json_bytes(event)
                            # Prepared data has the same capture limits; allow metadata overhead.
                            if len(data) > MAX_EVENT_BYTES:
                                raise PoisonEvent("prepared event oversize")
                            _publish(path, data)
                        process(event, root, engram)
                        receipt.parent.mkdir(exist_ok=True)
                        _publish(receipt, _json_bytes({"event_id": event["event_id"],
                                                      "processed_at": datetime.now(timezone.utc).isoformat()}))
                        report["processed"] += 1
                    path.unlink()  # only after durable receipt
                except PoisonEvent:
                    _quarantine(path, "invalid-prepared-event")
                    report["quarantined_now"] += 1
                except Exception as exc:
                    report["failed"] += 1
                    log_failure("hook_spool", "drain deferred (" + type(exc).__name__ + ")", root=root)
                    # Disk/store failures are often shared: avoid repeated lock waits in this run.
                    break
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
