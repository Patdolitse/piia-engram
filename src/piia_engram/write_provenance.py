"""Where a new knowledge row came from: write origin and the MCP client's self-report.

Every new lesson, decision and playbook row is stamped once, at its insert
point, with ``provenance.origin``:

* ``mcp``    -- written through the MCP server. The row also carries the
  client's ``clientInfo`` from the MCP handshake: ``client_name`` and
  ``client_version`` as sent (control characters removed, length capped) and
  ``client``, a closed normalized label (``claude_code``, ``cursor``, ...,
  ``other``, ``unknown``).
* ``cli``    -- written by the local ``engram`` command line.
* ``import`` -- written by an import (``engram import-memories``, a backup import).
* ``local``  -- any other in-process caller (a script using the library).

The client fields are the client's own claim. Any MCP client can send any name,
so they are a label for the reviewer, never a trust or authorization signal:
tier, risk, the approval gate and recall eligibility do not read them. A caller
cannot set them either: the stamp overwrites whatever a payload carried, and
updates may not change ``provenance`` or ``source_tool`` afterwards.

Reserved: ``provenance.observed_at`` and ``provenance.effective_from`` are kept
free for a later "when was this observed / since when does it hold" contract.
Nothing reads them yet, and a caller's values are dropped on insert (internal
paths that pass ``allow_reserved`` keep them).
"""

from __future__ import annotations

import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

ORIGIN_MCP = "mcp"
ORIGIN_CLI = "cli"
ORIGIN_IMPORT = "import"
ORIGIN_LOCAL = "local"
ORIGINS = frozenset({ORIGIN_MCP, ORIGIN_CLI, ORIGIN_IMPORT, ORIGIN_LOCAL})

# Stamped by the system; any value a caller sends for these is replaced.
CLIENT_FIELDS: tuple[str, ...] = ("origin", "client_name", "client_version", "client")
# Reserved for a later temporal contract; documented, never written.
RESERVED_PROVENANCE_FIELDS: tuple[str, ...] = ("observed_at", "effective_from")
# Fields an update may not change once a row exists.
IMMUTABLE_UPDATE_FIELDS = frozenset({"provenance", "source_tool"})

MAX_CLIENT_TEXT = 128
SELF_REPORTED_NOTE = "客户端自报，不作为可信或授权依据 / self-reported by the client, not verified"

_CURRENT: ContextVar[dict[str, str] | None] = ContextVar("engram_write_origin", default=None)


def clean_client_text(value: Any, limit: int = MAX_CLIENT_TEXT) -> str:
    """The client's string, safe to store and print: no control or format
    characters (newlines, escapes, bidi overrides), whitespace collapsed, capped."""
    text = "" if value is None else str(value)
    kept = [" " if unicodedata.category(ch).startswith("C") else ch for ch in text]
    text = " ".join("".join(kept).split())
    if len(text) > limit:
        text = text[:limit]
    return text


def client_label(name: str) -> str:
    """Closed label for a client name, shared with the usage ping's normalization.

    A client calling itself ``cli`` is labeled ``other``: over MCP it is not the
    local command line, and the label must not suggest it is.
    """
    try:
        from .usage_ping import normalize_client
    except ImportError:  # pragma: no cover - module ships with the package
        return "other" if str(name or "").strip() else "unknown"
    label = normalize_client(name)
    return "other" if label == "cli" else label


def _origin_record(origin: str, client_name: str = "", client_version: str = "") -> dict[str, str]:
    if origin not in ORIGINS:
        origin = ORIGIN_LOCAL
    record = {"origin": origin}
    if origin == ORIGIN_MCP:
        name = clean_client_text(client_name)
        record["client_name"] = name
        record["client_version"] = clean_client_text(client_version)
        record["client"] = client_label(name)
    return record


@contextmanager
def origin_scope(origin: str, *, client_name: str = "", client_version: str = "") -> Iterator[None]:
    """Rows inserted inside this block are stamped with ``origin`` (and the client)."""
    token = _CURRENT.set(_origin_record(origin, client_name, client_version))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current() -> dict[str, str]:
    record = _CURRENT.get()
    return dict(record) if record else {"origin": ORIGIN_LOCAL}


def stamp(entry: dict, *, allow_reserved: bool = False) -> dict:
    """Stamp a NEW row in place (call once, at the insert point).

    Replaces any caller-supplied origin/client fields, drops the reserved fields
    unless ``allow_reserved`` (internal paths), and fills a missing
    ``source_tool`` with the normalized client label (MCP only).
    """
    if not isinstance(entry, dict):
        return entry
    record = current()
    provenance = entry.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, dict) else {}
    for key in CLIENT_FIELDS:
        provenance.pop(key, None)
    if not allow_reserved:
        for key in RESERVED_PROVENANCE_FIELDS:
            provenance.pop(key, None)
            entry.pop(f"provenance.{key}", None)
    provenance["origin"] = record["origin"]
    if record["origin"] == ORIGIN_MCP:
        if record.get("client_name"):
            provenance["client_name"] = record["client_name"]
        if record.get("client_version"):
            provenance["client_version"] = record["client_version"]
        provenance["client"] = record.get("client") or "unknown"
        label = provenance["client"]
        if label != "unknown" and not str(entry.get("source_tool") or "").strip():
            entry["source_tool"] = label
    entry["provenance"] = provenance
    return entry


def stamp_imported(entry: dict) -> dict:
    """An imported row keeps an origin it already carries (a restored backup);
    otherwise it is marked ``import``. A row whose origin is not ``mcp`` keeps no
    client fields. An ``mcp`` row keeps its ``client_name`` / ``client_version``
    (cleaned and capped like a fresh stamp) and its ``client`` label is derived
    again from the name, so a row without ``client_name`` gets
    ``client="unknown"``, as an MCP write without client info does."""
    if not isinstance(entry, dict):
        return entry
    provenance = entry.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, dict) else {}
    if provenance.get("origin") not in ORIGINS:
        for key in CLIENT_FIELDS:
            provenance.pop(key, None)
        provenance["origin"] = ORIGIN_IMPORT
    if provenance["origin"] != ORIGIN_MCP:
        # Only MCP writes name a client.
        for key in ("client_name", "client_version", "client"):
            provenance.pop(key, None)
    else:
        for key in ("client_name", "client_version"):
            if key in provenance:
                value = clean_client_text(provenance[key])
                if value:
                    provenance[key] = value
                else:
                    provenance.pop(key)
        # The label is derived again, so it stays within the fixed label set.
        provenance["client"] = client_label(provenance.get("client_name", ""))
    entry["provenance"] = provenance
    return entry


def update_refusal(item_id: str, updates: Any) -> dict | None:
    """The error for an update that tries to change provenance or source_tool."""
    if not isinstance(updates, dict):
        return None
    blocked = sorted(
        key for key in updates
        if key in IMMUTABLE_UPDATE_FIELDS or str(key).startswith("provenance.")
    )
    if not blocked:
        return None
    return {
        "error": "provenance_immutable",
        "item_id": item_id,
        "fields": blocked,
        "message": "provenance and source_tool are recorded when an entry is written and cannot be changed",
    }


def client_summary(row: dict) -> dict[str, str]:
    """Origin and client of a stored row, cleaned for display (empty when absent)."""
    provenance = row.get("provenance") if isinstance(row, dict) else None
    if not isinstance(provenance, dict):
        return {}
    out: dict[str, str] = {}
    origin = clean_client_text(provenance.get("origin"), 16)
    if origin:
        out["origin"] = origin
    for key in ("client_name", "client_version", "client"):
        value = clean_client_text(provenance.get(key))
        if value:
            out[key] = value
    return out


def client_card_line(row: dict) -> str:
    """One review-card line naming where a row came from."""
    info = client_summary(row)
    if not info:
        return "- origin: unknown"
    origin = info.get("origin", "unknown")
    if origin != ORIGIN_MCP:
        return f"- origin: {origin}"
    name = " ".join(part for part in (info.get("client_name"), info.get("client_version")) if part)
    label = info.get("client", "unknown")
    return f"- client: {name or '(not sent)'} [{label}] ({SELF_REPORTED_NOTE})"
