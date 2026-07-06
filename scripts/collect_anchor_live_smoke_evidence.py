"""Collect public-safe aggregate Anchor/LIVE_SMOKE evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("ENGRAM_TEST", "1")

from piia_engram.core import Engram  # noqa: E402


ZERO_ANCHOR_REASON = "current live store has no structured anchor records"


def _empty_anchor_counts() -> dict[str, int]:
    return {
        "checked": 0,
        "valid": 0,
        "invalid": 0,
        "unknown": 0,
        "superseded": 0,
        "demoted_to_staging": 0,
    }


def _empty_live_smoke_counts() -> dict[str, Any]:
    return {
        "runs": 0,
        "passed": 0,
        "failed": 0,
        "failure_classes": {},
    }


def _load_json_file(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _non_negative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _safe_failure_class(label: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_.-")
    if 1 <= len(label) <= 64 and all(char in allowed for char in label):
        return label
    return "other"


def _merge_anchor_counts(base: dict[str, int], loaded: dict[str, Any]) -> dict[str, int]:
    source = loaded.get("anchors") if isinstance(loaded.get("anchors"), dict) else loaded
    merged = dict(base)
    if not isinstance(source, dict):
        return merged
    for key, current in merged.items():
        merged[key] = _non_negative_int(source.get(key, current), current)
    return merged


def _merge_live_smoke_counts(base: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    source = loaded.get("live_smoke") if isinstance(loaded.get("live_smoke"), dict) else loaded
    merged = dict(base)
    if not isinstance(source, dict):
        return merged
    for key in ("runs", "passed", "failed"):
        merged[key] = _non_negative_int(source.get(key, merged[key]), merged[key])
    failures = source.get("failure_classes")
    if isinstance(failures, dict):
        clean_failures: dict[str, int] = {}
        for key, value in failures.items():
            if not isinstance(key, str):
                continue
            clean_key = _safe_failure_class(key)
            clean_failures[clean_key] = clean_failures.get(clean_key, 0) + _non_negative_int(value, 0)
        merged["failure_classes"] = clean_failures
    return merged


def synthetic_payload() -> dict[str, Any]:
    anchors = _empty_anchor_counts()
    anchors.update({
        "checked": 5,
        "valid": 3,
        "invalid": 1,
        "unknown": 1,
        "demoted_to_staging": 1,
    })
    live_smoke = _empty_live_smoke_counts()
    live_smoke.update({"runs": 3, "passed": 3})
    return _base_payload(mode="synthetic", anchors=anchors, live_smoke=live_smoke)


def _iter_knowledge_items(eng: Engram) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for getter_name in ("get_lessons", "get_decisions", "get_playbooks"):
        getter = getattr(eng, getter_name, None)
        if getter is None:
            continue
        try:
            batch = getter(limit=None, _update_access=False)
        except TypeError:
            batch = getter(limit=None)
        except Exception:
            batch = []
        if isinstance(batch, list):
            items.extend(item for item in batch if isinstance(item, dict))
    return items


def live_aggregate_payload() -> dict[str, Any]:
    eng = Engram(read_only=True)
    anchors = _empty_anchor_counts()
    for item in _iter_knowledge_items(eng):
        provenance = item.get("provenance")
        if not isinstance(provenance, dict) or "anchor_ref" not in provenance:
            continue
        anchors["checked"] += 1
        status = str(provenance.get("anchor_status") or "unknown").strip().lower()
        if status in {"valid", "invalid", "unknown"}:
            anchors[status] += 1
        else:
            anchors["unknown"] += 1
        if provenance.get("anchor_event") == "superseded":
            anchors["superseded"] += 1
        if status == "invalid" and item.get("tier") == "staging":
            anchors["demoted_to_staging"] += 1
    return _base_payload(
        mode="live",
        anchors=anchors,
        live_smoke=_collect_live_smoke_counts(),
    )


def _collect_live_smoke_counts() -> dict[str, Any]:
    counts = _empty_live_smoke_counts()
    counts["runs"] = 1
    script = ROOT / "scripts" / "diagnose_wrap_up_session.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(result.stdout)
    except Exception:
        counts["failed"] = 1
        counts["failure_classes"] = {"diagnostic_failed": 1}
        return counts

    if payload.get("schema") == "wrap_up_session_diagnostic.v1":
        counts["passed"] = 1
    else:
        counts["failed"] = 1
        counts["failure_classes"] = {"unexpected_schema": 1}
    return counts


def _base_payload(
    *,
    mode: str,
    anchors: dict[str, int],
    live_smoke: dict[str, Any],
) -> dict[str, Any]:
    notes = [
        "Aggregate counts only.",
        "No raw memory bodies, local paths, or private identifiers.",
    ]
    if anchors.get("checked", 0) == 0:
        notes.append(f"Anchor checks are 0 because {ZERO_ANCHOR_REASON}.")
    return {
        "schema": "anchor_live_smoke_evidence.v1",
        "date": date.today().isoformat(),
        "public_safe": True,
        "mode": mode,
        "anchors": anchors,
        "live_smoke": live_smoke,
        "notes": notes,
    }


def _ensure_zero_anchor_reason(payload: dict[str, Any]) -> None:
    anchors = payload.get("anchors") if isinstance(payload.get("anchors"), dict) else {}
    if _non_negative_int(anchors.get("checked", 0), 0) != 0:
        return
    notes = payload.get("notes")
    if not isinstance(notes, list):
        notes = []
    reason = f"Anchor checks are 0 because {ZERO_ANCHOR_REASON}."
    if reason not in notes:
        notes.append(reason)
    payload["notes"] = notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic fixture counts.")
    parser.add_argument("--live", action="store_true", help="Collect owner-approved live aggregate counts.")
    parser.add_argument("--allow-live", action="store_true", help="Required with --live.")
    parser.add_argument("--anchor-json", default="", help="Optional aggregate anchor JSON file.")
    parser.add_argument("--live-smoke-json", default="", help="Optional aggregate LIVE_SMOKE JSON file.")
    parser.add_argument("--out", default="", help="Optional path for public-safe JSON output.")
    args = parser.parse_args()

    if args.live and not args.allow_live:
        print("--live requires --allow-live", file=sys.stderr)
        return 2
    if args.synthetic == args.live:
        print("choose exactly one of --synthetic or --live", file=sys.stderr)
        return 2

    payload = synthetic_payload() if args.synthetic else live_aggregate_payload()
    if args.anchor_json:
        payload["anchors"] = _merge_anchor_counts(payload["anchors"], _load_json_file(args.anchor_json))
    if args.live_smoke_json:
        payload["live_smoke"] = _merge_live_smoke_counts(
            payload["live_smoke"],
            _load_json_file(args.live_smoke_json),
        )
    _ensure_zero_anchor_reason(payload)
    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Anchor/LIVE_SMOKE aggregate evidence")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
