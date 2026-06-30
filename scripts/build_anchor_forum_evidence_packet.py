"""Build a local public-safe Anchor/LIVE_SMOKE forum evidence packet."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _load_script_attr(script: Path, name: str):
    spec = importlib.util.spec_from_file_location(script.stem, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load script dependency: {script.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


render_reply = _load_script_attr(ROOT / "scripts" / "render_anchor_forum_reply.py", "render_reply")
validate_payload = _load_script_attr(
    ROOT / "scripts" / "validate_anchor_live_smoke_evidence.py",
    "validate_payload",
)
validation_warnings = _load_script_attr(
    ROOT / "scripts" / "validate_anchor_live_smoke_evidence.py",
    "validation_warnings",
)
contains_private_content = _load_script_attr(
    ROOT / "scripts" / "validate_anchor_live_smoke_evidence.py",
    "contains_private_content",
)


EVIDENCE_FILE = "anchor-live-smoke-evidence.json"
METRICS_FILE = "anchor-live-smoke-metrics.md"
DRAFT_FILE = "cursor-forum-reply-draft.md"
MANIFEST_FILE = "manifest.json"


def _load_json(path_text: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except Exception:
        raise ValueError("evidence file must be valid JSON")
    return data if isinstance(data, dict) else {}


def _prevalidate_aggregate_input(path_text: str) -> None:
    if not path_text:
        return
    loaded = _load_json(path_text)
    if contains_private_content(loaded):
        raise ValueError("aggregate input contains private-looking content")


def _collect_from_cli(args: argparse.Namespace) -> dict[str, Any]:
    _prevalidate_aggregate_input(args.anchor_json)
    _prevalidate_aggregate_input(args.live_smoke_json)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "collect_anchor_live_smoke_evidence.py"),
        "--json",
        "--live" if args.live else "--synthetic",
    ]
    if args.live:
        command.append("--allow-live")
    if args.anchor_json:
        command.extend(["--anchor-json", args.anchor_json])
    if args.live_smoke_json:
        command.extend(["--live-smoke-json", args.live_smoke_json])
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


def _count(payload: dict[str, Any], section: str, key: str) -> int:
    block = payload.get(section)
    if not isinstance(block, dict):
        return 0
    try:
        return max(0, int(block.get(key, 0)))
    except (TypeError, ValueError):
        return 0


def render_metrics(payload: dict[str, Any]) -> str:
    checked = _count(payload, "anchors", "checked")
    valid = _count(payload, "anchors", "valid")
    invalid = _count(payload, "anchors", "invalid")
    unknown = _count(payload, "anchors", "unknown")
    superseded = _count(payload, "anchors", "superseded")
    demoted = _count(payload, "anchors", "demoted_to_staging")
    runs = _count(payload, "live_smoke", "runs")
    passed = _count(payload, "live_smoke", "passed")
    failed = _count(payload, "live_smoke", "failed")
    warnings = validation_warnings(payload)
    warning_lines = ["", "Validation warnings:"] + [f"- {warning}" for warning in warnings] if warnings else []
    return "\n".join([
        "# Anchor / LIVE_SMOKE Metrics",
        "",
        "Owner confirmation required before posting.",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Anchor checks | {checked} anchor checks |",
        f"| Anchor valid | {valid} |",
        f"| Anchor invalid | {invalid} |",
        f"| Anchor unknown | {unknown} |",
        f"| Anchor superseded | {superseded} |",
        f"| Anchor demoted to staging | {demoted} |",
        f"| LIVE_SMOKE runs | {runs} LIVE_SMOKE runs |",
        f"| LIVE_SMOKE passed | {passed} |",
        f"| LIVE_SMOKE failed | {failed} |",
        "",
        "Caveat: local aggregate evidence only; not a broad benchmark or statistically significant result.",
        *warning_lines,
        "",
    ])


def _manifest(label: str, source_mode: str) -> dict[str, Any]:
    return {
        "schema": "anchor_forum_evidence_packet.v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "label": label,
        "source_mode": source_mode,
        "public_action": False,
        "owner_confirmation_required": True,
        "files": {
            "evidence": EVIDENCE_FILE,
            "metrics": METRICS_FILE,
            "draft": DRAFT_FILE,
        },
    }


def build_packet(payload: dict[str, Any], out_dir: Path, *, label: str, source_mode: str) -> None:
    errors = validate_payload(payload)
    if errors:
        raise ValueError("validation failed: " + "; ".join(errors))

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / EVIDENCE_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / METRICS_FILE).write_text(render_metrics(payload), encoding="utf-8")
    (out_dir / DRAFT_FILE).write_text(render_reply(payload), encoding="utf-8")
    (out_dir / MANIFEST_FILE).write_text(
        json.dumps(_manifest(label, source_mode), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence", default="", help="Existing aggregate evidence JSON.")
    source.add_argument("--synthetic", action="store_true", help="Build from synthetic collector data.")
    source.add_argument("--live", action="store_true", help="Build from owner-approved live aggregate collector data.")
    parser.add_argument("--allow-live", action="store_true", help="Required with --live.")
    parser.add_argument("--anchor-json", default="", help="Optional aggregate anchor JSON input.")
    parser.add_argument("--live-smoke-json", default="", help="Optional aggregate LIVE_SMOKE JSON input.")
    parser.add_argument("--out-dir", required=True, help="Local output directory for packet files.")
    parser.add_argument("--label", default="weekend-evidence", help="Human-readable packet label.")
    args = parser.parse_args()

    if args.live and not args.allow_live:
        print("--live requires --allow-live", file=sys.stderr)
        return 2
    if args.evidence and (args.anchor_json or args.live_smoke_json):
        print("--anchor-json and --live-smoke-json are only used with collector modes", file=sys.stderr)
        return 2

    try:
        payload = _load_json(args.evidence) if args.evidence else _collect_from_cli(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    source_mode = "evidence-file" if args.evidence else ("live" if args.live else "synthetic")
    try:
        build_packet(payload, Path(args.out_dir), label=args.label, source_mode=source_mode)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("packet built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
