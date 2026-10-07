"""Rejection tombstones: text-free records of an Owner reject mark.

Tombstones act at insert time only: they refuse a NEW row with the same claim;
they never remove an existing row. Only the Owner withdraws one
(``engram review untombstone <id>``).

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
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILENAME = "tombstones.jsonl"
# Version of normalize()/claim_hashes(). Bump on any normalize change.
# v3 hashes each claim field on its own and joins them with a unit separator, and a
# decision without a question uses its title. v2 records (question + " " + choice)
# are still compared, with v2 hashes, so earlier rejections keep refusing. A record
# from any other version never matches; doctor reports it.
HASH_VERSION = 3
MATCHED_HASH_VERSIONS = (2, 3)
_FIELD_SEP = "\x1f"

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


# A leading label ("Lesson: ...", "教训：...") is framing, not the claim.
_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:lesson|lessons|learned|decision|decided|rule|note|takeaway|insight|tip|"
    r"教训|经验|决策|决定|规则|注意|心得|要点|结论)\s*[:：]\s*",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    """Casefold, drop a leading label, markdown and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = "".join(ch for ch in text if ch not in _MARKDOWN_CHARS)  # "**Lesson:**" -> "Lesson:"
    text = _LABEL_PREFIX_RE.sub("", text)
    kept = []
    for ch in text.casefold():
        if unicodedata.category(ch).startswith("P"):
            continue
        kept.append(ch)
    return _WS_RE.sub(" ", "".join(kept)).strip()


def _step_actions(row: dict) -> list[str]:
    return [
        str(step.get("action", "")) if isinstance(step, dict) else str(step)
        for step in (row.get("steps") or [])
    ]


def claim_fields(kind: str, row: dict) -> tuple[str, ...]:
    """The fields a claim is made of: a lesson summary; a decision's question (else
    its title) and choice; a playbook's title and step actions."""
    if kind == "decision":
        return (str(row.get("question") or row.get("title") or ""), str(row.get("choice") or ""))
    if kind == "playbook":
        return (str(row.get("title") or ""), *_step_actions(row))
    return (str(row.get("summary", "") or ""),)


def claim_text(kind: str, row: dict) -> str:
    """The v2 claim text (question + " " + choice; title + actions), kept for v2 records."""
    if kind == "decision":
        return f"{row.get('question', '')} {row.get('choice', '')}"
    if kind == "playbook":
        return f"{row.get('title', '')} {' '.join(_step_actions(row))}"
    return str(row.get("summary", "") or "")


def _tokens(normalized: str) -> set[str]:
    cjk = "".join(ch for ch in normalized if _is_cjk(ch))
    rest = "".join(" " if _is_cjk(ch) else ch for ch in normalized)
    tokens = set(_WORD_RE.findall(rest))
    if len(cjk) == 1:
        tokens.add(cjk)
    tokens.update(cjk[i:i + 2] for i in range(len(cjk) - 1))
    return tokens


def _hash_pair(normalized: str) -> tuple[str, str]:
    h1 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    h2 = hashlib.sha256(" ".join(sorted(_tokens(normalized))).encode("utf-8")).hexdigest()
    return h1, h2


@lru_cache(maxsize=8192)
def _hashes_v3(fields: tuple[str, ...]) -> tuple[str, str]:
    # Each field is normalized on its own: the separator would otherwise be
    # collapsed as whitespace, and "a b"+"c" would equal "a"+"b c".
    return _hash_pair(_FIELD_SEP.join(normalize(field) for field in fields))


@lru_cache(maxsize=8192)
def _hashes_v2(text: str) -> tuple[str, str]:
    return _hash_pair(normalize(text))


def claim_hashes(kind: str, row: dict) -> tuple[str, str]:
    """(h1, h2) of the claim under the current HASH_VERSION (cached by content)."""
    return _hashes_v3(claim_fields(kind, row))


def claim_hashes_for_version(kind: str, row: dict, version: Any) -> tuple[str, str] | None:
    """(h1, h2) of the claim under ``version``; None when it cannot be compared.

    v2 hashed a decision as question + choice, so every question-less decision
    with the same choice shared one v2 hash. Such a decision is not compared with
    v2 records at all; an identical re-proposal of the rejected row is still
    refused by the retired-twin check (``duplicate_retired``).
    """
    if version == HASH_VERSION:
        return claim_hashes(kind, row)
    if version == 2:
        if kind == "decision" and not str(row.get("question") or "").strip():
            return None
        return _hashes_v2(claim_text(kind, row))
    return None


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
    """The tombstone refusing this row, if any: same h1 (of the record's version) and scope."""
    scope = scope_of(row)
    hashes: dict[Any, tuple[str, str] | None] = {}
    for record in load(root):
        version = record.get("hv")
        if version not in MATCHED_HASH_VERSIONS or record.get("scope", "global") != scope:
            continue
        if version not in hashes:
            hashes[version] = claim_hashes_for_version(kind, row, version)
        pair = hashes[version]
        if pair is not None and record.get("h1") == pair[0]:
            return record
    return None


def stale_version_ids(root) -> list[str]:
    """Tombstones written by another hash version: they match nothing until migrated."""
    return [str(r.get("id")) for r in load(root) if r.get("hv") not in MATCHED_HASH_VERSIONS]


def near(root, kind: str, row: dict) -> dict | None:
    """A tombstone whose token set matches (h2) -- a flag for the review export only."""
    hashes: dict[Any, tuple[str, str] | None] = {}
    for record in load(root):
        version = record.get("hv")
        if version not in MATCHED_HASH_VERSIONS:
            continue
        if version not in hashes:
            hashes[version] = claim_hashes_for_version(kind, row, version)
        pair = hashes[version]
        if pair is not None and record.get("h2") == pair[1]:
            return record
    return None


def by_id(root, item_id: str) -> dict | None:
    for record in load(root):
        if record.get("id") == item_id:
            return record
    return None


def _locked(root):
    from .storage import hold_directory_lock

    return hold_directory_lock(_path(root).parent, timeout=30)


def append(root, kind: str, row: dict, *, via: str, prior_rejection_id: str = "") -> dict | None:
    """Append one tombstone for ``row``; idempotent per id; under the knowledge dir lock."""
    item_id = str(row.get("id") or "")
    if not item_id:
        return None
    with _locked(root):
        return _append_locked(root, kind, row, item_id, via=via, prior_rejection_id=prior_rejection_id)


def _append_locked(root, kind: str, row: dict, item_id: str, *, via: str, prior_rejection_id: str) -> dict | None:
    if by_id(root, item_id) is not None:
        return None
    h1, h2 = claim_hashes(kind, row)
    record: dict[str, Any] = {
        "id": item_id,
        "kind": kind,
        "scope": scope_of(row),
        "h1": h1,
        "h2": h2,
        "hv": HASH_VERSION,
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


def remove(root, item_id: str) -> bool:
    """Drop one tombstone (the Owner's untombstone verb); atomic, under the dir lock."""
    path = _path(root)
    if not path.is_file():
        return False
    with _locked(root):
        return _remove_locked(path, item_id)


def _remove_locked(path: Path, item_id: str) -> bool:
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = []
    removed = False
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            kept.append(line)
            continue
        if isinstance(record, dict) and record.get("id") == item_id:
            removed = True
            continue
        kept.append(line)
    if removed:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(k + "\n" for k in kept), encoding="utf-8")
        from .atomic_replace import replace_with_retry

        replace_with_retry(tmp, path)
    return removed
