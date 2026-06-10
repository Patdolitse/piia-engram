"""Shared incremental-read helper for watcher adapters.

Transcripts are append-only JSONL files. Incremental capture reads only the
bytes added since the last successful save (``start_offset``), never re-sends
already-captured turns, and never consumes a trailing half-written line — the
writer may be mid-append, so the segment ends at the last complete newline
and the returned ``end_offset`` lets the next scan resume exactly there.
"""

from __future__ import annotations

from pathlib import Path

#: Cap on how much of a large delta is read (tail wins — conclusions beat
#: openings; same rationale as the Cursor hook transcript reader).
MAX_SEGMENT_BYTES = 512_000


def read_segment(
    path: Path, start_offset: int = 0, max_bytes: int = MAX_SEGMENT_BYTES
) -> tuple[str, int]:
    """Read complete lines from ``start_offset`` to EOF.

    Returns ``(text, end_offset)`` where ``end_offset`` is the byte position
    just past the last complete line consumed. Rules:

    - an invalid ``start_offset`` (negative, or past EOF after a rewrite)
      resets to ``0`` — the file changed identity, re-read it;
    - oversized deltas keep only the tail ``max_bytes`` (the first, likely
      mid-JSON line of the tail is dropped);
    - a trailing line without ``\\n`` is *not* consumed: ``end_offset`` stops
      before it so the next scan picks it up once the writer finishes it.

    ``OSError`` propagates — the core loop logs it and leaves the watermark
    untouched, so transient read failures retry on the next scan.
    """
    size = path.stat().st_size
    if start_offset < 0 or start_offset > size:
        start_offset = 0
    if size == start_offset:
        return "", start_offset

    seek = start_offset
    dropped_head = size - seek > max_bytes
    if dropped_head:
        seek = size - max_bytes
    with open(path, "rb") as handle:
        handle.seek(seek)
        raw = handle.read()

    if dropped_head:
        cut = raw.find(b"\n")
        if cut == -1:
            # One giant unterminated blob: nothing parseable, skip past it.
            return "", size
        raw = raw[cut + 1 :]
        seek += cut + 1

    if not raw.endswith(b"\n"):
        cut = raw.rfind(b"\n")
        if cut == -1:
            return "", start_offset  # no complete new line yet
        raw = raw[: cut + 1]
    return raw.decode("utf-8", errors="replace"), seek + len(raw)
