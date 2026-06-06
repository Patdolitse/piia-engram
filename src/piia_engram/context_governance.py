"""Unified proposal facade for context-governance helpers.

The functions here return local proposals and drafts only. They never publish,
push, tag, write files, mutate stored knowledge, or apply archive/replay
actions. MCP and CLI callers remain responsible for caller authorization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import context_replay as _replay
from . import external_evidence_page as _evidence
from . import freshness_conflict_resolver as _freshness
from . import recall_service as _recall_service
from . import safe_context as _safe_context

MODE_SAFE_CONTEXT = "safe_context"
MODE_FRESHNESS_CONFLICTS = "freshness_conflicts"
MODE_REPLAY_PACKET = "replay_packet"
MODE_EXTERNAL_EVIDENCE = "external_evidence"

MODES = frozenset(
    {
        MODE_SAFE_CONTEXT,
        MODE_FRESHNESS_CONFLICTS,
        MODE_REPLAY_PACKET,
        MODE_EXTERNAL_EVIDENCE,
    }
)


def _options(value: dict[str, Any] | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _int_option(options: dict[str, Any], name: str, default: int) -> int:
    try:
        return int(options.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool_option(options: dict[str, Any], name: str, default: bool = False) -> bool:
    value = options.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _string_option(options: dict[str, Any], name: str, default: str = "") -> str:
    value = options.get(name, default)
    return value if isinstance(value, str) else str(value or "")


def build_context_governance_preview(
    mode: str,
    *,
    engram: Any | None = None,
    payload: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build one context-governance preview payload for ``mode``.

    ``payload`` is caller-supplied input. ``engram`` is only required for modes
    that need a live store read. All modes include ``applied: false``.
    """
    normalized_mode = str(mode or "").strip().lower()
    opts = _options(options)
    body = dict(payload) if isinstance(payload, dict) else {}

    if normalized_mode == MODE_SAFE_CONTEXT:
        source_payload = body
        if not source_payload and engram is not None:
            source_payload = _recall_service.gather_recall(
                engram,
                project_folder=_string_option(opts, "project_folder"),
                query=_string_option(opts, "query"),
                limit=max(1, min(_int_option(opts, "limit", 8), 20)),
                token_budget=max(0, _int_option(opts, "token_budget", 2000)),
                include_freshness=_bool_option(opts, "include_freshness", True),
                collapse_versions=_bool_option(opts, "collapse_versions", True),
                role_scoped_memory=_bool_option(opts, "role_scoped_memory", False),
                now=now,
            )
        proposal = _safe_context.build_safe_context(
            source_payload,
            max_chars=max(0, _int_option(opts, "max_chars", 8000)),
            lockdown=_bool_option(opts, "lockdown", False),
        )
        return {
            "mode": MODE_SAFE_CONTEXT,
            "proposal": proposal,
            "applied": False,
            "invariant": "context_governance_preview_only",
        }

    if normalized_mode == MODE_FRESHNESS_CONFLICTS:
        if engram is None:
            return {
                "mode": MODE_FRESHNESS_CONFLICTS,
                "error": "engram_required",
                "applied": False,
                "invariant": "context_governance_preview_only",
            }
        lessons = engram.get_lessons(limit=None, _update_access=False) or []
        decisions = engram.get_decisions(limit=None, _update_access=False) or []
        proposal = _freshness.build_freshness_conflict_proposal(
            lessons,
            decisions,
            now=now,
        )
        return {
            "mode": MODE_FRESHNESS_CONFLICTS,
            "proposal": proposal,
            "applied": False,
            "invariant": "context_governance_preview_only",
        }

    if normalized_mode == MODE_REPLAY_PACKET:
        summary = _string_option(opts, "compact_summary") or _string_option(
            body, "compact_summary"
        )
        proposal = _replay.build_replay_packet(
            summary,
            source=_string_option(opts, "source", "postcompact"),
            max_summary_chars=max(0, _int_option(opts, "max_summary_chars", 1200)),
            now=now,
        )
        return {
            "mode": MODE_REPLAY_PACKET,
            "proposal": proposal,
            "applied": False,
            "invariant": "context_governance_preview_only",
        }

    if normalized_mode == MODE_EXTERNAL_EVIDENCE:
        evidence = body.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
        draft = _evidence.render_external_evidence_draft(
            evidence,
            title=_string_option(opts, "title", "External Evidence"),
        )
        return {
            "mode": MODE_EXTERNAL_EVIDENCE,
            "proposal": {
                "format": "markdown",
                "draft": draft,
                "publication_guard": "owner_confirmation_required",
            },
            "applied": False,
            "invariant": "context_governance_preview_only",
        }

    return {
        "mode": normalized_mode,
        "error": "unknown_mode",
        "allowed_modes": sorted(MODES),
        "applied": False,
        "invariant": "context_governance_preview_only",
    }
