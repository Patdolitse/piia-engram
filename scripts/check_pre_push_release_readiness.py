"""Local pre-push / pre-release readiness aggregator.

Runs the repo-local, read-only gates that should be green before asking the
owner to approve a public action. It performs no push, tag, upload, registry
write, external refresh, or deploy.

The default set is a 1:1 mirror of the ci.yml ``test`` job's guard steps (see
CI_TEST_JOB_CHECKS) plus a few cheap extras, so a green run here means those CI
guards are green locally. Add ``--full-tests`` to also run the full pytest suite
(``pytest tests/ -q``) -- that makes this a complete local replay of the ci
``test`` job before you push. Runs on the current interpreter only; it does not
replicate CI's 3.10-3.13 matrix. A parity test (tests/) fails if the mirror
drifts from ci.yml.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


# --- ci.yml `test` job mirror -------------------------------------------------
# These MUST stay a 1:1, in-order mirror of the guard steps in
# .github/workflows/ci.yml `test` job (minus `pip install`). 4.3.0 AND 4.4.0
# both shipped a CI-red because pre-push verification ran only a hand-picked
# SUBSET of these. test_ci_test_job_parity (tests/test_pre_push_release_
# readiness.py) parses ci.yml and FAILS if this list drifts from the workflow,
# so the mirror can never silently fall behind CI again. Run on the current
# local interpreter only -- this does NOT replicate CI's 3.10-3.13 matrix.
CI_TEST_JOB_CHECKS = (
    ("publish_workflow_order", [sys.executable, "scripts/check_publish_workflow_order.py"]),
    ("ci_pytest_entrypoint",
     [sys.executable, "scripts/check_ci_pytest_entrypoint.py", "--discover-script-imports"]),
    ("public_fact_sync", [sys.executable, "scripts/check_public_fact_sync.py"]),
    ("public_claim_drift", [sys.executable, "scripts/check_public_claim_drift.py"]),
    ("public_trust_claims", [sys.executable, "scripts/check_public_trust_claims.py"]),
    ("public_reference_truth", [sys.executable, "scripts/check_public_reference_truth.py"]),
    ("release_preflight", [sys.executable, "scripts/check_release_preflight.py"]),
    ("release_sanitize",
     [sys.executable, "scripts/release_sanitize_check.py", "--internal", "--strict"]),
    ("worker_public_config", [sys.executable, "scripts/check_worker_public_config.py"]),
    ("export_redaction",
     [sys.executable, "scripts/check_export_redaction.py", "--strict",
      "docs/samples/export-redaction-clean-sample.md"]),
    ("generated_export_redaction",
     [sys.executable, "scripts/check_generated_export_redaction.py"]),
    ("embedded_contract",
     [sys.executable, "scripts/check_embedded_contract.py"]),
    ("embedded_witness",
     [sys.executable, "scripts/generate_capability_witness.py", "--verify",
      "docs/embedded/capability-witness.json"]),
    ("memory_eval_suite", [sys.executable, "scripts/run_memory_evals.py", "--json"]),
)

# Extra local guards NOT in the ci `test` job but cheap and worth running before
# a push (they run in other ci jobs / git hooks). Additive -- not part of the
# parity mirror.
EXTRA_CHECKS = (
    ("count_mcp_tools_smoke", [sys.executable, "scripts/count_mcp_tools.py", "--json"]),
    ("product_boundary", [sys.executable, "scripts/check_product_boundary.py"]),
    ("publish_allowlist", [sys.executable, "scripts/check_publish_allowlist.py"]),
    ("public_release_surface", [sys.executable, "scripts/check_public_release_surface.py"]),
)

DEFAULT_CHECKS = CI_TEST_JOB_CHECKS + EXTRA_CHECKS

# Final step of the ci `test` job; gated behind --full-tests so the default stays
# fast. `tests/ -q` matches ci exactly (run via -m for interpreter parity).
FULL_TEST_CHECK = ("pytest", [sys.executable, "-m", "pytest", "tests/", "-q"])


def _run(cmd: list[str], root: Path, timeout: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as exc:
        return 127, "", f"executable not found: {exc.filename}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


def run_checks(root: str | Path, *, full_tests: bool = False, timeout: int = 1200) -> dict:
    root_path = Path(root).resolve()
    checks = list(DEFAULT_CHECKS)
    if full_tests:
        checks.append(FULL_TEST_CHECK)

    results = []
    for name, cmd in checks:
        rc, stdout, stderr = _run(cmd, root_path, timeout)
        results.append({
            "name": name,
            "command": cmd,
            "returncode": rc,
            "ok": rc == 0,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        })

    ok = all(item["ok"] for item in results)
    return {
        "ok": ok,
        "root": str(root_path),
        "full_tests": full_tests,
        "results": results,
        "note": "read-only local checks only; no public action performed",
    }


def _summarize_output(item: dict, *, max_lines: int = 3) -> list[str]:
    output = item["stderr"] if not item["ok"] and item["stderr"] else item["stdout"] or item["stderr"]
    if not output:
        return []
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return [f"  {line}" for line in lines]


def render_text(report: dict) -> str:
    lines = [
        "Pre-push / pre-release readiness",
        f"Root: {report['root']}",
        f"Mode: {'full' if report['full_tests'] else 'lightweight'}",
        "",
    ]
    for item in report["results"]:
        status = "OK" if item["ok"] else "FAIL"
        lines.append(f"[{status}] {item['name']}")
        lines.extend(_summarize_output(item))
    lines.extend([
        "",
        "[OK] all local readiness checks passed." if report["ok"]
        else "[FAIL] one or more local readiness checks failed.",
        "No push/tag/release/upload/registry/deploy/external refresh was performed.",
    ])
    return "\n".join(lines)


def _print_safe(text: str) -> None:
    """Print without crashing on narrow Windows console encodings."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(safe)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Local pre-push / pre-release readiness aggregator. "
            "Default checks are local file / git-index reads only; performs no "
            "push, tag, release, upload, registry write, deploy, or external "
            "refresh."
        )
    )
    parser.add_argument("--root", default=".", help="Repo root (default: cwd)")
    parser.add_argument("--full-tests", action="store_true",
                        help="Also run the full suite (pytest tests/ -q) for a complete ci test-job replay")
    parser.add_argument("--timeout", type=int, default=1200, help="Per-check timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    report = run_checks(args.root, full_tests=args.full_tests, timeout=args.timeout)
    if args.json:
        _print_safe(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_safe(render_text(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
