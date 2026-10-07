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


def _receipt(eng, verb: str, attribution: dict, counts: dict, reject_reasons: dict | None = None,
             more: dict | None = None) -> None:
    extra = {"verb": verb, **attribution, "counts": counts, **(more or {})}
    if reject_reasons:
        # The Owner's own notes, cleaned and capped. Local audit only:
        # get_audit_log over MCP drops this field.
        extra["reject_reasons"] = reject_reasons
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
        f"- version: {_row_version(row)}",
        f"- claim: {claim}",
    ]
    if row.get("pinned") is True:
        lines.append(f"- pinned: yes (since {row.get('pinned_at') or '?'})")
    if detail:
        lines.append(f"- why / detail: {detail[:_DETAIL_CAP]}")
    if row.get("pending_supersedes"):
        target = _dedup_review.safe_id(row.get("pending_supersedes")) or "(malformed id)"
        lines.append(f"- relation: SUPERSEDES {target} (proposed; checked again on approval)")
        if _target_is_pinned(eng, row.get("pending_supersedes")):
            lines.append("- note: the target is a PINNED entry; approving this replaces it and removes the pin")
    if kind == "playbook":
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


def _target_is_pinned(eng, target_id: Any) -> bool:
    if not isinstance(target_id, str) or not target_id:
        return False
    from . import pinning as _pinning

    _kind, row = _pinning.find(eng, target_id)
    return _pinning.is_pinned(row)


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
        f"Pending proposals: {len(pending)}. Fill in marks-template.json (one entry per proposal; "
        "save it as marks.json), then run:",
        "`engram review apply marks.json` (dry run), then add `--operator <name> --yes`.",
        "",
        f"mark: approve | reject | edit-type:<{'|'.join(MEM_TYPES)}> | supersede:<id> | retire | restore"
        " | skip (leave it pending).",
        "supersede:<id> approves the proposal as the replacement of the approved entry <id> (same kind and"
        " scope; one proposal per entry in a run). One of approve / reject / supersede / skip per id.",
        "Order: plain approvals and rejections, then the marks that replace an entry (oldest first along a"
        " chain), then edit-type, then retire / restore; an entry approved in the same run can be replaced.",
        "Optional fields: reason (reject only; your note, kept in this run's receipt, never on the"
        " rejection record) and expected_version (approve, reject and supersede only; the version below;"
        " the item is skipped if it changed).",
        "",
    ]
    for n, (kind, row) in enumerate(pending, 1):
        lines.extend(_card(n, kind, row, eng, lookup.get(kind)))
    (out_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "ids.json").write_text(
        json.dumps([row.get("id") for _kind, row in pending], indent=1), encoding="utf-8"
    )
    template = [{"id": row.get("id"), "kind": kind, "mark": "skip", "expected_version": _row_version(row)}
                for kind, row in pending]
    (out_dir / "marks-template.json").write_text(json.dumps(template, indent=1), encoding="utf-8")
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
    | edit-type:<type> | supersede:<old id> | skip``; optional ``expected_version``
    (approve / reject / supersede: skip the item if it changed since) and
    ``reason`` (reject: the Owner's note, kept in the receipt of the run only;
    the tombstone stays text-free). ``skip`` leaves the item pending.

    A run names each supersede target once (a file that does not is refused
    before anything is written); targets an agent proposed are added by
    ``batch_target_problem``. A target decided in the same run is fine: marks
    apply in two phases (see ``apply_marks``).
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
        if mark in ("approve", "reject", "retire", "restore", "skip"):
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
    decided: set[str] = set()
    for m in marks:
        if m["mark"] in REVIEW_MARKS or m["mark"] == "skip":
            if m["id"] in decided:
                return [], f"{m['id']} has more than one of approve / reject / supersede / skip; keep one"
            decided.add(m["id"])
    error = _target_problem(marks, {m["id"]: m["target"] for m in marks if m["mark"] == "supersede"})
    if error:
        return [], error
    return marks, ""


def _target_problem(marks: list[dict], targets: dict[str, str]) -> str:
    """'' or why the supersede targets of one run cannot be applied as given:
    one entry replaced by two proposals of the same run."""
    seen: dict[str, str] = {}
    for item_id, target in targets.items():
        if target in seen:
            return (f"{seen[target]} and {item_id} both supersede {target}; one entry is replaced by one"
                    " proposal per run")
        seen[target] = item_id
    return ""


def batch_target_problem(eng, marks: list[dict]) -> str:
    """Like ``validate_marks``' target check, with the targets agents proposed:
    an approve mark carries its row's ``pending_supersedes``. '' when fine."""
    targets: dict[str, str] = {}
    for m in marks:
        if m["mark"] == "supersede":
            targets[m["id"]] = m["target"]
        elif m["mark"] == "approve":
            _kind, row = eng._find_item_by_id(m["id"])
            if isinstance(row, dict) and row.get("tier") == "staging" and row.get("pending_supersedes"):
                targets[m["id"]] = str(row["pending_supersedes"])
    return _target_problem(marks, targets)


def _row_version(row: dict) -> int:
    try:
        return int(row.get("version") or 1)
    except (TypeError, ValueError):
        return 1


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


def supersede_problem(eng, item_id: str, target_id: str, *, assume_trusted: Any = (),
                      assume_untrusted: Any = (), extra_edges: Any = (),
                      final_types: dict[str, str] | None = None) -> str:
    """'' when the pending ``item_id`` may supersede ``target_id``, else a code.

    The target must exist, be trusted (approved, active, not superseded), be the
    same kind (lesson / decision / playbook) with the same ``type:`` label when
    both carry one, share the proposal's project scope, not be the proposal, and
    the new edge must not close a cycle of ``supersedes`` edges.

    A dry run (or an interactive review) simulates decisions not applied yet:
    ids in ``assume_trusted`` count as approved, ids in ``assume_untrusted`` as
    rejected or replaced, and ``extra_edges`` as written. ``final_types`` maps
    an id to the ``type:`` label an edit-type mark of the same run gives it; the
    type check compares the labels both entries have once the run is done.
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
    final_types = final_types or {}
    labels = (final_types.get(item_id) or _type_label(row), final_types.get(target_id) or _type_label(target))
    if all(labels) and labels[0] != labels[1]:
        return "type_mismatch"
    extra = [dict(e) for e in extra_edges or ()]
    edges = [*RelationStore(eng.root).all_edges(), *extra]
    index = _recall_policy.build_supersede_index([*eng._honored_relation_edges(), *extra])
    if target_id in set(assume_untrusted or ()):
        return "target_not_trusted"
    if target_id in set(assume_trusted or ()):
        target = {**target, "status": "active", "tier": "verified", "memory_state": "verified",
                  "approval_status": "approved"}
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
            "supersede": 0, "supersede_failed": 0, "approved_unlinked": 0}


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


def _proposed_target(row: dict | None) -> str:
    """The ``pending_supersedes`` an agent put on a pending row ('' when none)."""
    if not isinstance(row, dict) or row.get("tier") != "staging":
        return ""
    return str(row.get("pending_supersedes") or "")


def _approve_with_link(eng, mark: dict, kind: str, target: str, counts: dict, *, via: str,
                       set_target: bool) -> dict:
    """Approve a pending row that supersedes ``target``; the link is written on promotion.

    ``set_target``: the Owner chose the target (supersede mark), so the row
    points at it first; otherwise the row already carries the agent's target.
    A run stopped anywhere before the approval landed (even inside the write
    that points the row) puts a still-pending row back as it was.
    """
    from contextlib import nullcontext

    row = _batch_row(mark)
    before = (eng._find_item_by_id(mark["id"])[1] or {}).get("pending_supersedes")
    try:
        if set_target:
            changed, _previous = _set_pending_supersede(eng, kind, mark["id"], target,
                                                        mark.get("expected_version"))
            if not changed:
                _add_counts(counts, {"requested": 1, "approve": 1, "planned": 1, "failed": 1,
                                     "supersede_failed": 1})
                return _item_view({"id": mark["id"], "status": "version_conflict"}, mark)
        recovering = kind == "playbook" and mark["mark"] == "supersede" and not set_target
        with eng._review_locks() if recovering else nullcontext():
            if recovering:
                current = eng._find_item_by_id(mark["id"])[1]
                if eng.unfinished_playbook_replacement(current) != target:
                    linked = (isinstance(current, dict) and current.get("pending_supersedes") == target
                              and _supersede_linked(eng, kind, mark["id"], target))
                    _add_counts(counts, {"requested": 1, "approve": 1, "noop": 1})
                    return _item_view({"id": mark["id"], "status": "already_applied" if linked else "not_staging"}, mark)
            result = batch_review_staging(eng, [row], dry_run=False, confirm=True, via=via, limit=1, owner_cli=True)
    except BaseException:
        if set_target:  # only a row that is still pending is changed back
            _set_pending_supersede(eng, kind, mark["id"], before or None, None)
        raise
    _add_counts(counts, result["counts"])
    item = _item_view(result["items"][0], mark)
    item["target"] = target
    if item["status"] != "applied":
        if set_target:  # not approved after all: the row goes back to what it pointed at
            _set_pending_supersede(eng, kind, mark["id"], before or None, None)
        if mark["mark"] == "supersede":
            counts["supersede_failed"] += 1
    elif _supersede_linked(eng, kind, mark["id"], target):
        counts["supersede"] += 1
    else:
        item["status"] = "applied_unlinked"
        item["unlinked_reason"] = "link_not_written"
        counts["approved_unlinked"] += 1
    return item


def _approve_without_link(eng, mark: dict, kind: str, target: str, problem: str, counts: dict, *,
                          via: str) -> dict:
    """Approve a row whose agent-proposed target may not be superseded: the row is
    approved, its ``pending_supersedes`` dropped, and no link is written."""
    row = _batch_row(mark)
    before = (eng._find_item_by_id(mark["id"])[1] or {}).get("pending_supersedes")
    try:
        changed, _previous = _set_pending_supersede(eng, kind, mark["id"], None, mark.get("expected_version"))
        if not changed:
            _add_counts(counts, {"requested": 1, "approve": 1, "planned": 1, "failed": 1})
            return _item_view({"id": mark["id"], "status": "version_conflict"}, mark)
        result = batch_review_staging(eng, [row], dry_run=False, confirm=True, via=via, limit=1, owner_cli=True)
    except BaseException:
        _set_pending_supersede(eng, kind, mark["id"], before or None, None)  # still-pending rows only
        raise
    _add_counts(counts, result["counts"])
    item = _item_view(result["items"][0], mark)
    item["target"] = target
    if item["status"] == "applied":
        item["status"] = "applied_unlinked"
        item["unlinked_reason"] = problem
        counts["approved_unlinked"] += 1
    else:
        _set_pending_supersede(eng, kind, mark["id"], before or None, None)
    return item


def _review_one(eng, mark: dict, counts: dict, *, dry_run: bool, via: str, sim: dict | None = None,
                final_types: dict[str, str] | None = None) -> dict:
    """Run one approve / reject / supersede mark; returns its receipt item.

    ``sim`` (dry runs) carries the decisions planned earlier in the run, so a
    preview judges a supersede target the way the applying run will.
    """
    row = _batch_row(mark)
    sim_args: dict[str, Any] = {"final_types": final_types or {}}
    recovering = False
    if sim is not None:
        sim_args.update(assume_trusted=sim["trusted"], assume_untrusted=sim["untrusted"], extra_edges=sim["edges"])
    if mark["mark"] == "supersede":
        target = mark["target"]
        kind, current = eng._find_item_by_id(mark["id"])
        recovering = (kind == "playbook" and eng.unfinished_playbook_replacement(current) == target)
        if isinstance(current, dict) and kind in ("lesson", "decision", "playbook") \
                and current.get("tier") != "staging" and not recovering:
            # Decided already (e.g. the same file applied again): done when the
            # link exists, like an approve that finds the row no longer pending.
            status = "already_applied" if _supersede_linked(eng, kind, mark["id"], target) else "not_staging"
            _add_counts(counts, {"requested": 1, "approve": 1, "noop": 1})
            return _item_view({"id": mark["id"], "status": status}, mark)
        problem = supersede_problem(eng, mark["id"], target, **sim_args)
        if problem:
            _add_counts(counts, {"requested": 1, "approve": 1, "failed": 1, "supersede_failed": 1})
            return _item_view({"id": mark["id"], "status": problem}, mark)
    else:
        target = _proposed_target(eng._find_item_by_id(mark["id"])[1]) if mark["mark"] == "approve" else ""
        problem = supersede_problem(eng, mark["id"], target, **sim_args) if target else ""
    preview = batch_review_staging(eng, [row], dry_run=True, limit=1, owner_cli=True)
    planned = preview["items"][0].get("status") == "planned"
    if dry_run or not planned:
        _add_counts(counts, preview["counts"])
        item = _item_view(preview["items"][0], mark)
        if target:
            item["target"] = target
            if problem:
                item["unlinked_reason"] = problem  # approved without the link
        if mark["mark"] == "supersede" and not planned:
            counts["supersede_failed"] += 1
        return item
    if not target:
        result = batch_review_staging(eng, [row], dry_run=False, confirm=True, via=via, limit=1, owner_cli=True)
        _add_counts(counts, result["counts"])
        return _item_view(result["items"][0], mark)
    kind = preview["items"][0].get("type") or eng._find_item_by_id(mark["id"])[0]
    if problem:
        return _approve_without_link(eng, mark, kind, target, problem, counts, via=via)
    return _approve_with_link(eng, mark, kind, target, counts, via=via,
                              set_target=mark["mark"] == "supersede" and not recovering)


def _replaces(eng, mark: dict) -> str:
    """The entry a review mark replaces: the Owner's target, or the agent's on an approved row."""
    if mark["mark"] == "supersede":
        return mark["target"]
    if mark["mark"] == "approve":
        return _proposed_target(eng._find_item_by_id(mark["id"])[1])
    return ""


def _dependency_order(marks: list[tuple[int, dict]], targets: dict[int, str]) -> list[tuple[int, dict]]:
    """Phase 2 in file order, except that a mark replacing an entry another phase-2
    mark approves comes after it (oldest first along a chain).

    Kahn's topological sort over the precomputed ``targets``; among marks that
    are ready the earliest in the file goes first; marks caught in a loop keep
    file order. Linear in the number of marks (plus the heap).
    """
    import heapq

    by_id = {m["id"]: n for n, m in marks}
    mark_of = {n: m for n, m in marks}
    waiting_on: dict[int, list[int]] = {}
    indegree = {n: 0 for n, _m in marks}
    for n, m in marks:
        dep = by_id.get(targets.get(n, ""))
        if dep is not None and dep != n:
            waiting_on.setdefault(dep, []).append(n)
            indegree[n] += 1
    ready = [n for n, count in indegree.items() if count == 0]
    heapq.heapify(ready)
    ordered: list[tuple[int, dict]] = []
    while ready:
        n = heapq.heappop(ready)
        ordered.append((n, mark_of[n]))
        for later in waiting_on.get(n, ()):
            indegree[later] -= 1
            if indegree[later] == 0:
                heapq.heappush(ready, later)
    done = {n for n, _m in ordered}
    ordered.extend((n, m) for n, m in marks if n not in done)  # a loop keeps file order
    return ordered


def _plan(eng, marks: list[dict]) -> list[tuple[int, dict, Any]]:
    """Every mark with its index and phase, in the order a run applies them.

    Phase 1: approvals and rejections that replace nothing. Phase 2: decisions
    that replace an entry (``supersede:<id>`` and approving a row with an
    agent's ``pending_supersedes``), in file order but oldest first along a
    chain. Then edit-type, then retire / restore. ``skip`` marks do nothing.
    Each mark's target is looked up once.
    """
    targets = {n: _replaces(eng, m) for n, m in enumerate(marks) if m["mark"] in REVIEW_MARKS}
    first = [(n, marks[n]) for n in targets if not targets[n]]
    second = [(n, marks[n]) for n in targets if targets[n]]
    return ([(n, m, 1) for n, m in first]
            + [(n, m, 2) for n, m in _dependency_order(second, targets)]
            + [(n, m, "edit") for n, m in enumerate(marks) if m["mark"] == "edit-type"]
            + [(n, m, "lifecycle") for n, m in enumerate(marks) if m["mark"] in ("retire", "restore")])


def _keeps_label(row: Any, kind: str | None, replaced: Any = ()) -> bool:
    """An edit-type skips a playbook that is archived or that a replacement of
    this run archives; such an entry keeps the label it has now."""
    return kind == "playbook" and isinstance(row, dict) and (
        str(row.get("status") or "active") != "active" or str(row.get("id") or "") in replaced)


def _final_types(eng, marks: list[dict]) -> dict[str, str]:
    """The ``type:`` label each id has after the run's edit-type marks, for the
    same-type check of a supersede.

    Left out: an archived playbook and a playbook another mark of this run
    replaces. Its edit-type is skipped (the replacement archives it), so the
    check judges it by the label it carries now.

    The check goes by the planned types. If an edit-type of a decision or a
    lesson then fails, a replacement of the same run that already went through
    is not rolled back.
    """
    edits = [m for m in marks if m["mark"] == "edit-type"]
    if not edits:
        return {}
    replaced = {t for t in (_replaces(eng, m) for m in marks if m["mark"] in REVIEW_MARKS) if t}
    final: dict[str, str] = {}
    for m in edits:
        kind, row = eng._find_item_by_id(m["id"])
        if not _keeps_label(row, kind, replaced):
            final[m["id"]] = m["type"]
    return final


def _simulate(eng, sim: dict, mark: dict, item: dict) -> None:
    """Record what a planned dry-run item will change, for the items after it."""
    if item.get("status") != "planned":
        return
    if mark["mark"] == "reject":
        sim["untrusted"].add(mark["id"])
        return
    sim["trusted"].add(mark["id"])
    target = item.get("target")
    if target and not item.get("unlinked_reason"):
        sim["untrusted"].add(target)  # replaced from now on
        sim["edges"].append({"src": mark["id"], "rel": "supersedes", "dst": target})
        if eng._find_item_by_id(target)[0] == "playbook":
            sim["retired"].add(target)  # approving a playbook's replacement archives it


def relabel_type(eng, kind: str, item_id: str, mem_type: str) -> Any:
    """The Owner's relabel: set the ``type:`` label of one entry (local CLI only).

    Lessons and playbooks go through their update path. A decision's ``domain``
    is not a field an update may change (the MCP whitelist stays as it is), so
    the Owner's path writes the label directly under the file lock and touches
    only ``domain`` and ``last_updated``: unlike a lesson or a playbook, a
    decision's version is not incremented and no snapshot is left.

    ``mem_type`` must be one of ``MEM_TYPES``; anything else returns
    ``{"error": "invalid_type"}`` and writes nothing.
    """
    if mem_type not in MEM_TYPES:
        return {"error": "invalid_type"}
    if kind == "decision":
        from .storage import SkipWrite, _now_iso

        def _mutate(entry: dict) -> dict:
            domain = _relabel(entry.get("domain", ""), mem_type)
            if domain == entry.get("domain"):
                raise SkipWrite
            entry["domain"] = domain
            entry["last_updated"] = _now_iso()
            return entry

        return eng._update_knowledge_item("decision", item_id, _mutate)
    update = {"lesson": eng.update_lesson, "playbook": eng.update_playbook}[kind]
    _kind, row = eng._find_item_by_id(item_id)
    return update(item_id, {"domain": _relabel((row or {}).get("domain", ""), mem_type)})


def _edit_one(eng, mark: dict, counts: dict, edit_failed: list[str], *, dry_run: bool,
              sim: dict | None = None) -> dict:
    kind, row = eng._find_item_by_id(mark["id"])
    # the trail of a relabel: the label the entry had (None when it had none) and the one asked for
    view = {"id": mark["id"], "action": "edit-type",
            "from": (_type_label(row) or None) if isinstance(row, dict) else None, "to": mark["type"]}
    if _keeps_label(row, kind, sim["retired"] if sim is not None else ()):
        # An archived playbook (or one its replacement archives in this run) keeps its label.
        counts["edit_type_skipped"] = counts.get("edit_type_skipped", 0) + 1
        return {**view, "status": "skipped", "reason": "archived"}
    state = _mark_state(eng, mark)
    if dry_run:
        return {**view, "status": state}
    if row is None or kind not in ("lesson", "decision", "playbook"):
        edit_failed.append(mark["id"])
        return {**view, "status": "not_found"}
    if state == "already_applied":
        counts["already_applied"] += 1  # no second version snapshot for an unchanged label
        return {**view, "status": "already_applied"}
    outcome = relabel_type(eng, kind, mark["id"], mark["type"])
    if (isinstance(outcome, dict) and outcome.get("error")) or _mark_state(eng, mark) != "already_applied":
        edit_failed.append(mark["id"])  # read back: the label did not change
        return {**view, "status": "failed"}
    counts["edit_type"] += 1
    return {**view, "status": "applied"}


def _lifecycle_one(eng, mark: dict, counts: dict, *, dry_run: bool, sim: dict | None = None) -> dict:
    view = {"id": mark["id"], "action": mark["mark"]}
    state = _mark_state(eng, mark)
    if dry_run:
        if sim is not None and mark["id"] in sim["retired"] and state != "not_found":
            # archived by an approved replacement earlier in the run
            state = "already_applied" if mark["mark"] == "retire" else "planned"
        return {**view, "status": state}
    if state == "not_found":
        counts["lifecycle_failed"] += 1
        return {**view, "status": "not_found"}
    if state == "already_applied":
        counts["already_applied"] += 1
        return {**view, "status": "already_applied"}
    if mark["mark"] == "retire":
        outcome = eng.archive_playbook(mark["id"])  # an archive, never a tombstone
    else:
        outcome = eng.restore_playbook(mark["id"], dry_run=False, confirm=True)
    if isinstance(outcome, dict) and outcome.get("error"):
        counts["lifecycle_failed"] += 1
        return {**view, "status": "failed"}
    counts["lifecycle"] += 1
    return {**view, "status": "applied"}


def preview_marks(eng, marks: list[dict]) -> dict:
    """The dry-run payload of ``engram review apply`` (read-only store).

    Follows the applying run's order (``_plan``) and simulates each planned
    decision, so every item reads as the applying run will report it.
    """
    counts = _new_counts()
    sim: dict = {"trusted": set(), "untrusted": set(), "edges": [], "retired": set()}
    final_types = _final_types(eng, marks)
    by_index: dict[int, dict] = {}
    order: list[str] = []
    for n, m, phase in _plan(eng, marks):
        if phase in (1, 2):
            item = _review_one(eng, m, counts, dry_run=True, via="", sim=sim, final_types=final_types)
            _simulate(eng, sim, m, item)
        elif phase == "edit":
            item = _edit_one(eng, m, counts, [], dry_run=True, sim=sim)
        else:
            item = _lifecycle_one(eng, m, counts, dry_run=True, sim=sim)
        by_index[n] = {**item, "phase": phase}
        order.append(m["id"])
    items = [by_index[n] for n in sorted(by_index)]
    edit_states = [i["status"] for i in items if i["phase"] == "edit"]
    life_states = [i["status"] for i in items if i["phase"] == "lifecycle"]
    pending = counts["planned"] + edit_states.count("planned") + life_states.count("planned")
    return {
        "status": "dry_run",
        "counts": {
            **counts,
            "edit_type": len(edit_states),
            "edit_type_planned": edit_states.count("planned"),
            "edit_type_already_applied": edit_states.count("already_applied"),
            "edit_type_not_found": edit_states.count("not_found"),
            "edit_type_skipped": edit_states.count("skipped"),
            "lifecycle": len(life_states),
            "lifecycle_planned": life_states.count("planned"),
            "lifecycle_already_applied": life_states.count("already_applied"),
            "lifecycle_not_found": life_states.count("not_found"),
            "pending": pending,
        },
        "items": items,
        "order": order,
        "not_found": [i["id"] for i in items if i["phase"] == "edit" and i["status"] == "not_found"],
    }


def apply_marks(eng, marks: list[dict], attribution: dict, *, progress: dict | None = None) -> dict:
    """Apply validated marks and leave the audit receipt; returns the applied payload.

    The one apply path: ``engram review apply --yes`` and the interactive
    review both end here. A run whose supersede targets clash (see
    ``batch_target_problem``) is refused before anything is written.

    Order (``_plan``): phase 1, the approvals and rejections that replace
    nothing; phase 2, the decisions that replace an entry (``supersede:<id>``
    and approving a row with an agent's ``pending_supersedes``), in file order
    but oldest first along a chain; then edit-type; then retire / restore. So
    approving an entry and a proposal that replaces it works in one run, and a
    rejected target fails only the mark that names it. Items are reported in
    file order, each with its ``phase``; ``order`` lists the ids as applied.

    If a run stops part-way the receipt (written then too, with ``aborted``,
    the number of marks and the id being applied) counts what was done, and
    ``progress["items"]`` holds the items handled so far.
    """
    from . import strict_mode as _strict_mode

    problem = batch_target_problem(eng, marks)
    if problem:
        return {"status": "refused", "error": problem}
    _strict_mode.bootstrap(eng.root, source="cli")
    via = f"cli:{attribution['operator']}"
    reviews = [m for m in marks if m["mark"] in REVIEW_MARKS]
    plan = _plan(eng, marks)
    final_types = _final_types(eng, marks)
    counts: dict[str, Any] = {**_new_counts(), "edit_type": 0, "edit_type_failed": 0, "edit_type_skipped": 0,
                              "lifecycle": 0, "lifecycle_failed": 0, "already_applied": 0}
    handled: list[dict] = progress.setdefault("items", []) if progress is not None else []
    by_index: dict[int, dict] = {}
    order: list[str] = []
    edit_failed: list[str] = []
    current = {"id": ""}
    try:
        for n, m, phase in plan:
            current["id"] = m["id"]
            if phase in (1, 2):
                item = _review_one(eng, m, counts, dry_run=False, via=via, final_types=final_types)
            elif phase == "edit":
                item = _edit_one(eng, m, counts, edit_failed, dry_run=False)
            else:
                item = _lifecycle_one(eng, m, counts, dry_run=False)
            item = {**item, "phase": phase}
            by_index[n] = item
            handled.append(item)
            order.append(m["id"])
    except BaseException:
        counts["edit_type_failed"] = len(edit_failed)
        _receipt(eng, "apply", attribution, {**counts, "aborted": 1}, _reject_reasons(reviews),
                 more={"total_marks": len(marks), "aborted_at": current["id"], "order": order})
        raise
    counts["edit_type_failed"] = len(edit_failed)
    _receipt(eng, "apply", attribution, counts, _reject_reasons(reviews), more={"order": order})
    items = [by_index[n] for n in sorted(by_index)]
    return {"status": "applied", "counts": counts, "items": items, "order": order,
            "edit_type_failed": edit_failed}


_DONE_STATUSES = frozenset({"applied", "applied_unlinked", "already_applied", "not_staging", "skipped"})


def all_failed(payload: dict) -> bool:
    """Every mark of an applied run failed (none applied, none already done)."""
    items = payload.get("items") or []
    done = sum(1 for i in items if i.get("status") in _DONE_STATUSES)
    return bool(items) and done == 0


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
    error = batch_target_problem(probe, marks)
    if error:
        print(error)
        return 2

    if "--yes" not in args:
        _print(preview_marks(_engram(read_only=True), marks))
        return 0

    attribution, error = _attribution(args, mode="marks")
    if error:
        print(error)
        return 2
    payload = apply_marks(_engram(read_only=False), marks, attribution)
    if payload.get("status") == "refused":  # the store changed since the check above
        print(payload["error"])
        return 2
    _print(payload)
    return 1 if all_failed(payload) else 0


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
