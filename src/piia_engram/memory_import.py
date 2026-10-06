"""Explicit import of other AI tools' memories (``engram import-memories``).

Engram never reads other AI tools' memory or rule files on its own: not at
server start, not on cold start, not on a read, not at session close-out. This
command is the one way in, and it is run by the Owner:

1. It always starts with a preview that writes nothing (a read-only store
   handle; no rows, no audit line, no receipt).
2. After a confirmation (an interactive "y" or ``--yes``) exactly the listed
   items are added to the review queue (staging tier); nothing is scanned
   again, and a source file that changed after the confirmation is marked in
   the receipt. Nothing becomes trusted memory without ``engram review``.
3. A run that wrote anything, or stopped early (an error part-way, or a full
   review queue), writes a receipt to ``<store>/import_receipts/<id>.json``
   (time, status, source files, count, entry ids, content hashes, what was not
   written; never the text) and an audit line naming the receipt.

Limits: rule-file sections at most 25 per run; memory files are limited by the
review queue's room (ENGRAM_REVIEW_QUEUE_MAX) and the import stops there.

Items already in the store (any tier, or moved to the overflow archive) and
items the Owner rejected before are skipped, so running it again imports
nothing twice. ``ENGRAM_RECONCILE=0`` or ``"reconcile_authorized": false`` in
``telemetry_config.json`` switch the whole thing off.
"""

from __future__ import annotations

import json
import secrets
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

COMMAND = "engram import-memories"
SOURCES = ("memories", "configs")
RECEIPT_DIR = "import_receipts"
_SOURCE_ALIASES = {
    "memories": ("memories",),
    "memory": ("memories",),
    "configs": ("configs",),
    "config": ("configs",),
    "all": SOURCES,
}
_SUMMARY_PREVIEW = 100

# Rule-file sections: at most 25 per run (reconcile_ai_configs max_imports).
# Memory files: no per-run cap, but the import stops when the review queue
# reaches ENGRAM_REVIEW_QUEUE_MAX and counts the rest as not written.
LIMITS_NOTE = (
    "limits: rule-file sections at most 25 per run (run again to continue); "
    "memory files are limited by the review queue's room (ENGRAM_REVIEW_QUEUE_MAX), "
    "the import stops when it is full"
)

USAGE = (
    "Usage:\n"
    "  engram import-memories [--source memories|configs|all] [--dry-run] [--yes] [--json]\n\n"
    "Imports memories from other AI tools into the review queue. Nothing is\n"
    "imported automatically; this command always lists the items first.\n"
    "  memories  AI tool memory files (~/.claude/projects/*/memory/*.md)\n"
    "  configs   AI tool rule files (CLAUDE.md, AGENTS.md, .cursorrules, ...)\n"
    "  --dry-run  list only; writes nothing\n"
    "  --yes      import without the interactive question\n"
    "  --json     machine-readable output\n"
    "Limits: rule-file sections at most 25 per run (run again to continue);\n"
    "memory files are limited by the review queue's room (ENGRAM_REVIEW_QUEUE_MAX):\n"
    "the import stops when the queue is full and counts what was not written.\n"
    "What you confirmed is exactly what is written; each import leaves a receipt\n"
    "in import_receipts/ in the store.\n"
    "Imported items wait in the review queue: engram review\n"
)


def _t(zh: str, en: str) -> str:
    try:
        from .i18n import t

        return t(zh, en)
    except Exception:  # pragma: no cover - i18n is always importable
        return en


def parse_sources(raw: str | None) -> tuple[str, ...]:
    """``memories`` / ``configs`` / ``all`` (default) -> source tuple."""
    if raw is None or not str(raw).strip():
        return SOURCES
    picked: list[str] = []
    for token in str(raw).split(","):
        name = token.strip().lower()
        if name not in _SOURCE_ALIASES:
            raise ValueError(f"unknown source: {token.strip()!r} (use memories, configs or all)")
        for source in _SOURCE_ALIASES[name]:
            if source not in picked:
                picked.append(source)
    return tuple(picked)


def switch_state() -> dict[str, Any]:
    """Is reading other AI tools' files allowed at all, and if not, why."""
    from .reconcile import ReconcileMixin, _reconcile_env

    enabled = ReconcileMixin._reconcile_authorized()
    reason = ""
    if not enabled:
        reason = "ENGRAM_RECONCILE=0" if _reconcile_env() == "off" else "reconcile_authorized=false"
    return {"enabled": enabled, "disabled_by": reason}


def _empty_payload(sources: tuple[str, ...], *, dry_run: bool) -> dict[str, Any]:
    state = switch_state()
    return {
        "schema": 1,
        "action": "import_memories",
        "dry_run": dry_run,
        "enabled": state["enabled"],
        "disabled_by": state["disabled_by"],
        "sources": list(sources),
        "count": 0,
        "imported": 0,
        "duplicates": 0,
        "queue_full": 0,
        "scanned_files": 0,
        "budget_exhausted": False,
        "items": [],
        "receipt": "",
    }


def _merge(payload: dict[str, Any], result: dict[str, Any]) -> None:
    payload["items"].extend(result.get("items") or [])
    for key in ("imported", "duplicates", "queue_full", "scanned_files"):
        payload[key] += int(result.get(key, 0) or 0)
    payload["duplicates"] += int(result.get("rejected_under_old_summary", 0) or 0)
    payload["budget_exhausted"] = payload["budget_exhausted"] or bool(result.get("budget_exhausted"))
    archived = result.get("overflow_archived_ids") or []
    if archived:
        payload.setdefault("overflow_archived_ids", []).extend(archived)


def plan(eng, sources: tuple[str, ...] = SOURCES, *, project_roots: tuple = ()) -> dict[str, Any]:
    """Preview what an import would add. Writes nothing.

    Pass a read-only handle (``Engram(read_only=True)``) for a zero-write
    preview: a writable handle still appends audit lines for its reads.
    """
    payload = _empty_payload(sources, dry_run=True)
    if not payload["enabled"]:
        return payload
    planned: set[str] = set()
    if "memories" in sources:
        result = eng.plan_memory_import()
        _merge(payload, result)
        planned |= {item["summary"] for item in result.get("items") or []}
    if "configs" in sources:
        _merge(payload, eng.plan_config_import(
            also_existing=frozenset(planned), extra_project_roots=tuple(project_roots),
        ))
    payload["count"] = len(payload["items"])
    return payload


def run(eng, sources: tuple[str, ...] = SOURCES, *, project_roots: tuple = ()) -> dict[str, Any]:
    """Plan and write in one step (no question asked): ``write_plan(eng, plan(...))``."""
    return write_plan(eng, plan(eng, sources, project_roots=project_roots))


def write_plan(eng, preview: dict[str, Any], *, command: str = COMMAND) -> dict[str, Any]:
    """Write exactly the confirmed plan; nothing is scanned again.

    ``preview`` is what :func:`plan` returned and the Owner confirmed. If a
    source file changed after that, the confirmed text is still what gets
    written, and the receipt marks the item ``source_changed``.
    """
    sources = tuple(preview.get("sources") or SOURCES)
    payload = _empty_payload(sources, dry_run=False)
    payload["scanned_files"] = int(preview.get("scanned_files", 0) or 0)
    payload["budget_exhausted"] = bool(preview.get("budget_exhausted"))
    if not preview.get("enabled", payload["enabled"]) or not payload["enabled"]:
        payload["enabled"] = False
        payload["disabled_by"] = payload["disabled_by"] or preview.get("disabled_by", "")
        return payload
    written = write_items(eng, preview.get("items") or [], sources=sources, command=command)
    payload.update(
        items=written["items"],
        count=len(preview.get("items") or []),
        imported=written["imported"],
        duplicates=int(preview.get("duplicates", 0) or 0) + written["duplicates"],
        queue_full=written["queue_full"],
        not_written=written["not_written"],
        source_changed=written["source_changed"],
        partial=written["partial"],
        error=written["error"],
        receipt=written["receipt"],
    )
    if written.get("overflow_archived_ids"):
        payload["overflow_archived_ids"] = written["overflow_archived_ids"]
    return payload


# Fields of a planned item that may appear in results and receipts (never the
# detail text or the absolute source path).
_PUBLIC_ITEM_FIELDS = ("source", "file", "label", "summary", "content_sha256", "status", "id")


def _source_changed(item: dict[str, Any]) -> bool:
    from .reconcile import _file_sha256

    path = item.get("path")
    planned = item.get("source_sha256")
    if not path or not planned:
        return False
    return _file_sha256(Path(path)) != planned


def _queue_room(eng) -> int:
    """Free places in the review queue before it reaches ENGRAM_REVIEW_QUEUE_MAX."""
    from . import capacity as _capacity

    limits = _capacity.limits_from_env()
    rows = eng.get_lessons(limit=None, _update_access=False, _migrate_fields=False)
    queued = sum(1 for row in rows if _capacity.pool_of(row) == _capacity.POOL_Q)
    return max(0, limits.review_queue_max - queued)


def write_items(
    eng,
    items: list[dict[str, Any]],
    *,
    sources,
    command: str = COMMAND,
    resource: str = "knowledge/import_memories",
    source_tool: str = "engram_cli",
    stop_when_queue_full: bool = True,
) -> dict[str, Any]:
    """Write these planned items to the review queue, then the receipt and audit.

    The one writer behind ``engram import-memories``, setup and
    ``engram reconcile apply``:

    - every row is a staging (review queue) lesson with the planned text;
    - with ``stop_when_queue_full`` it stops before the review queue passes
      ENGRAM_REVIEW_QUEUE_MAX, so an import never pushes queued items out;
      the rest is counted as ``queue_full`` / ``not_written``;
    - an error part-way still writes the receipt and the audit line for what
      was written (``partial`` with the error class), and is not raised.
    """
    from .reconcile import _insert_outcome
    from .storage import overflow_batch_scope

    result: dict[str, Any] = {
        "items": [], "imported": 0, "duplicates": 0, "queue_full": 0,
        "not_written": 0, "source_changed": 0, "partial": False, "error": "",
        "receipt": "",
    }
    record = ImportRecord(eng, sources=sources, command=command, resource=resource,
                          source_tool=source_tool)
    handled = 0
    try:
        with overflow_batch_scope() as batch:
            room = _queue_room(eng) if stop_when_queue_full else None
            for item in items:
                if room is not None and room <= 0:
                    break
                changed = _source_changed(item)
                outcome = eng.add_lesson(
                    item["summary"],
                    domain=item.get("domain", ""),
                    detail=item.get("detail", ""),
                    source_tool=item.get("source_tool") or "auto_reconcile",
                    tier="staging",
                    project_folder=item.get("project_folder") or None,
                )
                status, new_id = _insert_outcome(outcome)
                if status == "queue_full":
                    break
                handled += 1
                if status != "imported":
                    result["duplicates"] += 1
                    continue
                public = {key: item.get(key, "") for key in _PUBLIC_ITEM_FIELDS}
                public.update(status="imported", id=new_id, source_changed=changed)
                result["items"].append(public)
                result["imported"] += 1
                result["source_changed"] += int(changed)
                record.add(public)
                if room is not None:
                    room -= 1
            archived = list(batch.get("archived") or [])
        if archived:
            result["overflow_archived_ids"] = archived
            record.overflow_archived_ids = archived
    except Exception as exc:  # recorded below, never lost
        result["partial"] = True
        result["error"] = type(exc).__name__
    finally:
        result["not_written"] = len(items) - handled
        if not result["partial"]:
            result["queue_full"] = result["not_written"]
        record.duplicates = result["duplicates"]
        record.queue_full = result["queue_full"]
        record.not_written = result["not_written"]
        result["receipt"] = record.finish(error=result["error"])
    return result


class ImportRecord:
    """Collects what an import wrote; ``finish`` writes the receipt and audit line.

    Every path that imports outside content into the store records through
    this: ``write_items``, ``reconcile_apply.apply_reconcile``, the OpenClaw
    import, the legacy memory migration and the rule-file bootstrap helper.
    """

    def __init__(self, eng, *, sources, command: str, resource: str, source_tool: str):
        self.eng = eng
        self.sources = list(sources)
        self.command = command
        self.resource = resource
        self.source_tool = source_tool
        self.items: list[dict[str, Any]] = []
        self.duplicates = 0
        self.queue_full = 0
        self.not_written = 0
        self.overflow_archived_ids: list[str] = []
        self.receipt = ""

    def add(self, item: dict[str, Any]) -> None:
        self.items.append(item)

    def add_written(self, entry_id: str, *, source: str, file: str, summary: str, detail: str = "",
                    kind: str = "lesson") -> None:
        import hashlib

        self.items.append({
            "id": entry_id,
            "source": source,
            "file": file,
            "kind": kind,
            "content_sha256": hashlib.sha256(f"{summary}\n\n{detail}".encode("utf-8")).hexdigest(),
            "status": "imported",
        })

    def finish(self, *, error: str = "") -> str:
        payload = {
            "items": self.items,
            "imported": len(self.items),
            "sources": self.sources,
            "duplicates": self.duplicates,
            "queue_full": self.queue_full,
            "not_written": self.not_written,
            "partial": bool(error),
            "error": error,
            "overflow_archived_ids": self.overflow_archived_ids,
        }
        self.receipt = record_import(
            self.eng, payload, command=self.command, resource=self.resource,
            source_tool=self.source_tool,
        )
        return self.receipt


@contextmanager
def recording(eng, *, sources, command: str, resource: str, source_tool: str):
    """``with recording(...) as rec: ...`` -- the receipt and audit line are
    written on the way out, also when the block raises (then marked partial
    with the error class; the error is re-raised)."""
    record = ImportRecord(eng, sources=sources, command=command, resource=resource,
                          source_tool=source_tool)
    try:
        yield record
    except Exception as exc:
        record.finish(error=type(exc).__name__)
        raise
    else:
        record.finish()


def record_import(
    eng,
    payload: dict[str, Any],
    *,
    command: str = COMMAND,
    resource: str = "knowledge/import_memories",
    source_tool: str = "engram_cli",
) -> str:
    """Receipt plus one audit line; returns the receipt path relative to the store.

    A receipt is written when anything was imported, or when the import
    stopped early (partial, review queue full); the audit line is always
    written.
    """
    receipt_id = ""
    receipt = ""
    stopped = bool(payload.get("partial")) or int(payload.get("not_written", 0) or 0) > 0
    if payload["imported"] or stopped:
        receipt_id, path = write_receipt(eng.root, payload, command=command)
        receipt = f"{RECEIPT_DIR}/{path.name}"
    audit = getattr(eng, "_audit", None)
    if audit is not None:
        detail = (
            f"receipt={receipt_id or 'none'} imported={payload['imported']} "
            f"duplicates={payload.get('duplicates', 0)} queue_full={payload.get('queue_full', 0)} "
            f"not_written={payload.get('not_written', 0)} sources={','.join(payload['sources'])}"
        )
        if payload.get("partial"):
            detail += f" partial error={payload.get('error') or 'unknown'}"
        audit.log("import", resource, detail=detail, source_tool=source_tool)
    return receipt


def write_receipt(root: Path, payload: dict[str, Any], *, command: str = COMMAND) -> tuple[str, Path]:
    """Record an import: metadata only, never the imported text."""
    from .storage import _write_json

    now = datetime.now(timezone.utc)
    receipt_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    imported = [item for item in payload["items"] if item.get("status") == "imported"]
    files: dict[str, int] = {}
    for item in imported:
        files[item["file"]] = files.get(item["file"], 0) + 1
    receipt = {
        "schema": 1,
        "receipt_id": receipt_id,
        "created_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "command": command,
        "status": "partial" if payload.get("partial") or payload.get("not_written") else "complete",
        "error": payload.get("error", "") or "",
        "sources": payload["sources"],
        "imported": len(imported),
        "not_written": int(payload.get("not_written", 0) or 0),
        "tier": "staging",
        "files": [{"file": name, "count": count} for name, count in sorted(files.items())],
        "items": [
            {
                "id": item["id"],
                "source": item["source"],
                "file": item["file"],
                "content_sha256": item["content_sha256"],
                **({"kind": item["kind"]} if item.get("kind") and item["kind"] != "lesson" else {}),
                **({"source_changed": True} if item.get("source_changed") else {}),
            }
            for item in imported
        ],
        "source_changed": sum(1 for item in imported if item.get("source_changed")),
        "skipped": {
            "duplicates": int(payload.get("duplicates", 0) or 0),
            "queue_full": int(payload.get("queue_full", 0) or 0),
        },
    }
    if payload.get("overflow_archived_ids"):
        receipt["overflow_archived_ids"] = list(payload["overflow_archived_ids"])
    path = Path(root) / RECEIPT_DIR / f"{receipt_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, receipt)
    return receipt_id, path


def importable_summary(root: Path | None = None) -> dict[str, Any]:
    """Read-only count for ``engram doctor`` / ``engram status``."""
    state = switch_state()
    if not state["enabled"]:
        return {"enabled": False, "disabled_by": state["disabled_by"], "count": 0, "more": False}
    from .core import Engram

    reader = Engram(root=root, read_only=True) if root is not None else Engram(read_only=True)
    preview = plan(reader)
    by_source = {source: 0 for source in preview["sources"]}
    for item in preview["items"]:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    return {
        "enabled": True,
        "disabled_by": "",
        "count": preview["count"],
        "more": preview["budget_exhausted"],
        "by_source": by_source,
    }


def legacy_switch_notes(env=None) -> list[str]:
    """What the older import switches mean now (for ``engram doctor``)."""
    import os

    from .reconcile import _reconcile_config_value

    env = os.environ if env is None else env
    notes: list[str] = []
    sync = str(env.get("ENGRAM_MCP_STARTUP_SYNC", "") or "").strip()
    if sync:
        notes.append(
            f"ENGRAM_MCP_STARTUP_SYNC={sync} no longer has any effect: the MCP server "
            f"never imports at start ({COMMAND} does, when you run it). It can be removed."
        )
    reconcile = str(env.get("ENGRAM_RECONCILE", "") or "").strip()
    if reconcile:
        value = reconcile.lower()
        mode = ("off" if value in ("0", "false", "off", "no")
                else "on" if value in ("1", "true", "on", "yes") else "")
        if mode == "off":
            notes.append(
                f"ENGRAM_RECONCILE={reconcile}: other AI tools' files are never read; "
                f"{COMMAND} refuses and doctor/status show no count."
            )
        elif mode == "on":
            notes.append(
                f"ENGRAM_RECONCILE={reconcile} no longer turns on any automatic import; "
                f"only {COMMAND} reads other AI tools' files."
            )
        else:
            notes.append(
                f"ENGRAM_RECONCILE={reconcile} is not a recognised value and is ignored "
                "(0 switches reading other AI tools' files off)."
            )
    configured = _reconcile_config_value()
    if configured is True:
        notes.append(
            "reconcile_authorized=true (telemetry_config.json) no longer turns on any "
            f"automatic import; it only allows {COMMAND}."
        )
    elif configured is False:
        notes.append(
            "reconcile_authorized=false (telemetry_config.json): other AI tools' files "
            f"are never read; {COMMAND} refuses."
        )
    return notes


def importable_text(summary: dict[str, Any]) -> str:
    """One line for doctor / status."""
    if not summary.get("enabled"):
        return _t(
            f"读取其它 AI 工具的文件已关闭（{summary.get('disabled_by')}）",
            f"reading other AI tools' files is switched off ({summary.get('disabled_by')})",
        )
    count = summary.get("count", 0)
    if not count:
        return _t("其它 AI 工具里没有新的记忆可导入", "no new memories in other AI tools to import")
    shown = f"{count}+" if summary.get("more") else str(count)
    return _t(
        f"有 {shown} 条外部记忆可导入，运行 {COMMAND} 预览并导入",
        f"{shown} memories from other AI tools can be imported: run {COMMAND} to preview and import",
    )


# ---------------------------------------------------------------------------
# Owner-facing flow (CLI and setup)
# ---------------------------------------------------------------------------


def render_preview(payload: dict[str, Any]) -> str:
    if not payload["enabled"]:
        return _t(
            f"未导入：读取其它 AI 工具的文件已关闭（{payload['disabled_by']}）。"
            "如需导入，请去掉 ENGRAM_RECONCILE=0，或把 telemetry_config.json 里的 "
            "reconcile_authorized 改为 true。",
            f"Nothing imported: reading other AI tools' files is switched off "
            f"({payload['disabled_by']}). To import, remove ENGRAM_RECONCILE=0 or set "
            "reconcile_authorized to true in telemetry_config.json.",
        )
    lines = [_t(
        f"扫描了 {payload['scanned_files']} 个文件；可导入 {payload['count']} 条"
        f"（已有或已拒绝而跳过 {payload['duplicates']} 条）：",
        f"Scanned {payload['scanned_files']} files; {payload['count']} items can be imported "
        f"({payload['duplicates']} skipped as already present or rejected before):",
    )]
    for item in payload["items"]:
        summary = " ".join(str(item.get("summary") or "").split())
        if len(summary) > _SUMMARY_PREVIEW:
            summary = summary[: _SUMMARY_PREVIEW - 1] + "…"
        lines.append(f"  - [{item['source']}] {item['file']}: {summary}")
    if payload["budget_exhausted"]:
        lines.append(_t(
            "  （规则文件条目较多，本次只列出前一批；导入后再次运行可继续）",
            "  (more rule-file sections exist; run again after this import to continue)",
        ))
    return "\n".join(lines)


def public_view(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload without item bodies and absolute source paths (for printing)."""
    shown = dict(payload)
    shown["items"] = [
        {key: item[key] for key in (*_PUBLIC_ITEM_FIELDS, "source_changed") if key in item}
        for item in payload.get("items") or []
    ]
    return shown


def render_result(payload: dict[str, Any]) -> str:
    lines = [_t(
        f"已导入 {payload['imported']} 条，已放进待审区（审核前不生效）。",
        f"Imported {payload['imported']} items into the review queue (not trusted until reviewed).",
    )]
    if payload["duplicates"]:
        lines.append(_t(f"跳过重复或已拒绝：{payload['duplicates']} 条",
                        f"Skipped as duplicate or rejected before: {payload['duplicates']}"))
    if payload["queue_full"]:
        lines.append(_t(f"待审区已满，停止写入；未写入 {payload['queue_full']} 条（先审核再运行）",
                        f"Review queue full, stopped; {payload['queue_full']} not written (review, then run again)"))
    if payload.get("partial"):
        lines.append(_t(
            f"导入中途出错（{payload.get('error')}），未写入 {payload.get('not_written', 0)} 条；"
            "已写入的条目记在回执里。",
            f"The import stopped on an error ({payload.get('error')}); {payload.get('not_written', 0)} "
            "not written. What was written is in the receipt.",
        ))
    if payload.get("source_changed"):
        lines.append(_t(
            f"有 {payload['source_changed']} 条的源文件在确认后改动过，已按确认时的内容写入（回执里有标记）。",
            f"{payload['source_changed']} source files changed after you confirmed; the confirmed text "
            "was written (marked in the receipt).",
        ))
    archived = payload.get("overflow_archived_ids") or []
    if archived:
        lines.append(_t(
            f"容量上限移入溢出归档：{len(archived)} 条",
            f"Moved to the overflow archive by the capacity cap: {len(archived)}",
        ))
    if payload["receipt"]:
        lines.append(_t(f"导入回执：{payload['receipt']}（存储目录内）",
                        f"Receipt: {payload['receipt']} (in the store directory)"))
    lines.append(_t("用 engram review 批准。", "Approve them with engram review."))
    return "\n".join(lines)


def interactive_import(
    ask: Callable[[str], bool] | None,
    *,
    sources: tuple[str, ...] = SOURCES,
    out: Callable[[str], None] = print,
    root: Path | None = None,
    project_roots: tuple = (),
) -> dict[str, Any]:
    """Preview, then import after ``ask`` says yes. ``ask=None`` imports without asking.

    ``project_roots`` adds project folders whose rule files are read too
    (``engram setup`` passes the current directory).

    Returns the import payload, or the preview with ``"status"`` set to
    ``disabled`` / ``nothing_to_import`` / ``declined``.
    """
    from .core import Engram

    reader = Engram(root=root, read_only=True) if root is not None else Engram(read_only=True)
    preview = plan(reader, sources, project_roots=project_roots)
    out(render_preview(preview))
    if not preview["enabled"]:
        preview["status"] = "disabled"
        return preview
    if not preview["count"]:
        preview["status"] = "nothing_to_import"
        return preview
    question = _t(
        f"把以上 {preview['count']} 条导入待审区吗？",
        f"Import these {preview['count']} items into the review queue?",
    )
    if ask is not None and not ask(question):
        out(_t("未导入，没有写入任何内容。", "Not imported; nothing was written."))
        preview["status"] = "declined"
        return preview
    writer = Engram(root=root) if root is not None else Engram()
    result = write_plan(writer, preview)  # exactly the confirmed list
    result["status"] = "imported"
    out(render_result(result))
    return result


def _tty_ask(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.startswith("y")


def run_cli(args: list[str]) -> int:
    """``engram import-memories``. 0 = done or previewed, 1 = not imported, 2 = usage."""
    dry_run = yes = as_json = False
    raw_source: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"-h", "--help"}:
            print(USAGE, end="")
            return 0
        if arg == "--dry-run":
            dry_run = True
        elif arg in {"--yes", "-y"}:
            yes = True
        elif arg == "--json":
            as_json = True
        elif arg == "--source":
            if i + 1 >= len(args):
                print("--source needs a value: memories, configs or all", file=sys.stderr)
                return 2
            raw_source = args[i + 1]
            i += 1
        elif arg.startswith("--source="):
            raw_source = arg.split("=", 1)[1]
        else:
            print(f"Unknown option: {arg}\n", file=sys.stderr)
            print(USAGE, end="", file=sys.stderr)
            return 2
        i += 1
    try:
        sources = parse_sources(raw_source)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if as_json:
        from .core import Engram

        preview = plan(Engram(read_only=True), sources)
        if dry_run or not preview["enabled"] or not preview["count"]:
            print(json.dumps(public_view(preview), ensure_ascii=False, indent=2))
            return 0 if preview["enabled"] else 1
        if not yes:
            shown = public_view(preview)
            shown["requires_confirmation"] = True
            print(json.dumps(shown, ensure_ascii=False, indent=2))
            return 1
        result = write_plan(Engram(), preview)
        print(json.dumps(public_view(result), ensure_ascii=False, indent=2))
        return 1 if result.get("partial") else 0

    if dry_run:
        from .core import Engram

        preview = plan(Engram(read_only=True), sources)
        print(render_preview(preview))
        if preview["enabled"]:
            print(_t("预览：没有写入任何内容。导入请去掉 --dry-run。",
                     "Preview only: nothing was written. Drop --dry-run to import."))
        return 0 if preview["enabled"] else 1

    if yes:
        ask = None
    elif sys.stdin is not None and sys.stdin.isatty():
        ask = _tty_ask
    else:
        def ask(question: str) -> bool:
            print(_t("不是交互终端：加 --yes 才会导入。没有写入任何内容。",
                     "Not an interactive terminal: add --yes to import. Nothing was written."))
            return False

    result = interactive_import(ask, sources=sources)
    status = result.get("status")
    if status in {"disabled", "declined"} or result.get("partial"):
        return 1
    return 0
