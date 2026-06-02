"""Cross-tool continuity verification harness (Phase 10) — simulated, no writes.

Proves the cross-tool identity-continuity loop locally, end to end, without
touching any live tool install or the real store:

    resume/read  ->  tool export/use  ->  governed writeback  ->  next read

Every stage is pure and composes already-shipped building blocks:
- **resume/read & export** reuse ``agents_md_export.build_agents_md_export``
  (verified-only, sensitivity-screened, summary/metadata-only by construction).
- **governed writeback** uses :func:`prepare_writeback_candidates`, the pure
  rollout-phase-1 helper for the Cursor stop-hook design
  (``docs/specs/cursor-stop-hook-governed-writeback-design.md`` §6): it screens
  sensitivity, computes content hashes, dedups, and tags everything as
  **staging/pending** — it NEVER writes to disk and NEVER produces verified-tier
  items (invariants I2/I3/I6).
- **next read** re-runs the export over the (unchanged) verified set, so the
  harness can assert that freshly-staged writeback items do **not** leak into the
  next session's exported material without an explicit promotion.

This is a *dry-run contract harness*: it returns a structured trace plus leakage
checks. No live Cursor/Claude/Codex install is mutated; no plugin is published.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .agents_md_export import build_agents_md_export, select_exportable
from .sensitivity import classify_item

_SENS_RANK = {"public": 0, "work": 1, "private": 2, "secret": 3}


def content_hash(summary: str, detail: str = "") -> str:
    """Stable content hash for dedup + tamper-evidence (design §3.3)."""
    norm = (summary or "").strip().lower() + "\n" + (detail or "").strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _candidate_text(entry: dict[str, Any]) -> tuple[str, str]:
    summary = str(entry.get("summary") or entry.get("choice") or entry.get("question") or "")
    detail = str(entry.get("detail") or entry.get("reasoning") or "")
    return summary, detail


def prepare_writeback_candidates(
    items: list[dict[str, Any]],
    *,
    existing_hashes: set[str] | None = None,
    allow_private: bool = False,
    source_agent: str = "cursor",
    session_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Pure governed-writeback preparation (no disk writes, ever).

    Screens each candidate, drops ``secret`` (and ``private`` unless
    ``allow_private``), dedups by content hash, and tags survivors as
    **staging / pending** with provenance. Returns::

        {"staged": [item, ...],           # tier=staging, approval_status=pending
         "dropped_sensitive": int,
         "skipped_duplicate": int,
         "audit_record": { ... }}         # metadata-only (hashes, counts)

    Satisfies design invariants I2 (staging-default), I3 (content hash),
    I6 (no auto-promote — nothing is ever tagged verified here).
    """
    seen = set(existing_hashes or set())
    staged: list[dict[str, Any]] = []
    dropped_sensitive = 0
    skipped_duplicate = 0
    hashes: list[str] = []

    for item in items or []:
        if not isinstance(item, dict):
            continue
        level = classify_item(item)
        if level == "secret":
            dropped_sensitive += 1
            continue
        if level == "private" and not allow_private:
            dropped_sensitive += 1
            continue

        summary, detail = _candidate_text(item)
        h = content_hash(summary, detail)
        if h in seen:
            skipped_duplicate += 1
            continue
        seen.add(h)
        hashes.append(h)

        # Build the staged item — staging tier, pending, with provenance. Never
        # verified. We copy only known fields so nothing unexpected rides along.
        staged_item: dict[str, Any] = {
            "summary": summary,
            "tier": "staging",
            "approval_status": "pending",
            "status": "active",
            "sensitivity": level,
            "content_hash": h,
            "source_agent": source_agent,
            "source_tool": source_agent,
            "provenance": {"source_agent": source_agent},
        }
        if detail:
            staged_item["detail"] = detail
        if "question" in item and "choice" in item:
            staged_item["question"] = str(item.get("question") or "")
            staged_item["choice"] = str(item.get("choice") or "")
        if session_id:
            staged_item["provenance"]["session_id"] = session_id
        if run_id:
            staged_item["provenance"]["run_id"] = run_id
        domain = item.get("domain")
        if isinstance(domain, str) and domain.strip():
            staged_item["domain"] = domain.strip()
        staged.append(staged_item)

    audit_record = {
        "action": "writeback",
        "source": source_agent,
        "session_id": session_id,
        "run_id": run_id,
        "staged_count": len(staged),
        "skipped_duplicate": skipped_duplicate,
        "dropped_sensitive": dropped_sensitive,
        "content_hashes": hashes,
        "applied": False,  # dry-run: nothing written to disk
    }
    return {
        "staged": staged,
        "dropped_sensitive": dropped_sensitive,
        "skipped_duplicate": skipped_duplicate,
        "audit_record": audit_record,
    }


# --- tool writeback parsers (normalize each tool's export shape) -------------

def parse_tool_writeback(tool: str, raw: Any) -> list[dict[str, Any]]:
    """Normalize a tool's end-of-session output into candidate dicts.

    Supports the three local-tool shapes the harness fixtures use:
    - ``codex``  : already a list of ``{summary/detail/...}`` dicts.
    - ``claude`` : markdown with ``## Heading`` sections → summary/detail.
    - ``cursor`` : markdown rule list (``- rule`` lines) → one summary each.
    Unknown tools / malformed input return ``[]`` (never raises).
    """
    t = (tool or "").strip().lower()
    if t == "codex":
        return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
    if t == "claude":
        return _parse_markdown_sections(raw) if isinstance(raw, str) else []
    if t == "cursor":
        return _parse_rule_list(raw) if isinstance(raw, str) else []
    return []


def _parse_markdown_sections(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    heading: str | None = None
    body: list[str] = []

    def _flush():
        if heading:
            candidates.append({"summary": heading.strip(),
                               "detail": " ".join(body).strip()})

    for line in text.splitlines():
        if line.startswith("#"):
            _flush()
            heading = line.lstrip("#").strip()
            body = []
        elif line.strip():
            body.append(line.strip())
    _flush()
    return candidates


def _parse_rule_list(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            out.append({"summary": stripped[2:].strip()})
    return out


# --- the end-to-end simulation ----------------------------------------------

def simulate_continuity_cycle(
    *,
    lessons: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    tool: str = "codex",
    tool_writeback: Any = None,
    scope: str = "global",
    project: str = "",
    max_sensitivity: str = "work",
    allow_private: bool = False,
) -> dict[str, Any]:
    """Run the full simulated continuity cycle and return a trace + leak checks.

    No live tool is touched and nothing is written; the staged writeback items
    are returned in the trace so a caller (or test) can assert they did NOT leak
    into the next-session export.
    """
    lessons = lessons or []
    decisions = decisions or []

    # Stage 1+2 — resume/read & export the curated material the other tool uses.
    export_md = build_agents_md_export(
        lessons=lessons, decisions=decisions,
        scope=scope, project=project, max_sensitivity=max_sensitivity,
    )

    # Stage 3 — governed writeback from the tool's end-of-session output.
    candidates = parse_tool_writeback(tool, tool_writeback)
    writeback = prepare_writeback_candidates(
        candidates, allow_private=allow_private, source_agent=tool,
    )

    # Stage 4 — next-session read. The verified set is unchanged (staged items
    # are NOT promoted), so the next export must equal the first one.
    next_export_md = build_agents_md_export(
        lessons=lessons, decisions=decisions,
        scope=scope, project=project, max_sensitivity=max_sensitivity,
    )

    # Leak checks on the generated continuity material.
    leak = _leak_checks(export_md, lessons, decisions, writeback["staged"],
                        scope=scope, project=project, max_sensitivity=max_sensitivity)

    return {
        "tool": tool,
        "export_md": export_md,
        "next_export_md": next_export_md,
        "export_stable": export_md == next_export_md,
        "writeback": writeback,
        "leak_checks": leak,
        "all_writeback_staged": all(
            s.get("tier") == "staging" and s.get("approval_status") == "pending"
            for s in writeback["staged"]
        ),
    }


def _leak_checks(
    export_md: str,
    lessons: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    staged: list[dict[str, Any]],
    *,
    scope: str = "global",
    project: str = "",
    max_sensitivity: str = "work",
) -> dict[str, Any]:
    """Verify the export contains no staging/over-sensitive/just-staged content."""
    ceiling = _SENS_RANK.get(max_sensitivity, 1)

    staging_in_export = False
    sensitive_in_export = False
    for entry in list(lessons) + list(decisions):
        if not isinstance(entry, dict):
            continue
        summary, detail = _candidate_text(entry)
        present = bool(summary) and summary in export_md
        if not present:
            continue
        if entry.get("tier") == "staging":
            staging_in_export = True
        if _SENS_RANK.get(classify_item(entry), 1) > ceiling:
            sensitive_in_export = True

    # Freshly-staged writeback items must not *add* anything to the export. A
    # staged summary that happens to equal an already-verified exported entry is
    # NOT a leak (the export carries it because of the verified entry, not the
    # writeback). So a leak is: staged text present in the export that is not
    # attributable to a verified exported entry.
    verified_summaries: set[str] = set()
    for entry in select_exportable(list(lessons) + list(decisions),
                                   scope=scope, project=project,
                                   max_sensitivity=max_sensitivity):
        s, _ = _candidate_text(entry)
        if s:
            verified_summaries.add(s)
    staged_in_export = any(
        s.get("summary") and s["summary"] in export_md
        and s["summary"] not in verified_summaries
        for s in staged
    )

    return {
        "staging_in_export": staging_in_export,
        "sensitive_in_export": sensitive_in_export,
        "staged_writeback_in_export": staged_in_export,
        "clean": not (staging_in_export or sensitive_in_export or staged_in_export),
    }
