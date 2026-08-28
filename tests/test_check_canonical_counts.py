"""v4.19 canonical-count patch-artifact behavior (review+apply, never auto-merge).

Companion to the release-chain self-healing scope: the gate must emit a
manifest-count patch ONLY for a fully green suite with real drift; a red,
empty, or malformed run fails closed with NO patch (a broken run must never
turn into a confidently wrong suggestion).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "check_canonical_counts.py"

spec = importlib.util.spec_from_file_location("check_canonical_counts", _SCRIPT)
ccc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ccc)


def _junit(path: Path, *, tests: int, failures: int = 0, errors: int = 0, skipped: int = 0) -> Path:
    xml = (
        '<testsuites><testsuite name="s" '
        f'tests="{tests}" failures="{failures}" errors="{errors}" skipped="{skipped}"'
        "></testsuite></testsuites>"
    )
    path.write_text(xml, encoding="utf-8")
    return path


def _manifest(path: Path, *, passed: int, skipped: int, collected: int) -> Path:
    data = {"facts": {
        "test_passed": passed, "test_skipped": skipped, "test_collected": collected,
    }}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _run(junit: Path, manifest: Path, patch: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, str(_SCRIPT), str(junit),
            "--manifest", str(manifest), "--patch-output", str(patch),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_green_drift_emits_patch_and_still_blocks(tmp_path: Path):
    junit = _junit(tmp_path / "j.xml", tests=10)
    manifest = _manifest(tmp_path / "facts.json", passed=9, skipped=1, collected=10)
    patch = tmp_path / "out.patch"

    proc = _run(junit, manifest, patch)

    assert proc.returncode == 1  # drift still blocks
    assert patch.is_file()
    text = patch.read_text(encoding="utf-8")
    assert '"test_passed": 9' in text
    assert '"test_passed": 10' in text
    assert '"test_skipped": 1' in text  # unchanged fields stay out of the diff hunks


def test_no_drift_no_patch(tmp_path: Path):
    junit = _junit(tmp_path / "j.xml", tests=10)
    manifest = _manifest(tmp_path / "facts.json", passed=10, skipped=0, collected=10)
    patch = tmp_path / "out.patch"

    proc = _run(junit, manifest, patch)

    assert proc.returncode == 0
    assert not patch.exists()


def test_red_suite_fails_closed_with_no_patch(tmp_path: Path):
    junit = _junit(tmp_path / "j.xml", tests=10, failures=2)
    manifest = _manifest(tmp_path / "facts.json", passed=9, skipped=1, collected=10)
    patch = tmp_path / "out.patch"

    proc = _run(junit, manifest, patch)

    assert proc.returncode == 1
    assert "not green" in proc.stdout
    assert not patch.exists()


def test_empty_junit_fails_closed_with_no_patch(tmp_path: Path):
    junit = _junit(tmp_path / "j.xml", tests=0)
    manifest = _manifest(tmp_path / "facts.json", passed=9, skipped=1, collected=10)
    patch = tmp_path / "out.patch"

    proc = _run(junit, manifest, patch)

    assert proc.returncode == 1
    assert "no tests" in proc.stdout
    assert not patch.exists()


def test_malformed_junit_fails_closed_with_no_patch(tmp_path: Path):
    junit = tmp_path / "j.xml"
    junit.write_text("<testsuites><testsuite ", encoding="utf-8")
    manifest = _manifest(tmp_path / "facts.json", passed=9, skipped=1, collected=10)
    patch = tmp_path / "out.patch"

    proc = _run(junit, manifest, patch)

    assert proc.returncode == 1
    assert "unreadable or malformed" in proc.stdout
    assert not patch.exists()


def test_patch_changes_only_the_three_counts(tmp_path: Path):
    junit = _junit(tmp_path / "j.xml", tests=12, skipped=2)
    manifest_data = {
        "facts": {
            "test_passed": 5, "test_skipped": 5, "test_collected": 5,
            "mcp_tools_total": 59,
        },
        "other": {"keep": "me"},
    }
    manifest = tmp_path / "facts.json"
    manifest.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")
    patch = tmp_path / "out.patch"

    proc = _run(junit, manifest, patch)
    assert proc.returncode == 1
    assert patch.is_file()
    text = patch.read_text(encoding="utf-8")
    # only the three count lines move; untouched keys never appear as changes
    assert '-    "test_passed": 5,' in text
    assert '+    "test_passed": 10,' in text
    assert '+    "test_skipped": 2,' in text
    assert '+    "test_collected": 12,' in text
    # untouched keys may appear as diff CONTEXT lines, never as changes
    changed_lines = [ln for ln in text.splitlines() if ln[:1] in {"-", "+"}]
    assert not any("mcp_tools_total" in ln or '"keep"' in ln for ln in changed_lines)


def test_unit_junit_counts_parses_nested_suites(tmp_path: Path):
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1"/>'
        '<testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    counts = ccc.junit_counts(junit)
    assert counts == {"collected": 5, "passed": 3, "skipped": 1, "failed": 1, "errors": 0}
