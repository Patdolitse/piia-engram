"""Rejection tombstones: text-free, permanent records of an Owner reject mark.

A tombstone is written only by an explicit reject mark -- review_staging batch
reject, ``engram review apply`` reject, or the backfill -- never by capacity
moves, merges or a plain archive. It stores hashes, not text:

* ``h1`` -- sha256 of the normalized claim; an identical h1 in the same scope is
  always refused (``rejected_before``), on every insert route and in every mode.
* ``h2`` -- sha256 of the claim's token set (words, and CJK character bigrams);
  it only flags near-rejected proposals in the review export, never refuses.

Tombstones live in ``knowledge/tombstones.jsonl`` (append-only), outside every
capacity pool.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILENAME = "tombstones.jsonl"

_MARKDOWN_CHARS = set("`*_#>|~^")
_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+")


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
        or 0x20000 <= code <= 0x2FA1F
    )


def normalize(text: str) -> str:
    """Casefold, drop markdown and punctuation, collapse whitespace."""
    kept = []
    for ch in unicodedata.normalize("NFKC", str(text or "")).casefold():
        if ch in _MARKDOWN_CHARS or unicodedata.category(ch).startswith("P"):
            continue
        kept.append(ch)
    return _WS_RE.sub(" ", "".join(kept)).strip()


def claim_text(kind: str, row: dict) -> str:
    """The text a reject is about: a lesson summary, a decision's question + choice,
    or a playbook's purpose + step actions."""
    if kind == "decision":
        return f"{row.get('question', '')} {row.get('choice', '')}"
    if kind == "playbook":
        steps = row.get("steps") or []
        actions = " ".join(
            str(step.get("action", "")) if isinstance(step, dict) else str(step) for step in steps
        )
        return f"{row.get('title', '')} {actions}"
    return str(row.get("summary", "") or "")


def _tokens(normalized: str) -> set[str]:
    cjk = "".join(ch for ch in normalized if _is_cjk(ch))
    rest = "".join(" " if _is_cjk(ch) else ch for ch in normalized)
    tokens = set(_WORD_RE.findall(rest))
    if len(cjk) == 1:
        tokens.add(cjk)
    tokens.update(cjk[i:i + 2] for i in range(len(cjk) - 1))
    return tokens


def claim_hashes(kind: str, row: dict) -> tuple[str, str]:
    normalized = normalize(claim_text(kind, row))
    h1 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    h2 = hashlib.sha256(" ".join(sorted(_tokens(normalized))).encode("utf-8")).hexdigest()
    return h1, h2


def scope_of(row: dict) -> str:
    return str(row.get("project_id") or "global")


def _path(root) -> Path:
    return Path(root) / "knowledge" / FILENAME


def load(root) -> list[dict]:
    path = _path(root)
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def lookup(root, kind: str, row: dict) -> dict | None:
    """The tombstone refusing this row, if any: same h1 and same scope."""
    h1, _h2 = claim_hashes(kind, row)
    scope = scope_of(row)
    for record in load(root):
        if record.get("h1") == h1 and record.get("scope", "global") == scope:
            return record
    return None


def near(root, kind: str, row: dict) -> dict | None:
    """A tombstone whose token set matches (h2) -- a flag for the review export only."""
    _h1, h2 = claim_hashes(kind, row)
    for record in load(root):
        if record.get("h2") == h2:
            return record
    return None


def by_id(root, item_id: str) -> dict | None:
    for record in load(root):
        if record.get("id") == item_id:
            return record
    return None


def append(root, kind: str, row: dict, *, via: str, prior_rejection_id: str = "") -> dict | None:
    """Append one tombstone for ``row``; idempotent per id."""
    item_id = str(row.get("id") or "")
    if not item_id or by_id(root, item_id) is not None:
        return None
    h1, h2 = claim_hashes(kind, row)
    record: dict[str, Any] = {
        "id": item_id,
        "kind": kind,
        "scope": scope_of(row),
        "h1": h1,
        "h2": h2,
        "rejected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "via": via,
    }
    if prior_rejection_id:
        record["prior_rejection_id"] = prior_rejection_id
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True) + "\n")
    return record
