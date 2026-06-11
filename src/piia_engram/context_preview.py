"""Context Preview — show the owner exactly what an AI caller would receive.

``engram preview`` answers the sovereignty question *before* any injection
happens: "if an AI tool with role X asked for my context right now, what would
it actually get — and what would be withheld or redacted?"

This module is the pure core. :func:`build_context_preview` reads the live
store through the same already-governed paths the real injection uses
(:func:`recall_service.gather_recall_sources` for the raw sources — raw, so
per-item ``sensitivity``/``tier`` survive for the exposed/withheld split —
:func:`permission_profile_vnext.resolve_effective_profile` for the caller
ceiling, :func:`safe_context.build_safe_context` for redaction + budget) and
returns a structured dict. Renderers turn that dict into owner-facing text or
HTML. Nothing here mutates the store, calls the network, or widens what the
governance layer already allows — the preview can only *narrow* (it shows the
withheld side as metadata, never as full bodies).

CLI wiring lives in ``cli_commands.run_preview`` (owner-run, ``private-self``);
no MCP tool is added for this surface by design (the tool surface is frozen —
``preview_context_governance`` already covers the programmatic path).
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .export_redaction import redact_export_text
from .governance import DEFAULT_SENSITIVITY, TRUST_LEVELS, _sens_rank
from .permission_profile_vnext import (
    ROLE_PROFILES,
    CallerContext,
    resolve_effective_profile,
)
from .recall import _entry_type, merge_knowledge
from .recall_service import gather_recall_sources
from .safe_context import build_safe_context

# Injection tiers the preview can simulate. Mirrors the get_user_context level
# ladder: each level maps to the gather budget actually used at that tier.
LEVELS: dict[str, dict[str, int]] = {
    "quick": {"token_budget": 800, "limit": 4, "max_chars": 3200},
    "standard": {"token_budget": 2000, "limit": 8, "max_chars": 8000},
    "full": {"token_budget": 4000, "limit": 16, "max_chars": 16000},
}
DEFAULT_LEVEL = "standard"

# Display anchor: which trust level a simulated role is typically resolved to.
# owner == the user themselves; assistant == a primary local coding agent
# (Claude Code / Cursor / Codex); automation == an unknown/transient agent.
ROLE_TRUST_ANCHORS: dict[str, str] = {
    "owner": "private-self",
    "assistant": "trusted-local",
    "reviewer": "trusted-local",
    "automation": "read-only-external",
}
DEFAULT_ROLE = "assistant"

_REDACTION_PLACEHOLDER = "[REDACTED]"


def _knowledge_digest(item: dict[str, Any]) -> dict[str, Any]:
    """Project one knowledge item to the compact digest the preview shows."""
    label = (
        item.get("summary")
        or item.get("choice")
        or item.get("question")
        or "(no summary)"
    )
    return {
        "type": str(item.get("type") or item.get("_type") or _entry_type(item)),
        "tier": str(item.get("tier") or ""),
        "sensitivity": str(item.get("sensitivity") or DEFAULT_SENSITIVITY),
        "summary": str(label),
    }


def _placeholder_count(value: Any) -> int:
    try:
        return json.dumps(value, ensure_ascii=False).count(_REDACTION_PLACEHOLDER)
    except (TypeError, ValueError):
        return 0


def build_context_preview(
    eng: Any,
    *,
    level: str = DEFAULT_LEVEL,
    role: str = DEFAULT_ROLE,
    project_folder: str = "",
    query: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the owner-facing preview of one simulated injection.

    Returns a structured dict with three panels (identity / knowledge /
    governance) plus budget metadata. Raises ``ValueError`` for an unknown
    ``level`` or ``role`` — the CLI translates that to a usage error instead
    of silently previewing the wrong thing.
    """
    level_key = str(level or "").strip().lower() or DEFAULT_LEVEL
    if level_key not in LEVELS:
        raise ValueError(
            f"unknown level '{level}' (expected one of: {', '.join(sorted(LEVELS))})"
        )
    role_key = str(role or "").strip().lower() or DEFAULT_ROLE
    if role_key not in ROLE_TRUST_ANCHORS:
        raise ValueError(
            f"unknown role '{role}' "
            f"(expected one of: {', '.join(sorted(ROLE_TRUST_ANCHORS))})"
        )
    budget = LEVELS[level_key]

    # --- caller profile (what this role is allowed to see) ----------------
    trust_level = ROLE_TRUST_ANCHORS[role_key]
    profile = resolve_effective_profile(
        trust_level,
        CallerContext(caller_role=role_key),
    )
    ceiling_rank = _sens_rank(profile.effective_ceiling)

    # --- the real sources, fetched the same way the injection fetches -----
    # Raw (un-projected) items are required here: the projected recall view
    # intentionally drops ``sensitivity``/``tier``, which the split needs.
    sources = gather_recall_sources(
        eng,
        project_folder=project_folder,
        query=query,
        limit=budget["limit"],
    )
    identity = sources.get("identity", {})
    recent_activity = sources.get("recent_activity", {})
    merged = merge_knowledge(sources.get("relevant"), sources.get("query_knowledge"))

    # --- panel ②: split raw knowledge into exposed vs withheld -----------
    exposed_pre: list[dict[str, Any]] = []
    withheld_items: list[dict[str, Any]] = []
    for item in merged:
        if not isinstance(item, dict):
            continue
        digest = _knowledge_digest(item)
        sens = item.get("sensitivity", DEFAULT_SENSITIVITY)
        if _sens_rank(sens) > ceiling_rank:
            digest["withheld_reason"] = "sensitivity_above_ceiling"
            withheld_items.append(digest)
        elif profile.staging_excluded and str(item.get("tier") or "") == "staging":
            digest["withheld_reason"] = "staging_excluded"
            withheld_items.append(digest)
        else:
            exposed_pre.append(digest)
    # Withheld summaries are owner-facing metadata, but the report may be
    # saved/shared — scrub credential/PII shapes there too (not counted as
    # injection redaction hits; this is preview hygiene, not the send path).
    for digest in withheld_items:
        digest["summary"] = redact_export_text(digest["summary"])

    # --- redaction + budget pass on what survives -------------------------
    raw_exposed_payload = {
        "identity": identity,
        "recent_activity": recent_activity,
        "knowledge": exposed_pre,
    }
    before_placeholders = _placeholder_count(raw_exposed_payload)
    safe = build_safe_context(raw_exposed_payload, max_chars=budget["max_chars"])
    redaction_hits = max(0, _placeholder_count(safe) - before_placeholders)
    safe_meta = safe.get("meta", {}).get("safe_context", {}) if isinstance(safe, dict) else {}
    safe_knowledge = safe.get("knowledge", []) if isinstance(safe, dict) else []
    trimmed_by_budget = max(0, len(exposed_pre) - len(safe_knowledge))
    # Digests come out of the SAFE (redacted) payload so the preview itself
    # never carries an unredacted secret.
    exposed_digests = [item for item in safe_knowledge if isinstance(item, dict)]

    # --- panel ①: identity exposed slice + withheld field names ----------
    safe_identity = safe.get("identity", {}) if isinstance(safe, dict) else {}
    withheld_fields: list[str] = []
    getter = getattr(eng, "get_profile", None)
    if callable(getter):
        try:
            full_profile = getter() or {}
        except Exception:  # pragma: no cover - defensive; preview must build
            full_profile = {}
        if isinstance(full_profile, dict):
            # Names only — never the values of fields that are not injected.
            withheld_fields = sorted(
                str(key)
                for key in full_profile
                if key not in identity and not str(key).startswith("_")
            )

    generated_at = (now or datetime.now()).replace(microsecond=0).isoformat()
    return {
        "generated_at": generated_at,
        "level": level_key,
        "role": role_key,
        "project_folder": project_folder,
        "query": query,
        "caller": {
            "trust_level": profile.trust_level,
            "trust_ceiling": TRUST_LEVELS[profile.trust_level]["max_sensitivity"],
            "role_profile": dict(ROLE_PROFILES.get(role_key, {})),
            "effective_ceiling": profile.effective_ceiling,
            "effective_write": profile.effective_write,
            "staging_excluded": profile.staging_excluded,
            "reasons": list(profile.reasons),
        },
        "identity": {
            "exposed": safe_identity,
            "withheld_fields": withheld_fields,
        },
        "knowledge": {
            "exposed": exposed_digests,
            "withheld": withheld_items,
            "exposed_count": len(exposed_digests),
            "withheld_count": len(withheld_items),
            "trimmed_by_budget": trimmed_by_budget,
        },
        "redaction": {
            "placeholder": _REDACTION_PLACEHOLDER,
            "hits": redaction_hits,
        },
        "budget": {
            "level_token_budget": budget["token_budget"],
            "max_chars": budget["max_chars"],
            "estimated_chars": safe_meta.get("estimated_chars", 0),
            "trimmed": bool(safe_meta.get("trimmed", False)),
        },
        "invariant": "context_preview_read_only",
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_context_preview_text(preview: dict[str, Any]) -> str:
    """Render the preview as a compact owner-facing text report."""
    caller = preview.get("caller", {})
    identity = preview.get("identity", {})
    knowledge = preview.get("knowledge", {})
    redaction = preview.get("redaction", {})
    budget = preview.get("budget", {})

    lines = [
        "Context preview — what this caller would receive",
        (
            f"  simulated caller: role={preview.get('role')} "
            f"(trust={caller.get('trust_level')}, "
            f"ceiling={caller.get('effective_ceiling')}, "
            f"write={caller.get('effective_write')})"
        ),
        (
            f"  injection level: {preview.get('level')} "
            f"(budget {budget.get('level_token_budget')} tokens / "
            f"{budget.get('max_chars')} chars)"
        ),
    ]
    if preview.get("project_folder"):
        lines.append(f"  project: {preview['project_folder']}")
    if preview.get("query"):
        lines.append(f"  query focus: {preview['query']}")

    lines.append("Identity (exposed):")
    exposed_identity = identity.get("exposed", {})
    if exposed_identity:
        for key, value in exposed_identity.items():
            rendered = ", ".join(value) if isinstance(value, list) else str(value)
            lines.append(f"  {key}: {rendered}")
    else:
        lines.append("  (nothing — no identity fields would be injected)")
    withheld_fields = identity.get("withheld_fields", [])
    if withheld_fields:
        lines.append(
            f"Identity (withheld, names only): {', '.join(withheld_fields)}"
        )

    lines.append(
        f"Knowledge exposed ({knowledge.get('exposed_count', 0)} items):"
    )
    for item in knowledge.get("exposed", []):
        tier = f"/{item['tier']}" if item.get("tier") else ""
        lines.append(
            f"  - ({item.get('type')}{tier}, {item.get('sensitivity')}) "
            f"{item.get('summary')}"
        )
    withheld = knowledge.get("withheld", [])
    lines.append(f"Knowledge withheld ({len(withheld)} items):")
    if withheld:
        for item in withheld:
            lines.append(
                f"  - ({item.get('type')}, {item.get('sensitivity')}) "
                f"[{item.get('withheld_reason')}] {item.get('summary')}"
            )
    else:
        lines.append("  (none above this caller's ceiling)")

    lines.append(
        "Governance: "
        f"staging_excluded={'yes' if caller.get('staging_excluded') else 'no'}, "
        f"redaction_hits={redaction.get('hits', 0)}, "
        f"trimmed_by_budget={knowledge.get('trimmed_by_budget', 0)}, "
        f"estimated_chars={budget.get('estimated_chars', 0)}"
        f"{' (trimmed)' if budget.get('trimmed') else ''}"
    )
    lines.append(
        "Note: read-only preview; nothing was sent to any AI tool. "
        "Withheld bodies are never shown — names/summaries only."
    )
    return "\n".join(lines) + "\n"


def render_context_preview_html(preview: dict[str, Any]) -> str:
    """Render the preview as a standalone HTML page (all dynamic text escaped)."""
    caller = preview.get("caller", {})
    knowledge = preview.get("knowledge", {})
    text = html.escape(render_context_preview_text(preview))
    generated_at = html.escape(str(preview.get("generated_at", "")))
    role = html.escape(str(preview.get("role", "")))
    level = html.escape(str(preview.get("level", "")))
    ceiling = html.escape(str(caller.get("effective_ceiling", "")))

    def _rows(items: list[dict[str, Any]], with_reason: bool) -> str:
        rows = []
        for item in items:
            cells = [
                f"<td>{html.escape(str(item.get('type', '')))}</td>",
                f"<td>{html.escape(str(item.get('sensitivity', '')))}</td>",
                f"<td>{html.escape(str(item.get('summary', '')))}</td>",
            ]
            if with_reason:
                cells.append(
                    f"<td>{html.escape(str(item.get('withheld_reason', '')))}</td>"
                )
            rows.append("<tr>" + "".join(cells) + "</tr>")
        if not rows:
            span = 4 if with_reason else 3
            rows.append(f'<tr><td colspan="{span}">(none)</td></tr>')
        return "\n".join(rows)

    exposed_rows = _rows(knowledge.get("exposed", []), with_reason=False)
    withheld_rows = _rows(knowledge.get("withheld", []), with_reason=True)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Engram Context Preview</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 32px; color: #1f2937; }}
    main {{ max-width: 980px; }}
    h1 {{ font-size: 28px; margin-bottom: 4px; }}
    .meta {{ color: #64748b; margin-bottom: 24px; }}
    pre {{ background: #f8fafc; border: 1px solid #dbe3ee; border-radius: 8px; padding: 18px; white-space: pre-wrap; }}
    section {{ margin-top: 24px; }}
    h2 {{ font-size: 18px; margin-bottom: 8px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #dbe3ee; padding: 8px; text-align: left; }}
    th {{ background: #f8fafc; }}
    .exposed h2 {{ color: #166534; }}
    .withheld h2 {{ color: #9a3412; }}
  </style>
</head>
<body>
  <main>
    <h1>Engram Context Preview</h1>
    <div class="meta">role {role} - level {level} - ceiling {ceiling} - generated {generated_at} - read-only preview, nothing was sent</div>
    <pre>{text}</pre>
    <section class="exposed">
      <h2>Exposed to this caller</h2>
      <table>
        <thead><tr><th>Type</th><th>Sensitivity</th><th>Summary</th></tr></thead>
        <tbody>
{exposed_rows}
        </tbody>
      </table>
    </section>
    <section class="withheld">
      <h2>Withheld from this caller</h2>
      <table>
        <thead><tr><th>Type</th><th>Sensitivity</th><th>Summary</th><th>Reason</th></tr></thead>
        <tbody>
{withheld_rows}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def write_context_preview_html(
    preview: dict[str, Any],
    root: Path,
    output: Path | None = None,
) -> Path:
    """Write the HTML preview under ``<root>/reports/`` (or ``output``)."""
    stamp = str(preview.get("generated_at", "")).replace(":", "").replace("-", "")
    name = f"context-preview-{stamp or 'latest'}.html"
    path = output or (root / "reports" / name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_context_preview_html(preview), encoding="utf-8")
    return path
