"""Self-diagnostics & integrity scan (Phase 9) — report + proposals, no repair.

A metadata-only local integrity scan over an Engram root. It checks the things
that quietly corrupt a memory store over time and reports them with **self-heal
proposals** — but it never repairs anything itself. Acting on a proposal is an
explicit, owner-run command (``engram recover-json``, ``engram reindex``, …),
never something the scan triggers.

What it checks:
- **JSON validity** of each knowledge dataset (lessons / decisions / playbooks).
- **Duplicate ids** within a dataset (index/dedup hazard).
- **Store ↔ index drift** — whether the persisted hybrid ``search_index.db`` is
  older than the store files (stale index) or missing.
- **Governance ledger** hash-chain integrity (via ``GovernanceLedger.verify``).
- **Relation / version-chain** health — dangling edges (referencing ids not in
  the store) and cycles (via ``version_chain``).
- **Hash summaries** — sha256_12 per dataset, so a later scan can detect change.

Invariants:
- read-only & metadata-only: never echoes stored bodies; ids/counts/hashes only.
- proposal-only: ``build_self_heal_proposals`` maps problems to *suggested*
  owner commands marked ``destructive: false``; nothing is executed.
- resilient: a corrupt/locked file is reported as a problem, never crashes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .recovery import _knowledge_dir, _read_json_file  # reuse vetted JSON reader

_DATASETS = ("lessons", "decisions", "playbooks")
_INDEX_FILE = "search_index.db"


def _entry_ids(entries: list) -> list[str]:
    out: list[str] = []
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"]:
            out.append(e["id"])
    return out


def _scan_dataset(knowledge_dir: Path, dataset: str) -> tuple[dict[str, Any], list[str]]:
    """Scan one dataset. Returns ``(summary, ids)`` — ``ids`` is for the internal
    relation cross-check only and is never placed in the report (which stays
    metadata-only: counts/hashes, plus duplicate ids as a finding)."""
    path = knowledge_dir / f"{dataset}.json"
    if not path.is_file():
        return {"dataset": dataset, "status": "missing", "entries": None,
                "duplicate_ids": [], "sha256_12": ""}, []
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()[:12]
    data, status = _read_json_file(path)
    if status != "ok":
        return {"dataset": dataset, "status": "corrupt", "entries": None,
                "duplicate_ids": [], "sha256_12": sha, "error": status}, []
    if not isinstance(data, list):
        return {"dataset": dataset, "status": "corrupt", "entries": None,
                "duplicate_ids": [], "sha256_12": sha, "error": "not_a_list"}, []
    ids = _entry_ids(data)
    seen: set[str] = set()
    dups: set[str] = set()
    for i in ids:
        if i in seen:
            dups.add(i)
        seen.add(i)
    return ({"dataset": dataset, "status": "ok", "entries": len(data),
             "duplicate_ids": sorted(dups), "sha256_12": sha}, ids)


def _scan_index(root: Path, knowledge_dir: Path) -> dict[str, Any]:
    index_path = root / _INDEX_FILE
    present = index_path.is_file()
    store_mtime = 0.0
    for dataset in _DATASETS:
        p = knowledge_dir / f"{dataset}.json"
        if p.is_file():
            try:
                store_mtime = max(store_mtime, p.stat().st_mtime)
            except OSError:
                pass
    index_mtime = 0.0
    if present:
        try:
            index_mtime = index_path.stat().st_mtime
        except OSError:
            present = False
    stale = bool(present and store_mtime > index_mtime)
    return {"present": present, "stale": stale,
            "store_mtime": round(store_mtime, 3) if store_mtime else None,
            "index_mtime": round(index_mtime, 3) if index_mtime else None}


def _scan_ledger(root: Path) -> dict[str, Any]:
    try:
        from .governance import GovernanceLedger, default_ledger_path

        path = default_ledger_path(root)
        if not Path(path).is_file():
            return {"present": False, "ok": True, "length": 0}
        ledger = GovernanceLedger(path)
        records = ledger.records()
        # ``verify()`` returns ``(ok, message)``. Unpacking is essential: a bare
        # ``bool(ledger.verify())`` is ALWAYS True (a non-empty tuple is truthy),
        # which would silently report a tampered/broken chain as healthy.
        ok, message = ledger.verify()
        return {"present": True, "ok": bool(ok), "length": len(records),
                "detail": message if not ok else ""}
    except Exception as exc:  # corrupt/unreadable ledger is a finding, not a crash
        return {"present": True, "ok": False, "length": None,
                "error": type(exc).__name__}


def _scan_relations(root: Path, store_ids: set[str]) -> dict[str, Any]:
    path = root / "knowledge" / "relations.json"
    if not path.is_file():
        return {"present": False, "total_edges": 0, "valid_edges": 0,
                "dangling_edges": 0, "cycles": 0}
    data, status = _read_json_file(path)
    if status != "ok" or not isinstance(data, list):
        return {"present": True, "total_edges": None, "valid_edges": 0,
                "dangling_edges": 0, "cycles": 0, "error": status}
    from . import decision_thread as _dt
    from . import version_chain as _vc

    valid = _dt.validate_edges(data)
    dangling = 0
    if store_ids:
        for e in valid:
            if e["src"] not in store_ids or e["dst"] not in store_ids:
                dangling += 1
    report = _vc.build_version_report(valid)
    return {"present": True, "total_edges": len(data), "valid_edges": len(valid),
            "dangling_edges": dangling, "cycles": report["totals"]["cycles"]}


def _scan_split_playbooks(root_path: Path) -> list[dict[str, Any]]:
    """Cross-check playbooks/{id}.json body files against _index.json."""
    pb_dir = root_path / "playbooks"
    index_path = pb_dir / "_index.json"
    problems: list[dict[str, Any]] = []

    if not pb_dir.is_dir():
        return problems

    body_ids: set[str] = set()
    for f in pb_dir.iterdir():
        if f.name.endswith(".json") and f.name != "_index.json" and f.is_file():
            body_ids.add(f.stem)

    index_ids: set[str] = set()
    if index_path.is_file():
        data, status = _read_json_file(index_path)
        if status == "ok" and isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    index_ids.add(entry["id"])

    for orphan_id in sorted(body_ids - index_ids):
        problems.append({
            "severity": "medium",
            "code": "orphaned_playbook_body",
            "type": "orphaned_playbook_body",
            "target": f"playbooks/{orphan_id}.json",
            "playbook_id": orphan_id,
            "detail": "body file exists without index entry",
        })

    for dangling_id in sorted(index_ids - body_ids):
        problems.append({
            "severity": "medium",
            "code": "dangling_playbook_index",
            "type": "dangling_playbook_index",
            "target": "playbooks/_index.json",
            "playbook_id": dangling_id,
            "detail": "index entry references missing body file",
        })

    return problems


def scan_integrity(root: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Run the full metadata-only integrity scan over an Engram ``root``.

    Returns a report dict with ``datasets`` / ``index`` / ``ledger`` /
    ``relations`` sections, a flattened ``problems`` list, and an overall
    ``healthy`` flag. Read-only; never echoes stored bodies.
    """
    root_path = Path(root).expanduser().resolve()
    knowledge_dir = _knowledge_dir(root_path)
    now = now or datetime.now(timezone.utc)

    scanned = [_scan_dataset(knowledge_dir, d) for d in _DATASETS]
    datasets = [summary for summary, _ in scanned]
    store_ids: set[str] = set()
    for _, ids in scanned:
        store_ids.update(ids)

    index = _scan_index(root_path, knowledge_dir)
    ledger = _scan_ledger(root_path)
    relations = _scan_relations(root_path, store_ids)

    problems: list[dict[str, Any]] = []
    for ds in datasets:
        if ds["status"] == "corrupt":
            problems.append({"severity": "high", "code": "dataset_corrupt",
                             "target": ds["dataset"], "detail": ds.get("error", "")})
        if ds["duplicate_ids"]:
            problems.append({"severity": "medium", "code": "duplicate_ids",
                             "target": ds["dataset"],
                             "detail": f"{len(ds['duplicate_ids'])} duplicate id(s)"})
    if index["present"] and index["stale"]:
        problems.append({"severity": "low", "code": "index_stale",
                         "target": _INDEX_FILE, "detail": "store newer than index"})
    if not ledger["ok"]:
        problems.append({"severity": "high", "code": "ledger_chain_broken",
                         "target": "governance_ledger",
                         "detail": ledger.get("error") or ledger.get("detail") or "verify_failed"})
    if relations.get("dangling_edges"):
        problems.append({"severity": "medium", "code": "dangling_relations",
                         "target": "relations.json",
                         "detail": f"{relations['dangling_edges']} edge(s) reference missing ids"})
    if relations.get("cycles"):
        problems.append({"severity": "medium", "code": "relation_cycle",
                         "target": "relations.json",
                         "detail": f"{relations['cycles']} cycle(s) in version chain"})

    problems.extend(_scan_split_playbooks(root_path))

    return {
        "root": str(root_path),
        "exists": root_path.is_dir(),
        "datasets": datasets,
        "index": index,
        "ledger": ledger,
        "relations": relations,
        "problems": problems,
        "healthy": not problems,
        "scanned_at": now.replace(microsecond=0).isoformat(),
        "live_store_modified": False,
    }


# Maps a problem code → a *suggested* owner command. All non-destructive in the
# sense that they never silently overwrite the live store (recover-json writes to
# an explicit destination; reindex rebuilds a derived cache; the rest are manual
# reviews). The owner runs these explicitly; the scan never does.
_HEAL_ACTIONS = {
    "dataset_corrupt": ("engram recover-json {target}",
                        "Analyze backups and export a recovery candidate (does not overwrite the live store)."),
    "duplicate_ids": ("engram review",
                      "Review and merge/archive the duplicated entries via the audited review path."),
    "index_stale": ("engram reindex",
                    "Rebuild the derived hybrid search index from the JSON store."),
    "ledger_chain_broken": ("engram verify-ledger",
                            "Inspect the governance ledger chain; a break is append-only evidence, not auto-repairable."),
    "dangling_relations": ("engram doctor",
                           "Review relation edges referencing missing ids; remove stale edges explicitly."),
    "relation_cycle": ("engram doctor",
                       "Review the version-chain cycle; relations should form a DAG."),
}


def build_self_heal_proposals(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Map an integrity report's problems to suggested owner commands.

    Proposal-only: every entry is marked ``destructive: false`` and is a *suggested*
    command for the owner to run, never executed here.
    """
    proposals: list[dict[str, Any]] = []
    for problem in report.get("problems", []):
        code = problem.get("code", "")
        action = _HEAL_ACTIONS.get(code)
        if not action:
            continue
        command, rationale = action
        proposals.append({
            "problem_code": code,
            "target": problem.get("target", ""),
            "severity": problem.get("severity", ""),
            "command": command.format(target=problem.get("target", "")),
            "rationale": rationale,
            "destructive": False,
        })
    return proposals


def render_integrity_text(report: dict[str, Any], proposals: list[dict[str, Any]] | None = None) -> str:
    """Render an integrity report + proposals as an owner-facing digest."""
    lines = [f"Engram integrity scan (read-only): {report.get('root', '')}"]
    if not report.get("exists"):
        lines.append("  (no Engram root at this path)")
        return "\n".join(lines)
    status = "healthy" if report.get("healthy") else f"{len(report.get('problems', []))} problem(s)"
    lines.append(f"  status: {status}")
    for ds in report.get("datasets", []):
        lines.append(f"  dataset {ds['dataset']}: {ds['status']} "
                     f"entries={ds['entries']} sha256={ds['sha256_12']}")
    idx = report.get("index", {})
    lines.append(f"  index: present={idx.get('present')} stale={idx.get('stale')}")
    led = report.get("ledger", {})
    lines.append(f"  ledger: present={led.get('present')} ok={led.get('ok')} length={led.get('length')}")
    rel = report.get("relations", {})
    lines.append(f"  relations: edges={rel.get('total_edges')} dangling={rel.get('dangling_edges')} "
                 f"cycles={rel.get('cycles')}")
    if report.get("problems"):
        lines.append("  problems:")
        for p in report["problems"]:
            lines.append(f"    - [{p['severity']}] {p['code']} ({p['target']}): {p['detail']}")
    if proposals:
        lines.append("  self-heal proposals (run explicitly — nothing was changed):")
        for pr in proposals:
            lines.append(f"    - {pr['problem_code']}: `{pr['command']}` — {pr['rationale']}")
    lines.append("  live store modified: false")
    return "\n".join(lines)
