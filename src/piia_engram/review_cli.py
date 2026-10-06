"""Owner review verbs for the local CLI: ``engram review export | apply | tombstone``
(and ``interactive``, in ``review_interactive``, which applies through ``apply_marks``).

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
from . import dedup_review as _dedup_review
from . import write_provenance as _write_provenance
from .staging_review import batch_review_staging

MEM_TYPES = ("rule", "preference", "project_fact", "lesson", "decision")
SUPERSEDE_PREFIX = "supersede:"
# The Owner's optional reject reason: kept in this run's receipt only (audit
# event and printed payload), never on the tombstone, which stays text-free.
REASON_MAX = 200
PLAYBOOK_TYPES = ("rule", "lesson", "project_fact")
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


def _attribution(args: list[str], mode: str = "") -> tuple[dict | None, str]:
    """(receipt fields, error). --yes needs --operator, TTY or not."""
    operator = _option(args, "--operator").strip()
    if not operator:
        return None, "Refusing to apply without --operator <name> (every applying run is attributed)."
    return attribution_record(operator, mode=mode), ""


def attribution_record(operator: str, *, mode: str = "", isatty: bool | None = None) -> dict:
    """Who applied, from where: operator, TTY flag, parent process, host (and the
    apply ``route``, ``marks`` file or ``interactive``, when given)."""
    ppid = os.getppid()
    if isatty is None:
        isatty = bool(sys.stdin.isatty()) if sys.stdin else False
    record = {
        "operator": operator,
        "isatty": bool(isatty),
        "ppid": ppid,
        "parent_name": _parent_name(ppid),
        "host": platform.node(),
    }
    if mode:
        record["route"] = mode
    return record


def _receipt(eng, verb: str, attribution: dict, counts: dict, reject_reasons: dict | None = None) -> None:
    extra = {"verb": verb, **attribution, "counts": counts}
    if reject_reasons:
        extra["reject_reasons"] = reject_reasons  # the Owner's own notes, cleaned and capped
    eng._audit.log(
        "owner_cli",
        f"review/{verb}",
        detail=json.dumps(counts, sort_keys=True),
        source_tool="cli",
        extra=extra,
    )


def _reject_reasons(marks: list[dict]) -> dict[str, str]:
    return {m["id"]: m["reason"] for m in marks if m.get("mark") == "reject" and m.get("reason")}


def _type_label(row: dict) -> str:
    for part in str(row.get("domain") or "").split(","):
        part = part.strip()
        if part.startswith("type:"):
            return part[len("type:"):]
    return ""


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def active_rows(eng, kind: str) -> list[dict]:
    """Every active lesson or decision, whatever its project scope.

    The Owner's review sees the whole store: ``get_lessons`` / ``get_decisions``
    without a project return only global rows, which would hide every
    project-scoped proposal from review. Read-only: nothing is migrated on disk.
    """
    name = "lessons.json" if kind == "lesson" else "decisions.json"
    return [row for row in eng._read_entries(eng._knowledge_dir / name, kind, migrate=False)
            if isinstance(row, dict) and (row.get("status") or "active") == "active"]


def scope_label(eng, kind: str, row: dict) -> str:
    """``global`` or ``project:<name>`` (a sanitized project name or id, never a path)."""
    if kind == "playbook":
        scope = row.get("scope") if isinstance(row.get("scope"), dict) else {}
        kind_of = str(scope.get("type") or row.get("scope_type") or "global")
        if kind_of == "global":
            return "global"
        ids = scope.get("project_ids") or [scope.get("project_id") or row.get("project_id")]
        return f"{kind_of}:{','.join(str(i) for i in ids if i)}"
    label = eng._entry_project_label(row)
    if label:
        return f"project:{label}"
    pid = eng._entry_project_id(row)
    return f"project:{pid}" if pid else "global"


def _pending(eng, lookup: dict[str, dict[str, dict]] | None = None) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    for kind, items in (
        ("lesson", active_rows(eng, "lesson")),
        ("decision", active_rows(eng, "decision")),
    ):
        if lookup is not None:
            lookup[kind] = {str(row.get("id")): row for row in items if row.get("id")}
        rows.extend((kind, row) for row in items if row.get("tier") == "staging")
    listing = eng.list_playbooks_for_management(status="active", include_content=True, include_pending=True)
    rows.extend(("playbook", pb) for pb in listing.get("items", []) if pb.get("tier") == "staging")
    return rows


def _card(n: int, kind: str, row: dict, eng, lookup: dict[str, dict] | None = None) -> list[str]:
    root = eng.root
    flags = []
    if row.get("reproposal_of_rejected"):
        flags.append(f"re-proposal of rejected {row['reproposal_of_rejected']}")
    near = _tombstones.near(root, kind, row)
    if near is not None:
        flags.append(f"near-rejected {near.get('id')}")
    mem_type = _type_label(row)
    if not mem_type:
        flags.append("missing type")
    detail = str(row.get("detail") or row.get("reasoning") or row.get("description") or "").strip()
    if not detail:
        flags.append("missing rationale")
    if kind == "lesson":
        claim = row.get("summary")
    elif kind == "playbook":
        claim = row.get("title")
    else:
        claim = f"{row.get('question', '')} -> {row.get('choice', '')}"
    scope = scope_label(eng, kind, row)
    lines = [
        f"### {n}. {kind} `{row.get('id')}`" + (f"  [{'; '.join(flags)}]" if flags else ""),
        "",
        f"- type: {mem_type or 'MISSING'}",
        f"- scope: {scope}",
        f"- claim: {claim}",
    ]
    if detail:
        lines.append(f"- why / detail: {detail[:_DETAIL_CAP]}")
    if kind == "playbook":
        if row.get("pending_supersedes"):
            lines.append(f"- relation: SUPERSEDES {row['pending_supersedes']}")
        triggers = row.get("triggers") or []
        lines.append(f"- triggers: {', '.join(str(t) for t in triggers[:3])}")
        lines.append("- steps (full):")
        for i, step in enumerate(row.get("steps") or [], 1):
            text = step.get("action", "") if isinstance(step, dict) else str(step)
            lines.append(f"  {i}. {text}")
    lines.extend(_dedup_review.card_lines(kind, row, lookup or {}))
    lines.append(f"- source: {row.get('source_tool') or 'unknown'}, queued {row.get('queued_at') or row.get('timestamp') or '?'}")
    lines.append(_write_provenance.client_card_line(row))
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
    lookup: dict[str, dict[str, dict]] = {}
    pending = sorted(_pending(eng, lookup), key=_sort_key)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    from .playbooks import _playbook_queue_max

    pending_playbooks = sum(1 for kind, _row in pending if kind == "playbook")
    lines = [
        "# Engram review file",
        "",
        f"pending playbooks {pending_playbooks}/{_playbook_queue_max()}",
        "",
        f"Pending proposals: {len(pending)}. Mark each id in marks.json as approve | reject | "
        f"edit-type:<{'|'.join(MEM_TYPES)}>, then run:",
        "`engram review apply marks.json` (dry run), then add `--operator <name> --yes`.",
        "",
    ]
    for n, (kind, row) in enumerate(pending, 1):
        lines.extend(_card(n, kind, row, eng, lookup.get(kind)))
    (out_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "ids.json").write_text(
        json.dumps([row.get("id") for _kind, row in pending], indent=1), encoding="utf-8"
    )
    _print({"status": "exported", "pending": len(pending), "out": str(out_dir)})
    return 0


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def validate_marks(raw: Any) -> tuple[list[dict], str]:
    """The marks list (as read from a marks file) checked and normalized.

    One shape for both apply routes: ``engram review apply <marks.json>`` and
    the interactive review, which builds the same list from the keys typed.
    Each entry is ``{id, mark}`` with mark ``approve | reject | retire | restore
    | edit-type:<type> | supersede:<old id>``; optional ``expected_version``
    (approve / reject / supersede: skip the item if it changed since) and
    ``reason`` (reject: the Owner's note, kept in the receipt of the run only;
    the tombstone stays text-free).
    """
    if not isinstance(raw, list):
        return [], "marks file must be a JSON array of {id, mark}"
    marks = []
    for entry in raw:
        if not isinstance(entry, dict):
            return [], "every mark must be an object"
        item_id = str(entry.get("id") or "").strip()
        raw_mark = str(entry.get("mark") or entry.get("action") or "").strip()
        mark = raw_mark.lower()
        if not item_id:
            return [], "a mark has no id"
        if mark in ("approve", "reject", "retire", "restore"):
            parsed = {"id": item_id, "mark": mark}
        elif mark.startswith("edit-type:"):
            mem_type = mark[len("edit-type:"):]
            if mem_type not in MEM_TYPES:
                return [], f"unknown type {mem_type!r} for {item_id}; use one of {', '.join(MEM_TYPES)}"
            parsed = {"id": item_id, "mark": "edit-type", "type": mem_type}
        elif mark.startswith(SUPERSEDE_PREFIX):
            target = raw_mark[len(SUPERSEDE_PREFIX):].strip()
            if not _dedup_review.safe_id(target):
                return [], f"supersede needs the id of the entry it replaces: {item_id}"
            parsed = {"id": item_id, "mark": "supersede", "target": target}
        else:
            return [], f"unknown mark {mark!r} for {item_id}"
        if parsed["mark"] in REVIEW_MARKS:
            version = entry.get("expected_version")
            if version is not None:
                if isinstance(version, bool) or not isinstance(version, int):
                    return [], f"expected_version must be a whole number: {item_id}"
                parsed["expected_version"] = version
        if parsed["mark"] == "reject":
            reason = clean_reason(entry.get("reason"))
            if reason:
                parsed["reason"] = reason
        marks.append(parsed)
    return marks, ""


def clean_reason(value: Any) -> str:
    """An Owner's reject reason, safe to store and print: no control, bidi or
    zero-width characters, whitespace collapsed, at most ``REASON_MAX`` chars."""
    return _write_provenance.clean_client_text(value, limit=REASON_MAX)


def _parse_marks(path: Path) -> tuple[list[dict], str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"cannot read marks file: {exc}"
    return validate_marks(raw)


def _relabel(domain: str, mem_type: str) -> str:
    parts = [p.strip() for p in str(domain or "").split(",") if p.strip() and not p.strip().startswith("type:")]
    return ",".join(parts + [f"type:{mem_type}"])


_RETIRED_STATUSES = frozenset({"outdated", "archived", "deleted", "retired"})


def _mark_state(eng, mark: dict) -> str:
    """planned | already_applied | not_found for an edit-type / retire / restore mark,
    judged by the row as it is now -- so a re-run preview shows nothing left to do."""
    kind, row = eng._find_item_by_id(mark["id"])
    if row is None:
        return "not_found"
    if mark["mark"] == "edit-type":
        labels = [p.strip() for p in str(row.get("domain", "") or "").split(",") if p.strip().startswith("type:")]
        return "already_applied" if labels == [f"type:{mark['type']}"] else "planned"
    status = str(row.get("status", "active") or "active").strip().lower()
    if mark["mark"] == "retire":
        return "already_applied" if status in _RETIRED_STATUSES else "planned"
    return "already_applied" if status == "active" else "planned"


# -- supersede ---------------------------------------------------------------

SUPERSEDE_PROBLEMS = {
    "self": "an entry cannot supersede itself",
    "not_found": "the proposal is not in the store",
    "target_not_found": "no entry with that id",
    "type_mismatch": "the entry is of another kind or type",
    "target_not_trusted": "the entry is not an approved (trusted) entry",
    "scope_mismatch": "the entry belongs to another project scope",
    "cycle": "that entry already supersedes this one (the chain would loop)",
}


def supersede_problem(eng, item_id: str, target_id: str) -> str:
    """'' when the pending ``item_id`` may supersede ``target_id``, else a code.

    The target must exist, be trusted (approved, active, not superseded), be the
    same kind (lesson / decision / playbook) with the same ``type:`` label when
    both carry one, share the proposal's project scope, not be the proposal, and
    the new edge must not close a cycle of ``supersedes`` edges.
    """
    from . import recall_policy as _recall_policy
    from .governance_store import RelationStore

    item_id, target_id = str(item_id or ""), str(target_id or "")
    if not _dedup_review.safe_id(target_id):
        return "target_not_found"
    if item_id == target_id:
        return "self"
    kind, row = eng._find_item_by_id(item_id)
    if row is None or kind not in ("lesson", "decision", "playbook"):
        return "not_found"
    target_kind, target = eng._find_item_by_id(target_id)
    if target is None:
        return "target_not_found"
    if target_kind != kind:
        return "type_mismatch"
    labels = (_type_label(row), _type_label(target))
    if all(labels) and labels[0] != labels[1]:
        return "type_mismatch"
    edges = RelationStore(eng.root).all_edges()
    index = _recall_policy.build_supersede_index(eng._honored_relation_edges())
    if _recall_policy.classify(target, index).state != _recall_policy.TRUSTED:
        return "target_not_trusted"
    same_scope = (eng._same_playbook_scope(row, target) if kind == "playbook"
                  else eng._entries_share_project_scope(row, target))
    if not same_scope:
        return "scope_mismatch"
    proposed = [*edges, {"src": item_id, "rel": "supersedes", "dst": target_id}]
    if item_id in _recall_policy.build_supersede_index(proposed).cycle_ids:
        return "cycle"
    return ""


def _set_pending_supersede(eng, kind: str, item_id: str, target: str | None,
                           expected_version: int | None) -> tuple[bool, Any]:
    """Point a pending row's ``pending_supersedes`` at ``target`` (None removes it).

    Under the row's write lock: only a still-pending row at the expected
    version is changed. Returns (changed, previous value).
    """
    from .storage import SkipWrite

    box: dict[str, Any] = {"changed": False, "previous": None}

    def _mutate(entry: dict) -> dict:
        version = int(entry.get("version") or 1)
        if entry.get("tier") != "staging" or (expected_version is not None and version != expected_version):
            raise SkipWrite
        box["previous"] = entry.get("pending_supersedes")
        if target is None:
            entry.pop("pending_supersedes", None)
        else:
            entry["pending_supersedes"] = target
        box["changed"] = True
        return entry

    eng._update_knowledge_item(kind, item_id, _mutate)
    return box["changed"], box["previous"]


def _supersede_linked(eng, kind: str, item_id: str, target: str) -> bool:
    if kind == "playbook":
        old = eng._read_playbook_by_id(target)
        return old is None or str(old.get("status") or "active") != "active"
    from .governance_store import RelationStore

    return any(e.get("rel") == "supersedes" and e.get("src") == item_id and e.get("dst") == target
               for e in RelationStore(eng.root).all_edges())


# -- the apply function both routes share -------------------------------------

REVIEW_MARKS = ("approve", "reject", "supersede")


def _batch_row(mark: dict) -> dict:
    row = {"id": mark["id"], "action": "reject" if mark["mark"] == "reject" else "approve"}
    if "expected_version" in mark:
        row["expected_version"] = mark["expected_version"]
    return row


def _new_counts() -> dict[str, int]:
    return {"requested": 0, "approve": 0, "reject": 0, "planned": 0, "applied": 0, "noop": 0, "failed": 0,
            "supersede": 0, "supersede_failed": 0}


def _add_counts(counts: dict, more: dict) -> None:
    for key, value in more.items():
        if isinstance(value, int):
            counts[key] = counts.get(key, 0) + value


def _item_view(item: dict, mark: dict) -> dict:
    view = {"id": item.get("id", mark["id"]), "action": mark["mark"], "status": item.get("status", "")}
    if mark["mark"] == "supersede":
        view["target"] = mark["target"]
    if mark.get("reason"):
        view["reason"] = mark["reason"]
    return view


def _review_one(eng, mark: dict, counts: dict, *, dry_run: bool, via: str) -> dict:
    """Run one approve / reject / supersede mark; returns its receipt item."""
    row = _batch_row(mark)
    if mark["mark"] != "supersede":
        result = batch_review_staging(eng, [row], dry_run=dry_run, confirm=not dry_run, via=via,
                                      limit=1, owner_cli=True)
        _add_counts(counts, result["counts"])
        return _item_view(result["items"][0], mark)

    problem = supersede_problem(eng, mark["id"], mark["target"])
    if problem:
        _add_counts(counts, {"requested": 1, "approve": 1, "failed": 1, "supersede_failed": 1})
        return _item_view({"id": mark["id"], "status": problem}, mark)
    preview = batch_review_staging(eng, [row], dry_run=True, limit=1, owner_cli=True)
    planned = preview["items"][0].get("status") == "planned"
    if dry_run or not planned:
        _add_counts(counts, preview["counts"])
        if not planned:
            counts["supersede_failed"] += 1
        return _item_view(preview["items"][0], mark)

    kind = preview["items"][0].get("type") or eng._find_item_by_id(mark["id"])[0]
    changed, previous = _set_pending_supersede(eng, kind, mark["id"], mark["target"],
                                               mark.get("expected_version"))
    if not changed:
        _add_counts(counts, {"requested": 1, "approve": 1, "planned": 1, "failed": 1, "supersede_failed": 1})
        return _item_view({"id": mark["id"], "status": "version_conflict"}, mark)
    result = batch_review_staging(eng, [row], dry_run=False, confirm=True, via=via, limit=1, owner_cli=True)
    _add_counts(counts, result["counts"])
    item = _item_view(result["items"][0], mark)
    if item["status"] != "applied":
        # Not approved after all: the row goes back to what it pointed at.
        _set_pending_supersede(eng, kind, mark["id"], previous or None, None)
        counts["supersede_failed"] += 1
    elif _supersede_linked(eng, kind, mark["id"], mark["target"]):
        counts["supersede"] += 1
    else:
        item["status"] = "applied_unlinked"
        counts["supersede_failed"] += 1
    return item


def preview_marks(eng, marks: list[dict]) -> dict:
    """The dry-run payload of ``engram review apply`` (read-only store)."""
    reviews = [m for m in marks if m["mark"] in REVIEW_MARKS]
    edits = [m for m in marks if m["mark"] == "edit-type"]
    lifecycle = [m for m in marks if m["mark"] in ("retire", "restore")]
    counts = _new_counts()
    items = [_review_one(eng, m, counts, dry_run=True, via="") for m in reviews]
    edit_states = {m["id"]: _mark_state(eng, m) for m in edits}
    life_states = {m["id"]: _mark_state(eng, m) for m in lifecycle}
    missing = [i for i, st in edit_states.items() if st == "not_found"]
    pending = (
        counts["planned"]
        + sum(1 for st in edit_states.values() if st == "planned")
        + sum(1 for st in life_states.values() if st == "planned")
    )
    return {
        "status": "dry_run",
        "counts": {
            **counts,
            "edit_type": len(edits),
            "edit_type_planned": sum(1 for st in edit_states.values() if st == "planned"),
            "edit_type_already_applied": sum(1 for st in edit_states.values() if st == "already_applied"),
            "edit_type_not_found": len(missing),
            "lifecycle": len(lifecycle),
            "lifecycle_planned": sum(1 for st in life_states.values() if st == "planned"),
            "lifecycle_already_applied": sum(1 for st in life_states.values() if st == "already_applied"),
            "lifecycle_not_found": sum(1 for st in life_states.values() if st == "not_found"),
            "pending": pending,
        },
        "items": items,
        "not_found": missing,
    }


def apply_marks(eng, marks: list[dict], attribution: dict) -> dict:
    """Apply validated marks and leave the audit receipt; returns the applied payload.

    The one apply path: ``engram review apply --yes`` and the interactive
    review both end here. Marks run one at a time in order, so if a run stops
    part-way the receipt (written then too, with ``aborted``) counts what was done.
    """
    from . import strict_mode as _strict_mode

    _strict_mode.bootstrap(eng.root, source="cli")
    via = f"cli:{attribution['operator']}"
    reviews = [m for m in marks if m["mark"] in REVIEW_MARKS]
    edits = [m for m in marks if m["mark"] == "edit-type"]
    lifecycle = [m for m in marks if m["mark"] in ("retire", "restore")]
    counts: dict[str, Any] = {**_new_counts(), "edit_type": 0, "edit_type_failed": 0,
                              "lifecycle": 0, "lifecycle_failed": 0, "already_applied": 0}
    items: list[dict] = []
    edit_failed: list[str] = []
    try:
        for m in reviews:
            items.append(_review_one(eng, m, counts, dry_run=False, via=via))
        for m in edits:
            kind, row = eng._find_item_by_id(m["id"])
            if row is None or kind not in ("lesson", "decision", "playbook"):
                edit_failed.append(m["id"])
                continue
            if _mark_state(eng, m) == "already_applied":
                counts["already_applied"] += 1  # no second version snapshot for an unchanged label
                continue
            update = {"lesson": eng.update_lesson, "decision": eng.update_decision,
                      "playbook": eng.update_playbook}[kind]
            outcome = update(m["id"], {"domain": _relabel(row.get("domain", ""), m["type"])})
            if isinstance(outcome, dict) and outcome.get("error"):
                edit_failed.append(m["id"])
            else:
                counts["edit_type"] += 1
        for m in lifecycle:
            if _mark_state(eng, m) == "already_applied":
                counts["already_applied"] += 1
                continue
            if m["mark"] == "retire":
                outcome = eng.archive_playbook(m["id"])  # an archive, never a tombstone
            else:
                outcome = eng.restore_playbook(m["id"], dry_run=False, confirm=True)
            if isinstance(outcome, dict) and outcome.get("error"):
                counts["lifecycle_failed"] += 1
            else:
                counts["lifecycle"] += 1
    except BaseException:
        counts["edit_type_failed"] = len(edit_failed)
        _receipt(eng, "apply", attribution, {**counts, "aborted": 1}, _reject_reasons(reviews))
        raise
    counts["edit_type_failed"] = len(edit_failed)
    _receipt(eng, "apply", attribution, counts, _reject_reasons(reviews))
    return {"status": "applied", "counts": counts, "items": items, "edit_type_failed": edit_failed}


def run_apply(args: list[str]) -> int:
    paths = [a for a in args if not a.startswith("--") and a != _option(args, "--operator")]
    if not paths:
        print("Usage: engram review apply <marks.json> [--operator <name> --yes]")
        return 2
    marks, error = _parse_marks(Path(paths[0]))
    if error:
        print(error)
        return 2
    edits = [m for m in marks if m["mark"] == "edit-type"]
    lifecycle = [m for m in marks if m["mark"] in ("retire", "restore")]
    probe = _engram(read_only=True)
    for m in edits + lifecycle:
        kind, _row = probe._find_item_by_id(m["id"])
        if kind == "playbook" and m["mark"] == "edit-type" and m["type"] not in PLAYBOOK_TYPES:
            print(f"playbook {m['id']}: type must be one of {', '.join(PLAYBOOK_TYPES)}")
            return 2
        if m["mark"] in ("retire", "restore") and kind != "playbook":
            print(f"{m['mark']} applies to playbooks only: {m['id']}")
            return 2

    if "--yes" not in args:
        _print(preview_marks(_engram(read_only=True), marks))
        return 0

    attribution, error = _attribution(args, mode="marks")
    if error:
        print(error)
        return 2
    _print(apply_marks(_engram(read_only=False), marks, attribution))
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


def run_strict_marker(args: list[str]) -> int:
    """``engram review strict-marker --clear`` -- the audited way out of a strict latch."""
    from . import strict_mode as _strict_mode

    if "--clear" not in args:
        print("Usage: engram review strict-marker --clear [--operator <name> --yes]")
        return 2
    eng = _engram(read_only="--yes" not in args)
    latched = (Path(eng.root) / _strict_mode.MARKER).is_file()
    pending = len(_pending(eng))
    if "--yes" not in args:
        _print({"status": "dry_run", "latched": latched, "pending": pending})
        return 0
    attribution, error = _attribution(args)
    if error:
        print(error)
        return 2
    from .audit import audit_enabled_by_env

    if not audit_enabled_by_env():
        print("Refusing to clear the strict latch while audit logging is off (ENGRAM_AUDIT=0):"
              " the clear must leave a receipt.")
        return 2
    # Receipt first: if the delete fails, the attempt is still on record.
    _receipt(eng, "strict-marker-clear", attribution, {"latched": int(latched), "pending": pending})
    cleared = _strict_mode.clear_marker(eng.root)
    _print({"status": "applied", "cleared": int(cleared), "pending": pending})
    return 0


def run_untombstone(args: list[str]) -> int:
    """``engram review untombstone <id>`` -- the Owner withdraws a rejection.

    Tombstones only act at insert time (they refuse a new row with the same
    claim); removing one lets that claim be proposed again. Dry run by default.
    """
    ids = [a for a in args if not a.startswith("--") and a != _option(args, "--operator")]
    if len(ids) != 1:
        print("Usage: engram review untombstone <id> [--operator <name> --yes]")
        return 2
    eng = _engram(read_only="--yes" not in args)
    stone = _tombstones.by_id(eng.root, ids[0])
    if stone is None:
        _print({"status": "not_found", "id": ids[0]})
        return 1
    if "--yes" not in args:
        _print({"status": "dry_run", "id": ids[0], "kind": stone.get("kind"), "via": stone.get("via")})
        return 0
    attribution, error = _attribution(args)
    if error:
        print(error)
        return 2
    from .audit import audit_enabled_by_env

    if not audit_enabled_by_env():
        print("Refusing to withdraw a rejection while audit logging is off (ENGRAM_AUDIT=0):"
              " it must leave a receipt.")
        return 2
    # Receipt first: if the removal fails, the attempt is still on record.
    _receipt(eng, "untombstone", attribution, {"id": ids[0]})
    removed = _tombstones.remove(eng.root, ids[0])
    _print({"status": "applied", "id": ids[0], "removed": int(removed)})
    return 0


def run_interactive(args: list[str]) -> int:
    """``engram review interactive`` (or ``-i``): one item at a time in a terminal."""
    from .review_interactive import run

    return run(args)


VERBS = {"export": run_export, "apply": run_apply, "tombstone": run_tombstone,
         "strict-marker": run_strict_marker, "untombstone": run_untombstone,
         "interactive": run_interactive, "-i": run_interactive}


def run_playbook_list(args: list[str]) -> int:
    """``engram playbook list [--tier staging|verified]`` -- the Owner's read-only view."""
    tier = _option(args, "--tier")
    eng = _engram(read_only=True)
    listing = eng.list_playbooks_for_management(status="all", include_content=True, include_pending=True)
    items = [pb for pb in listing.get("items", []) if not tier or pb.get("tier", "verified") == tier]
    for pb in items:
        print(f"{pb.get('id')}  tier={pb.get('tier', 'verified')}  status={pb.get('status', 'active')}  "
              f"{pb.get('title', '')}")
    print(f"{len(items)} playbook(s)")
    return 0
