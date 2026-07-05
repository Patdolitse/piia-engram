"""Append public-safe Anchor/LIVE_SMOKE aggregate evidence to local history."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HISTORY_DIR = ROOT / ".engram-local-evidence" / "anchor-live-smoke-history"
HISTORY_FILE = "anchor-live-smoke-history.jsonl"
LATEST_FILE = "latest.json"
SUMMARY_FILE = "summary.md"
ENTRY_SCHEMA = "anchor_live_smoke_history_entry.v1"
SUMMARY_SCHEMA = "anchor_live_smoke_history_summary.v1"
ZERO_ANCHOR_REASON = "current live store has no structured anchor records"


def _load_script_attr(script: Path, name: str):
    spec = importlib.util.spec_from_file_location(script.stem, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load script dependency: {script.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


validate_payload = _load_script_attr(
    ROOT / "scripts" / "validate_anchor_live_smoke_evidence.py",
    "validate_payload",
)
contains_private_content = _load_script_attr(
    ROOT / "scripts" / "validate_anchor_live_smoke_evidence.py",
    "contains_private_content",
)


ANCHOR_KEYS = (
    "checked",
    "valid",
    "invalid",
    "unknown",
    "superseded",
    "demoted_to_staging",
)
LIVE_KEYS = ("runs", "passed", "failed")


def _now_utc_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json(path_text: str) -> dict[str, Any]:
    try:
        loaded = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except Exception:
        raise ValueError("evidence file must be valid JSON")
    return loaded if isinstance(loaded, dict) else {}


def _collect_from_cli(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "collect_anchor_live_smoke_evidence.py"),
        "--json",
        "--live" if args.live else "--synthetic",
    ]
    if args.live:
        command.append("--allow-live")
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    loaded = json.loads(result.stdout)
    return loaded if isinstance(loaded, dict) else {}


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _counts(payload: dict[str, Any], section: str, keys: tuple[str, ...]) -> dict[str, int]:
    source = payload.get(section) if isinstance(payload.get(section), dict) else {}
    return {key: _non_negative_int(source.get(key, 0)) for key in keys}


def _failure_classes(payload: dict[str, Any]) -> dict[str, int]:
    live_smoke = payload.get("live_smoke") if isinstance(payload.get("live_smoke"), dict) else {}
    failures = live_smoke.get("failure_classes") if isinstance(live_smoke.get("failure_classes"), dict) else {}
    clean: dict[str, int] = {}
    for key, value in failures.items():
        if not isinstance(key, str):
            continue
        clean[key] = clean.get(key, 0) + _non_negative_int(value)
    return clean


def normalize_entry(payload: dict[str, Any], collected_at: str) -> dict[str, Any]:
    if contains_private_content(payload):
        raise ValueError("private-looking content detected")
    errors = validate_payload(payload)
    if errors:
        raise ValueError("validation failed: " + "; ".join(errors))

    anchors = _counts(payload, "anchors", ANCHOR_KEYS)
    live_smoke: dict[str, Any] = _counts(payload, "live_smoke", LIVE_KEYS)
    live_smoke["failure_classes"] = _failure_classes(payload)
    entry: dict[str, Any] = {
        "schema": ENTRY_SCHEMA,
        "collected_at": collected_at,
        "mode": str(payload.get("mode") or "manual"),
        "anchors": anchors,
        "live_smoke": live_smoke,
        "public_safe": True,
    }
    if anchors["checked"] == 0:
        entry["anchor_zero_reason"] = ZERO_ANCHOR_REASON
    return entry


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except Exception:
            continue
        if isinstance(loaded, dict) and loaded.get("schema") == ENTRY_SCHEMA:
            entries.append(loaded)
    return entries


def _sum_entries(entries: list[dict[str, Any]], *, days: int, as_of: datetime) -> dict[str, Any]:
    cutoff = as_of - timedelta(days=days)
    selected = [
        entry
        for entry in entries
        if _parse_time(str(entry.get("collected_at", "1970-01-01T00:00:00Z"))) >= cutoff
    ]
    anchors = {key: 0 for key in ANCHOR_KEYS}
    live_smoke: dict[str, Any] = {key: 0 for key in LIVE_KEYS}
    live_smoke["failure_classes"] = {}
    for entry in selected:
        entry_anchors = entry.get("anchors") if isinstance(entry.get("anchors"), dict) else {}
        for key in ANCHOR_KEYS:
            anchors[key] += _non_negative_int(entry_anchors.get(key, 0))
        entry_live = entry.get("live_smoke") if isinstance(entry.get("live_smoke"), dict) else {}
        for key in LIVE_KEYS:
            live_smoke[key] += _non_negative_int(entry_live.get(key, 0))
        failures = entry_live.get("failure_classes") if isinstance(entry_live.get("failure_classes"), dict) else {}
        for label, value in failures.items():
            if not isinstance(label, str):
                continue
            live_smoke["failure_classes"][label] = (
                live_smoke["failure_classes"].get(label, 0) + _non_negative_int(value)
            )

    notes = [
        "Aggregate counts only.",
        "No raw memory bodies, local paths, or private identifiers.",
        f"History window: last {days} days from local aggregate entries.",
    ]
    if anchors["checked"] == 0:
        notes.append(f"Anchor checks are 0 because {ZERO_ANCHOR_REASON}.")
    evidence = {
        "schema": "anchor_live_smoke_evidence.v1",
        "date": as_of.date().isoformat(),
        "public_safe": True,
        "mode": f"history_{days}d",
        "anchors": anchors,
        "live_smoke": live_smoke,
        "notes": notes,
    }
    return {
        "days": days,
        "entry_count": len(selected),
        "evidence": evidence,
    }


def build_summary(entries: list[dict[str, Any]], *, generated_at: str) -> dict[str, Any]:
    as_of = max((_parse_time(str(entry.get("collected_at"))) for entry in entries), default=_parse_time(generated_at))
    return {
        "schema": SUMMARY_SCHEMA,
        "generated_at": generated_at,
        "public_safe": True,
        "history_entries": len(entries),
        "windows": {
            "7d": _sum_entries(entries, days=7, as_of=as_of),
            "14d": _sum_entries(entries, days=14, as_of=as_of),
        },
    }


def render_summary_md(summary: dict[str, Any]) -> str:
    lines = [
        "# Anchor / LIVE_SMOKE History Summary",
        "",
        "Local aggregate history only. Owner confirmation is required before any public use.",
        "",
        "| Window | Entries | Anchor checks | LIVE_SMOKE runs | LIVE_SMOKE passed | LIVE_SMOKE failed |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    windows = summary.get("windows") if isinstance(summary.get("windows"), dict) else {}
    for key, label in (("7d", "Last 7 days"), ("14d", "Last 14 days")):
        window = windows.get(key) if isinstance(windows.get(key), dict) else {}
        evidence = window.get("evidence") if isinstance(window.get("evidence"), dict) else {}
        anchors = evidence.get("anchors") if isinstance(evidence.get("anchors"), dict) else {}
        live = evidence.get("live_smoke") if isinstance(evidence.get("live_smoke"), dict) else {}
        lines.append(
            "| {label} | {entries} | {checked} | {runs} | {passed} | {failed} |".format(
                label=label,
                entries=_non_negative_int(window.get("entry_count", 0)),
                checked=_non_negative_int(anchors.get("checked", 0)),
                runs=_non_negative_int(live.get("runs", 0)),
                passed=_non_negative_int(live.get("passed", 0)),
                failed=_non_negative_int(live.get("failed", 0)),
            )
        )
    lines.extend([
        "",
        "Caveat: this is not a benchmark and not statistically significant.",
        "",
    ])
    return "\n".join(lines)


def append_history(payload: dict[str, Any], history_dir: Path, *, collected_at: str) -> dict[str, Any]:
    entry = normalize_entry(payload, collected_at)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / HISTORY_FILE
    with history_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    entries = _read_entries(history_file)
    summary = build_summary(entries, generated_at=collected_at)
    (history_dir / LATEST_FILE).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (history_dir / SUMMARY_FILE).write_text(render_summary_md(summary), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence", default="", help="Existing aggregate evidence JSON.")
    source.add_argument("--synthetic", action="store_true", help="Append synthetic collector data.")
    source.add_argument("--live", action="store_true", help="Append owner-approved live aggregate collector data.")
    parser.add_argument("--allow-live", action="store_true", help="Required with --live.")
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR), help="Local history output directory.")
    parser.add_argument("--collected-at", default="", help="ISO UTC timestamp for tests or manual backfill.")
    parser.add_argument("--json", action="store_true", help="Print summary JSON.")
    args = parser.parse_args()

    if args.live and not args.allow_live:
        print("--live requires --allow-live", file=sys.stderr)
        return 2

    collected_at = args.collected_at or _now_utc_text()
    try:
        _parse_time(collected_at)
        payload = _load_json(args.evidence) if args.evidence else _collect_from_cli(args)
        summary = append_history(payload, Path(args.history_dir), collected_at=collected_at)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("history appended")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
