"""Public-fact drift guard - keep released-vs-dev public truth honest.

WHY: piia-engram's public credibility erodes when the numbers we publish about
ourselves drift apart - README says one test count, the zh README another, the
architecture doc a stale tool split, a manifest an old version. Process docs
("remember to update the README") are pull-based and get skipped exactly when
shipping fast. This script makes the environment enforce it: a single
machine-readable manifest (``docs/public-facts.json``) is the source of truth,
and the CURRENT-STATE public docs must agree with it.

It deliberately does NOT touch *historical* surfaces (CHANGELOG entries, the
``release-evidence/`` files). Those record what was true at an older release and
are SUPPOSED to carry old numbers - failing them would punish keeping history.

What it checks (all driven by the manifest):

1. Manifest sanity: required keys present; ``test_passed + test_skipped ==
   test_collected``; ``mcp_tools_core + mcp_tools_advanced == mcp_tools_total``.
2. Manifest freshness: ``local_dev_version`` matches ``pyproject.toml`` so the
   manifest itself cannot silently lag the package version.
3. Runtime collection profile: when the manifest declares it, run a deterministic
   ``pytest tests/ --collect-only -q`` profile and compare it to
   ``facts.test_collected``.
4. Version-bearing surfaces (.mcp/server.json, plugin.json, glama.yaml) carry
   exactly ``local_dev_version``.
5. Test-count renderings in README / README.zh-CN equal ``facts.test_passed``
   (generic: catches 2346 and any other stale number, now or future).
6. Required current-state substrings (the manifest's current tool split, etc.)
   are present.
7. No known-stale string (e.g. ``**2346**``) appears in any current-state
   surface.

Run from repo root:

    python scripts/check_public_fact_sync.py            # human report
    python scripts/check_public_fact_sync.py --json     # machine-readable
    python scripts/check_public_fact_sync.py --manifest docs/public-facts.json

Exit codes:
- 0  every current-state public fact agrees with the manifest
- 1  drift found (stale number / version / tool count / known-stale string)
- 2  setup error (manifest missing/invalid, surface file missing)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_MANIFEST = "docs/public-facts.json"

_REQUIRED_TOP_KEYS = (
    "schema_version",
    "package_name",
    "local_dev_version",
    "release_frame",
    "facts",
    "last_verified_date",
    "sources",
    "current_state_surfaces",
    "historical_surfaces",
    "checks",
)
_REQUIRED_FACT_KEYS = (
    "test_passed",
    "test_skipped",
    "test_collected",
    "mcp_tools_total",
    "mcp_tools_core",
    "mcp_tools_advanced",
    "telemetry_default",
    "telemetry_remote_default",
)


class SetupError(Exception):
    """Manifest or repo layout problem - distinct from a drift failure."""


class CollectionProfileError(Exception):
    """The canonical test collection profile could not produce a count."""


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise SetupError(f"manifest not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SetupError(f"manifest is not valid JSON: {exc}") from exc


def _pyproject_version(root: Path) -> str | None:
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r'\s*version\s*=\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return None


def _read(root: Path, rel: str) -> str | None:
    p = root / rel
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="ignore")


_COLLECTED_RE = re.compile(r"(?m)(\d+)\s+tests?\s+collected\b")


def _collection_env(root: Path, isolated_store: Path) -> dict[str, str]:
    """Build the canonical collection environment for public test facts."""
    env = os.environ.copy()
    env["ENGRAM_TEST"] = "1"
    env["ENGRAM_DIR"] = str(isolated_store)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    src = str((root / "src").resolve())
    existing = env.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if src not in parts:
        env["PYTHONPATH"] = os.pathsep.join([src, *parts])
    return env


def _parse_collected_count(output: str) -> int:
    matches = _COLLECTED_RE.findall(output)
    if not matches:
        raise CollectionProfileError("pytest collect-only output had no collected-count summary")
    return int(matches[-1])


def collect_pytest_tests(root: Path, *, python: str = sys.executable) -> int:
    """Run the one canonical collect-only profile used by public facts."""
    with tempfile.TemporaryDirectory(prefix="engram-public-facts-") as tmp:
        proc = subprocess.run(
            [python, "-m", "pytest", "tests/", "--collect-only", "-q"],
            cwd=root,
            env=_collection_env(root, Path(tmp) / "engram-home"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
    if proc.returncode != 0:
        tail = " ".join(output.splitlines()[-3:])[:500] or "no output"
        raise CollectionProfileError(
            f"pytest collect-only exited {proc.returncode}: {tail}"
        )
    return _parse_collected_count(output)


def validate_manifest_schema(manifest: dict) -> list[str]:
    """Return structural problems (empty == schema OK)."""
    problems: list[str] = []
    for key in _REQUIRED_TOP_KEYS:
        if key not in manifest:
            problems.append(f"manifest missing top-level key: '{key}'")
    facts = manifest.get("facts")
    if not isinstance(facts, dict):
        problems.append("manifest 'facts' must be an object")
        return problems
    for key in _REQUIRED_FACT_KEYS:
        if key not in facts:
            problems.append(f"manifest facts missing key: '{key}'")

    # Internal invariants - a manifest that contradicts itself is its own bug.
    if all(k in facts for k in ("test_passed", "test_skipped", "test_collected")):
        if facts["test_passed"] + facts["test_skipped"] != facts["test_collected"]:
            problems.append(
                f"manifest invariant violated: test_passed ({facts['test_passed']}) "
                f"+ test_skipped ({facts['test_skipped']}) != test_collected "
                f"({facts['test_collected']})"
            )
    if all(k in facts for k in ("mcp_tools_total", "mcp_tools_core", "mcp_tools_advanced")):
        if facts["mcp_tools_core"] + facts["mcp_tools_advanced"] != facts["mcp_tools_total"]:
            problems.append(
                f"manifest invariant violated: mcp_tools_core "
                f"({facts['mcp_tools_core']}) + mcp_tools_advanced "
                f"({facts['mcp_tools_advanced']}) != mcp_tools_total "
                f"({facts['mcp_tools_total']})"
            )
    return problems


def check_facts(manifest: dict, root: Path) -> list[str]:
    """Return drift problems (empty == in sync). Raises SetupError for a
    missing surface file (we fail *closed*: a surface we expect to police but
    cannot read is a setup error, not a silent pass)."""
    problems: list[str] = []
    version = manifest["local_dev_version"]
    facts = manifest["facts"]
    checks = manifest.get("checks", {})

    # (2) Manifest must not lag the package source of truth.
    pyproj = _pyproject_version(root)
    if pyproj is not None and pyproj != version:
        problems.append(
            f"manifest local_dev_version ({version}) != pyproject.toml ({pyproj}) "
            f"-- refresh docs/public-facts.json"
        )

    # (3) Runtime collection profile must match facts.test_collected.
    profile = checks.get("collection_profile")
    if profile:
        fact = profile.get("fact", "test_collected")
        if fact not in facts:
            problems.append(f"collection profile fact missing from manifest facts: {fact}")
        else:
            try:
                actual_collected = collect_pytest_tests(root)
            except CollectionProfileError as exc:
                problems.append(f"collection profile failed: {exc}")
            else:
                expected_collected = int(facts[fact])
                if actual_collected != expected_collected:
                    problems.append(
                        f"collection profile drift: {fact} runtime value "
                        f"{actual_collected} != manifest facts.{fact} "
                        f"{expected_collected}"
                    )

    # (4) Version-bearing surfaces must carry the current version.
    for entry in checks.get("version_bearing", []):
        rel, pat = entry["file"], entry["pattern"]
        text = _read(root, rel)
        if text is None:
            raise SetupError(f"version-bearing surface missing: {rel}")
        m = re.search(pat, text)
        if not m:
            problems.append(f"{rel}: no version string matched /{pat}/")
        elif m.group(1) != version:
            problems.append(
                f"{rel}: version '{m.group(1)}' != manifest local_dev_version "
                f"'{version}'"
            )

    # (5) Test-count renderings must equal facts.test_passed.
    expected_passed = str(facts["test_passed"])
    for entry in checks.get("test_count_patterns", []):
        rel, pat = entry["file"], entry["pattern"]
        text = _read(root, rel)
        if text is None:
            raise SetupError(f"test-count surface missing: {rel}")
        found = re.findall(pat, text)
        if not found:
            problems.append(f"{rel}: no test count matched /{pat}/")
        for got in found:
            if got != expected_passed:
                problems.append(
                    f"{rel}: stale test count '{got}' (expected "
                    f"'{expected_passed}' from manifest)"
                )

    # (6) Required current-state substrings must be present.
    for entry in checks.get("required_substrings", []):
        rel = entry["file"]
        text = _read(root, rel)
        if text is None:
            raise SetupError(f"required-substring surface missing: {rel}")
        for needle in entry["must_contain"]:
            if needle not in text:
                problems.append(f"{rel}: missing required current-state text: {needle!r}")

    # (7) Known-stale strings must not appear in any current-state surface.
    forbidden = checks.get("forbidden_in_current_state", [])
    for rel in manifest.get("current_state_surfaces", []):
        text = _read(root, rel)
        if text is None:
            raise SetupError(f"current-state surface missing: {rel}")
        for bad in forbidden:
            if bad in text:
                problems.append(
                    f"{rel}: known-stale string present: {bad!r} "
                    f"(this is a current-state surface, not history)"
                )

    return problems


def run(manifest_path: Path, root: Path) -> tuple[bool, dict]:
    """Return (ok, report). Raises SetupError on manifest/layout problems."""
    manifest = load_manifest(manifest_path)
    schema_problems = validate_manifest_schema(manifest)
    if schema_problems:
        # A broken manifest is a setup error, not silent drift.
        raise SetupError("; ".join(schema_problems))
    drift = check_facts(manifest, root)
    report = {
        "ok": not drift,
        "manifest": str(manifest_path),
        "local_dev_version": manifest["local_dev_version"],
        "last_verified_date": manifest.get("last_verified_date"),
        "facts": manifest["facts"],
        "problems": drift,
    }
    return (not drift), report


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else ""
    )
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                    help=f"Path to fact manifest (default: {DEFAULT_MANIFEST})")
    ap.add_argument("--root", default=".", help="Repo root (default: cwd)")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of a human report")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    manifest_path = (root / args.manifest) if not Path(args.manifest).is_absolute() \
        else Path(args.manifest)

    try:
        ok, report = run(manifest_path, root)
    except SetupError as exc:
        if args.json:
            print(json.dumps({"ok": False, "setup_error": str(exc)}, indent=2))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    if ok:
        print(f"[OK] public facts in sync with {args.manifest} "
              f"(v{report['local_dev_version']}, verified "
              f"{report['last_verified_date']}).")
        return 0

    print(f"::error::public-fact drift detected against {args.manifest}:")
    for p in report["problems"]:
        print(f"  - {p}")
    print("")
    print("Current-state public docs disagree with the fact manifest. Either")
    print("update the doc to the manifest value, or - if the underlying number")
    print("really changed - refresh docs/public-facts.json (and re-verify the")
    print("source command in its 'sources' block) first. Historical surfaces")
    print("(CHANGELOG, release-evidence/) are intentionally NOT policed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
