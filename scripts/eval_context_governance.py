"""Run the offline context-governance preview eval.

The eval uses synthetic fixtures only. It never reads the user's real Engram
store, writes knowledge, publishes drafts, or applies governance decisions.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from piia_engram import context_governance as cg  # noqa: E402


SECRET_SAMPLE = "sk-test_1234567890abcdef1234567890abcdef"


class FixtureEngram:
    root = None

    def get_lessons(self, limit=None, _update_access=True):
        return [
            {
                "id": "lesson-old",
                "summary": "Use release gates before public actions",
                "status": "active",
                "last_validated_at": "2020-01-01",
            }
        ]

    def get_decisions(self, limit=None, _update_access=True):
        return [
            {
                "id": "decision-old",
                "question": "Publish automatically?",
                "choice": "No, require owner confirmation",
                "status": "active",
                "last_validated_at": "2020-01-01",
            }
        ]

    def get_safe_profile(self):
        return {"role": "tester", "language": "zh"}

    def get_recent_context(self, limit=1):
        return []

    def get_relevant_lessons(self, **kwargs):
        return [{"id": "recall-1", "summary": f"token {SECRET_SAMPLE}"}]


def run_eval(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    fixtures = [
        cg.build_context_governance_preview(
            "safe_context",
            payload={"knowledge": [{"summary": f"token {SECRET_SAMPLE}"}]},
            options={"max_chars": 2000},
            now=now,
        ),
        cg.build_context_governance_preview(
            "freshness_conflicts",
            engram=FixtureEngram(),
            now=now,
        ),
        cg.build_context_governance_preview(
            "replay_packet",
            payload={"compact_summary": f"resume without leaking {SECRET_SAMPLE}"},
            options={"source": "eval", "max_summary_chars": 120},
            now=now,
        ),
        cg.build_context_governance_preview(
            "external_evidence",
            payload={"evidence": [{"label": "local check", "status": "verified"}]},
            options={"title": "Local Evidence Draft"},
            now=now,
        ),
    ]
    serialized = json.dumps(fixtures, ensure_ascii=False, sort_keys=True)
    applied_false = sum(item.get("applied") is False for item in fixtures)
    invariant_marked = sum("preview" in str(item.get("invariant", "")) or item.get("mode") == "replay_packet" for item in fixtures)
    publication_guarded = "owner_confirmation_required" in serialized
    secret_redacted = SECRET_SAMPLE not in serialized
    ok = (
        len(fixtures) == len(cg.MODES)
        and applied_false == len(fixtures)
        and invariant_marked == len(fixtures)
        and publication_guarded
        and secret_redacted
    )
    return {
        "schema_version": 1,
        "ok": ok,
        "fixture_count": len(fixtures),
        "mode_count": len(cg.MODES),
        "applied_false": applied_false,
        "invariant_marked": invariant_marked,
        "publication_guarded": publication_guarded,
        "secret_redacted": secret_redacted,
        "note": "synthetic offline eval only; no real store read and no side effects",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)
    result = run_eval()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "OK" if result["ok"] else "FAIL"
        print(f"[{status}] context-governance eval: {result['fixture_count']} fixtures")
        print(result["note"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
