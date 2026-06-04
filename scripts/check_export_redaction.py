"""Export redaction linter CLI — scan generated export surfaces for leaks.

Runs the string-level boundary linter (:mod:`piia_engram.export_redaction`) over
one or more rendered export files (identity card, AGENTS.md export, public report,
harness trace) and reports any credential / absolute-home-path / bare-email shapes
that must not cross the export boundary.

Output is METADATA-ONLY: category counts and redacted previews (``sk-***``); the
raw matched text is never printed, so the linter's own output is safe to paste
into a committable evidence file.

    python scripts/check_export_redaction.py path/to/identity_card.md ...
    python scripts/check_export_redaction.py --strict file.md    # warn-level also blocks
    python scripts/check_export_redaction.py --json file.md

Exit codes:
- 0  every scanned surface is clean (no blocking findings)
- 1  a blocking finding was detected (credential always; warn too with --strict)
- 2  setup error (file missing / unreadable)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from a source checkout without an editable install.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from piia_engram.export_redaction import (  # noqa: E402
    is_export_clean,
    scan_export_text,
    summarize_findings,
)


def _scan_file(path: Path, *, allow_warn: bool) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    findings = scan_export_text(text)
    clean = is_export_clean(text, allow_warn=allow_warn)
    return {
        "file": path.name,  # name only — never an absolute user path
        "clean": clean,
        "summary": summarize_findings(findings),
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="rendered export files to scan")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warn-level findings (abs paths, emails) as blocking too",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    allow_warn = not args.strict
    results = []
    setup_error = False
    for raw in args.files:
        p = Path(raw)
        if not p.is_file():
            setup_error = True
            results.append({"file": p.name, "error": "not found"})
            continue
        results.append(_scan_file(p, allow_warn=allow_warn))

    if setup_error:
        if args.json:
            print(json.dumps({"ok": False, "results": results}, ensure_ascii=False))
        else:
            print("setup error: one or more files not found")
        return 2

    blocking = [r for r in results if not r.get("clean", False)]
    ok = not blocking

    if args.json:
        print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            s = r["summary"]
            status = "OK" if r["clean"] else "LEAK"
            print(f"[{status}] {r['file']}  total={s['total']} high={s['high_severity']} {s['by_category']}")
            if not r["clean"]:
                for f in r["findings"]:
                    if f["severity"] == "high" or args.strict:
                        print(f"    - {f['severity']:>4} {f['category']} @ {f['offset']}  {f['preview']}")
        print()
        print("RESULT:", "clean" if ok else "blocking findings present")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
