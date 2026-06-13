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
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .export_redaction import redact_export_text
from .governance import DEFAULT_SENSITIVITY, TRUST_LEVELS, _sens_rank
from .i18n import get_lang, t
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
# Renderers (bilingual: labels go through i18n.t — zh follows the owner's
# profile language preference, NOT the OS locale; values stay raw + escaped)
# ---------------------------------------------------------------------------

_REASON_LABELS: dict[str, tuple[str, str]] = {
    "sensitivity_above_ceiling": ("敏感度高于该调用方上限", "above this caller's sensitivity ceiling"),
    "staging_excluded": ("暂存层对该调用方不可见", "staging tier hidden from this caller"),
}
_TYPE_LABELS: dict[str, tuple[str, str]] = {
    "lesson": ("经验", "lesson"),
    "decision": ("决策", "decision"),
}
_SENS_LABELS: dict[str, tuple[str, str]] = {
    "public": ("公开", "public"),
    "work": ("工作", "work"),
    "secret": ("机密", "secret"),
}
_TIER_LABELS: dict[str, tuple[str, str]] = {
    "verified": ("已验证", "verified"),
    "staging": ("暂存", "staging"),
}
# Identity field names: zh UI shows a human label, en keeps the raw key.
# Unknown keys fall back to the raw name (never hidden, never invented).
_FIELD_LABELS: dict[str, tuple[str, str]] = {
    "role": ("角色", "role"),
    "language": ("语言", "language"),
    "technical_level": ("技术背景", "technical_level"),
    "preferences": ("偏好", "preferences"),
    "quality_standards": ("质量标准", "quality_standards"),
    "work_patterns": ("工作模式", "work_patterns"),
    "description": ("自述", "description"),
    "tech_stack": ("技术栈", "tech_stack"),
    "updated_at": ("更新时间", "updated_at"),
    "communication": ("沟通风格", "communication"),
    "name": ("姓名", "name"),
    "email": ("邮箱", "email"),
    "phone": ("电话", "phone"),
}


def _label(table: dict[str, tuple[str, str]], raw: Any) -> str:
    pair = table.get(str(raw or "").strip().lower())
    return t(*pair) if pair else str(raw or "")


def render_context_preview_text(preview: dict[str, Any]) -> str:
    """Render the preview as a compact owner-facing text report (bilingual)."""
    caller = preview.get("caller", {})
    identity = preview.get("identity", {})
    knowledge = preview.get("knowledge", {})
    redaction = preview.get("redaction", {})
    budget = preview.get("budget", {})

    role = preview.get("role")
    trust = caller.get("trust_level")
    ceiling = caller.get("effective_ceiling")
    write = caller.get("effective_write")
    level = preview.get("level")
    tokens = budget.get("level_token_budget")
    chars = budget.get("max_chars")

    lines = [
        t(
            "记忆透视 — 该调用方此刻会拿到什么",
            "Context preview — what this caller would receive",
        ),
        t(
            f"  模拟调用方: role={role}（信任={trust}, 敏感度上限={ceiling}, 写权限={write}）",
            f"  simulated caller: role={role} "
            f"(trust={trust}, ceiling={ceiling}, write={write})",
        ),
        t(
            f"  注入级别: {level}（预算 {tokens} tokens / {chars} 字符）",
            f"  injection level: {level} (budget {tokens} tokens / {chars} chars)",
        ),
    ]
    if preview.get("project_folder"):
        lines.append(t(
            f"  项目: {preview['project_folder']}",
            f"  project: {preview['project_folder']}",
        ))
    if preview.get("query"):
        lines.append(t(
            f"  查询焦点: {preview['query']}",
            f"  query focus: {preview['query']}",
        ))

    lines.append(t("身份（会注入）:", "Identity (exposed):"))
    exposed_identity = identity.get("exposed", {})
    if exposed_identity:
        for key, value in exposed_identity.items():
            rendered = ", ".join(value) if isinstance(value, list) else str(value)
            lines.append(f"  {_label(_FIELD_LABELS, key)}: {rendered}")
    else:
        lines.append(t(
            "  （无 — 不会注入任何身份字段）",
            "  (nothing — no identity fields would be injected)",
        ))
    withheld_fields = identity.get("withheld_fields", [])
    if withheld_fields:
        names = ", ".join(_label(_FIELD_LABELS, n) for n in withheld_fields)
        lines.append(t(
            f"身份（被保留，仅字段名）: {names}",
            f"Identity (withheld, names only): {names}",
        ))

    exposed_count = knowledge.get("exposed_count", 0)
    lines.append(t(
        f"会注入的知识（{exposed_count} 条）:",
        f"Knowledge exposed ({exposed_count} items):",
    ))
    for item in knowledge.get("exposed", []):
        tier = f"/{_label(_TIER_LABELS, item['tier'])}" if item.get("tier") else ""
        lines.append(
            f"  - ({_label(_TYPE_LABELS, item.get('type'))}{tier}, "
            f"{_label(_SENS_LABELS, item.get('sensitivity'))}) {item.get('summary')}"
        )
    withheld = knowledge.get("withheld", [])
    lines.append(t(
        f"被拦截的知识（{len(withheld)} 条）:",
        f"Knowledge withheld ({len(withheld)} items):",
    ))
    if withheld:
        for item in withheld:
            lines.append(
                f"  - ({_label(_TYPE_LABELS, item.get('type'))}, "
                f"{_label(_SENS_LABELS, item.get('sensitivity'))}) "
                f"[{_label(_REASON_LABELS, item.get('withheld_reason'))}] "
                f"{item.get('summary')}"
            )
    else:
        lines.append(t(
            "  （没有超出该调用方上限的条目）",
            "  (none above this caller's ceiling)",
        ))

    staging = caller.get("staging_excluded")
    hits = redaction.get("hits", 0)
    trimmed_n = knowledge.get("trimmed_by_budget", 0)
    est = budget.get("estimated_chars", 0)
    lines.append(t(
        f"治理: 排除暂存层={'是' if staging else '否'}, 脱敏命中={hits}, "
        f"预算裁剪={trimmed_n}, 估算字符={est}"
        f"{'（已裁剪）' if budget.get('trimmed') else ''}",
        f"Governance: staging_excluded={'yes' if staging else 'no'}, "
        f"redaction_hits={hits}, trimmed_by_budget={trimmed_n}, "
        f"estimated_chars={est}{' (trimmed)' if budget.get('trimmed') else ''}",
    ))
    lines.append(t(
        "说明: 只读预览，未向任何 AI 工具发送内容。被拦截条目绝不显示正文——仅名称/摘要。",
        "Note: read-only preview; nothing was sent to any AI tool. "
        "Withheld bodies are never shown — names/summaries only.",
    ))
    return "\n".join(lines) + "\n"


def _sens_tag(sensitivity: Any) -> str:
    """Render one sensitivity value as a colored tag (class is allowlisted)."""
    key = str(sensitivity or "").strip().lower()
    css = key if key in ("public", "work", "secret") else "work"
    return f'<span class="tag tag-{css}">{html.escape(_label(_SENS_LABELS, sensitivity))}</span>'


def _split_item_label(text: str) -> tuple[str, str]:
    """Split a "前缀: 正文" list item so the prefix can be highlighted.

    Only short prefixes (≤16 chars, not URL-ish) count — free text that merely
    contains a colon stays whole. Returns ("", text) when there is no prefix.
    """
    for sep in ("：", ":"):
        idx = text.find(sep)
        if 0 < idx <= 16:
            prefix = text[:idx].strip()
            if (prefix and "http" not in prefix.lower()
                    and "/" not in prefix and "。" not in prefix):
                return prefix, text[idx + len(sep):].strip()
    return "", text


def _split_title(text: str) -> tuple[str, str]:
    """Split a "标题：正文" lead (≤48 chars, not URL-ish) for stacked layout."""
    for sep in ("：", ": "):
        idx = text.find(sep)
        if 0 < idx <= 48:
            prefix = text[:idx].strip()
            if prefix and "http" not in prefix.lower():
                return prefix, text[idx + len(sep):].strip()
    return "", text


def _flow(text: str) -> str:
    """Escape text and highlight "→" step arrows for visual rhythm."""
    return html.escape(text).replace("→", '<span class="arr">→</span>')


def _body_parts(body: str) -> list[str]:
    """Split a long body into readable parts, most-confident separator first:
    "；" lists → " + " enumerations (≥3) → "。" sentences (long text only)."""
    parts = [p.strip() for p in re.split(r"；\s*|;\s+", body) if p.strip()]
    if len(parts) >= 2:
        return parts
    seq = [p.strip() for p in body.split(" + ") if p.strip()]
    if len(seq) >= 3:
        return seq
    if len(body) > 60:
        sents = [s.strip() for s in re.split(r"。\s*", body) if s.strip()]
        if len(sents) >= 2:
            return sents
    return [body] if body else []


def _bullet_row(text: str) -> str:
    """One bullet row; a short "标签:" prefix gets a cyan chip, a longer
    "标题：" lead gets stacked title-over-body layout."""
    prefix, body = _split_item_label(text)
    if prefix:
        return (
            '<div class="val-item"><span class="vk">' + html.escape(prefix)
            + "</span><span>" + _flow(body) + "</span></div>"
        )
    title, rest = _split_title(text)
    if title and rest:
        pieces = _body_parts(rest)
        if len(pieces) >= 2:
            inner = "".join(
                '<div class="val-sub"><span>' + _flow(p) + "</span></div>"
                for p in pieces
            )
        else:
            inner = "<div>" + _flow(rest) + "</div>"
        return (
            '<div class="val-item"><div><div class="sum-title">'
            + html.escape(title) + "</div>" + inner + "</div></div>"
        )
    return '<div class="val-item"><span>' + _flow(text) + "</span></div>"


def _identity_value_html(value: Any) -> str:
    """Render an identity value: scalars stay inline; lists become one bullet
    row per item with the "标签:" prefix highlighted for scanability."""
    if not isinstance(value, list):
        return '<span class="val">' + html.escape(str(value)) + "</span>"
    rows = "".join(_bullet_row(str(raw)) for raw in value)
    return '<div class="val val-list">' + rows + "</div>"


def _summary_html(text: Any) -> str:
    """Rich-render a summary cell: "；" multi-part → bullets; "标题：正文" →
    stacked title + structured body (sentences / " + " enumerations become
    bullets, "→" chains highlighted); plain short text stays plain."""
    raw = str(text or "").strip()
    parts = [p.strip() for p in re.split(r"；\s*|;\s+", raw) if p.strip()]
    if len(parts) >= 2:
        return '<div class="val-list">' + "".join(_bullet_row(p) for p in parts) + "</div>"
    title, body = _split_title(raw)
    if title:
        pieces = _body_parts(body)
        out = ['<div class="sum-title">' + html.escape(title) + "</div>"]
        if len(pieces) >= 2:
            out.append(
                '<div class="val-list">' + "".join(_bullet_row(p) for p in pieces) + "</div>"
            )
        elif pieces:
            out.append('<div class="sum-body">' + _flow(pieces[0]) + "</div>")
        return "".join(out)
    pieces = _body_parts(raw)
    if len(pieces) >= 2:
        return '<div class="val-list">' + "".join(_bullet_row(p) for p in pieces) + "</div>"
    prefix, body = _split_item_label(raw)
    if prefix:
        return '<span class="vk">' + html.escape(prefix) + "</span> " + _flow(body)
    return _flow(raw)


def render_context_preview_html(preview: dict[str, Any]) -> str:
    """Render the preview as a standalone HTML page (all dynamic text escaped).

    Owner-facing layout (not a terminal dump): hero header with caller badges,
    stat cards, identity card, exposed/withheld knowledge tables, governance
    footer. ``<html lang>`` and every label follow ``i18n`` (zh/en).
    """
    caller = preview.get("caller", {})
    identity = preview.get("identity", {})
    knowledge = preview.get("knowledge", {})
    redaction = preview.get("redaction", {})
    budget = preview.get("budget", {})

    def esc(value: Any) -> str:
        return html.escape(str(value))

    lang = "zh" if get_lang() == "zh" else "en"
    title = t("Engram 记忆透视", "Engram Memory Lens")
    subtitle = t(
        "只读预览 — 模拟该调用方此刻会拿到什么，未向任何 AI 工具发送内容",
        "Read-only preview — what this caller would receive right now; nothing was sent to any AI tool",
    )
    tagline = (
        f"{t('模拟调用方', 'Simulated caller')}: {preview.get('role', '')}"
        f" · {caller.get('trust_level', '')}"
    )

    # --- hero meta row -----------------------------------------------------
    meta_pairs: list[tuple[str, str]] = [
        (t("注入级别", "Level"), str(preview.get("level", ""))),
        (t("敏感度上限", "Ceiling"), _label(_SENS_LABELS, caller.get("effective_ceiling"))),
        (t("写权限", "Write"), str(caller.get("effective_write", ""))),
        (
            t("暂存层", "Staging"),
            t("不可见", "hidden") if caller.get("staging_excluded") else t("可见", "visible"),
        ),
        (t("生成时间", "Generated"), str(preview.get("generated_at", ""))),
    ]
    if preview.get("project_folder"):
        meta_pairs.append((t("项目", "Project"), str(preview["project_folder"])))
    if preview.get("query"):
        meta_pairs.append((t("查询", "Query"), str(preview["query"])))
    meta_items = "\n".join(
        f'      <span class="meta-item"><span class="dot"></span>'
        f'{esc(k)} <b>{esc(v)}</b></span>'
        for k, v in meta_pairs
    )

    # --- stat cards --------------------------------------------------------
    est = budget.get("estimated_chars", 0)
    est_label = f"{est}{t('（已裁剪）', ' (trimmed)') if budget.get('trimmed') else ''}"
    stats = [
        ("c-green", knowledge.get("exposed_count", 0), t("会注入的知识", "Knowledge exposed")),
        ("c-orange", knowledge.get("withheld_count", 0), t("被拦截的知识", "Knowledge withheld")),
        ("c-cyan", redaction.get("hits", 0), t("脱敏命中", "Redaction hits")),
        ("c-accent", knowledge.get("trimmed_by_budget", 0), t("预算裁剪", "Trimmed by budget")),
        ("c-pink", est_label, t("估算字符", "Estimated chars")),
    ]
    stat_cards = "\n".join(
        f'      <div class="stat-card"><div class="stat-number {css}">{esc(num)}</div>'
        f'<div class="stat-label">{esc(lbl)}</div></div>'
        for css, num, lbl in stats
    )

    # --- identity cards (two-col) ------------------------------------------
    exposed_identity = identity.get("exposed", {})
    if exposed_identity:
        kv_rows = "\n".join(
            '          <li><span class="key" title="' + esc(key) + '">'
            + esc(_label(_FIELD_LABELS, key)) + '</span>'
            + _identity_value_html(value)
            + "</li>"
            for key, value in exposed_identity.items()
        )
        identity_block = f'        <ul class="card-list">\n{kv_rows}\n        </ul>'
    else:
        identity_block = (
            f'        <p class="empty">'
            f'{esc(t("（无 — 不会注入任何身份字段）", "(nothing — no identity fields would be injected)"))}</p>'
        )
    withheld_fields = identity.get("withheld_fields", [])
    if withheld_fields:
        chips = "".join(
            f'<span class="tag tag-dim" title="{esc(name)}">'
            f'{esc(_label(_FIELD_LABELS, name))}</span>'
            for name in withheld_fields
        )
        withheld_identity_block = f'        <div class="tag-wrap">{chips}</div>'
    else:
        withheld_identity_block = (
            f'        <p class="empty">{esc(t("（无）", "(none)"))}</p>'
        )

    # --- knowledge tables ---------------------------------------------------
    def _exposed_rows(items: list[dict[str, Any]]) -> str:
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(
                "<tr>"
                f"<td>{esc(_label(_TYPE_LABELS, item.get('type')))}</td>"
                f"<td>{esc(_label(_TIER_LABELS, item.get('tier')) or '—')}</td>"
                f"<td>{_sens_tag(item.get('sensitivity'))}</td>"
                f"<td>{_summary_html(item.get('summary', ''))}</td>"
                "</tr>"
            )
        if not rows:
            rows.append(f'<tr><td colspan="4" class="muted">{esc(t("（无）", "(none)"))}</td></tr>')
        return "\n".join(rows)

    def _withheld_rows(items: list[dict[str, Any]]) -> str:
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(
                "<tr>"
                f"<td>{esc(_label(_TYPE_LABELS, item.get('type')))}</td>"
                f"<td>{_sens_tag(item.get('sensitivity'))}</td>"
                f"<td>{esc(_label(_REASON_LABELS, item.get('withheld_reason')))}</td>"
                f"<td>{_summary_html(item.get('summary', ''))}</td>"
                "</tr>"
            )
        if not rows:
            none_label = t(
                "（没有超出该调用方上限的条目）",
                "(none above this caller's ceiling)",
            )
            rows.append(
                f'<tr><td colspan="4" class="muted">{esc(none_label)}</td></tr>'
            )
        return "\n".join(rows)

    exposed_rows = _exposed_rows(knowledge.get("exposed", []))
    withheld_rows = _withheld_rows(knowledge.get("withheld", []))

    footer = t(
        "只读预览，未向任何 AI 工具发送内容 · 被拦截条目仅显示名称/摘要，绝不显示正文",
        "Read-only preview — nothing was sent · withheld items show names/summaries only, never bodies",
    )
    local_note = t(
        "所有数据存于本地 ~/.engram/",
        "All data stored locally at ~/.engram/",
    )
    items_word = t("条", "items")

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
  :root {{
    --bg: #0a0a0f; --bg-card: #12121a; --border: #1e1e2e; --border-glow: #6366f130;
    --text: #e4e4ef; --text-dim: #8888a0; --text-muted: #55556a;
    --accent: #818cf8; --accent-glow: #818cf840;
    --green: #34d399; --green-dim: #34d39930;
    --orange: #fb923c; --orange-dim: #fb923c30;
    --red: #f87171; --red-dim: #f8717130;
    --cyan: #22d3ee; --cyan-dim: #22d3ee25;
    --pink: #f472b6; --pink-dim: #f472b625;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: "Segoe UI", system-ui, -apple-system, "Microsoft YaHei", "PingFang SC", sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.6; min-height: 100vh;
  }}
  body::before {{
    content: ''; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background:
      radial-gradient(ellipse 80% 50% at 20% 20%, #818cf808 0%, transparent 50%),
      radial-gradient(ellipse 60% 40% at 80% 80%, #22d3ee06 0%, transparent 50%);
    pointer-events: none; z-index: 0;
  }}
  .container {{ max-width: 1000px; margin: 0 auto; padding: 36px 24px 60px;
               position: relative; z-index: 1; }}
  .hero {{ text-align: center; padding: 28px 0 36px; position: relative; }}
  .hero::after {{ content: ''; position: absolute; bottom: 0; left: 10%; right: 10%; height: 1px;
                 background: linear-gradient(90deg, transparent, var(--border), transparent); }}
  .hero h1 {{ font-size: 2rem; font-weight: 800; letter-spacing: -0.5px;
             background: linear-gradient(135deg, var(--text), var(--accent));
             -webkit-background-clip: text; -webkit-text-fill-color: transparent;
             margin-bottom: 8px; }}
  .hero .subtitle {{ font-size: 0.9rem; color: var(--text-dim); margin: 0 auto 16px;
                    max-width: 600px; }}
  .hero .tagline {{ display: inline-block; padding: 6px 18px; border-radius: 999px;
                   font-size: 0.82rem; font-weight: 500; color: var(--accent);
                   background: var(--accent-glow); border: 1px solid #818cf820;
                   letter-spacing: 0.5px; }}
  .meta-row {{ display: flex; justify-content: center; gap: 22px; margin-top: 20px;
              flex-wrap: wrap; }}
  .meta-item {{ font-size: 0.78rem; color: var(--text-muted); display: flex;
               align-items: center; gap: 6px; }}
  .meta-item b {{ color: var(--text-dim); font-weight: 600; }}
  .meta-item .dot {{ width: 6px; height: 6px; border-radius: 50%; background: var(--green);
                    box-shadow: 0 0 6px var(--green); }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
           gap: 14px; margin: 36px 0 40px; }}
  .stat-card {{ background: var(--bg-card); border: 1px solid var(--border);
               border-radius: 14px; padding: 20px 14px; text-align: center;
               transition: all 0.3s ease; }}
  .stat-card:hover {{ border-color: var(--border-glow); transform: translateY(-2px);
                     box-shadow: 0 6px 20px #00000050; }}
  .stat-number {{ font-size: 2rem; font-weight: 700; line-height: 1.2;
                 font-variant-numeric: tabular-nums;
                 font-family: Consolas, "JetBrains Mono", monospace; }}
  .stat-label {{ font-size: 0.74rem; color: var(--text-dim); letter-spacing: 0.5px;
                margin-top: 4px; }}
  .c-accent {{ color: var(--accent); }}
  .c-green {{ color: var(--green); }}
  .c-orange {{ color: var(--orange); }}
  .c-cyan {{ color: var(--cyan); }}
  .c-pink {{ color: var(--pink); }}
  .section {{ margin-bottom: 40px; }}
  .section-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 16px;
                    padding-bottom: 10px; border-bottom: 1px solid var(--border); }}
  .section-icon {{ width: 34px; height: 34px; border-radius: 10px; display: flex;
                  align-items: center; justify-content: center; font-size: 16px;
                  flex-shrink: 0; }}
  .section-header h2 {{ font-size: 1.05rem; font-weight: 700; letter-spacing: -0.3px; }}
  .section-header .count {{ margin-left: auto; font-size: 0.72rem; color: var(--text-dim);
                           font-family: Consolas, monospace; padding: 2px 12px;
                           border: 1px solid var(--border); border-radius: 999px;
                           background: #ffffff05; font-variant-numeric: tabular-nums; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
  .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px;
          padding: 20px 22px; transition: all 0.3s; }}
  .card:hover {{ border-color: var(--border-glow); }}
  .card-title {{ font-size: 0.85rem; font-weight: 600; margin-bottom: 12px;
                display: flex; align-items: center; gap: 8px; }}
  .card-hint {{ font-size: 0.76rem; color: var(--text-dim); margin: -8px 0 12px; }}
  .card-list {{ list-style: none; }}
  .card-list li {{ font-size: 0.82rem; color: var(--text-dim); padding: 6px 0;
                  border-bottom: 1px solid #ffffff06; display: flex;
                  align-items: flex-start; gap: 10px; }}
  .card-list li:last-child {{ border-bottom: none; }}
  .card-list .key {{ color: #a5b0e8; min-width: 110px; max-width: 160px;
                    flex-shrink: 0; overflow-wrap: anywhere; font-weight: 600;
                    font-size: 0.82rem; letter-spacing: 0.3px; padding-top: 1px; }}
  .card-list .val {{ color: var(--text); flex: 1; min-width: 0; }}
  .val-list {{ display: flex; flex-direction: column; gap: 7px; }}
  .val-item {{ display: flex; gap: 8px; align-items: flex-start; line-height: 1.7; }}
  .val-item::before {{ content: ""; width: 4px; height: 4px; border-radius: 50%;
                      background: var(--accent); margin-top: 9px; flex-shrink: 0;
                      opacity: 0.75; }}
  .val-item .vk {{ color: var(--cyan); font-weight: 600; flex-shrink: 0; }}
  .val-item span:last-child {{ overflow-wrap: anywhere; }}
  .sum-title {{ color: #b7befa; font-weight: 600; margin-bottom: 5px;
               line-height: 1.55; overflow-wrap: anywhere; }}
  .sum-body {{ overflow-wrap: anywhere; }}
  .val-item .sum-title {{ margin-bottom: 3px; }}
  .val-sub {{ display: flex; gap: 7px; align-items: flex-start; padding: 2px 0;
             overflow-wrap: anywhere; }}
  .val-sub::before {{ content: "–"; color: var(--text-muted); flex-shrink: 0; }}
  .arr {{ color: var(--accent); font-weight: 700; padding: 0 1px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  th {{ text-align: left; color: var(--text-muted); text-transform: uppercase;
       letter-spacing: 1px; font-size: 0.68rem; font-weight: 600; padding: 8px 10px;
       border-bottom: 1px solid var(--border); white-space: nowrap; }}
  td {{ padding: 10px; border-bottom: 1px solid #ffffff06; color: var(--text-dim);
       vertical-align: top; white-space: nowrap; transition: background 0.2s ease; }}
  td:last-child {{ color: var(--text); white-space: normal; width: 100%; }}
  tbody tr:nth-child(even) td {{ background: #ffffff02; }}
  tbody tr:hover td {{ background: #ffffff05; }}
  tr:last-child td {{ border-bottom: none; }}
  .tag {{ display: inline-block; padding: 3px 10px; border-radius: 6px; font-size: 0.72rem;
         font-weight: 500; font-family: Consolas, monospace; margin: 2px 3px 2px 0;
         white-space: nowrap; }}
  .tag-public {{ background: var(--cyan-dim); color: var(--cyan); border: 1px solid #22d3ee15; }}
  .tag-work {{ background: var(--accent-glow); color: var(--accent); border: 1px solid #818cf815; }}
  .tag-secret {{ background: var(--red-dim); color: var(--red); border: 1px solid #f8717115; }}
  .tag-dim {{ background: #ffffff0d; color: #b9bdd6; border: 1px solid #2a2a40;
             font-size: 0.78rem; padding: 5px 12px; font-weight: 500; }}
  .tag-wrap {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .empty, .muted {{ color: var(--text-muted); font-size: 0.8rem; }}
  .footer {{ text-align: center; padding-top: 32px; margin-top: 24px;
            border-top: 1px solid var(--border); }}
  .footer p {{ font-size: 0.74rem; color: var(--text-muted); line-height: 1.8; }}
  .footer .brand {{ font-family: Consolas, monospace; font-weight: 600; color: var(--accent);
                   letter-spacing: 1px; }}
  ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
  ::-webkit-scrollbar-track {{ background: var(--bg); }}
  ::-webkit-scrollbar-thumb {{ background: #25253a; border-radius: 5px;
                              border: 2px solid var(--bg); }}
  ::-webkit-scrollbar-thumb:hover {{ background: #32324e; }}
  @media (max-width: 768px) {{
    .two-col {{ grid-template-columns: 1fr; }}
    .hero h1 {{ font-size: 1.5rem; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <h1>{esc(title)}</h1>
    <div class="subtitle">{esc(subtitle)}</div>
    <div class="tagline">{esc(tagline)}</div>
    <div class="meta-row">
{meta_items}
    </div>
  </div>
  <div class="stats">
{stat_cards}
  </div>
  <div class="section">
    <div class="section-header">
      <div class="section-icon" style="background: var(--accent-glow);"><span style="color: var(--accent);">ID</span></div>
      <h2>{esc(t("身份 / Identity", "Identity"))}</h2>
    </div>
    <div class="two-col">
      <div class="card">
        <div class="card-title"><span class="c-accent">◆</span> {esc(t("会注入的字段", "Fields that would be injected"))}</div>
{identity_block}
      </div>
      <div class="card">
        <div class="card-title"><span class="c-orange">◆</span> {esc(t("被保留的字段", "Withheld fields"))}</div>
        <p class="card-hint">{esc(t("仅字段名，绝不显示值", "Names only — values are never shown"))}</p>
{withheld_identity_block}
      </div>
    </div>
  </div>
  <div class="section">
    <div class="section-header">
      <div class="section-icon" style="background: var(--green-dim);"><span style="color: var(--green);">✓</span></div>
      <h2>{esc(t("会注入的知识", "Knowledge exposed to this caller"))}</h2>
      <span class="count">{esc(knowledge.get("exposed_count", 0))} {esc(items_word)}</span>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>{esc(t("类型", "Type"))}</th><th>{esc(t("层级", "Tier"))}</th><th>{esc(t("敏感度", "Sensitivity"))}</th><th>{esc(t("摘要", "Summary"))}</th></tr></thead>
        <tbody>
{exposed_rows}
        </tbody>
      </table>
    </div>
  </div>
  <div class="section">
    <div class="section-header">
      <div class="section-icon" style="background: var(--orange-dim);"><span style="color: var(--orange);">⚠</span></div>
      <h2>{esc(t("被拦截的知识", "Knowledge withheld from this caller"))}</h2>
      <span class="count">{esc(knowledge.get("withheld_count", 0))} {esc(items_word)}</span>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>{esc(t("类型", "Type"))}</th><th>{esc(t("敏感度", "Sensitivity"))}</th><th>{esc(t("拦截原因", "Reason"))}</th><th>{esc(t("摘要", "Summary"))}</th></tr></thead>
        <tbody>
{withheld_rows}
        </tbody>
      </table>
    </div>
  </div>
  <div class="footer">
    <p>
      Generated by <span class="brand">ENGRAM</span> &mdash; {esc(footer)}<br>
      {esc(local_note)} &bull; {esc(preview.get("generated_at", ""))}
    </p>
  </div>
</div>
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
