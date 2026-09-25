"""Owner review verbs for the local CLI: ``engram review export | apply | tombstone``.

These are the Owner's side of strict mode: agents propose over MCP, the Owner
(or the lane, with the Owner's go) decides here. Nothing here is reachable over
MCP. Every applying run needs ``--operator <name>`` and leaves an audit receipt
(operator, TTY flag, parent process, host, time, counts); output is ids and
counts only, never stored text. Without ``--yes`` every verb is a dry run on a
read-only store.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import tombstones as _tombstones
from .staging_review import batch_review_staging

MEM_TYPES = ("rule", "preference", "project_fact", "lesson", "decision")
_TYPE_ORDER = {t: i for i, t in enumerate(("rule", "preference", "decision", "project_fact", "lesson"))}
_DETAIL_CAP = 500


def _engram(read_only: bool):
    from .core import Engram

    return Engram(read_only=read_only)


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _option(args: list[str], name: str) -> str:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return ""


def _parent_name(ppid: int) -> str:
    """Best-effort parent process name; stdlib only, "unknown" on any failure."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {ppid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
            if out.startswith('"'):
                return out.split('","')[0].strip('"')
            return "unknown"
        comm = Path(f"/proc/{ppid}/comm")
        if comm.is_file():
            return comm.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return "unknown"


def _attribution(args: list[str]) -> tuple[dict | None, str]:
    """(receipt fields, error). --yes needs --operator, TTY or not."""
    operator = _option(args, "--operator").strip()
    if not operator:
        return None, "Refusing to apply without --operator <name> (every applying run is attributed)."
    ppid = os.getppid()
    return {
        "operator": operator,
        "isatty": bool(sys.stdin.isatty()) if sys.stdin else False,
        "ppid": ppid,
        "parent_name": _parent_name(ppid),
        "host": platform.node(),
    }, ""


def _receipt(eng, verb: str, attribution: dict, counts: dict) -> None:
    eng._audit.log(
        "owner_cli",
        f"review/{verb}",
        detail=json.dumps(counts, sort_keys=True),
        source_tool="cli",
        extra={"verb": verb, **attribution, "counts": counts},
    )


def _type_label(row: dict) -> str:
    for part in str(row.get("domain") or "").split(","):
        part = part.strip()
        if part.startswith("type:"):
            return part[len("type:"):]
    return ""


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _pending(eng) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    for kind, items in (
        ("lesson", eng.get_lessons(limit=None, _update_access=False)),
        ("decision", eng.get_decisions(limit=None, _update_access=False)),
    ):
        rows.extend((kind, row) for row in items if row.get("tier") == "staging")
    return rows


def _card(n: int, kind: str, row: dict, root) -> list[str]:
    flags = []
    if row.get("reproposal_of_rejected"):
        flags.append(f"re-proposal of rejected {row['reproposal_of_rejected']}")
    near = _tombstones.near(root, kind, row)
    if near is not None:
        flags.append(f"near-rejected {near.get('id')}")
    mem_type = _type_label(row)
    if not mem_type:
        flags.append("missing type")
    detail = str(row.get("detail") or row.get("reasoning") or "").strip()
    if not detail:
        flags.append("missing rationale")
    claim = row.get("summary") if kind == "lesson" else f"{row.get('question', '')} -> {row.get('choice', '')}"
    scope = f"project:{row.get('project') or row.get('project_id')}" if row.get("project_id") else "global"
    lines = [
        f"### {n}. {kind} `{row.get('id')}`" + (f"  [{'; '.join(flags)}]" if flags else ""),
        "",
        f"- type: {mem_type or 'MISSING'}",
        f"- scope: {scope}",
        f"- claim: {claim}",
    ]
    if detail:
        lines.append(f"- why / detail: {detail[:_DETAIL_CAP]}")
    lines.append(f"- source: {row.get('source_tool') or 'unknown'}, queued {row.get('queued_at') or row.get('timestamp') or '?'}")
    lines.append("")
    return lines


def _sort_key(item: tuple[str, dict]) -> tuple:
    kind, row = item
    mem_type = _type_label(row) or ("decision" if kind == "decision" else "")
    return (
        0 if row.get("reproposal_of_rejected") else 1,
        _TYPE_ORDER.get(mem_type, len(_TYPE_ORDER)),
        str(row.get("queued_at") or row.get("timestamp") or ""),
        str(row.get("id")),
    )


def run_export(args: list[str]) -> int:
    out = _option(args, "--out")
    if not out:
        print("Usage: engram review export --out <dir>")
        return 2
    eng = _engram(read_only=True)
    pending = sorted(_pending(eng), key=_sort_key)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Engram review file",
        "",
        f"Pending proposals: {len(pending)}. Mark each id in marks.json as approve | reject | "
        f"edit-type:<{'|'.join(MEM_TYPES)}>, then run:",
        "`engram review apply marks.json` (dry run), then add `--operator <name> --yes`.",
        "",
    ]
    for n, (kind, row) in enumerate(pending, 1):
        lines.extend(_card(n, kind, row, eng.root))
    (out_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "ids.json").write_text(
        json.dumps([row.get("id") for _kind, row in pending], indent=1), encoding="utf-8"
    )
    _print({"status": "exported", "pending": len(pending), "out": str(out_dir)})
    return 0


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def _parse_marks(path: Path) -> tuple[list[dict], str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"cannot read marks file: {exc}"
    if not isinstance(raw, list):
        return [], "marks file must be a JSON array of {id, mark}"
    marks = []
    for entry in raw:
        if not isinstance(entry, dict):
            return [], "every mark must be an object"
        item_id = str(entry.get("id") or "").strip()
        mark = str(entry.get("mark") or entry.get("action") or "").strip().lower()
        if not item_id:
            return [], "a mark has no id"
        if mark in ("approve", "reject"):
            marks.append({"id": item_id, "mark": mark})
        elif mark.startswith("edit-type:"):
            mem_type = mark[len("edit-type:"):]
            if mem_type not in MEM_TYPES:
                return [], f"unknown type {mem_type!r} for {item_id}; use one of {', '.join(MEM_TYPES)}"
            marks.append({"id": item_id, "mark": "edit-type", "type": mem_type})
        else:
            return [], f"unknown mark {mark!r} for {item_id}"
    return marks, ""


def _relabel(domain: str, mem_type: str) -> str:
    parts = [p.strip() for p in str(domain or "").split(",") if p.strip() and not p.strip().startswith("type:")]
    return ",".join(parts + [f"type:{mem_type}"])


def run_apply(args: list[str]) -> int:
    paths = [a for a in args if not a.startswith("--") and a != _option(args, "--operator")]
    if not paths:
        print("Usage: engram review apply <marks.json> [--operator <name> --yes]")
        return 2
    marks, error = _parse_marks(Path(paths[0]))
    if error:
        print(error)
        return 2
    decisions = [{"id": m["id"], "action": m["mark"]} for m in marks if m["mark"] in ("approve", "reject")]
    edits = [m for m in marks if m["mark"] == "edit-type"]

    if "--yes" not in args:
        eng = _engram(read_only=True)
        preview = batch_review_staging(eng, decisions, dry_run=True, limit=len(decisions) or 1)
        missing = [m["id"] for m in edits if eng._find_item_by_id(m["id"])[1] is None]
        _print({
            "status": "dry_run",
            "counts": {**preview["counts"], "edit_type": len(edits), "edit_type_not_found": len(missing)},
            "items": [{"id": i["id"], "action": i["action"], "status": i["status"]} for i in preview["items"]],
            "not_found": missing,
        })
        return 0

    attribution, error = _attribution(args)
    if error:
        print(error)
        return 2
    eng = _engram(read_only=False)
    result = batch_review_staging(
        eng, decisions, dry_run=False, confirm=True, via=f"cli:{attribution['operator']}",
        limit=len(decisions) or 1,
    )
    edited, edit_failed = 0, []
    for m in edits:
        kind, row = eng._find_item_by_id(m["id"])
        if row is None or kind not in ("lesson", "decision"):
            edit_failed.append(m["id"])
            continue
        update = eng.update_lesson if kind == "lesson" else eng.update_decision
        outcome = update(m["id"], {"domain": _relabel(row.get("domain", ""), m["type"])})
        if isinstance(outcome, dict) and outcome.get("error"):
            edit_failed.append(m["id"])
        else:
            edited += 1
    counts = {**result["counts"], "edit_type": edited, "edit_type_failed": len(edit_failed)}
    _receipt(eng, "apply", attribution, counts)
    _print({
        "status": "applied",
        "counts": counts,
        "items": [{"id": i["id"], "action": i["action"], "status": i["status"]} for i in result["items"]],
        "edit_type_failed": edit_failed,
    })
    return 0


# ---------------------------------------------------------------------------
# tombstone backfill
# ---------------------------------------------------------------------------

_BACKFILL_ARCHIVE_REASONS = {"retired_overflow", "retired_grace"}


def _backfill_ids(path: Path) -> tuple[list[str], str, str]:
    try:
        data = path.read_bytes()
        raw = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as exc:
        return [], "", f"cannot read ids file: {exc}"
    if isinstance(raw, dict):
        ids = [str(v.get("id")) for v in raw.values() if isinstance(v, dict) and v.get("id")]
    elif isinstance(raw, list):
        ids = [str(v) for v in raw if v]
    else:
        return [], "", "ids file must be a JSON array of ids or a {n: {id: ...}} map"
    return ids, hashlib.sha256(data).hexdigest()[:16], ""


def _classify(eng, item_id: str) -> tuple[str, str, dict | None]:
    """('tombstone'|'already'|'refused_active'|'not_found', kind, row)."""
    if _tombstones.by_id(eng.root, item_id) is not None:
        return "already", "", None
    for kind, name in (("lesson", "lessons.json"), ("decision", "decisions.json")):
        for row in eng._read_entries(eng._knowledge_dir / name, kind, migrate=False):
            if row.get("id") != item_id:
                continue
            if (row.get("status") or "active") == "active":
                return "refused_active", kind, row
            return "tombstone", kind, row
    for kind in ("lesson", "decision"):
        for row in eng._archive_rows_cached(kind):
            if row.get("id") == item_id:
                if row.get("overflow_archive_reason") in _BACKFILL_ARCHIVE_REASONS:
                    return "tombstone", kind, row
                return "refused_active", kind, row
    return "not_found", "", None


def run_tombstone(args: list[str]) -> int:
    ids_file = _option(args, "--ids-file")
    if not ids_file:
        print("Usage: engram review tombstone --ids-file <file> [--go-ref <ref>] [--operator <name> --yes]")
        return 2
    ids, digest, error = _backfill_ids(Path(ids_file))
    if error:
        print(error)
        return 2
    applying = "--yes" in args
    attribution = None
    if applying:
        attribution, error = _attribution(args)
        if error:
            print(error)
            return 2
    eng = _engram(read_only=not applying)
    buckets: dict[str, list[str]] = {"tombstone": [], "already": [], "refused_active": [], "not_found": []}
    plan = []
    for item_id in ids:
        verdict, kind, row = _classify(eng, item_id)
        buckets[verdict].append(item_id)
        if verdict == "tombstone":
            plan.append((kind, row))
    counts: dict[str, Any] = {k: len(v) for k, v in buckets.items()}
    if not applying:
        _print({"status": "dry_run", "counts": counts, "refused_active": buckets["refused_active"],
                "not_found": buckets["not_found"]})
        return 0
    go_ref = _option(args, "--go-ref") or "none"
    via = f"backfill:{digest}:{go_ref}:op={attribution['operator']}"
    written = sum(1 for kind, row in plan if _tombstones.append(eng.root, kind, row, via=via) is not None)
    counts["written"] = written
    _receipt(eng, "tombstone", attribution, counts)
    _print({"status": "applied", "counts": counts, "refused_active": buckets["refused_active"],
            "not_found": buckets["not_found"]})
    return 0


VERBS = {"export": run_export, "apply": run_apply, "tombstone": run_tombstone}
