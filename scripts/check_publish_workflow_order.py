"""Static lint for publish.yml release-gate ordering.

The publish workflow runs on a fresh GitHub Actions runner. Any gate that
imports ``piia_engram`` must run after project dependencies are installed, or
the release can fail before reaching PyPI. This script is intentionally
dependency-free: it scans the workflow text for step order and fails if a known
project-script gate appears before the dependency install step.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


DEFAULT_WORKFLOW = Path(".github") / "workflows" / "publish.yml"
INSTALL_MARKERS = (
    "pip install -e .",
    'pip install -e ".[dev]"',
    "pip install -e '.[dev]'",
)
PROJECT_GATE_MARKERS = (
    "python scripts/check_export_redaction.py",
    "python scripts/check_generated_export_redaction.py",
    "python scripts/check_release_gate.py",
    "python scripts/release_sanitize_check.py",
    "python scripts/check_release_artifact_private_terms.py",
)
REQUIRED_SUPPLY_CHAIN_PERMISSIONS = (
    "id-token: write",
    "attestations: write",
    "artifact-metadata: write",
)
SUPPLY_CHAIN_ORDER_MARKERS = (
    ("SBOM generation", "cyclonedx-py environment"),
    ("SBOM hygiene", "python scripts/check_sbom_hygiene.py dist/piia-engram-sbom.cdx.json"),
    ("build provenance attestation", "- name: Attest build provenance"),
    ("SBOM attestation", "- name: Attest SBOM"),
    # gh-action-pypi-publish uploads every file in dist/ and fails on
    # non-distribution files, so the SBOM must be removed before publish.
    ("SBOM removal before publish", "rm dist/piia-engram-sbom.cdx.json"),
    ("PyPI publish", "pypa/gh-action-pypi-publish@release/v1"),
)
ATTEST_ACTION_RE = re.compile(r"uses:\s*actions/(attest(?:-build-provenance|-sbom)?)@([^\s#]+)")
STEP_RE = re.compile(r"(?ms)^      - name: (?P<name>[^\n]+)\n(?P<body>.*?)(?=^      - name: |\Z)")
EXPECTED_ATTEST_SUBJECTS = {"dist/*.whl", "dist/*.tar.gz"}


def _check_dependency_install_order(text: str) -> list[str]:
    problems: list[str] = []
    install_positions = [text.find(marker) for marker in INSTALL_MARKERS]
    install_positions = [pos for pos in install_positions if pos >= 0]
    if not install_positions:
        return ["missing project dependency install step before publish gates"]

    first_install = min(install_positions)
    for marker in PROJECT_GATE_MARKERS:
        pos = text.find(marker)
        if pos >= 0 and pos < first_install:
            problems.append(f"project gate appears before dependency install: {marker}")
    return problems


def _attest_steps(text: str) -> dict[str, str]:
    return {
        match.group("name").strip(): match.group("body")
        for match in STEP_RE.finditer(text)
        if "uses: actions/attest@v4" in match.group("body")
    }


def _subject_path_values(step_body: str) -> list[str]:
    lines = step_body.splitlines()
    for index, line in enumerate(lines):
        if "subject-path:" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        values: list[str] = []
        for item in lines[index + 1:]:
            stripped = item.strip()
            if not stripped:
                continue
            item_indent = len(item) - len(item.lstrip())
            if item_indent <= indent or stripped.endswith(":") or ":" in stripped:
                break
            values.append(stripped)
        return values
    return []


def _check_attest_subjects(text: str) -> list[str]:
    problems: list[str] = []
    attest_steps = _attest_steps(text)
    for step_name in ("Attest build provenance", "Attest SBOM"):
        body = attest_steps.get(step_name)
        if body is None:
            problems.append(f"missing {step_name} step using actions/attest@v4")
            continue
        subjects = _subject_path_values(body)
        if set(subjects) != EXPECTED_ATTEST_SUBJECTS:
            problems.append(
                f"{step_name} subject-path must contain only dist/*.whl and dist/*.tar.gz"
            )
        if "dist/piia-engram-sbom.cdx.json" in subjects:
            problems.append("SBOM file must not be an attestation subject")
    sbom_body = attest_steps.get("Attest SBOM", "")
    if "sbom-path: dist/piia-engram-sbom.cdx.json" not in sbom_body:
        problems.append("Attest SBOM must use sbom-path: dist/piia-engram-sbom.cdx.json")
    return problems


def _check_supply_chain_contract(text: str) -> list[str]:
    problems: list[str] = []

    attest_actions = ATTEST_ACTION_RE.findall(text)
    if not attest_actions:
        problems.append("missing actions/attest@v4 usage")
    for action, version in attest_actions:
        if action != "attest" or version != "v4":
            problems.append(f"unsupported attest action reference: actions/{action}@{version}")

    for permission in REQUIRED_SUPPLY_CHAIN_PERMISSIONS:
        if permission not in text:
            problems.append(f"missing required permission: {permission}")
    if "contents: write" in text:
        problems.append("publish workflow must not request contents: write")

    last_pos = -1
    for label, marker in SUPPLY_CHAIN_ORDER_MARKERS:
        pos = text.find(marker)
        if pos < 0:
            problems.append(f"missing supply-chain step marker: {label}")
            continue
        if pos <= last_pos:
            problems.append(f"supply-chain step out of order: {label}")
        last_pos = pos

    problems.extend(_check_attest_subjects(text))
    return problems


def check_publish_workflow_order(
    text: str,
    *,
    require_supply_chain: bool = False,
) -> tuple[bool, list[str]]:
    """Return whether publish workflow ordering and optional contract are safe."""
    problems = _check_dependency_install_order(text)
    if require_supply_chain:
        problems.extend(_check_supply_chain_contract(text))
    return not problems, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflow", default=str(DEFAULT_WORKFLOW))
    args = parser.parse_args(argv)

    path = Path(args.workflow)
    if not path.is_file():
        print(f"::error::workflow not found: {path}", file=sys.stderr)
        return 2
    ok, problems = check_publish_workflow_order(
        path.read_text(encoding="utf-8"),
        require_supply_chain=True,
    )
    if ok:
        print(f"[OK] publish workflow order and supply-chain contract passed ({path}).")
        return 0
    print("::error::publish workflow order is unsafe:")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
