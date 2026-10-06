"""Explicit import of other AI tools' memories (``engram import-memories``).

Engram never reads other AI tools' memory or rule files on its own: not at
server start, not on cold start, not on a read, not at session close-out. This
command is the one way in, and it is run by the Owner:

1. It always starts with a preview that writes nothing (a read-only store
   handle; no rows, no audit line, no receipt).
2. After a confirmation (an interactive "y" or ``--yes``) every listed item is
   added to the review queue (staging tier) -- nothing becomes trusted memory
   without ``engram review``.
3. A confirmed run that imported anything writes a receipt to
   ``<store>/import_receipts/<id>.json`` (time, source files, count, entry ids,
   content hashes; never the text) and an audit line naming the receipt.

Items already in the store (any tier, or moved to the overflow archive) and
items the Owner rejected before are skipped, so running it again imports
nothing twice. ``ENGRAM_RECONCILE=0`` or ``"reconcile_authorized": false`` in
``telemetry_config.json`` switch the whole thing off.
"""

from __future__ import annotations

import json
import secrets
import sys
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


def plan(eng, sources: tuple[str, ...] = SOURCES) -> dict[str, Any]:
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
        _merge(payload, eng.plan_config_import(also_existing=frozenset(planned)))
    payload["count"] = len(payload["items"])
    return payload


def run(eng, sources: tuple[str, ...] = SOURCES) -> dict[str, Any]:
    """Add the items to the review queue, then write the receipt and audit line."""
    payload = _empty_payload(sources, dry_run=False)
    if not payload["enabled"]:
        return payload
    if "memories" in sources:
        _merge(payload, eng.reconcile_memories())
    if "configs" in sources:
        _merge(payload, eng.reconcile_ai_configs())
    payload["count"] = len(payload["items"])
    receipt_id = ""
    if payload["imported"]:
        receipt_id, path = write_receipt(eng.root, payload)
        payload["receipt"] = f"{RECEIPT_DIR}/{path.name}"
    audit = getattr(eng, "_audit", None)
    if audit is not None:
        audit.log(
            "import",
            "knowledge/import_memories",
            detail=(
                f"receipt={receipt_id or 'none'} imported={payload['imported']} "
                f"duplicates={payload['duplicates']} queue_full={payload['queue_full']} "
                f"sources={','.join(sources)}"
            ),
            source_tool="engram_cli",
        )
    return payload


def write_receipt(root: Path, payload: dict[str, Any]) -> tuple[str, Path]:
    """Record a confirmed import: metadata only, never the imported text."""
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
        "command": COMMAND,
        "sources": payload["sources"],
        "imported": len(imported),
        "tier": "staging",
        "files": [{"file": name, "count": count} for name, count in sorted(files.items())],
        "items": [
            {
                "id": item["id"],
                "source": item["source"],
                "file": item["file"],
                "content_sha256": item["content_sha256"],
            }
            for item in imported
        ],
        "skipped": {
            "duplicates": payload["duplicates"],
            "queue_full": payload["queue_full"],
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


def render_result(payload: dict[str, Any]) -> str:
    lines = [_t(
        f"已导入 {payload['imported']} 条到待审区（未生效，需审核）。",
        f"Imported {payload['imported']} items into the review queue (not trusted until reviewed).",
    )]
    if payload["duplicates"]:
        lines.append(_t(f"跳过重复或已拒绝：{payload['duplicates']} 条",
                        f"Skipped as duplicate or rejected before: {payload['duplicates']}"))
    if payload["queue_full"]:
        lines.append(_t(f"待审区已满未导入：{payload['queue_full']} 条（先审核再运行）",
                        f"Not imported, review queue full: {payload['queue_full']} (review, then run again)"))
    archived = payload.get("overflow_archived_ids") or []
    if archived:
        lines.append(_t(
            f"容量上限移入溢出归档：{len(archived)} 条",
            f"Moved to the overflow archive by the capacity cap: {len(archived)}",
        ))
    if payload["receipt"]:
        lines.append(_t(f"导入回执：{payload['receipt']}（存储目录内）",
                        f"Receipt: {payload['receipt']} (in the store directory)"))
    lines.append(_t("审核：engram review", "Review them: engram review"))
    return "\n".join(lines)


def interactive_import(
    ask: Callable[[str], bool] | None,
    *,
    sources: tuple[str, ...] = SOURCES,
    out: Callable[[str], None] = print,
    root: Path | None = None,
) -> dict[str, Any]:
    """Preview, then import after ``ask`` says yes. ``ask=None`` imports without asking.

    Returns the import payload, or the preview with ``"status"`` set to
    ``disabled`` / ``nothing_to_import`` / ``declined``.
    """
    from .core import Engram

    reader = Engram(root=root, read_only=True) if root is not None else Engram(read_only=True)
    preview = plan(reader, sources)
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
    result = run(writer, sources)
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
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            return 0 if preview["enabled"] else 1
        if not yes:
            preview["requires_confirmation"] = True
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(run(Engram(), sources), ensure_ascii=False, indent=2))
        return 0

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
    if status == "disabled":
        return 1
    if status == "declined":
        return 1
    return 0
