"""Collect public-safe aggregate Anchor/LIVE_SMOKE evidence."""

from __future__ import annotations

import argparse
import json
import os
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
    eng = Engram()
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
        live_smoke=_empty_live_smoke_counts(),
    )


def _base_payload(
    *,
    mode: str,
    anchors: dict[str, int],
    live_smoke: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "anchor_live_smoke_evidence.v1",
        "date": date.today().isoformat(),
        "public_safe": True,
        "mode": mode,
        "anchors": anchors,
        "live_smoke": live_smoke,
        "notes": [
            "Aggregate counts only.",
            "No raw memory bodies, local paths, or private identifiers.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic fixture counts.")
    parser.add_argument("--live", action="store_true", help="Collect owner-approved live aggregate counts.")
    parser.add_argument("--allow-live", action="store_true", help="Required with --live.")
    args = parser.parse_args()

    if args.live and not args.allow_live:
        print("--live requires --allow-live", file=sys.stderr)
        return 2
    if args.synthetic == args.live:
        print("choose exactly one of --synthetic or --live", file=sys.stderr)
        return 2

    payload = synthetic_payload() if args.synthetic else live_aggregate_payload()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Anchor/LIVE_SMOKE aggregate evidence")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
