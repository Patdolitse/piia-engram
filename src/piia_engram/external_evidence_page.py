"""Local draft renderer for external evidence pages.

This helper renders Markdown only. It never writes, publishes, pushes, tags, or
opens a release. Public use requires an explicit owner confirmation outside this
module.
"""

from __future__ import annotations

from typing import Any

_ALLOWED_STATUSES = {"verified", "pending", "failed", "cached", "unknown"}


def _one_line(value: Any, *, limit: int = 200) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def _status(value: Any) -> str:
    text = _one_line(value, limit=40).lower()
    return text if text in _ALLOWED_STATUSES else "unknown"


def render_external_evidence_draft(
    evidence: list[dict[str, Any]] | None,
    *,
    title: str = "External Evidence",
) -> str:
    """Render a local-only Markdown evidence page draft."""
    lines = [
        f"# {_one_line(title, limit=120)}",
        "",
        "> LOCAL DRAFT: requires owner confirmation before publishing.",
        "",
        "| Evidence | Status | Checked At | URL |",
        "| --- | --- | --- | --- |",
    ]
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        label = _one_line(item.get("label"), limit=80) or "Evidence"
        status = _status(item.get("status"))
        checked_at = _one_line(item.get("checked_at"), limit=80)
        url = _one_line(item.get("url"), limit=300)
        lines.append(f"| {label} | status={status} | {checked_at} | {url} |")
    if len(lines) == 5:
        lines.append("| No evidence supplied | status=unknown |  |  |")
    lines.append("")
    lines.append("Publication guard: do not push, tag, release, or publish this draft without owner confirmation.")
    return "\n".join(lines)
