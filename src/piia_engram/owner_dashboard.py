"""Non-technical owner control surface (Phase 11) — read-mostly, proposal-only.

A single local dashboard that makes Engram's governance / lifecycle / integrity /
readiness state legible to a **non-technical owner** in one place. It composes
the read-only surfaces built in earlier phases — recall trust, lifecycle decay
proposals, integrity status, and export/telemetry readiness — and renders them
as bilingual text or escaped HTML.

Constraints:
- **read-mostly / proposal-only**: the dashboard surfaces *proposals* (lifecycle
  archive/prune candidates, integrity self-heal suggestions) and the explicit
  commands to act on them. It performs no destructive action and exposes no
  one-click destructive control.
- **metadata only**: counts, statuses, freshness buckets — never stored bodies.
- **bilingual**: labels go through ``i18n.t(zh, en)``.
- **safe HTML**: every dynamic value is ``html.escape``-d (the dashboard could
  surface user-derived strings such as a domain label).
- **no private-mechanism leak**: the dashboard describes Engram's own state only;
  it never references private workflow or internal maintainer mechanisms.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from . import lifecycle as _lifecycle
from . import provenance as _provenance
from .agents_md_export import select_exportable
from .i18n import t

_FRESH_BUCKETS = ("fresh", "aging", "stale", "unknown")


def _freshness_distribution(entries: list[dict[str, Any]], now: datetime | None) -> dict[str, int]:
    dist = {b: 0 for b in _FRESH_BUCKETS}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        status = _provenance.compute_freshness(entry, now=now).get("freshness_status", "unknown")
        dist[status if status in dist else "unknown"] += 1
    return dist


def build_owner_dashboard(
    *,
    lessons: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    integrity_report: dict[str, Any] | None = None,
    telemetry_status: dict[str, Any] | None = None,
    merge_report: dict[str, Any] | None = None,
    reconcile_report: dict[str, Any] | None = None,
    version_report: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the owner dashboard from already-loaded read-only inputs.

    Pure: takes active knowledge entries plus optional integrity/telemetry
    snapshots and optional already-computed merge / reconcile / version-chain
    reports, and returns a structured, metadata-only dashboard dict. Does not
    read the store or mutate inputs.

    The optional reports feed the ``readiness`` block — metadata-only counts of
    how much owner-confirmed apply work is pending across lifecycle, reconcile
    (import-only), near-duplicate merge, and version-chain HEAD state. They are
    counts only; the dashboard never triggers any apply.
    """
    lessons = [e for e in (lessons or []) if isinstance(e, dict)]
    decisions = [e for e in (decisions or []) if isinstance(e, dict)]
    now = now or datetime.now(timezone.utc)
    all_entries = lessons + decisions

    # Recall trust — how explainable is the knowledge (freshness + provenance).
    freshness = _freshness_distribution(all_entries, now)
    with_provenance = sum(
        1 for e in all_entries
        if isinstance(e.get("provenance"), dict) or e.get("source_tool") or e.get("source_agent")
    )
    recall_trust = {
        "lessons": len(lessons),
        "decisions": len(decisions),
        "total": len(all_entries),
        "freshness": freshness,
        "with_provenance": with_provenance,
    }

    # Lifecycle — decay/archive proposal counts (proposal-only).
    lifecycle_report = _lifecycle.build_lifecycle_proposal(all_entries, now=now)
    lifecycle_summary = {
        "scored": lifecycle_report["scored"],
        "counts": lifecycle_report["counts"],
        "invariant": lifecycle_report["invariant"],
    }

    # Integrity — health + problem count (from a supplied scan, if any).
    if isinstance(integrity_report, dict):
        integrity_summary = {
            "healthy": integrity_report.get("healthy"),
            "problems": len(integrity_report.get("problems", [])),
            "problem_codes": [p.get("code") for p in integrity_report.get("problems", [])],
        }
    else:
        integrity_summary = {"healthy": None, "problems": None, "problem_codes": []}

    # Export readiness — how much verified, non-sensitive knowledge is shareable.
    exportable = len(select_exportable(all_entries, scope="global", max_sensitivity="work"))
    export_readiness = {"exportable_global": exportable, "total": len(all_entries)}

    # Telemetry readiness — opt-in state only (never enables anything).
    ts = telemetry_status if isinstance(telemetry_status, dict) else {}
    telemetry_readiness = {
        "enabled": bool(ts.get("enabled", False)),
        "remote_enabled": bool(ts.get("remote_enabled", False)),
        "phase": ts.get("phase"),
        "vnext_local_signals": {
            "names": [
                "recall_hit_rate",
                "cross_tool_handoffs",
                "user_bucket",
                "activation_depth",
            ],
            "default_on": False,
            "in_remote_d1": False,
            "note": (
                "Computed locally only; default off; not transmitted to remote D1."
            ),
        },
    }

    # Readiness — metadata-only "pending owner-confirmed apply" counts across the
    # local apply paths. Each block defaults to zero when no report is supplied.
    lc_counts = lifecycle_report.get("counts", {})
    archive_n = int(lc_counts.get("archive_candidate", 0) or 0)
    prune_n = int(lc_counts.get("prune_candidate", 0) or 0)
    merge_n = int((merge_report or {}).get("total_candidates", 0) or 0)
    rec_counts = (reconcile_report or {}).get("counts", {})
    ver_totals = (version_report or {}).get("totals", {})
    readiness = {
        "lifecycle": {
            "archive_candidates": archive_n,
            "prune_candidates": prune_n,
            "pending_apply": archive_n + prune_n,
        },
        "reconcile": {
            "import": int(rec_counts.get("import", 0) or 0),
            "duplicate": int(rec_counts.get("duplicate", 0) or 0),
            "conflict": int(rec_counts.get("conflict", 0) or 0),
        },
        "merge": {"candidates": merge_n},
        "version_chain": {
            "topics": int(ver_totals.get("topics", 0) or 0),
            "heads": int(ver_totals.get("heads", 0) or 0),
            "superseded": int(ver_totals.get("superseded", 0) or 0),
        },
    }
    next_action = _recommend_next_action(readiness)
    actions = _build_action_metadata(readiness)

    return {
        "generated_at": now.replace(microsecond=0).isoformat(),
        "recall_trust": recall_trust,
        "lifecycle": lifecycle_summary,
        "integrity": integrity_summary,
        "export_readiness": export_readiness,
        "telemetry": telemetry_readiness,
        "readiness": readiness,
        "next_action": next_action,
        "actions": actions,
        "note": "read-only metadata; proposals require explicit owner action",
    }


def _recommend_next_action(readiness: dict[str, Any]) -> dict[str, Any]:
    """Pick one owner-facing next action from metadata-only readiness counts."""
    rec = readiness.get("reconcile", {})
    lifecycle = readiness.get("lifecycle", {})
    merge = readiness.get("merge", {})
    version = readiness.get("version_chain", {})
    candidates = [
        (
            int(rec.get("conflict", 0) or 0),
            "review_reconcile_conflicts",
            "engram reconcile conflicts",
            "Review reconcile conflicts before importing or merging anything.",
        ),
        (
            int(rec.get("import", 0) or 0),
            "preview_reconcile_imports",
            "engram reconcile apply",
            "Preview import-only reconcile candidates.",
        ),
        (
            int(merge.get("candidates", 0) or 0),
            "preview_merge_candidates",
            "engram merge",
            "Preview near-duplicate merge candidates.",
        ),
        (
            int(lifecycle.get("pending_apply", 0) or 0),
            "preview_lifecycle_archive",
            "engram lifecycle apply",
            "Preview lifecycle archive candidates.",
        ),
        (
            int(version.get("superseded", 0) or 0),
            "review_version_chain_heads",
            "engram dashboard",
            "Review version-chain HEAD/superseded counts.",
        ),
    ]
    for count, code, command, reason in candidates:
        if count > 0:
            return {
                "code": code,
                "command": command,
                "count": count,
                "reason": reason,
            }
    return {
        "code": "none",
        "command": "",
        "count": 0,
        "reason": "No owner-confirmed local apply work is pending.",
    }


def _build_action_metadata(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    """Return GUI-safe owner action metadata; it never executes commands."""
    rec = readiness.get("reconcile", {})
    lifecycle = readiness.get("lifecycle", {})
    merge = readiness.get("merge", {})
    version = readiness.get("version_chain", {})
    specs = [
        (
            "review_reconcile_conflicts",
            "Review reconcile conflicts",
            "engram reconcile conflicts",
            int(rec.get("conflict", 0) or 0),
            "read_only",
        ),
        (
            "preview_reconcile_imports",
            "Preview reconcile imports",
            "engram reconcile apply",
            int(rec.get("import", 0) or 0),
            "dry_run_default",
        ),
        (
            "preview_merge_candidates",
            "Preview near-duplicate merges",
            "engram merge",
            int(merge.get("candidates", 0) or 0),
            "dry_run_default",
        ),
        (
            "preview_lifecycle_archive",
            "Preview lifecycle archive",
            "engram lifecycle apply",
            int(lifecycle.get("pending_apply", 0) or 0),
            "dry_run_default",
        ),
        (
            "review_version_chain_heads",
            "Review version-chain heads",
            "engram dashboard",
            int(version.get("superseded", 0) or 0),
            "read_only",
        ),
    ]
    return [
        {
            "code": code,
            "label": label,
            "command": command,
            "count": count,
            "risk": risk,
            "executes": False,
        }
        for code, label, command, count, risk in specs
        if count > 0
    ]


def render_dashboard_text(dashboard: dict[str, Any]) -> str:
    """Render the dashboard as bilingual, owner-facing text (metadata only)."""
    rt = dashboard.get("recall_trust", {})
    lc = dashboard.get("lifecycle", {})
    ig = dashboard.get("integrity", {})
    ex = dashboard.get("export_readiness", {})
    tm = dashboard.get("telemetry", {})
    vn = tm.get("vnext_local_signals", {}) if isinstance(tm, dict) else {}
    rd = dashboard.get("readiness", {})
    fr = rt.get("freshness", {})
    rd_lc = rd.get("lifecycle", {})
    rd_rec = rd.get("reconcile", {})
    rd_mrg = rd.get("merge", {})
    rd_ver = rd.get("version_chain", {})

    lines = [
        t("Engram 控制台（只读，仅元数据）", "Engram dashboard (read-only, metadata only)"),
        f"  {t('生成时间', 'generated')}: {dashboard.get('generated_at', '')}",
        t("召回信任 / Recall trust:", "Recall trust:"),
        f"  {t('知识总数', 'knowledge')}: {rt.get('total', 0)} "
        f"({t('教训', 'lessons')}={rt.get('lessons', 0)}, {t('决策', 'decisions')}={rt.get('decisions', 0)})",
        f"  {t('新鲜度', 'freshness')}: fresh={fr.get('fresh', 0)} aging={fr.get('aging', 0)} "
        f"stale={fr.get('stale', 0)} unknown={fr.get('unknown', 0)}",
        f"  {t('带来源', 'with provenance')}: {rt.get('with_provenance', 0)}",
        t("生命周期 / Lifecycle (提案，不会删除):", "Lifecycle (proposals, never deletes):"),
        f"  keep={lc.get('counts', {}).get('keep', 0)} "
        f"archive={lc.get('counts', {}).get('archive_candidate', 0)} "
        f"prune={lc.get('counts', {}).get('prune_candidate', 0)} "
        f"review={lc.get('counts', {}).get('review', 0)}",
        t("完整性 / Integrity:", "Integrity:"),
        f"  {t('健康', 'healthy')}: {ig.get('healthy')}  {t('问题数', 'problems')}: {ig.get('problems')}",
        t("导出就绪 / Export readiness:", "Export readiness:"),
        f"  {t('可导出(全局)', 'exportable (global)')}: {ex.get('exportable_global', 0)} / {ex.get('total', 0)}",
        t("遥测 / Telemetry (默认关闭，需手动开启):", "Telemetry (off by default, opt-in):"),
        f"  enabled={tm.get('enabled')} remote={tm.get('remote_enabled')} phase={tm.get('phase')}",
        t("vNext 本地信号 / vNext Local Signals:", "vNext Local Signals:"),
        f"  {', '.join(vn.get('names', []))} — "
        f"{t('默认关闭 / 仅本地 / 未写入远程 D1', 'default OFF / local only / not in remote D1')}",
        t("待确认就绪 / Readiness (待你确认的本地应用，纯计数):",
          "Readiness (pending owner-confirmed applies, counts only):"),
        f"  {t('生命周期', 'lifecycle')}: pending_apply={rd_lc.get('pending_apply', 0)} "
        f"(archive={rd_lc.get('archive_candidates', 0)} prune={rd_lc.get('prune_candidates', 0)})",
        f"  {t('对账导入', 'reconcile')}: import={rd_rec.get('import', 0)} "
        f"duplicate={rd_rec.get('duplicate', 0)} conflict={rd_rec.get('conflict', 0)}",
        f"  {t('近重复合并', 'merge')}: candidates={rd_mrg.get('candidates', 0)}",
        f"  {t('版本链', 'version-chain')}: topics={rd_ver.get('topics', 0)} "
        f"heads={rd_ver.get('heads', 0)} superseded={rd_ver.get('superseded', 0)}",
        t("提示: 归档/清理/修复/导入/合并都需你显式确认。",
          "Note: archive/prune/repair/import/merge all require your explicit confirmation."),
    ]
    return "\n".join(lines)


def render_dashboard_html(dashboard: dict[str, Any]) -> str:
    """Render the dashboard as a self-contained, fully-escaped HTML page."""
    body = html.escape(render_dashboard_text(dashboard))
    generated = html.escape(str(dashboard.get("generated_at", "")))
    title = html.escape(t("Engram 控制台", "Engram Dashboard"))
    return f"""<!doctype html>
<html lang="{html.escape(_lang_attr())}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 32px; color: #1f2937; }}
    main {{ max-width: 760px; }}
    h1 {{ font-size: 26px; margin-bottom: 4px; }}
    .meta {{ color: #64748b; margin-bottom: 20px; }}
    pre {{ background: #f8fafc; border: 1px solid #dbe3ee; border-radius: 8px; padding: 18px; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <div class="meta">{generated} — {html.escape(t('只读元数据，提案需显式确认', 'read-only metadata; proposals need explicit confirmation'))}</div>
    <pre>{body}</pre>
  </main>
</body>
</html>
"""


def _lang_attr() -> str:
    from .i18n import get_lang

    return "zh" if get_lang() == "zh" else "en"
