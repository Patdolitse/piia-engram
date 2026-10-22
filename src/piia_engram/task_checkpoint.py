"""Bounded, opt-in assistant task snapshots. No store writes or model calls."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

from .hook_digest import (normalize_text, sanitize_line, shape_scan,
                          _SECRET_KEY_SUFFIX_RE, _VALUEISH_PREFIX_RE)

SCHEMA = "task_checkpoint.v1"
OPT_IN = "ENGRAM_HOOK_TASK_CHECKPOINT_V1"
ROOTS = "ENGRAM_HOOK_TRANSCRIPT_ROOTS"
MAX_TAIL_BYTES = 256 * 1024
MAX_RECORD_BYTES = 64 * 1024
MAX_OBJECT_BYTES = 8 * 1024
MAX_VALUE_CHARS = 240
FIELDS = ("goal", "constraint", "blocker", "last_failure_reason", "next_action")
LABELS = ("Goal", "Constraint", "Risk", "Last failure reason", "Next")
# Deliberately exact: no inferred synonyms, case folding or semantic extraction.
ALIASES = dict(zip(LABELS, FIELDS)) | dict(zip(
    ("目标", "约束", "风险", "上次失败原因", "下一步"), FIELDS))
UNKNOWN = frozenset({"unknown", "Unknown", "未知", "不详", "未提供", "不确定", "N/A", "n/a"})
_LABEL = re.compile(r"^(" + "|".join(re.escape(label) for label in ALIASES) + r")[:：] *(.*)$")
_META = {"schema", "omissions", "capture_event", "captured_at"}
_OMISSIONS = {"unknown", "oversized", "privacy"}
_INLINE_SECRET_RE = re.compile(_SECRET_KEY_SUFFIX_RE.pattern.removesuffix("$") + r"\S")


def _private(values: list[str]) -> bool:
    """Scan complete normalized values and their boundaries before any cuts."""
    for value in [*values, "\n".join(values), " ".join(values), "".join(values)]:
        sanitized, hit = sanitize_line(value)
        if hit or shape_scan(sanitized):
            return True
        # Reuse the digest's cross-line key vocabulary for an inline value as
        # well (some export scrubbers intentionally require quoted passwords).
        if _INLINE_SECRET_RE.search(value):
            return True
    for left, right in zip(values, values[1:]):
        if _SECRET_KEY_SUFFIX_RE.search(left) and _VALUEISH_PREFIX_RE.match(right):
            return True
    return False


def validate(value: object) -> dict:
    """Recheck untrusted queue/prepared/sidecar input; return a detached object."""
    if not isinstance(value, dict) or set(value) != set(FIELDS) | _META:
        raise ValueError("task_shape")
    if value["schema"] != SCHEMA or value["capture_event"] not in {"Stop", "PreCompact"}:
        raise ValueError("task_schema")
    if not isinstance(value["captured_at"], str):
        raise ValueError("task_time")
    stamp = datetime.fromisoformat(value["captured_at"])
    if (stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0
            or stamp.isoformat() != value["captured_at"]):
        raise ValueError("task_time")
    omissions = value["omissions"]
    if (not isinstance(omissions, dict) or set(omissions) - set(FIELDS)
            or any(not isinstance(code, str) or code not in _OMISSIONS for code in omissions.values())):
        raise ValueError("task_omissions")
    retained = []
    for key in FIELDS:
        text = value[key]
        if text is None:
            if key not in omissions:
                raise ValueError("task_omission-missing")
            continue
        if not isinstance(text, str) or not text or text != normalize_text(text).strip():
            raise ValueError("task_value")
        if (key in omissions or len(text) > MAX_VALUE_CHARS or text in UNKNOWN
                or any(c in text for c in "\r\n\t`<>\"'") or not text.isprintable()):
            raise ValueError("task_value")
        retained.append(text)
    if not retained or _private(retained):
        raise ValueError("task_privacy")
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_OBJECT_BYTES:
        raise ValueError("task_size")
    if _private([raw.decode("utf-8")]):
        raise ValueError("task_privacy")
    return json.loads(raw.decode("utf-8"))


def parse_text(text: str, capture_event: str = "Stop") -> tuple[dict | None, str]:
    """Accept exactly one contiguous, plain five-label block in one record."""
    if capture_event not in {"Stop", "PreCompact"}:
        return None, "event-refused"
    found = {}
    positions = []
    lines = text.splitlines()
    fenced = False
    fence = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not fenced:
                fenced, fence = True, marker
            elif marker == fence:
                fenced = False
            continue
        match = _LABEL.fullmatch(stripped)
        # Quoted/fenced labels make even a second valid group ambiguous.
        disguised = _LABEL.match(stripped.lstrip(">\"'` "))
        if disguised and (fenced or line.startswith(("    ", "\t")) or stripped != disguised.group(0)):
            return None, "ambiguous-labels"
        if match:
            if fenced or line.startswith(("    ", "\t")):
                return None, "ambiguous-labels"
            key = ALIASES[match[1]]
            if key in found:
                return None, "ambiguous-labels"
            found[key] = normalize_text(match[2]).strip()
            positions.append(index)
    if set(found) != set(FIELDS):
        return None, "missing-labels"
    if any(line.strip() and not _LABEL.fullmatch(line.strip())
           for line in lines[min(positions):max(positions) + 1]):
        return None, "ambiguous-labels"
    if ((min(positions) and lines[min(positions) - 1].strip())
            or (max(positions) + 1 < len(lines) and lines[max(positions) + 1].strip())):
        return None, "ambiguous-labels"
    # Do not persist literal examples, multiline continuations or markup.
    values = [found[key] for key in FIELDS]
    if any(not v or not v.isprintable() or any(c in v for c in "`<>\"'") for v in values):
        return None, "ambiguous-labels"
    private = {key for key, value in found.items() if _private([value])}
    combined_private = _private(values) or _private(list(found.values()))
    sequences = (list(FIELDS), list(found))
    cross_field = any(_SECRET_KEY_SUFFIX_RE.search(found[left]) and _VALUEISH_PREFIX_RE.match(found[right])
                      for sequence in sequences for left, right in zip(sequence, sequence[1:]))
    # A field-local hit can be omitted. A boundary-only hit cannot safely be
    # assigned to one field, so refuse the candidate rather than guess.
    survivors_private = any(_private([found[key] for key in sequence if key not in private])
                            for sequence in sequences)
    if cross_field or (combined_private and not private) or survivors_private:
        return None, "privacy-refused"
    result = {"schema": SCHEMA, "capture_event": capture_event,
              "captured_at": datetime.now(timezone.utc).isoformat(), "omissions": {}}
    for key, value in zip(FIELDS, values):
        reason = ("privacy" if key in private else "unknown" if value in UNKNOWN else
                  "oversized" if len(value) > MAX_VALUE_CHARS else "")
        result[key] = None if reason else value
        if reason:
            result["omissions"][key] = reason
    try:
        return validate(result), "captured"
    except (ValueError, TypeError, OverflowError):
        return None, "no-useful-fields"


def render(value: dict) -> str:
    """Only this canonical five-field rendering enters the session archive."""
    task = validate(value)
    return "\n".join(f"{label}: {task[key]}" for label, key in zip(LABELS, FIELDS) if task[key] is not None)


def _local_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith(("\\\\", "//")) or "\x00" in raw:
        raise ValueError("path-refused")
    path = Path(raw)
    if not path.is_absolute() or ":" in raw[2:]:
        raise ValueError("path-refused")
    return path


def _no_links(path: Path) -> None:
    for part in (path, *path.parents):
        metadata = part.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("path-refused")


def _opened_path(fd: int, expected: Path) -> None:
    # Windows reparse races need a handle-derived path, not just path.resolve().
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        function.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = function(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise ValueError("path-refused")
        final = buffer.value
        if final.startswith("\\\\?\\UNC\\"):
            raise ValueError("path-refused")
        if final.startswith("\\\\?\\"):
            final = final[4:]
        if Path(final) != expected:
            raise ValueError("path-refused")


def _identity(metadata):
    # On Windows path stat reports creation time as ctime, while fstat can
    # report change time. Only compare the identity/size/mtime shared by both.
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _read_latest(raw_path: str, roots_json: str, capture_event: str) -> tuple[dict | None, str]:
    """One regular file, one tail read; worker can only return memory."""
    try:
        roots = json.loads(roots_json)
        if not isinstance(roots, list) or not 1 <= len(roots) <= 16:
            raise ValueError("path-refused")
        allowed = []
        for raw in roots:
            path = _local_path(raw)
            _no_links(path)
            resolved = path.resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError("path-refused")
            allowed.append(resolved)
        path = _local_path(raw_path)
        if path.suffix != ".jsonl":
            raise ValueError("path-refused")
        _no_links(path)
        path = path.resolve(strict=True)
        if not any(path.is_relative_to(root) for root in allowed):
            raise ValueError("path-refused")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("path-refused")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            _opened_path(handle.fileno(), path)
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
                return None, "source-changed"
            start = max(0, opened.st_size - MAX_TAIL_BYTES)
            handle.seek(start)
            raw = handle.read(MAX_TAIL_BYTES)
            _no_links(path)
            if (_identity(os.fstat(handle.fileno())) != _identity(opened)
                    or _identity(path.stat()) != _identity(opened) or len(raw) != opened.st_size - start):
                return None, "source-changed"
        if not raw.endswith(b"\n"):
            return None, "incomplete-tail"
        if start:
            raw = raw.partition(b"\n")[2]  # discard a potentially partial boundary record
        for line in reversed(raw.splitlines()):
            if not line.strip():
                continue
            if len(line) > MAX_RECORD_BYTES:
                return None, "record-too-large"
            try:
                from .hooks._spool_protection import loads
                entry = loads(line.decode("utf-8"))  # duplicate JSON keys are ambiguous
            except (ValueError, UnicodeError):
                return None, "invalid-record"
            if not isinstance(entry, dict):
                return None, "invalid-record"
            outer, message = entry.get("type"), entry.get("message")
            nested = message.get("role") if isinstance(message, dict) else None
            if outer != "assistant" and nested != "assistant" and entry.get("role") != "assistant":
                continue
            if (("type" in entry and outer != "assistant") or
                    (isinstance(message, dict) and "role" in message and nested != "assistant")):
                return None, "role-conflict"
            if outer != "assistant" and nested != "assistant":
                return None, "missing-assistant-shape"
            if "role" in entry and entry["role"] != "assistant":
                return None, "role-conflict"
            top = entry.get("content")
            inner = message.get("content") if isinstance(message, dict) and nested == "assistant" else None
            if isinstance(top, list) and isinstance(inner, list) and top != inner:
                return None, "role-conflict"
            content = top if isinstance(top, list) else inner
            if not isinstance(content, list):
                return None, "missing-labels"
            text = "\n".join(item["text"] for item in content if isinstance(item, dict)
                             and item.get("type") == "text" and isinstance(item.get("text"), str))
            return parse_text(text, capture_event)
        return None, "missing-assistant"
    except (OSError, ValueError, TypeError, RuntimeError):
        return None, "path-refused"


def capture(path: str, deadline: float, capture_event: str = "Stop") -> tuple[dict | None, str]:
    """Reserve 500 ms for protection and 25 ms for diagnostics in the hook deadline."""
    if os.environ.get(OPT_IN) != "1":
        return None, "disabled"
    from .hooks._producer import _task_snapshot
    return _task_snapshot(path, deadline, capture_event)
