"""Duplicate handling at write time and what the reviewer sees about it.

Write-time rule (lessons and decisions):

* Exactly the same claim -- the same normalized text hash in the same project
  scope, the hash rejection tombstones and the retired-twin check use (NFKC,
  case folded, punctuation and a leading label dropped, whitespace collapsed;
  a decision is its question, else its title, plus its choice) -- is refused.
* Very similar but not the same (bigram similarity >= 0.95) is no longer
  refused. The new row goes to the review queue, also outside strict mode, and
  carries ``duplicate_candidate = {"existing_id", "similarity"}``; the Owner
  decides whether it is new.
* Related (0.55 up to 0.95) is stored as before, cross-linked, with a
  ``_dedup_note``.

Only the duplicate check writes ``duplicate_candidate`` and ``_dedup_note``; a
caller's values are dropped on insert. Display code still treats stored values
as untrusted: ids must look like ids, a similarity must be a number in [0, 1].
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from typing import Any, Iterable

from . import tombstones as _tombstones

DIFF_MAX_LINES = 40
_LINE_MAX = 300
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_RELATED_NOTE_RE = re.compile(r"\brelated to ([A-Za-z0-9_-]{1,64}) \((?:sim|cos)=([0-9.]{1,6}%?)\)")
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|(?<=[。！？；])")

# Written by the duplicate check only; a caller's values are dropped on insert.
CALLER_STRIPPED_FIELDS: tuple[str, ...] = ("duplicate_candidate", "_dedup_note")
# Cleared when an entry leaves the review queue (approved or promoted).
REVIEW_ONLY_FIELDS: tuple[str, ...] = ("duplicate_candidate",)


def strip_caller_fields(entry: dict) -> None:
    for key in CALLER_STRIPPED_FIELDS:
        entry.pop(key, None)


def clear_review_fields(entry: dict) -> dict:
    for key in REVIEW_ONLY_FIELDS:
        entry.pop(key, None)
    return entry


def exact_key(kind: str, row: dict) -> str:
    """Hash of the normalized claim (tombstone h1, cached by content)."""
    return _tombstones.claim_hashes(kind, row)[0]


def safe_id(value: Any) -> str:
    """An entry id fit to print: letters, digits, ``_`` and ``-`` only; else ''."""
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else ""


def safe_similarity(value: Any) -> float | None:
    """A similarity clamped to [0, 1]; None when it is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return min(1.0, max(0.0, value))


def pending_candidate(row: Any) -> tuple[str, float | None] | None:
    """(earlier id, similarity) of a pending row's duplicate candidate, if well formed."""
    if not isinstance(row, dict) or row.get("tier") != "staging":
        return None
    record = row.get("duplicate_candidate")
    if not isinstance(record, dict):
        return None
    existing_id = safe_id(record.get("existing_id"))
    if not existing_id:
        return None
    return existing_id, safe_similarity(record.get("similarity"))


def _pct(similarity: float | None) -> str:
    return f"{similarity:.0%}" if similarity is not None else "unknown"


def candidate_record(existing_id: str, similarity: float) -> dict[str, Any]:
    return {"existing_id": str(existing_id or ""), "similarity": round(float(similarity), 2)}


def candidate_message(record: Any) -> str:
    """What the writing agent is told about a duplicate candidate ('' if malformed)."""
    if not isinstance(record, dict):
        return ""
    existing_id = safe_id(record.get("existing_id"))
    if not existing_id:
        return ""
    pct = _pct(safe_similarity(record.get("similarity")))
    return (
        f"已作为重复候选进入待审：与 {existing_id} 相似度 {pct}，需主人审核确认。"
        "若这是对旧条目的修订，请用 supersedes 指明被取代的条目（目标由你确认，不要只凭相似度）。"
        f" / Queued for review as a possible duplicate of {existing_id} "
        f"(similarity {pct}); the Owner confirms it is new. If it revises an "
        "earlier entry, write it with supersedes naming that entry (choose the target "
        "yourself; similarity alone does not pick it)."
    )


def existing_guidance(existing_id: str, kind: str) -> dict[str, Any]:
    """Guidance for a refused identical write: the content exists; how to revise it.

    Nothing here offers a new-entry bypass: identical content has none.
    """
    existing_id = safe_id(existing_id)
    if kind == "playbook":
        how_zh = '用 manage_playbook(action="update") 提交修订（严格模式下为修订提案）'
        how_en = 'use manage_playbook(action="update") (a revision proposal under strict approval)'
    else:
        how_zh = f"请写入修订后的内容并用 supersedes={existing_id} 指向它（修订提案），或在允许编辑时用 update_knowledge"
        how_en = (f"write the revised text with supersedes={existing_id} (a revision proposal), "
                  "or use update_knowledge where edits are allowed")
    return {
        "existing_id": existing_id,
        "note": f"内容已存在，指向 {existing_id}；若要修订，{how_zh}。 / "
                f"Already stored as {existing_id}. To revise it, {how_en}.",
    }


def related_from_note(note: Any) -> tuple[str, str] | None:
    """(existing id, similarity text) from a ``_dedup_note``, if it names one."""
    if not isinstance(note, str):
        return None
    match = _RELATED_NOTE_RE.search(note)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _clean(text: Any) -> str:
    text = "" if text is None else str(text)
    kept = [" " if unicodedata.category(ch).startswith("C") else ch for ch in text]
    text = " ".join("".join(kept).split())
    return text if len(text) <= _LINE_MAX else text[: _LINE_MAX - 3] + "..."


def _field_texts(kind: str, row: dict) -> list[tuple[str, str]]:
    if kind == "decision":
        fields = [("question", row.get("question") or row.get("title")), ("choice", row.get("choice")),
                  ("reasoning", row.get("reasoning"))]
    elif kind == "playbook":
        steps = row.get("steps") or []
        fields = [("title", row.get("title")), ("description", row.get("description"))]
        for i, step in enumerate(steps, 1):
            fields.append((f"step {i}", step.get("action", "") if isinstance(step, dict) else step))
    else:
        fields = [("summary", row.get("summary")), ("detail", row.get("detail"))]
    return [(name, str(value)) for name, value in fields if value not in (None, "")]


def diff_units(kind: str, row: dict) -> list[str]:
    """The entry as one line per sentence, each prefixed with its field name."""
    units: list[str] = []
    for name, text in _field_texts(kind, row):
        for part in re.split(r"[\r\n]+", text):
            for sentence in _SENTENCE_RE.split(part):
                sentence = _clean(sentence)
                if sentence:
                    units.append(f"{name}: {sentence}")
    return units


def text_diff(kind: str, old: dict, new: dict, max_lines: int = DIFF_MAX_LINES) -> list[str]:
    """Sentence-level unified diff (earlier entry -> proposal), at most ``max_lines``."""
    lines = list(difflib.unified_diff(
        diff_units(kind, old), diff_units(kind, new),
        fromfile=f"earlier {safe_id(old.get('id'))}", tofile=f"proposed {safe_id(new.get('id'))}",
        n=1, lineterm="",
    ))
    if not lines:
        return ["(no text difference)"]
    if len(lines) > max_lines:
        hidden = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... diff truncated: {hidden} more line(s)"]
    return lines


def fenced(lines: Iterable[str]) -> list[str]:
    """A markdown code block that no line inside can close early."""
    body = list(lines)
    longest = max((len(m.group(0)) for line in body for m in re.finditer(r"`+", line)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}diff", *body, fence]


def card_lines(kind: str, row: dict, lookup: dict[str, dict]) -> list[str]:
    """Review-card lines for a pending duplicate candidate or near-duplicate (else []).

    Only pending rows, only well-formed ids, and only an earlier entry this row
    is linked to (``related_ids``, which the duplicate check always sets): a
    stray value never pulls an unrelated entry into the diff.
    """
    if not isinstance(row, dict) or row.get("tier") != "staging":
        return []
    candidate = pending_candidate(row)
    if candidate is not None:
        existing_id, similarity = candidate
        head = (f"- possible duplicate of `{existing_id}` (similarity {_pct(similarity)}): "
                "confirm it is new before approving / 重复候选，批准前请确认确属新条目")
    else:
        related = related_from_note(row.get("_dedup_note"))
        if related is None:
            return []
        existing_id = related[0]
        head = f"- near-duplicate: related to `{existing_id}` ({related[1]}) / 近重复"
    linked = existing_id in (row.get("related_ids") or [])
    if candidate is None and not linked:
        return []  # a near-duplicate note without its link is not trusted
    lines = [head]
    if not linked:
        # e.g. the earlier entry was merged and the link moved: keep the
        # heading, but never diff against an entry this row is not linked to.
        lines.append("  (no longer linked to that entry; no diff)")
        return lines
    earlier = lookup.get(existing_id)
    if earlier is None:
        lines.append("  (the earlier entry is no longer active)")
        return lines
    lines.append("- difference (earlier -> proposed):")
    lines.extend("  " + line for line in fenced(text_diff(kind, earlier, row)))
    return lines
