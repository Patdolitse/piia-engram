"""Shared Dock write-contract core.

Pure-ish functions that BOTH the CLI ``dock-*`` handlers and the HTTP routes call,
so they behave identically. No env reads, no printing, no HTTP/CLI parsing — each
takes an already-opened ``Engram`` and returns a receipt dict (or a structured
``{"ok": False, "error": ...}``). The caller owns authorization (CLI = local owner;
HTTP = authenticated dock session) and output formatting.
"""

from __future__ import annotations

from typing import Any


def archive_entry(eng: Any, item_id: str) -> dict:
    """Reversible soft-archive of one lesson/decision/playbook by id.

    Moves the entry to the ``archived`` tier — a deliberate, REVERSIBLE write
    (recover via the restore contract); nothing is deleted. ``allow_verified=True``
    so even a verified entry can be archived (still fully reversible).
    """
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"ok": False, "error": "id is required"}
    try:
        result = eng.soft_archive_knowledge_tier(item_id, allow_verified=True)
    except Exception as exc:  # never crash the caller — return a usable error
        return {"ok": False, "error": str(exc)}
    if isinstance(result, dict) and result.get("error"):
        return {"ok": False, "error": str(result["error"])}
    return {"ok": True, "reversible": True, "changed": True, "result": result}


def restore_entry(eng: Any, item_id: str) -> dict:
    """Reverse a soft-archive: move one ``archived`` entry back to its prior tier.

    The inverse of :func:`archive_entry` and itself reversible (re-archivable);
    nothing is hard-deleted. Returns a receipt dict or a structured
    ``{"ok": False, "error": ...}`` (id absent, entry not found, or restore failed).
    """
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"ok": False, "error": "id is required"}
    try:
        result = eng.restore_lifecycle_archive(item_id)
    except Exception as exc:  # never crash the caller — return a usable error
        return {"ok": False, "error": str(exc)}
    if isinstance(result, dict) and result.get("error"):
        return {"ok": False, "error": str(result["error"])}
    return {"ok": True, "reversible": True, "changed": True, "result": result}


def _mem_title(kind: str, it: dict) -> str:
    if kind == "decision":
        q = (it.get("question") or it.get("title") or "").strip()
        c = (it.get("choice") or "").strip()
        return f"{q} → {c}" if q and c else (q or c or "(decision)")
    return (it.get("summary") or "(lesson)").strip()


def _mem_copy(kind: str, it: dict) -> str:
    if kind == "decision":
        parts = [_mem_title(kind, it)]
        reasoning = (it.get("reasoning") or "").strip()
        if reasoning:
            parts.append(reasoning)
        return "\n".join(parts)
    parts = [(it.get("summary") or "").strip()]
    detail = (it.get("detail") or "").strip()
    if detail:
        parts.append(detail)
    return "\n".join([p for p in parts if p])


def _labeling_projection(item: dict) -> dict:
    labeling = item.get("labeling") if isinstance(item, dict) else None
    if not isinstance(labeling, dict):
        return {}
    out: dict = {}
    for key in ("source_kind", "annotation_quality", "validation_state"):
        value = labeling.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    signals = labeling.get("signals")
    if isinstance(signals, list):
        clean = [str(s) for s in signals if str(s)]
        if clean:
            out["signals"] = clean[:12]
    return out


def dock_memory_list_payload(eng: Any, *, limit: int = 0) -> dict:
    """Zero-write list of all ACTIVE lessons/decisions for the dock 记忆 view.

    Takes an ALREADY-OPENED read_only Engram (zero-write). Mirrors the dock-list
    contract: excludes the archived tier and non-active status; projects
    id/kind/tier/title/copy/labeling + the editable ``fields`` shape the detail
    panel edits. Caller adds any framing (CLI adds engram_dir; HTTP omits it).
    """
    try:
        results: list[dict] = []
        for kind, fname in (("lesson", "lessons.json"), ("decision", "decisions.json")):
            for it in eng._read_entries(eng._knowledge_dir / fname, kind):
                if it.get("tier") == "archived":
                    continue  # belongs to dock-archived / restore
                if (it.get("status") or "active") != "active":
                    continue  # superseded / outdated — not active memory
                entry = {
                    "kind": kind,
                    "title": _mem_title(kind, it),
                    "tier": it.get("tier", "") or "",
                    "id": it.get("id", ""),
                    "copy": _mem_copy(kind, it),
                }
                labeling = _labeling_projection(it)
                if labeling:
                    entry["labeling"] = labeling
                if kind == "lesson":
                    entry["fields"] = {
                        "summary": it.get("summary", "") or "",
                        "detail": it.get("detail", "") or "",
                    }
                else:
                    entry["fields"] = {
                        "question": it.get("question") or it.get("title") or "",
                        "choice": it.get("choice", "") or "",
                        "reasoning": it.get("reasoning", "") or "",
                    }
                results.append(entry)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "count": 0, "results": []}
    if limit and len(results) > limit:
        results = results[-limit:]  # most-recent N (entries append-ordered)
    return {"ok": True, "read_only": True, "count": len(results), "results": results}


def dock_archived_list_payload(eng: Any) -> dict:
    """Zero-write list of archived (soft-deleted) lessons/decisions for the 回收站.

    Takes an ALREADY-OPENED read_only Engram (zero-write). The inverse of the active
    set: lists exactly the ``archived`` tier (id/kind/title) so the dock can offer
    one-click restore. Caller adds any framing (CLI adds engram_dir; HTTP omits it,
    never leaking the server's local store path to the browser).
    """
    try:
        results: list[dict] = []
        for kind, fname in (("lesson", "lessons.json"), ("decision", "decisions.json")):
            for it in eng._read_entries(eng._knowledge_dir / fname, kind):
                if it.get("tier") == "archived":
                    results.append({
                        "kind": kind,
                        "title": _mem_title(kind, it),
                        "id": it.get("id", ""),
                    })
    except Exception as exc:
        return {"ok": False, "error": str(exc), "count": 0, "results": []}
    return {"ok": True, "read_only": True, "count": len(results), "results": results}


_EDITABLE_FIELDS = {"summary", "detail", "question", "choice", "reasoning"}


def update_entry(eng: Any, item_id: str, raw_updates: dict) -> dict:
    """Edit one entry's whitelisted content fields by id (owner-confirmed at the
    call site). Only ``_EDITABLE_FIELDS`` are honored; a primary field
    (summary/question/choice) may be edited but never blanked (would gut the
    entry). Returns a receipt dict or a structured ``{"ok": False, "error": ...}``.

    Each failure carries ``error_kind`` so every transport maps it to its own
    protocol: ``"validation"`` is a bad request (HTTP 400, CLI exit 2) — the fields
    were absent, empty, or unknown; ``"write"`` is a valid request that couldn't
    land (HTTP 400, CLI exit 1) — entry not found or the write itself failed.
    """
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"ok": False, "error": "id is required", "error_kind": "validation"}
    if not isinstance(raw_updates, dict):
        return {"ok": False, "error": "updates must be an object", "error_kind": "validation"}
    updates = {
        k: v.strip() for k, v in raw_updates.items()
        if k in _EDITABLE_FIELDS and isinstance(v, str)
    }
    if not updates:
        return {"ok": False, "error": "no valid fields to update", "error_kind": "validation"}
    for k in ("summary", "question", "choice"):
        if k in updates and not updates[k]:
            return {"ok": False, "error": f"{k} cannot be empty", "error_kind": "validation"}
    if updates.get("question"):
        updates["title"] = updates["question"]  # legacy: keep title in sync for dedup/report
    try:
        result = eng.update_knowledge(item_id, updates)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_kind": "write"}
    if isinstance(result, dict) and result.get("error"):
        return {"ok": False, "error": str(result["error"]), "error_kind": "write"}
    return {"ok": True, "changed": True, "result": result}


def dock_resume_payload(eng: Any, *, project: str = "", budget: int = 2000) -> dict:
    """接续 (the soul): a zero-write, paste-ready cross-tool resume brief — the
    「智能标准包」(identity + current project + recent key decisions). Takes an
    already-opened read_only Engram; reuses ``get_resume_brief``; never mutates.
    """
    try:
        brief = eng.get_resume_brief(project_folder=project, token_budget=budget)
    except Exception as exc:  # never crash the dock — return a usable error
        return {"ok": False, "error": str(exc), "markdown": ""}
    markdown = brief.get("markdown", "") if isinstance(brief, dict) else str(brief)
    return {"ok": True, "read_only": True, "markdown": markdown}


def dock_playbook_list_payload(eng: Any) -> dict:
    """Zero-write list of active playbooks for the Playbooks view (Codex design pass).

    Playbooks are a SEPARATE per-id-file subsystem (not lessons.json/decisions.json),
    so this has its own core. Takes an ALREADY-OPENED read_only Engram and calls
    ``get_playbooks(_update_access=False)`` — read_only suppresses the audit log and
    _update_access=False suppresses the last_reviewed write-back, together guaranteeing
    zero write. Projects only display fields; the raw ``scope`` (which may carry a
    ``project_folder`` local path) is reduced to a ``scope_type`` label so the server's
    filesystem never leaks to the browser. v1 is view-only — no edit/archive here.
    """
    try:
        playbooks = eng.get_playbooks(limit=None, _update_access=False) or []
        results: list[dict] = []
        for pb in playbooks:
            if not isinstance(pb, dict):
                continue
            scope = pb.get("scope") if isinstance(pb.get("scope"), dict) else {}
            scope_type = str(scope.get("type") or "global")
            if scope_type == "shared":
                project_count = len(scope.get("project_ids") or [])
            elif scope_type == "project":
                project_count = 1
            else:
                project_count = 0
            steps = [
                {"action": str(s.get("action", "") or ""), "detail": str(s.get("detail", "") or "")}
                for s in (pb.get("steps") or []) if isinstance(s, dict)
            ]
            results.append({
                "id": str(pb.get("id", "") or ""),
                "title": str(pb.get("title", "") or ""),
                "description": str(pb.get("description", "") or ""),
                "outcome": str(pb.get("outcome", "") or ""),
                "steps": steps,
                "pitfalls": [str(p) for p in (pb.get("pitfalls") or []) if str(p)],
                "preconditions": [str(p) for p in (pb.get("preconditions") or []) if str(p)],
                "triggers": [str(t) for t in (pb.get("triggers") or []) if str(t)],
                "domain": str(pb.get("domain", "") or ""),
                "status": str(pb.get("status", "active") or "active"),
                "version": pb.get("version", 1),
                "scope_type": scope_type,       # global / project / shared — never a path
                "project_count": project_count,
                "last_reviewed": str(pb.get("last_reviewed", "") or ""),
            })
    except Exception as exc:
        return {"ok": False, "error": str(exc), "count": 0, "results": []}
    return {"ok": True, "read_only": True, "count": len(results), "results": results}


def dock_status_payload(eng: Any) -> dict:
    """Zero-write, metadata-only status for the 概览 page (Codex ship-line task 3).

    Reuses ``build_status`` with the Dock server's OWN root (not the ambient
    ENGRAM_DIR) and probe=False (no MCP subprocess). Projects only counts/flags via an
    explicit allowlist — never the local store path (build_status carries ``root`` +
    ``storage.path``, which must never reach the browser).
    """
    from piia_engram.status_report import build_status
    try:
        status = build_status(root=eng.root, probe=False)
    except Exception as exc:  # never crash the dock — return a usable error
        return {"ok": False, "error": str(exc)}

    def _d(key: str) -> dict:
        value = status.get(key)
        return value if isinstance(value, dict) else {}

    knowledge, storage, sessions = _d("knowledge"), _d("storage"), _d("sessions")
    clients, governance, telemetry, encoding = _d("clients"), _d("governance"), _d("telemetry"), _d("encoding")
    latest = sessions.get("latest") if isinstance(sessions.get("latest"), dict) else {}
    return {
        "ok": True,
        "read_only": True,
        "status": {
            "version": str(status.get("version", "") or ""),
            "platform": str(status.get("platform", "") or ""),
            "knowledge": {
                k: int(knowledge.get(k, 0) or 0)
                for k in ("total", "verified", "staging", "archived", "lessons", "decisions", "playbooks")
            },
            "storage": {
                "file_count": int(storage.get("file_count", 0) or 0),
                "bytes": int(storage.get("bytes", 0) or 0),
                "skipped": int(storage.get("skipped", 0) or 0),
            },
            "sessions": {
                "count": int(sessions.get("count", 0) or 0),
                "latest_tool": str(latest.get("tool", "") or ""),
                "latest_at": str(latest.get("modified_at", "") or ""),
            },
            "clients": {
                "configured": int(clients.get("configured", 0) or 0),
                "total": int(clients.get("total", 0) or 0),
            },
            "governance_enabled": bool(governance.get("enabled")),
            "telemetry_enabled": bool(telemetry.get("local_enabled")),
            "encoding_ok": bool(encoding.get("ok")),
            "warnings": [str(w) for w in (status.get("warnings") or [])],
        },
    }
