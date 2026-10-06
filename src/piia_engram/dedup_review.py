"""Duplicate handling at write time and what the reviewer sees about it.

Write-time rule (lessons and decisions):

* Exactly the same claim -- the same normalized text hash (the normalization
  that rejection tombstones use: NFKC, case folded, punctuation and a leading
  label dropped, whitespace collapsed) in the same project scope -- is refused.
* Very similar but not the same (bigram similarity >= 0.95) is no longer
  refused. The new row goes to the review queue, also outside strict mode, and
  carries ``duplicate_candidate = {"existing_id", "similarity"}``; the Owner
  decides whether it is new.
* Related (0.55 up to 0.95) is stored as before, cross-linked, with a
  ``_dedup_note``.

For the review card this module renders the candidate (or near-duplicate) line
and a short sentence-level diff between the earlier entry and the proposal.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata
from typing import Any, Iterable

from . import tombstones as _tombstones

DIFF_MAX_LINES = 40
_LINE_MAX = 300
_RELATED_NOTE_RE = re.compile(r"\brelated to ([A-Za-z0-9_-]+) \((?:sim|cos)=([0-9.]+%?)\)")
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|(?<=[。！？；])")


def exact_key(identity: str, choice: str = "") -> str:
    """Hash of the normalized claim: a lesson summary, or a decision's title or
    question plus its choice."""
    text = f"{identity} {choice}" if choice else str(identity or "")
    return hashlib.sha256(_tombstones.normalize(text).encode("utf-8")).hexdigest()


def candidate_record(existing_id: str, similarity: float) -> dict[str, Any]:
    return {"existing_id": str(existing_id or ""), "similarity": round(float(similarity), 2)}


def candidate_message(record: dict[str, Any]) -> str:
    """What the writing agent is told about a duplicate candidate."""
    existing_id = record.get("existing_id", "")
    similarity = float(record.get("similarity") or 0.0)
    return (
        f"已作为重复候选进入待审：与 {existing_id} 相似度 {similarity:.0%}，需主人审核确认。"
        "若这是对旧条目的修订，请用 supersedes 指明被取代的条目（目标由你确认，不要只凭相似度）。"
        f" / Queued for review as a possible duplicate of {existing_id} "
        f"(similarity {similarity:.2f}); the Owner confirms it is new. If it revises an "
        "earlier entry, write it with supersedes naming that entry (choose the target "
        "yourself; similarity alone does not pick it)."
    )


def related_from_note(note: Any) -> tuple[str, str] | None:
    """(existing id, similarity text) from a ``_dedup_note``, if it names one."""
    match = _RELATED_NOTE_RE.search(str(note or ""))
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
        fromfile=f"earlier {_clean(old.get('id'))}", tofile=f"proposed {_clean(new.get('id'))}",
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
    """Review-card lines for a duplicate candidate or a near-duplicate (else [])."""
    candidate = row.get("duplicate_candidate")
    if isinstance(candidate, dict) and candidate.get("existing_id"):
        existing_id = _clean(candidate.get("existing_id"))
        similarity = float(candidate.get("similarity") or 0.0)
        head = (f"- possible duplicate of `{existing_id}` (similarity {similarity:.0%}): "
                "confirm it is new before approving / 重复候选，批准前请确认确属新条目")
    else:
        related = related_from_note(row.get("_dedup_note"))
        if related is None:
            return []
        existing_id = _clean(related[0])
        head = f"- near-duplicate: related to `{existing_id}` ({_clean(related[1])}) / 近重复"
    lines = [head]
    earlier = lookup.get(existing_id)
    if earlier is None:
        lines.append("  (the earlier entry is no longer active)")
        return lines
    lines.append("- difference (earlier -> proposed):")
    lines.extend("  " + line for line in fenced(text_diff(kind, earlier, row)))
    return lines
