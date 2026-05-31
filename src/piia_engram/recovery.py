"""Metadata-only recovery helpers for JSON files backed up as ``*.corrupt``.

These helpers deliberately avoid printing stored knowledge text. They are for
diagnosis and candidate export; restoring into the live store remains an
explicit human decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


_CONTENT_KEYS = {
    "body",
    "choice",
    "content",
    "detail",
    "question",
    "reasoning",
    "steps",
    "steps_json",
    "summary",
    "title",
}

_ENGRAM_METADATA_KEYS = {
    "access_count",
    "archived_at",
    "created_at",
    "domain",
    "last_reviewed",
    "last_updated",
    "promoted_at",
    "promotion_reason",
    "related_ids",
    "sensitivity",
    "source_tool",
    "source_url",
    "status",
    "tier",
    "timestamp",
    "updated_at",
}

_ACCESS_METADATA_KEYS = {"access_count", "last_reviewed"}
_ACTIVE_CAP_DEFAULT = 200


def _knowledge_dir(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / "knowledge"


def _dataset_paths(root: str | Path, dataset: str) -> list[Path]:
    if not dataset.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"invalid dataset name: {dataset!r}")
    base = _knowledge_dir(root)
    active = base / f"{dataset}.json"
    backups = sorted(base.glob(f"{dataset}.corrupt.*.json"), key=lambda p: p.name)
    return [active, *backups]


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _read_json_file(path: Path) -> tuple[Any | None, str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        return None, f"decode_error:{type(exc).__name__}"
    except OSError as exc:
        return None, f"io_error:{type(exc).__name__}"
    try:
        return json.loads(text), "ok"
    except Exception as exc:
        return None, f"json_error:{type(exc).__name__}"


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def _date_range(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    values: list[tuple[datetime, str]] = []
    for row in rows:
        for key in ("created_at", "updated_at", "last_updated", "last_reviewed"):
            raw = row.get(key)
            dt = _parse_dt(raw)
            if dt is not None and isinstance(raw, str):
                values.append((dt, raw))
    if not values:
        return None, None
    return min(values, key=lambda item: item[0])[1], max(values, key=lambda item: item[0])[1]


def _count_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key, "")
        label = value if isinstance(value, str) else str(value)
        counts[label] = counts.get(label, 0) + 1
    return counts


def _valid_candidate_reports(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in report.get("files", [])
        if item.get("role") == "backup"
        and item.get("json_status") == "ok"
        and item.get("top_type") == "list"
        and isinstance(item.get("entries"), int)
        and item["entries"] > 0
        and item.get("unique_ids", 0) > 0
        and item.get("schema_score", 0) > 0
    ]


def _rows_by_id(path: Path) -> dict[str, dict[str, Any]]:
    data, status = _read_json_file(path)
    if status != "ok" or not isinstance(data, list):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            rows[item["id"]] = item
    return rows


def _metadata_without_content(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in _CONTENT_KEYS}


def _is_archived(row: dict[str, Any]) -> bool:
    tier = row.get("tier")
    status = row.get("status")
    return tier == "archived" or status in {"archived", "outdated", "deprecated"}


def _engram_schema_score(field_names: list[str]) -> int:
    fields = set(field_names)
    has_content = bool(fields & _CONTENT_KEYS)
    metadata_count = len(fields & _ENGRAM_METADATA_KEYS)
    if not has_content or metadata_count == 0:
        return 0
    return 1 + metadata_count


def _file_report(path: Path, *, dataset: str, active_name: str) -> dict[str, Any]:
    exists = path.is_file()
    raw = path.read_bytes() if exists else b""
    report: dict[str, Any] = {
        "file_name": path.name,
        "dataset": dataset,
        "role": "active" if path.name == active_name else "backup",
        "exists": exists,
        "bytes": len(raw),
        "sha256_12": hashlib.sha256(raw).hexdigest()[:12] if exists else None,
        "starts_bom": raw.startswith(b"\xef\xbb\xbf"),
        "json_status": "missing",
        "top_type": None,
        "entries": None,
        "dict_entries": 0,
        "field_names": [],
        "content_keys_present": [],
        "schema_score": 0,
        "unique_ids": 0,
        "duplicate_ids": 0,
        "tier_counts": {},
        "sensitivity_counts": {},
        "source_tool_counts": {},
        "date_min": None,
        "date_max": None,
    }
    if not exists:
        return report

    data, status = _read_json_file(path)
    report["json_status"] = status
    if status != "ok":
        return report
    report["top_type"] = type(data).__name__
    if not isinstance(data, list):
        return report

    rows = [item for item in data if isinstance(item, dict)]
    field_names = sorted({key for row in rows for key in row})
    ids = [row.get("id") for row in rows if isinstance(row.get("id"), str)]
    date_min, date_max = _date_range(rows)
    report.update({
        "entries": len(data),
        "dict_entries": len(rows),
        "field_names": field_names,
        "content_keys_present": sorted(set(field_names) & _CONTENT_KEYS),
        "schema_score": _engram_schema_score(field_names),
        "unique_ids": len(set(ids)),
        "duplicate_ids": len(ids) - len(set(ids)),
        "tier_counts": _count_by_key(rows, "tier"),
        "sensitivity_counts": _count_by_key(rows, "sensitivity"),
        "source_tool_counts": _count_by_key(rows, "source_tool"),
        "date_min": date_min,
        "date_max": date_max,
    })
    return report


def _candidate_sort_key(report: dict[str, Any]) -> tuple[int, int, str, int, str]:
    entries = report.get("entries")
    date_max = _parse_dt(report.get("date_max"))
    return (
        entries if isinstance(entries, int) else -1,
        date_max.isoformat() if date_max is not None else "",
        int(report.get("schema_score") or 0),
        int(report.get("bytes") or 0),
        str(report.get("file_name") or ""),
    )


def analyze_json_recovery_candidates(root: str | Path, *, dataset: str = "lessons") -> dict[str, Any]:
    """Return a metadata-only report for the active JSON file and backups."""
    active_name = f"{dataset}.json"
    files = [
        _file_report(path, dataset=dataset, active_name=active_name)
        for path in _dataset_paths(root, dataset)
    ]
    active = next((item for item in files if item["role"] == "active"), None)
    candidates = _valid_candidate_reports({"files": files})
    best = max(candidates, key=_candidate_sort_key) if candidates else None
    return {
        "dataset": dataset,
        "active": active,
        "files": files,
        "candidate_count": len(candidates),
        "best_candidate": best,
        "live_store_modified": False,
    }


def analyze_recovery_retention_plan(
    root: str | Path,
    *,
    dataset: str = "lessons",
    max_entries: int = _ACTIVE_CAP_DEFAULT,
) -> dict[str, Any]:
    """Compare the top two recovery candidates without exposing stored content.

    The returned plan contains counts only: no summaries, details, titles, or
    record IDs. It is intended to guide a human restore decision and never
    modifies the live store.
    """
    report = analyze_json_recovery_candidates(root, dataset=dataset)
    candidates = sorted(_valid_candidate_reports(report), key=_candidate_sort_key, reverse=True)
    primary = candidates[0] if candidates else None
    secondary = candidates[1] if len(candidates) > 1 else None

    plan: dict[str, Any] = {
        "dataset": dataset,
        "max_entries": max_entries,
        "primary_candidate": primary,
        "secondary_candidate": secondary,
        "overlap_ids": 0,
        "primary_only_ids": 0,
        "secondary_only_ids": 0,
        "union_ids": 0,
        "overflow_ids": 0,
        "active_merge_safe": True,
        "secondary_only_archived": 0,
        "overlap_access_metadata_only": 0,
        "recommendation": "no_recovery_candidate",
        "live_store_modified": False,
    }
    if not primary:
        return plan

    knowledge = _knowledge_dir(root)
    primary_rows = _rows_by_id(knowledge / str(primary["file_name"]))
    secondary_rows = _rows_by_id(knowledge / str(secondary["file_name"])) if secondary else {}
    primary_ids = set(primary_rows)
    secondary_ids = set(secondary_rows)
    overlap = primary_ids & secondary_ids
    primary_only = primary_ids - secondary_ids
    secondary_only = secondary_ids - primary_ids
    union_ids = primary_ids | secondary_ids

    metadata_only_delta = 0
    for item_id in overlap:
        left = _metadata_without_content(primary_rows[item_id])
        right = _metadata_without_content(secondary_rows[item_id])
        if left == right:
            continue
        differing = {key for key in set(left) | set(right) if left.get(key) != right.get(key)}
        if differing and differing <= _ACCESS_METADATA_KEYS:
            metadata_only_delta += 1

    overflow = max(0, len(union_ids) - max_entries)
    plan.update({
        "overlap_ids": len(overlap),
        "primary_only_ids": len(primary_only),
        "secondary_only_ids": len(secondary_only),
        "union_ids": len(union_ids),
        "overflow_ids": overflow,
        "active_merge_safe": overflow == 0,
        "secondary_only_archived": sum(1 for item_id in secondary_only if _is_archived(secondary_rows[item_id])),
        "overlap_access_metadata_only": metadata_only_delta,
    })
    if secondary and overflow:
        plan["recommendation"] = "restore_primary_preserve_secondary_overflow"
    elif secondary:
        plan["recommendation"] = "active_merge_possible_with_human_review"
    else:
        plan["recommendation"] = "restore_primary_candidate"
    return plan


def write_recovery_candidate(
    root: str | Path,
    *,
    dataset: str = "lessons",
    output_path: str | Path,
) -> dict[str, Any]:
    """Write the best backup candidate to an explicit destination only."""
    report = analyze_json_recovery_candidates(root, dataset=dataset)
    best = report.get("best_candidate")
    if not isinstance(best, dict):
        raise RuntimeError(f"no valid recovery candidate found for {dataset}")

    knowledge = _knowledge_dir(root)
    source = knowledge / str(best["file_name"])
    destination = Path(output_path).expanduser().resolve()
    live_path = (knowledge / f"{dataset}.json").resolve()
    if destination == live_path or _same_existing_file(destination, live_path):
        raise RuntimeError("refusing to overwrite the live Engram store")
    if destination.exists():
        raise RuntimeError("refusing to overwrite an existing output file")

    data, status = _read_json_file(source)
    if status != "ok":
        raise RuntimeError(f"candidate became unreadable: {status}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as exc:
        if _same_existing_file(destination, live_path):
            raise RuntimeError("refusing to overwrite the live Engram store") from exc
        raise RuntimeError("refusing to overwrite an existing output file") from exc
    return {
        "dataset": dataset,
        "source_file": source.name,
        "output_path": str(destination),
        "entries": best.get("entries"),
        "sha256_12": best.get("sha256_12"),
        "live_store_modified": False,
    }
