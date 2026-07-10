"""Build a private longitudinal real-use evidence artifact.

Inputs are explicit and read-only. The output is path-independent and intended
for owner review, not automatic public export.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from longitudinal_real_use_evidence import build_evidence, render_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--first-value-jsonl", default="", help="Explicit first_value_events.jsonl path.")
    parser.add_argument("--anchor-run-jsonl", default="", help="Explicit Anchor LIVE_SMOKE run JSONL path.")
    parser.add_argument(
        "--memory-eval-json",
        action="append",
        default=[],
        help="Explicit memory eval aggregate JSON path; may be repeated.",
    )
    parser.add_argument("--as-of", required=True, help="UTC ISO timestamp or date used as deterministic window end.")
    parser.add_argument("--window-days", required=True, type=int, help="Positive UTC day window size.")
    parser.add_argument("--output", default="", help="Optional output artifact path.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON instead of text preview.")
    args = parser.parse_args(argv)

    try:
        artifact = build_evidence(
            first_value_jsonl=args.first_value_jsonl or None,
            anchor_run_jsonl=args.anchor_run_jsonl or None,
            memory_eval_jsons=args.memory_eval_json,
            as_of=args.as_of,
            window_days=args.window_days,
        )
    except ValueError:
        print("invalid longitudinal evidence arguments", file=sys.stderr)
        return 2

    rendered = (
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.as_json
        else render_text(artifact)
    )
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except Exception:
            print("output_write_failed", file=sys.stderr)
            return 1
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
