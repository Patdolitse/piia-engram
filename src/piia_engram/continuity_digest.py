"""Structured session continuity digest (``session_digest.v1``).

Turn a free-form session summary into a deterministic, redacted, structured
digest — a compact snapshot of one working session: the goal, what was
completed, what was verified, which decisions/lessons emerged (as review
*candidates*, never auto-verified), known risks, and next actions.

Pure functions only: no LLM call, no file I/O, no global MCP state. The
project resume pack (Task 4) assembles from these digests.
"""

from __future__ import annotations

import re
from typing import Any

from .export_redaction import redact_export_text
from .session_filters import (
    has_explicit_decision_signal,
    has_lesson_outcome_signal,
    is_process_or_delegation_sentence,
    strip_session_noise_blocks,
)

SCHEMA = "session_digest.v1"
_PLACEHOLDER = "[REDACTED]"

# Digest surfaces must drop ANY absolute drive path (E:\…, C:\Users\…), not
# only home dirs — this matches the all-drive leakage guard. The audited
# export-redaction scrubber covers credential/PII shapes; these patterns add
# the all-drive path and bare-key shapes it intentionally leaves to home-only.
_ABS_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s,;，。！？\"'<>|]+")
_ABS_POSIX_PATH_RE = re.compile(
    r"(?<![\w.])/(?:home|Users|var|etc|opt|tmp|root)/[^\s,;，。！？\"'<>|]+"
)
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9._\-]{16,}")


def _sanitize_str(text: str) -> str:
    if not text:
        return text
    text = _ABS_WIN_PATH_RE.sub(_PLACEHOLDER, text)
    text = _ABS_POSIX_PATH_RE.sub(_PLACEHOLDER, text)
    text = _AWS_KEY_RE.sub(_PLACEHOLDER, text)
    text = _SK_KEY_RE.sub(_PLACEHOLDER, text)
    text = redact_export_text(text, placeholder=_PLACEHOLDER)
    return text


def sanitize_digest_value(value: Any) -> Any:
    """Recursively scrub secrets / PII / absolute paths from a digest value.

    Reuses the audited export-redaction scrubber for credential and PII shapes,
    then adds the all-drive absolute-path and bare AWS/sk- key redaction the
    digest surface specifically requires. Ordinary relative paths survive.
    """
    if isinstance(value, str):
        return _sanitize_str(value)
    if isinstance(value, list):
        return [sanitize_digest_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(sanitize_digest_value(v) for v in value)
    if isinstance(value, dict):
        return {k: sanitize_digest_value(v) for k, v in value.items()}
    return value


# --- extraction helpers (deterministic, heuristic, no LLM) ------------------

_GOAL_RE = re.compile(r"(?:^|\n)\s*(?:goal|目标)\s*[:：]\s*(.+)", re.IGNORECASE)
_COMPLETED_RE = re.compile(
    r"(?:^|\n)\s*(?:completed|done|已完成|完成)\s*[:：]\s*(.+)", re.IGNORECASE
)
_NEXT_RE = re.compile(
    r"(?:^|\n)\s*(?:next\s+actions?|next|下一步|后续)\s*[:：]\s*(.+)", re.IGNORECASE
)
_RISK_RE = re.compile(r"(?:^|\n)\s*(?:risks?|风险)\s*[:：]\s*(.+)", re.IGNORECASE)
_CHANGED_RE = re.compile(
    r"(?:^|\n)\s*(?:changed\s+files?|changed|modified|files?)\s*[:：]\s*(.+)",
    re.IGNORECASE,
)
_VERIF_RE = re.compile(
    r"(?:^|\n)\s*(?:tests?|verification|验证)\s*[:：]\s*(.+)", re.IGNORECASE
)
_CMD_RE = re.compile(r"(`[^`]+`|\bpytest\b[\w\s/._\-]*)")

_DECISION_TRIGGER = re.compile(
    r"\b(?:decided to|decided|chose|switched to|adopted)\b|采用|选择|决定|改用|改为",
    re.IGNORECASE,
)
_LESSON_TRIGGER = re.compile(
    r"\b(?:lesson|always|never|avoid|make sure|remember)\b"
    r"|经验|教训|记住|避免|务必|切记",
    re.IGNORECASE,
)

_MAX_ITEMS = 12
_MAX_LEN = 300


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _split_items(raw: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[;；\n]+", raw):
        cleaned = part.strip().lstrip("-*0123456789.、)（） ").strip()
        if cleaned:
            out.append(cleaned[:_MAX_LEN])
    return out[:_MAX_ITEMS]


def _labeled_list(pattern: re.Pattern[str], text: str) -> list[str]:
    items: list[str] = []
    for match in pattern.finditer(text):
        items.extend(_split_items(match.group(1)))
    return items[:_MAX_ITEMS]


def _verif_status(text: str) -> str:
    lowered = text.lower()
    if "pass" in lowered or "通过" in text:
        return "passed"
    if "fail" in lowered or "失败" in text:
        return "failed"
    if "skip" in lowered or "跳过" in text:
        return "skipped"
    return "not_run"


def _extract_verification(text: str) -> list[dict]:
    out: list[dict] = []
    for match in _VERIF_RE.finditer(text):
        line = match.group(1).strip()
        cmd_match = _CMD_RE.search(line)
        command = cmd_match.group(1).strip("` ") if cmd_match else ""
        out.append({
            "command": command[:200],
            "status": _verif_status(line),
            "summary": line[:_MAX_LEN],
        })
    return out[:_MAX_ITEMS]


def _kind_of(path: str) -> str:
    lowered = path.lower()
    if "test" in lowered:
        return "test"
    if lowered.endswith((".md", ".rst", ".txt")):
        return "doc"
    if lowered.endswith(
        (".py", ".js", ".ts", ".tsx", ".rs", ".go", ".java", ".c", ".cpp")
    ):
        return "code"
    if lowered.endswith(
        (".json", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".conf")
    ):
        return "config"
    return "unknown"


def _extract_changed_files(text: str) -> list[dict]:
    out: list[dict] = []
    for match in _CHANGED_RE.finditer(text):
        for item in _split_items(match.group(1)):
            out.append({
                "path_hint": item[:200],
                "kind": _kind_of(item),
                "summary": "",
            })
    return out[:_MAX_ITEMS]


def _extract_decisions_lessons(text: str) -> tuple[list[dict], list[dict]]:
    decisions: list[dict] = []
    lessons: list[dict] = []
    for raw in re.split(r"[。！？.!?\n]+", strip_session_noise_blocks(text)):
        sentence = raw.strip()
        if len(sentence) < 6:
            continue
        if is_process_or_delegation_sentence(sentence):
            continue
        if _DECISION_TRIGGER.search(sentence) and has_explicit_decision_signal(sentence):
            decisions.append({
                "summary": sentence[:_MAX_LEN],
                "reason": "",
                "status": "candidate",
            })
        elif _LESSON_TRIGGER.search(sentence) and has_lesson_outcome_signal(sentence):
            lessons.append({
                "summary": sentence[:_MAX_LEN],
                "evidence": "",
                "status": "candidate",
            })
    return decisions[:_MAX_ITEMS], lessons[:_MAX_ITEMS]


def build_session_digest(
    summary: str,
    *,
    tool: str = "",
    project_id: str = "",
    session_ref: str = "",
) -> dict:
    """Build a ``session_digest.v1`` dict from a free-form session summary.

    Deterministic and heuristic — never calls a model. Empty or unstructured
    input still returns a valid digest with stable empty containers. Every
    string value is redacted before return.
    """
    text = strip_session_noise_blocks(summary or "")
    decisions, lessons = _extract_decisions_lessons(text)
    digest = {
        "schema": SCHEMA,
        "goal": _first(_GOAL_RE, text),
        "completed": _labeled_list(_COMPLETED_RE, text),
        "changed_files": _extract_changed_files(text),
        "verification": _extract_verification(text),
        "decisions": decisions,
        "lessons": lessons,
        "risks": _labeled_list(_RISK_RE, text),
        "next_actions": _labeled_list(_NEXT_RE, text),
        "source": {
            "tool": tool or "unknown",
            "project_id": project_id or "",
            "session_ref": session_ref or "",
        },
    }
    # The internal project_id is a verified path-derived hash, not user
    # content — redacting it (e.g. a 12-hex hash that happens to match the
    # CN mobile phone pattern) breaks the exact-scope filter downstream,
    # which compares the digest's source id against the project's canonical
    # id. Preserve the internal identifier; the digest BODY (goal, lessons,
    # etc.) still passes through the full scrubber below.
    internal_project_id = digest["source"]["project_id"]
    result = sanitize_digest_value(digest)
    result["source"]["project_id"] = internal_project_id
    return result


def render_session_digest_markdown(digest: dict, *, max_chars: int = 2000) -> str:
    """Render a digest to compact markdown, bounded by ``max_chars``."""
    if not isinstance(digest, dict):
        return ""
    digest = sanitize_digest_value(digest)
    lines = ["## Session digest"]
    goal = digest.get("goal") or ""
    if goal:
        lines.append(f"**Goal:** {goal}")

    def _section(title: str, items: list, fmt) -> None:
        if items:
            lines.append(f"**{title}:**")
            for item in items:
                lines.append(f"- {fmt(item)}")

    _section("Completed", digest.get("completed") or [], str)
    _section(
        "Verification",
        digest.get("verification") or [],
        lambda v: f"{v.get('command', '')} — {v.get('status', 'not_run')}".strip(" —"),
    )
    _section(
        "Changed files",
        digest.get("changed_files") or [],
        lambda f: f"{f.get('path_hint', '')} ({f.get('kind', 'unknown')})",
    )
    _section(
        "Decisions (candidate)",
        digest.get("decisions") or [],
        lambda d: str(d.get("summary", "")),
    )
    _section(
        "Lessons (candidate)",
        digest.get("lessons") or [],
        lambda x: str(x.get("summary", "")),
    )
    _section("Risks", digest.get("risks") or [], str)
    _section("Next actions", digest.get("next_actions") or [], str)

    md = "\n".join(lines)
    if max_chars and len(md) > max_chars:
        md = md[:max_chars].rstrip()
    return md
