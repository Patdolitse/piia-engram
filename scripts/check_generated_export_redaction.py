"""Generate synthetic export surfaces and scan them for redaction leaks.

Unlike ``check_export_redaction.py`` over a committed clean sample, this guard
builds a temporary Engram store, seeds fake leak-shaped data, renders real
export surfaces, and then runs the export redaction linter in strict mode.

No real user data is read. Output is metadata-only: surface names and counts,
never raw generated content.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from piia_engram.agents_md_export import build_agents_md_export  # noqa: E402
from piia_engram.core import Engram  # noqa: E402
from piia_engram.export_redaction import (  # noqa: E402
    is_export_clean,
    scan_export_text,
    summarize_findings,
)


FAKE_OPENAI = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FAKE_WIN_PATH = r"C:\Users\victim\secret\notes.txt"
FAKE_EMAIL = "victim@corp.example.com"


def _seed_synthetic_store(root: Path) -> Engram:
    eng = Engram(root=root)
    eng.update_profile({
        "role": "synthetic developer",
        "language": "English",
        "description": "CI-only export redaction fixture",
    })
    eng.add_lesson({
        "summary": f"deploy note accidentally included {FAKE_OPENAI}",
        "detail": f"old path was {FAKE_WIN_PATH}",
        "domain": FAKE_EMAIL,
        "source_tool": FAKE_WIN_PATH,
        "tier": "verified",
        "status": "active",
    })
    eng.add_decision({
        "question": "where should synthetic export evidence live",
        "choice": f"never send reports to {FAKE_EMAIL}",
        "reasoning": f"the fake path {FAKE_WIN_PATH} must be redacted",
        "domain": FAKE_EMAIL,
        "tier": "verified",
        "status": "active",
    })
    eng.add_lesson({
        "summary": "Always pin dependency versions in CI",
        "domain": "ci",
        "tier": "verified",
        "status": "active",
    })
    return eng


def _scan_surface(surface: str, path: Path, text: str) -> dict:
    findings = scan_export_text(text)
    return {
        "surface": surface,
        "file": path.name,
        "clean": is_export_clean(text, allow_warn=False),
        "summary": summarize_findings(findings),
    }


def run_guard(work_dir: Path | None = None) -> dict:
    """Generate and scan synthetic export surfaces.

    Returns a metadata-only report. ``root`` is a directory name only, never an
    absolute path, so JSON output is safe to paste into release evidence.
    """
    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="engram-export-redaction-") as raw:
            return run_guard(Path(raw))

    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    old_engram_test = os.environ.get("ENGRAM_TEST")
    os.environ["ENGRAM_TEST"] = "1"
    try:
        eng = _seed_synthetic_store(root / "engram")
        out_dir = root / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)

        identity_text = eng.export_identity_card()
        identity_path = eng._exports_dir / "identity_card.md"

        report_text = eng.export_knowledge_report()
        report_candidates = sorted(eng._exports_dir.glob("knowledge_report_*.md"))
        report_path = report_candidates[-1] if report_candidates else out_dir / "knowledge_report.md"

        agents_text = build_agents_md_export(
            lessons=eng.get_lessons(limit=None, _update_access=False),
            decisions=eng.get_decisions(limit=None, _update_access=False),
        )
        agents_path = out_dir / "AGENTS_block.md"
        agents_path.write_text(agents_text, encoding="utf-8")
    finally:
        if old_engram_test is None:
            os.environ.pop("ENGRAM_TEST", None)
        else:
            os.environ["ENGRAM_TEST"] = old_engram_test

    surfaces = [
        _scan_surface("identity_card", identity_path, identity_text),
        _scan_surface("knowledge_report", report_path, report_text),
        _scan_surface("agents_md", agents_path, agents_text),
    ]
    return {
        "ok": all(item["clean"] for item in surfaces),
        "root": root.name,
        "surfaces": surfaces,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default="", help="optional temp work dir")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    result = run_guard(Path(args.work_dir) if args.work_dir else None)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result["surfaces"]:
            status = "OK" if item["clean"] else "LEAK"
            summary = item["summary"]
            print(
                f"[{status}] {item['surface']} {item['file']} "
                f"total={summary['total']} high={summary['high_severity']} "
                f"{summary['by_category']}"
            )
        print()
        print("RESULT:", "clean" if result["ok"] else "blocking findings present")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
