"""AGENTS.md / CLAUDE.md compatible export — metadata-only, verified-only.

Produces a Markdown block suitable for pasting into an ``AGENTS.md`` or
``CLAUDE.md`` so any agent that reads those files inherits the user's *verified,
non-sensitive, generalizable* knowledge — without exposing staging, sensitive,
or private content, and without leaking internal bookkeeping fields.

Design constraints (see docs/specs — Task 6):

- **Verified-only**: only ``tier == "verified"`` and ``status == "active"``.
- **Sensitivity-screened**: items above the caller's ``max_sensitivity`` ceiling
  (default ``work``) are excluded, using the real ``sensitivity.classify_item``.
- **Metadata/summary-only**: emits the human-facing summary/choice + domain, not
  internal fields (ids, access_count, risk flags, provenance internals).
- **Project/global separation**: ``scope="global"`` emits only non-project
  (generalizable) knowledge; ``scope="project"`` emits knowledge for one project.

This is a pure function over already-loaded entries. It does not read or write
the store, and it does not register an MCP tool — wiring is a separate, reviewed
step so no new agent-facing surface ships unreviewed.
"""

from __future__ import annotations

from typing import Any, Iterable

from .export_redaction import redact_export_text
from .sensitivity import SENSITIVITY_ORDER, classify_item

_DEFAULT_MAX_SENSITIVITY = "work"
_EXPORT_ALLOWED_LEVELS = frozenset({"public", "work", "private"})


def _validate_max_sensitivity(level: str) -> None:
    if level not in _EXPORT_ALLOWED_LEVELS:
        raise ValueError(
            f"Invalid max_sensitivity={level!r} for export. "
            f"Allowed: {sorted(_EXPORT_ALLOWED_LEVELS)}. "
            "'secret' is not allowed — secret items must not be exported."
        )


def _rank(level: str) -> int:
    return SENSITIVITY_ORDER.get(level, SENSITIVITY_ORDER["work"])


def _entry_project(entry: dict[str, Any]) -> str:
    """Best-effort project label for an entry (decisions carry ``project``)."""
    proj = entry.get("project")
    if isinstance(proj, str) and proj.strip():
        return proj.strip()
    prov = entry.get("provenance")
    if isinstance(prov, dict):
        p = prov.get("project")
        if isinstance(p, str) and p.strip():
            return p.strip()
    return ""


def _is_verified_active(entry: dict[str, Any]) -> bool:
    # Fail closed: an entry must EXPLICITLY be verified to be exported. A missing
    # tier is treated as not-exportable rather than defaulting to verified, so a
    # future writer that forgets to stamp the tier cannot leak into the export.
    return entry.get("tier") == "verified" and entry.get("status", "active") == "active"


def _within_sensitivity(entry: dict[str, Any], max_rank: int) -> bool:
    return _rank(classify_item(entry)) <= max_rank


def _scope_match(entry: dict[str, Any], scope: str, project: str) -> bool:
    ep = _entry_project(entry)
    if scope == "global":
        return ep == ""          # only generalizable, non-project knowledge
    if scope == "project":
        return ep == (project or "").strip() and ep != ""
    return True


def select_exportable(
    entries: Iterable[dict[str, Any]],
    *,
    scope: str = "global",
    project: str = "",
    max_sensitivity: str = _DEFAULT_MAX_SENSITIVITY,
) -> list[dict[str, Any]]:
    """Return the subset of entries eligible for AGENTS.md/CLAUDE.md export."""
    _validate_max_sensitivity(max_sensitivity)
    max_rank = _rank(max_sensitivity)
    selected: list[dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if not _is_verified_active(entry):
            continue
        if not _within_sensitivity(entry, max_rank):
            continue
        if not _scope_match(entry, scope, project):
            continue
        selected.append(entry)
    return selected


def _clean_one_line(text: Any, limit: int = 280) -> str:
    if not isinstance(text, str):
        return ""
    flattened = " ".join(text.split())
    if len(flattened) > limit:
        flattened = flattened[: limit - 1].rstrip() + "…"
    return flattened


def _render_lessons(lessons: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in lessons:
        summary = _clean_one_line(item.get("summary"))
        if not summary:
            continue
        domain = _clean_one_line(redact_export_text(item.get("domain")), 60)
        suffix = f" _(domain: {domain})_" if domain else ""
        lines.append(f"- {summary}{suffix}")
    return lines


def _render_decisions(decisions: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in decisions:
        choice = _clean_one_line(item.get("choice"))
        question = _clean_one_line(item.get("question"), 160)
        if not choice and not question:
            continue
        if question and choice:
            lines.append(f"- **{question}** → {choice}")
        else:
            lines.append(f"- {choice or question}")
    return lines


def build_agents_md_export(
    *,
    lessons: Iterable[dict[str, Any]] | None = None,
    decisions: Iterable[dict[str, Any]] | None = None,
    scope: str = "global",
    project: str = "",
    max_sensitivity: str = _DEFAULT_MAX_SENSITIVITY,
    heading: str = "Engram — durable knowledge",
) -> str:
    """Build an AGENTS.md/CLAUDE.md-compatible Markdown block.

    Only verified, active, non-sensitive, scope-matching knowledge is included.
    The output is summary/metadata only — safe to commit into an AGENTS.md or
    CLAUDE.md. Returns a short "nothing to export" block if nothing qualifies.
    """
    sel_lessons = select_exportable(
        lessons or [], scope=scope, project=project, max_sensitivity=max_sensitivity
    )
    sel_decisions = select_exportable(
        decisions or [], scope=scope, project=project, max_sensitivity=max_sensitivity
    )

    scope_label = "global" if scope == "global" else f"project: {project}"
    out: list[str] = [f"## {heading} ({scope_label})", ""]
    out.append(
        "<!-- Generated by Engram. Verified, non-sensitive knowledge only. "
        "Staging and sensitive content are excluded by design. -->"
    )
    out.append("")

    lesson_lines = _render_lessons(sel_lessons)
    decision_lines = _render_decisions(sel_decisions)

    if not lesson_lines and not decision_lines:
        out.append("_No verified, non-sensitive knowledge to export for this scope._")
        return "\n".join(out) + "\n"

    if decision_lines:
        out.append("### Key decisions")
        out.extend(decision_lines)
        out.append("")
    if lesson_lines:
        out.append("### Lessons")
        out.extend(lesson_lines)
        out.append("")

    return "\n".join(out).rstrip() + "\n"
