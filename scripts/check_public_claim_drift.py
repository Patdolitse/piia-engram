"""Public CLAIM drift sweep — catch stale numbers the surface-list guard can't see.

WHY: ``check_public_fact_sync.py`` is authoritative but EXPLICIT — it only checks
the surfaces named in ``docs/public-facts.json::current_state_surfaces``. If a NEW
doc (a fresh integration guide, a listing copy, a blog-style README section)
starts carrying a test count, a tool count, or a benchmark/compatibility claim and
nobody adds it to that list, the stale number is invisible to the guard. This
sweep closes that gap: it scans EVERY tracked Markdown surface (minus the
intentionally-historical ones) for quantified self-claims and asserts each agrees
with the manifest.

It is offline and deterministic — no web access, no network. It reads only
``docs/public-facts.json`` and the tracked doc files.

What it catches:
- ``<N> passed`` / ``<N> skipped`` / ``<N> (tests) collected`` test-count claims
  that disagree with ``facts.test_passed / test_skipped / test_collected``.
- ``<N> MCP tools`` / ``ships <N> ... tools`` tool-count claims that disagree with
  ``facts.mcp_tools_total``.
- A small bl* of *overclaim phrases* (universal-compatibility / guaranteed
  continuity / unverified live-agent claims) that must never appear in public copy.

Historical surfaces (CHANGELOG, release-evidence/) and a short, EXPLICIT ignore
list (docs that legitimately quote an old number as an example) are skipped and
reported, so nothing is silently excluded.

    python scripts/check_public_claim_drift.py            # human report
    python scripts/check_public_claim_drift.py --json     # machine-readable
    python scripts/check_public_claim_drift.py --list     # show scanned/skipped files

Exit codes:
- 0  every quantified public claim agrees with the manifest; no overclaim phrase
- 1  drift or overclaim found
- 2  setup error (manifest missing/invalid, not a repo root)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MANIFEST = "docs/public-facts.json"

# Surfaces that legitimately quote an OLD number as an example / teaching aid, or
# that describe the drift machinery itself. Skipped EXPLICITLY (reported, never
# silent). Keep this list short and justified.
_EXPLICIT_IGNORE = (
    "docs/runbooks/public-truth-sync.md",   # documents the re-derive workflow, cites examples
)

# Path prefixes that are historical by construction (carry old numbers on purpose).
_HISTORICAL_PREFIXES = (
    "CHANGELOG",
    "release-evidence/",
)

# Quantified-claim patterns -> manifest fact key they must equal.
_TEST_CLAIM_PATTERNS = (
    (re.compile(r"(\d{3,6})\s+passed"), "test_passed"),
    (re.compile(r"测试通过[^\d]{0,8}\*{0,2}(\d{3,6})"), "test_passed"),
    (re.compile(r"(\d{1,4})\s+skipped"), "test_skipped"),
    (re.compile(r"(\d{3,6})\s+(?:tests?\s+)?collected"), "test_collected"),
    (re.compile(r"共收集\s*(\d{3,6})"), "test_collected"),
)
_TOOL_CLAIM_PATTERNS = (
    (re.compile(r"(\d{2,3})\s+MCP tools"), "mcp_tools_total"),
    (re.compile(r"ships\s+(\d{2,3})\s+MCP"), "mcp_tools_total"),
    (re.compile(r"(\d{2,3})\s*个\s*MCP\s*工具"), "mcp_tools_total"),
    (re.compile(r"(\d{2,3})\s*个知识生命周期管理工具"), "mcp_tools_total"),
)

# Negation / disclaimer markers. A phrase that appears in the SAME or an adjacent
# context line carrying one of these is a "don't say this" example, not an actual
# claim — the docs that teach honest positioning legitimately quote the bad phrase.
_NEGATION_MARKERS = (
    "avoid", "don't", "do not", "never", "not claim", "no claim",
    "overstate", "overclaim", "instead of", "rather than", "broad claim",
    "避免", "不要", "不得", "不应", "禁止", "切勿", "夸大",
)

# Overclaim phrases that must never appear in public copy. Lowercased substring
# match. Deliberately narrow (quantified/absolute compatibility + unverified
# live-agent claims) so it does not collide with honest hedged language.
_OVERCLAIM_PHRASES = (
    "works with every ai tool",
    "compatible with all ai",
    "100% compatible",
    "guaranteed continuity",
    "guaranteed memory",
    "verified live agent continuity",
    "proven across all clients",
    "universal memory across all",
)


class SetupError(Exception):
    """Manifest/config problem — distinct from a drift finding (exit 2)."""


def _load_manifest(root: Path, manifest_rel: str) -> dict:
    path = root / manifest_rel
    if not path.is_file():
        raise SetupError(f"manifest not found: {manifest_rel}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SetupError(f"manifest is not valid JSON: {exc}") from exc
    facts = data.get("facts")
    if not isinstance(facts, dict):
        raise SetupError("manifest missing 'facts' object")
    for key in ("test_passed", "test_skipped", "test_collected", "mcp_tools_total"):
        if key not in facts:
            raise SetupError(f"manifest facts missing '{key}'")
    return data


def _tracked_markdown(root: Path) -> list[str]:
    """All git-tracked .md files (POSIX-relative), or a filesystem fallback."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.md", "**/*.md"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        files = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        if files:
            return sorted(set(files))
    except (OSError, subprocess.SubprocessError):
        pass
    # Fallback: walk the tree (used when git is unavailable, e.g. an sdist).
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md"))


def _is_historical(rel: str) -> bool:
    return any(rel == p or rel.startswith(p) for p in _HISTORICAL_PREFIXES)


def scan(root: Path, manifest_rel: str = DEFAULT_MANIFEST) -> dict:
    """Run the sweep. Returns a structured, metadata-only result dict."""
    manifest = _load_manifest(root, manifest_rel)
    facts = manifest["facts"]
    expected = {
        "test_passed": int(facts["test_passed"]),
        "test_skipped": int(facts["test_skipped"]),
        "test_collected": int(facts["test_collected"]),
        "mcp_tools_total": int(facts["mcp_tools_total"]),
    }

    scanned: list[str] = []
    skipped: list[dict] = []
    problems: list[dict] = []

    for rel in _tracked_markdown(root):
        if _is_historical(rel):
            skipped.append({"file": rel, "reason": "historical"})
            continue
        if rel in _EXPLICIT_IGNORE:
            skipped.append({"file": rel, "reason": "explicit_ignore"})
            continue
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        scanned.append(rel)

        for patterns in (_TEST_CLAIM_PATTERNS, _TOOL_CLAIM_PATTERNS):
            for rx, key in patterns:
                for m in rx.finditer(text):
                    claimed = int(m.group(1))
                    if claimed != expected[key]:
                        problems.append({
                            "file": rel,
                            "kind": "stale_claim",
                            "fact": key,
                            "claimed": claimed,
                            "expected": expected[key],
                        })

        # Overclaim scan is line-aware: a phrase quoted under "Avoid:" / negated
        # in its own or an adjacent line is a teaching example, not a live claim.
        lines = text.splitlines()
        lowered_lines = [ln.lower() for ln in lines]
        for i, line in enumerate(lowered_lines):
            for phrase in _OVERCLAIM_PHRASES:
                if phrase not in line:
                    continue
                context = " ".join(lowered_lines[max(0, i - 2): i + 1])
                if any(mark in context for mark in _NEGATION_MARKERS):
                    continue  # disclaimer / bad-example, not an actual claim
                problems.append({
                    "file": rel,
                    "kind": "overclaim_phrase",
                    "phrase": phrase,
                })

    # De-dup identical problems (same file/kind/fact/value or phrase).
    seen = set()
    deduped = []
    for p in problems:
        sig = tuple(sorted(p.items()))
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(p)

    return {
        "ok": not deduped,
        "expected": expected,
        "scanned_count": len(scanned),
        "skipped": skipped,
        "scanned": scanned,
        "problems": deduped,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--root", default=".", help="Repo root (default: cwd).")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST, help="Manifest path.")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    ap.add_argument("--list", action="store_true", help="List scanned/skipped files.")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        result = scan(root, args.manifest)
    except SetupError as exc:
        if args.json:
            print(json.dumps({"ok": False, "setup_error": str(exc)}, ensure_ascii=False))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    print(f"Public claim drift sweep — scanned {result['scanned_count']} markdown "
          f"surface(s) against {args.manifest}")
    if args.list:
        for rel in result["scanned"]:
            print(f"  scanned: {rel}")
        for s in result["skipped"]:
            print(f"  skipped: {s['file']} ({s['reason']})")
    if result["ok"]:
        print("[OK] every quantified public claim agrees with the manifest.")
        return 0
    for p in result["problems"]:
        if p["kind"] == "stale_claim":
            print(f"::error::{p['file']}: {p['fact']} claims {p['claimed']} "
                  f"but manifest says {p['expected']}")
        else:
            print(f"::error::{p['file']}: overclaim phrase present: \"{p['phrase']}\"")
    print(f"[FAIL] {len(result['problems'])} claim drift / overclaim problem(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
