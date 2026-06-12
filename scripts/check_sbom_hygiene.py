"""Fail closed on unsafe CycloneDX SBOM output.

Exit codes:
  0: clean
  1: dirty SBOM content
  2: setup/input error
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


CLEAN = 0
DIRTY = 1
SETUP_ERROR = 2

PRIVATE_STRING_MARKERS = (
    r"C:\Users",
    "C:/Users",
    "E:\\",
    "E:/",
    "/home/runner",
    "/Users/runner",
    "\\AppData\\",
    "RUNNER_TEMP",
)

# Usernames too generic to treat as identifying markers (CI runners,
# containers); matching them as substrings would false-positive.
_GENERIC_USERNAMES = frozenset(
    {"runner", "runneradmin", "user", "admin", "root", "administrator", "default"}
)


def _dynamic_private_markers() -> tuple[str, ...]:
    """Markers that identify this build machine without hardcoding them.

    The local username must never appear in a published SBOM, but writing
    it into this public script would itself leak it. Derive it at runtime
    and allow extra markers via ENGRAM_SBOM_PRIVATE_MARKERS (comma-separated).
    """
    markers = [
        m.strip()
        for m in os.environ.get("ENGRAM_SBOM_PRIVATE_MARKERS", "").split(",")
        if m.strip()
    ]
    try:
        username = getpass.getuser()
    except Exception:
        username = ""
    if len(username) >= 4 and username.lower() not in _GENERIC_USERNAMES:
        markers.append(username)
    return tuple(markers)

# actions/attest only recognizes a CycloneDX SBOM when bomFormat,
# serialNumber and specVersion are all present; a missing field fails the
# publish run at attestation time, which dryrun cannot exercise (OIDC-only).
_SERIAL_NUMBER_RE = re.compile(
    r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

TOOLCHAIN_COMPONENT_NAMES = {
    "cyclonedx-bom",
    "cyclonedx-python-lib",
    "packageurl-python",
}


def _iter_string_values(value: Any, path: str = "$"):
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_string_values(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_string_values(item, f"{path}.{key}")


def _main_component_mentions_project(document: dict[str, Any]) -> bool:
    component = document.get("metadata", {}).get("component")
    if not isinstance(component, dict):
        return False
    haystack = " ".join(
        str(component.get(key, ""))
        for key in ("name", "bom-ref", "purl", "group", "description")
    ).lower()
    return "piia-engram" in haystack


def _component_identity(component: dict[str, Any]) -> str:
    return " ".join(
        str(component.get(key, ""))
        for key in ("name", "bom-ref", "purl", "group")
    ).lower()


def _validate_structure(document: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["SBOM root must be a JSON object"]
    if document.get("bomFormat") != "CycloneDX":
        problems.append('SBOM bomFormat must be "CycloneDX"')
    spec_version = document.get("specVersion")
    if not isinstance(spec_version, str) or not spec_version:
        problems.append(
            "SBOM specVersion must be a non-empty string (required by actions/attest)"
        )
    serial_number = document.get("serialNumber")
    if not isinstance(serial_number, str) or not _SERIAL_NUMBER_RE.match(serial_number):
        problems.append(
            'SBOM serialNumber must match "urn:uuid:<uuid>" (required by actions/attest)'
        )
    components = document.get("components")
    if not isinstance(components, list):
        problems.append("SBOM components must be a list")
    if not _main_component_mentions_project(document):
        problems.append("SBOM metadata.component must identify piia-engram")
    return problems


def _find_dirty_content(document: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    markers = PRIVATE_STRING_MARKERS + _dynamic_private_markers()
    for path, value in _iter_string_values(document):
        for marker in markers:
            if marker in value:
                problems.append(f"private or runner path marker {marker!r} found at {path}")

    components = document.get("components", [])
    if isinstance(components, list):
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                continue
            identity = _component_identity(component)
            for name in TOOLCHAIN_COMPONENT_NAMES:
                if name in identity:
                    problems.append(
                        f"toolchain component {name!r} found in components[{index}]"
                    )
    return problems


def check_sbom_hygiene(path: Path | str) -> tuple[int, list[str]]:
    sbom_path = Path(path)
    try:
        text = sbom_path.read_text(encoding="utf-8")
    except OSError as exc:
        return SETUP_ERROR, [f"cannot read SBOM: {exc}"]

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return SETUP_ERROR, [f"invalid JSON: {exc}"]

    structure_problems = _validate_structure(document)
    if structure_problems:
        return SETUP_ERROR, structure_problems

    dirty_problems = _find_dirty_content(document)
    if dirty_problems:
        return DIRTY, dirty_problems
    return CLEAN, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sbom_json")
    args = parser.parse_args(argv)

    code, problems = check_sbom_hygiene(args.sbom_json)
    if code == CLEAN:
        print(f"[OK] SBOM hygiene check passed ({args.sbom_json}).")
        return CLEAN

    marker = "::error::" if code == DIRTY else "[error]"
    print(f"{marker} SBOM hygiene check failed ({args.sbom_json}):", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
